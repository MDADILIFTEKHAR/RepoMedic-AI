"""FastAPI application for RepoMedic AI.

Provides REST API endpoints for health status, benchmark task inspection,
background repair job execution, evaluation metrics, and serves the developer frontend.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import uuid

from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from repomedic import __version__
from repomedic.config import Settings, get_settings
from repomedic.evaluation.benchmark import BENCHMARK_TASKS, BenchmarkRunner, BenchmarkTaskSpec
from repomedic.models import create_model_client
from repomedic.orchestrator import EvidenceGatedOrchestrator, RepairSessionReport, RepairState
from repomedic.sandbox.runner import create_sandbox_runner

logger = logging.getLogger("repomedic.api")

app = FastAPI(
    title="RepoMedic AI",
    description="Evidence-Gated Python Bug-Repair Harness using Open-Weight Coding Models",
    version=__version__,
)

# CORS middleware for local frontend development or separate origin deployments
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory repair job registry
repair_jobs: Dict[str, Dict[str, Any]] = {}
executor = ThreadPoolExecutor(max_workers=4)


class RepairRequest(BaseModel):
    """Payload to initiate a bug repair session."""

    fixture_id: Optional[str] = Field(
        default=None,
        description="ID of a predefined benchmark fixture (e.g. task_01_arithmetic_discount)",
    )
    local_repo_path: Optional[str] = Field(
        default=None,
        description="Absolute local filesystem path to target repository (Local mode with Docker only)",
    )
    issue_description: Optional[str] = Field(
        default=None,
        description="Description of the bug, symptom, or expected behavior",
    )


class JobStatusResponse(BaseModel):
    """Current state and audit progress of an active or finished repair job."""

    job_id: str
    status: str
    current_state: str
    progress_message: str
    attempts_used: int
    max_attempts: int
    created_at: float
    completed_at: Optional[float] = None
    report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Truthful health check endpoint reporting service, model, and sandbox status."""
    settings = get_settings()
    model_client = create_model_client(settings)

    model_health: Dict[str, Any] = {}
    try:
        model_health = await model_client.check_health()
    except Exception as ex:
        model_health = {"available": False, "status": f"Error: {str(ex)}", "model_present": False}

    return {
        "status": "online",
        "version": __version__,
        "environment": settings.environment,
        "hosted_mode": settings.hosted_mode,
        "model_provider": settings.model_provider,
        "model_name": (
            settings.ollama_model if settings.model_provider == "ollama" else settings.openai_model
        ),
        "model_health": model_health,
        "docker_available": settings.is_docker_available,
        "arbitrary_repo_repair_enabled": settings.is_docker_available and not settings.hosted_mode,
        "timestamp": time.time(),
    }


@app.get("/api/benchmark/tasks")
async def list_benchmark_tasks() -> List[Dict[str, Any]]:
    """List all safe, predefined benchmark tasks available for demo and evaluation."""
    return [
        {
            "task_id": t.task_id,
            "name": t.name,
            "category": t.category,
            "issue_description": t.issue_description,
            "fixture_dir_name": t.fixture_dir_name,
        }
        for t in BENCHMARK_TASKS
    ]


async def _execute_repair_task(job_id: str, repo_path: Path, issue_desc: str, is_fixture: bool) -> None:
    """Asynchronous background worker function executing the repair harness."""
    settings = get_settings()
    model_client = create_model_client(settings)
    sandbox_runner = create_sandbox_runner(timeout_seconds=settings.test_timeout_seconds)

    def on_state_update(state: RepairState, message: str) -> None:
        if job_id in repair_jobs:
            repair_jobs[job_id]["current_state"] = state.value
            repair_jobs[job_id]["progress_message"] = message

    orchestrator = EvidenceGatedOrchestrator(
        model_client=model_client,
        settings=settings,
        sandbox_runner=sandbox_runner,
        on_state_change=on_state_update,
    )

    try:
        report: RepairSessionReport = await orchestrator.run_repair(
            repo_path=repo_path,
            issue_description=issue_desc,
            is_predefined_fixture=is_fixture,
        )
        repair_jobs[job_id]["status"] = "completed"
        repair_jobs[job_id]["completed_at"] = time.time()
        repair_jobs[job_id]["current_state"] = report.terminal_state.value
        repair_jobs[job_id]["attempts_used"] = report.attempts_count
        repair_jobs[job_id]["report"] = report.model_dump()
        repair_jobs[job_id]["progress_message"] = report.summary_message
    except Exception as ex:
        logger.exception("Error executing repair job %s", job_id)
        repair_jobs[job_id]["status"] = "failed"
        repair_jobs[job_id]["completed_at"] = time.time()
        repair_jobs[job_id]["error"] = str(ex)
        repair_jobs[job_id]["current_state"] = RepairState.INFRASTRUCTURE_ERROR.value
        repair_jobs[job_id]["progress_message"] = f"Internal execution failure: {str(ex)}"


@app.post("/api/repair/start", response_model=JobStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_repair(req: RepairRequest, background_tasks: BackgroundTasks) -> JobStatusResponse:
    """Start an asynchronous repair session on a predefined fixture or local repository."""
    settings = get_settings()
    fixtures_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures"

    target_path: Path
    issue_desc: str
    is_fixture: bool = False

    if req.fixture_id:
        matched = next((t for t in BENCHMARK_TASKS if t.task_id == req.fixture_id), None)
        if not matched:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Benchmark fixture '{req.fixture_id}' not found.",
            )
        target_path = fixtures_dir / matched.fixture_dir_name
        issue_desc = req.issue_description or matched.issue_description
        is_fixture = True
    elif req.local_repo_path:
        if settings.hosted_mode:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Arbitrary repository repair is disabled in hosted demo mode. Please select a predefined benchmark fixture.",
            )
        if not settings.is_docker_available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Docker is required for arbitrary repository test execution, but is unavailable on this host. Arbitrary code execution is disabled to protect the host system.",
            )
        target_path = Path(req.local_repo_path).resolve()
        if not target_path.exists() or not target_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Local repository directory does not exist: {req.local_repo_path}",
            )
        if not req.issue_description or not req.issue_description.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An issue description is required when targeting an arbitrary local repository.",
            )
        issue_desc = req.issue_description.strip()
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either 'fixture_id' or 'local_repo_path'.",
        )

    job_id = uuid.uuid4().hex[:12]
    job_info = {
        "job_id": job_id,
        "status": "running",
        "current_state": RepairState.INITIALIZING.value,
        "progress_message": "Initializing repair harness",
        "attempts_used": 0,
        "max_attempts": settings.max_repair_attempts,
        "created_at": time.time(),
        "completed_at": None,
        "report": None,
        "error": None,
    }
    repair_jobs[job_id] = job_info

    # Launch task asynchronously
    background_tasks.add_task(_execute_repair_task, job_id, target_path, issue_desc, is_fixture)

    return JobStatusResponse(**job_info)


@app.get("/api/repair/status/{job_id}", response_model=JobStatusResponse)
async def get_repair_status(job_id: str) -> JobStatusResponse:
    """Retrieve current progress and results of a repair job."""
    if job_id not in repair_jobs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")
    return JobStatusResponse(**repair_jobs[job_id])


@app.post("/api/benchmark/run")
async def run_benchmark_endpoint(mode: str = "bounded_repair_loop") -> Dict[str, Any]:
    """Execute full 10-task evaluation benchmark and compute metrics."""
    settings = get_settings()
    fixtures_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures"
    model_client = create_model_client(settings)

    runner = BenchmarkRunner(fixtures_root=fixtures_dir, settings=settings)
    max_attempts = 1 if mode == "one_shot" else settings.max_repair_attempts

    report = await runner.run_suite(
        model_client=model_client,
        max_attempts=max_attempts,
        mode_label=mode,
    )
    return report.model_dump()


# Serve frontend static assets
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_index() -> FileResponse:
    """Serve the single-page developer frontend."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Frontend assets not found.")
    return FileResponse(index_file)
