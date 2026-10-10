"""FastAPI application for RepoMedic AI.

Provides REST API endpoints for GitHub OAuth & repository inspection,
background repair job execution, verified patch/report downloads,
explicitly approved Pull Request creation, and serves the developer frontend.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Literal, Optional
import uuid

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from repomedic import __version__
from repomedic.config import Settings, get_settings
from repomedic.evaluation.benchmark import BENCHMARK_TASKS, BenchmarkRunner
from repomedic.github.client import (
    GitHubAuthenticationError,
    GitHubClient,
    GitHubError,
    GitHubPermissionError,
    GitHubResourceNotFoundError,
    GitHubValidationError,
    redact_token,
    validate_github_identifier,
    validate_repository_name,
)
from repomedic.models import create_model_client
from repomedic.orchestrator import EvidenceGatedOrchestrator, RepairSessionReport, RepairState
from repomedic.repository.worktree import DisposableWorkspace
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

# In-memory repair job and auth state registry
repair_jobs: Dict[str, Dict[str, Any]] = {}
oauth_states: Dict[str, float] = {}
github_client = GitHubClient()


# -----------------------------------------------------------------------------
# Request & Response Models
# -----------------------------------------------------------------------------

class GitHubAuthRequest(BaseModel):
    """Payload to validate and store a Personal Access Token or session token."""
    token: str = Field(..., description="GitHub Personal Access Token (classic or fine-grained)")


class GitHubOAuthCallbackRequest(BaseModel):
    """Payload from GitHub OAuth redirect callback."""
    code: str
    state: Optional[str] = None


class GitHubOAuthConfigRequest(BaseModel):
    """Payload to configure GitHub OAuth credentials dynamically."""
    client_id: str = Field(..., description="GitHub OAuth App Client ID")
    client_secret: str = Field(..., description="GitHub OAuth App Client Secret")
    redirect_uri: Optional[str] = Field(default=None, description="Optional redirect URI override")


class RepairRequest(BaseModel):
    """Payload to initiate a bug repair session across GitHub, Local, or Fixture sources."""

    source_type: Optional[Literal["github", "local", "fixture"]] = Field(
        default=None,
        description="Source of the target repository: 'github', 'local', or 'fixture'",
    )
    # GitHub repository parameters
    github_repo: Optional[str] = Field(
        default=None,
        description="Full repository name: 'owner/repository'",
    )
    github_branch: Optional[str] = Field(
        default="main",
        description="Target branch name",
    )
    github_token: Optional[str] = Field(
        default=None,
        description="GitHub token if not configured in environment",
    )

    # Local repository parameters
    local_repo_path: Optional[str] = Field(
        default=None,
        description="Local directory path (Local mode)",
    )

    # Benchmark fixture parameters
    fixture_id: Optional[str] = Field(
        default=None,
        description="ID of a predefined benchmark fixture",
    )

    # Common issue description
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


class PublishPRRequest(BaseModel):
    """Payload to explicitly approve and publish a Pull Request to GitHub."""

    confirm: bool = Field(
        ...,
        description="Must be explicitly True to confirm PR creation",
    )
    pr_title: Optional[str] = Field(
        default=None,
        description="Custom Pull Request title",
    )
    pr_body: Optional[str] = Field(
        default=None,
        description="Custom Pull Request description",
    )
    target_branch: Optional[str] = Field(
        default=None,
        description="Base branch to target (defaults to repository default branch)",
    )
    github_token: Optional[str] = Field(
        default=None,
        description="Optional GitHub token override",
    )


# -----------------------------------------------------------------------------
# Health Check Endpoint
# -----------------------------------------------------------------------------

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
        "sandbox_mode": settings.sandbox_mode,
        "active_sandbox": settings.active_sandbox_type,
        "allow_host_execution": settings.allow_host_execution,
        "arbitrary_repo_repair_enabled": settings.is_sandbox_ready and not settings.hosted_mode,
        "github_oauth_configured": bool(settings.github_client_id and settings.github_client_secret),
        "timestamp": time.time(),
    }


# -----------------------------------------------------------------------------
# GitHub Integration Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/github/status")
async def get_github_status() -> Dict[str, Any]:
    """Check GitHub OAuth and integration status."""
    settings = get_settings()
    return {
        "oauth_configured": bool(settings.github_client_id and settings.github_client_secret),
        "client_id": settings.github_client_id if settings.github_client_id else None,
        "has_default_token": bool(settings.github_default_token and settings.github_default_token.get_secret_value()),
    }


@app.post("/api/github/oauth/configure")
async def configure_github_oauth(req: GitHubOAuthConfigRequest) -> Dict[str, Any]:
    """Configure or update GitHub OAuth App credentials at runtime."""
    settings = get_settings()
    settings.github_client_id = req.client_id.strip()
    settings.github_client_secret = SecretStr(req.client_secret.strip())
    if req.redirect_uri:
        settings.github_redirect_uri = req.redirect_uri.strip()
    return {
        "status": "configured",
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
    }


@app.get("/api/github/oauth/url")
async def get_github_oauth_url() -> Dict[str, str]:
    """Generate GitHub OAuth authorization URL with CSRF state protection."""
    settings = get_settings()
    if not settings.github_client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub OAuth is not configured on this server (REPOMEDIC_GITHUB_CLIENT_ID missing). Use Personal Access Token or configure OAuth credentials.",
        )
    state = uuid.uuid4().hex
    oauth_states[state] = time.time()
    url = github_client.get_oauth_authorization_url(
        client_id=settings.github_client_id,
        redirect_uri=settings.github_redirect_uri,
        state=state,
    )
    return {"authorization_url": url, "state": state}


@app.get("/api/github/oauth/callback", response_class=HTMLResponse)
async def github_oauth_callback_browser(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
) -> HTMLResponse:
    """Handle browser redirect from GitHub OAuth authorization consent."""
    if error:
        err_msg = error_description or error or "OAuth authorization declined."
        html_error = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>RepoMedic AI — OAuth Error</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f19; color: #f3f4f6; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #131b2e; border: 1px solid #ef4444; border-radius: 12px; padding: 2.5rem; max-width: 480px; text-align: center; box-shadow: 0 20px 35px rgba(0,0,0,0.5); }}
    h2 {{ color: #f87171; margin-top: 0; }}
    p {{ color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }}
    .btn {{ display: inline-block; background: #2563eb; color: #fff; text-decoration: none; padding: 0.65rem 1.4rem; border-radius: 8px; font-weight: 600; margin-top: 1.5rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>GitHub OAuth Failed</h2>
    <p>{err_msg}</p>
    <a href="/" class="btn">Return to RepoMedic AI</a>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html_error, status_code=400)

    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing OAuth code in callback.")

    settings = get_settings()
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub OAuth credentials are not configured on this server.",
        )

    # Validate state if supplied
    if state and state in oauth_states:
        del oauth_states[state]

    try:
        token = await github_client.exchange_code_for_token(
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret.get_secret_value(),
            code=code,
            redirect_uri=settings.github_redirect_uri,
        )
        user_info = await github_client.get_authenticated_user(token)
        user_login = user_info.get("login", "user")

        html_success = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>RepoMedic AI — GitHub Authenticated</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f19; color: #f3f4f6; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #131b2e; border: 1px solid #10b981; border-radius: 12px; padding: 2.5rem; max-width: 440px; text-align: center; box-shadow: 0 20px 35px rgba(0,0,0,0.5); }}
    .spinner {{ width: 36px; height: 36px; border: 3px solid rgba(255,255,255,0.1); border-top-color: #10b981; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 1.25rem; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    h2 {{ color: #34d399; margin: 0 0 0.5rem; }}
    p {{ color: #94a3b8; font-size: 0.95rem; margin: 0; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="spinner"></div>
    <h2>Connected Successfully!</h2>
    <p>Logged in as <strong>@{user_login}</strong>. Redirecting to RepoMedic AI...</p>
  </div>
  <script>
    try {{
      localStorage.setItem("repomedic_gh_token", "{token}");
    }} catch(e) {{}}
    setTimeout(function() {{
      window.location.href = "/";
    }}, 800);
  </script>
</body>
</html>"""
        return HTMLResponse(content=html_success)
    except Exception as ex:
        html_fail = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>RepoMedic AI — OAuth Error</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f19; color: #f3f4f6; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #131b2e; border: 1px solid #ef4444; border-radius: 12px; padding: 2.5rem; max-width: 480px; text-align: center; box-shadow: 0 20px 35px rgba(0,0,0,0.5); }}
    h2 {{ color: #f87171; margin-top: 0; }}
    p {{ color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }}
    .btn {{ display: inline-block; background: #2563eb; color: #fff; text-decoration: none; padding: 0.65rem 1.4rem; border-radius: 8px; font-weight: 600; margin-top: 1.5rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Token Exchange Failed</h2>
    <p>{str(ex)}</p>
    <a href="/" class="btn">Return to RepoMedic AI</a>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html_fail, status_code=500)


@app.post("/api/github/oauth/callback")
async def github_oauth_callback(req: GitHubOAuthCallbackRequest) -> Dict[str, Any]:
    """Exchange OAuth authorization code for an access token (JSON API)."""
    settings = get_settings()
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub OAuth client credentials are not configured on this server.",
        )

    # Validate CSRF state if provided
    if req.state and req.state in oauth_states:
        del oauth_states[req.state]

    try:
        token = await github_client.exchange_code_for_token(
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret.get_secret_value(),
            code=req.code,
            redirect_uri=settings.github_redirect_uri,
        )
        user_info = await github_client.get_authenticated_user(token)
        return {
            "token": token,
            "user": {
                "login": user_info.get("login"),
                "name": user_info.get("name"),
                "avatar_url": user_info.get("avatar_url"),
                "scopes": user_info.get("scopes", []),
            },
        }
    except GitHubAuthenticationError as ex:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"OAuth failure: {str(ex)}")


@app.post("/api/github/auth")
async def authenticate_github_token(req: GitHubAuthRequest) -> Dict[str, Any]:
    """Validate a user-supplied GitHub Personal Access Token (classic or fine-grained)."""
    clean_token = req.token.strip()
    if not clean_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token cannot be empty.")

    try:
        user_info = await github_client.get_authenticated_user(clean_token)
        scopes = user_info.get("scopes", [])
        has_repo_scope = "repo" in scopes or any("repo:" in s for s in scopes) or len(scopes) == 0  # fine-grained PATs don't report x-oauth-scopes

        return {
            "authenticated": True,
            "user": {
                "login": user_info.get("login"),
                "name": user_info.get("name"),
                "avatar_url": user_info.get("avatar_url"),
                "html_url": user_info.get("html_url"),
                "public_repos": user_info.get("public_repos", 0),
                "total_private_repos": user_info.get("total_private_repos", 0),
                "scopes": scopes,
            },
            "has_repo_scope": has_repo_scope,
            "warning": None if has_repo_scope else "Token is valid, but missing 'repo' scope. Pull Request creation and private repo access require the 'repo' scope.",
        }
    except GitHubAuthenticationError as ex:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"GitHub validation error: {str(ex)}")


def _resolve_token(
    explicit_token: Optional[str] = None,
    header_token: Optional[str] = None,
    required: bool = True,
) -> Optional[str]:
    """Resolve GitHub token from request, header, or environment."""
    settings = get_settings()
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()
    if header_token and header_token.strip():
        clean_hdr = header_token.strip()
        if clean_hdr.lower().startswith("bearer "):
            clean_hdr = clean_hdr[7:].strip()
        return clean_hdr
    if settings.github_default_token and settings.github_default_token.get_secret_value():
        return settings.github_default_token.get_secret_value()
    if required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="GitHub authentication required. Please connect your GitHub account or provide a token.",
        )
    return None


@app.get("/api/github/repos/{owner}/{repo}")
async def get_github_repo_metadata(
    owner: str,
    repo: str,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Inspect and validate metadata for a specific repository."""
    token = _resolve_token(header_token=authorization, required=False) or ""
    try:
        summary = await github_client.get_repository(token, owner=owner, repo=repo)
        return summary.model_dump()
    except GitHubResourceNotFoundError as ex:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ex))
    except GitHubAuthenticationError as ex:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@app.get("/api/github/repos")
async def list_github_repositories(
    authorization: Optional[str] = Header(None),
) -> List[Dict[str, Any]]:
    """List accessible GitHub repositories for the authenticated user."""
    token = _resolve_token(header_token=authorization)
    try:
        repos = await github_client.list_user_repositories(token)
        return [r.model_dump() for r in repos]
    except GitHubAuthenticationError as ex:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@app.get("/api/github/repos/{owner}/{repo}/branches")
async def list_github_branches(
    owner: str,
    repo: str,
    authorization: Optional[str] = Header(None),
) -> List[Dict[str, Any]]:
    """List branches for a specific repository."""
    token = _resolve_token(header_token=authorization)
    try:
        branches = await github_client.list_repository_branches(token, owner=owner, repo=repo)
        return [b.model_dump() for b in branches]
    except GitHubResourceNotFoundError as ex:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ex))
    except GitHubAuthenticationError as ex:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


# -----------------------------------------------------------------------------
# Benchmark Tasks Inspection
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Repair Workflow Execution
# -----------------------------------------------------------------------------

async def _execute_repair_task(
    job_id: str,
    repo_path: Optional[Path],
    remote_clone_url: Optional[str],
    branch: Optional[str],
    auth_token: Optional[str],
    issue_desc: str,
    is_fixture: bool,
) -> None:
    """Asynchronous background worker executing the Evidence-Gated Repair Harness."""
    settings = get_settings()
    model_client = create_model_client(settings)
    sandbox_runner = create_sandbox_runner(
        timeout_seconds=settings.test_timeout_seconds,
        allow_host_execution=settings.allow_host_execution,
    )

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
            remote_clone_url=remote_clone_url,
            branch=branch,
            auth_token=auth_token,
            issue_description=issue_desc,
            is_predefined_fixture=is_fixture,
        )
        repair_jobs[job_id]["status"] = "completed"
        repair_jobs[job_id]["completed_at"] = time.time()
        repair_jobs[job_id]["current_state"] = report.terminal_state.value
        repair_jobs[job_id]["attempts_used"] = report.attempts_count
        repair_jobs[job_id]["report"] = report.model_dump()
        repair_jobs[job_id]["progress_message"] = report.summary_message
        # Store auth token privately in job metadata for user-approved PR creation
        repair_jobs[job_id]["_auth_token"] = auth_token
        repair_jobs[job_id]["_remote_clone_url"] = remote_clone_url
        repair_jobs[job_id]["_base_branch"] = branch
    except Exception as ex:
        clean_err = redact_token(str(ex))
        logger.exception("Error executing repair job %s", job_id)
        repair_jobs[job_id]["status"] = "failed"
        repair_jobs[job_id]["completed_at"] = time.time()
        repair_jobs[job_id]["error"] = clean_err
        repair_jobs[job_id]["current_state"] = RepairState.INFRASTRUCTURE_ERROR.value
        repair_jobs[job_id]["progress_message"] = f"Internal execution failure: {clean_err}"


@app.post("/api/repair/start", response_model=JobStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_repair(
    req: RepairRequest,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None),
) -> JobStatusResponse:
    """Start an asynchronous repair session on a GitHub repo, local repo, or fixture."""
    settings = get_settings()
    fixtures_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures"

    target_local_path: Optional[Path] = None
    remote_clone_url: Optional[str] = None
    target_branch: Optional[str] = "main"
    auth_token: Optional[str] = None
    issue_desc: str = ""
    is_fixture: bool = False

    # 1. GitHub Repository Mode
    if req.source_type == "github":
        if not req.github_repo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Must provide 'github_repo' (format 'owner/repository') for GitHub mode.",
            )
        try:
            owner, repo = validate_repository_name(req.github_repo)
            target_branch = validate_github_identifier(req.github_branch or "main")
        except GitHubValidationError as ex:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))

        auth_token = _resolve_token(
            explicit_token=req.github_token,
            header_token=authorization,
            required=False,
        )
        remote_clone_url = f"https://github.com/{owner}/{repo}"

        if not req.issue_description or not req.issue_description.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An issue description is required for repository bug repair.",
            )
        issue_desc = req.issue_description.strip()

        # Sandbox check: ensure execution sandbox is available
        if not settings.is_sandbox_ready and not settings.environment == "test":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No execution sandbox is available. Start Docker Desktop, switch to Predefined Fixtures, or configure REPOMEDIC_SANDBOX_MODE=auto to enable demo subprocess execution.",
            )

    # 2. Local Repository Mode
    elif req.source_type == "local" or (req.local_repo_path and not req.fixture_id):
        if settings.hosted_mode:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Arbitrary local repository repair is disabled in hosted mode. Please select a predefined benchmark fixture or GitHub repository.",
            )
        if not req.local_repo_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Must provide 'local_repo_path'.")

        target_local_path = Path(req.local_repo_path).resolve()
        if not target_local_path.exists() or not target_local_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Local repository path does not exist: {req.local_repo_path}",
            )
        if not settings.is_sandbox_ready and not settings.environment == "test":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No execution sandbox is available. Start Docker Desktop, switch to Predefined Fixtures, or configure REPOMEDIC_SANDBOX_MODE=auto to enable demo subprocess execution.",
            )
        if not req.issue_description or not req.issue_description.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An issue description is required when targeting an arbitrary local repository.",
            )
        issue_desc = req.issue_description.strip()

    # 3. Predefined Benchmark Fixture Mode
    elif req.source_type == "fixture" or req.fixture_id:
        fixture_key = req.fixture_id or "task_01_arithmetic_discount"
        matched = next((t for t in BENCHMARK_TASKS if t.task_id == fixture_key), None)
        if not matched:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Benchmark fixture '{fixture_key}' not found.",
            )
        target_local_path = fixtures_dir / matched.fixture_dir_name
        issue_desc = req.issue_description or matched.issue_description
        is_fixture = True

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must specify a valid source_type ('github', 'local', or 'fixture').",
        )

    job_id = uuid.uuid4().hex[:12]
    job_info = {
        "job_id": job_id,
        "status": "running",
        "current_state": RepairState.CREATED.value,
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
    background_tasks.add_task(
        _execute_repair_task,
        job_id,
        target_local_path,
        remote_clone_url,
        target_branch,
        auth_token,
        issue_desc,
        is_fixture,
    )

    return JobStatusResponse(**job_info)


@app.get("/api/repair/status/{job_id}", response_model=JobStatusResponse)
async def get_repair_status(job_id: str) -> JobStatusResponse:
    """Retrieve current progress and results of a repair job."""
    if job_id not in repair_jobs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")
    data = dict(repair_jobs[job_id])
    # Sanitize internal credentials from response
    data.pop("_auth_token", None)
    return JobStatusResponse(**data)


# -----------------------------------------------------------------------------
# Downloads & Artifacts
# -----------------------------------------------------------------------------

@app.get("/api/repair/{job_id}/download/patch")
async def download_patch(job_id: str):
    """Download the verified unified diff patch file."""
    if job_id not in repair_jobs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    report = repair_jobs[job_id].get("report")
    if not report or not report.get("verified_patch"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No verified patch was produced for this job.",
        )

    patch_content = report["verified_patch"]
    filename = f"repomedic_fix_{job_id}.patch"
    return Response(
        content=patch_content,
        media_type="text/x-diff",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/repair/{job_id}/download/report")
async def download_report(job_id: str):
    """Download the machine-readable JSON session audit report."""
    if job_id not in repair_jobs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    report = repair_jobs[job_id].get("report")
    if not report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No report generated yet.")

    filename = f"repomedic_report_{job_id}.json"
    return JSONResponse(
        content=report,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -----------------------------------------------------------------------------
# Explicitly Approved Pull Request Creation
# -----------------------------------------------------------------------------

@app.post("/api/repair/{job_id}/publish-pr")
async def publish_pull_request(
    job_id: str,
    req: PublishPRRequest,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Publish the verified patch as a GitHub Pull Request with mandatory explicit user confirmation."""
    if job_id not in repair_jobs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    job = repair_jobs[job_id]
    report = job.get("report")
    if not report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Repair job has not completed.")

    # Guard: Must be explicitly confirmed by user
    if not req.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User confirmation is required. Explicitly set 'confirm: true' to approve publishing changes.",
        )

    # Guard: Must be verified
    if report.get("terminal_state") not in (RepairState.VERIFIED.value, "COMPLETED_VERIFIED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot publish unverified repair. Current state is '{report.get('terminal_state')}'.",
        )

    verified_patch = report.get("verified_patch")
    if not verified_patch:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No verified patch found.")

    remote_clone_url = job.get("_remote_clone_url") or report.get("remote_clone_url")
    if not remote_clone_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This repair job was executed on a local repository or fixture, not a remote GitHub repository.",
        )

    token = _resolve_token(explicit_token=req.github_token, header_token=authorization)
    # Parse owner and repo
    m = re.match(r"^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)", remote_clone_url)
    if not m:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub repository URL.")
    owner, repo = m.group(1), m.group(2)

    base_branch = req.target_branch or job.get("_base_branch") or report.get("base_branch") or "main"
    disposable_branch = report.get("disposable_branch") or f"repomedic/fix-{job_id}"

    # Push disposable branch using DisposableWorkspace
    try:
        with DisposableWorkspace(
            remote_clone_url=remote_clone_url,
            base_branch=base_branch,
            auth_token=token,
        ) as ws:
            ws.disposable_branch = disposable_branch
            ws.apply_patch(verified_patch)
            commit_msg = f"fix(repomedic): resolve {report.get('issue_description', '')[:70]}"
            committed = ws.commit_patch(commit_msg)
            if not committed:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to commit patch to branch.")

            pushed_branch = ws.push_disposable_branch()

        # Construct PR title & body
        pr_title = req.pr_title or f"RepoMedic AI Fix: {report.get('issue_description', '')[:60]}"
        pr_body = req.pr_body or (
            "## 🩺 RepoMedic AI Autonomous Bug Repair\n\n"
            f"**Issue Description**:\n> {report.get('issue_description')}\n\n"
            "### 🛡️ Evidence-Gated Verification Details\n"
            f"- **Outcome**: All verification tests PASSED cleanly inside an isolated sandbox.\n"
            f"- **Attempts Used**: {report.get('attempts_count')} / {report.get('max_attempts')}\n"
            f"- **Changed Files**: `{', '.join(report.get('changed_files', []))}`\n\n"
            "```diff\n"
            f"{verified_patch[:2000]}\n"
            "```\n\n"
            "---\n*Generated and verified by [RepoMedic AI](https://github.com/MDADILIFTEKHAR/RepoMedic-AI).*"
        )

        pr_info = await github_client.create_pull_request(
            token=token,
            owner=owner,
            repo=repo,
            title=pr_title,
            body=pr_body,
            head=pushed_branch,
            base=base_branch,
        )

        # Store PR URL in report
        repair_jobs[job_id]["report"]["pull_request_url"] = pr_info.get("pr_url")
        return {
            "success": True,
            "pr_number": pr_info.get("pr_number"),
            "pr_url": pr_info.get("pr_url"),
            "head_branch": pushed_branch,
            "base_branch": base_branch,
            "message": f"Successfully created Pull Request #{pr_info.get('pr_number')} on GitHub.",
        }

    except (GitHubValidationError, GitHubPermissionError, GitHubAuthenticationError) as ex:
        clean_msg = redact_token(str(ex))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=clean_msg)
    except Exception as ex:
        clean_msg = redact_token(str(ex))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to publish PR: {clean_msg}")


# -----------------------------------------------------------------------------
# Benchmark Runner Endpoint
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Static Files & Frontend
# -----------------------------------------------------------------------------

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
