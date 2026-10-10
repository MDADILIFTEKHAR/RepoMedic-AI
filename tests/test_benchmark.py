"""Tests for benchmark execution across the 10 deterministic fixtures."""

from pathlib import Path
import pytest
from repomedic.config import Settings
from repomedic.evaluation.benchmark import BENCHMARK_TASKS, BenchmarkRunner
from repomedic.models.mock_client import MockModelClient
from repomedic.sandbox.runner import SubprocessSandboxRunner


def test_all_10_fixtures_exist():
    fixtures_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures"
    assert fixtures_dir.exists()

    assert len(BENCHMARK_TASKS) == 10
    for task in BENCHMARK_TASKS:
        dir_path = fixtures_dir / task.fixture_dir_name
        assert dir_path.exists(), f"Missing fixture directory for {task.task_id}"
        py_files = list(dir_path.glob("*.py"))
        assert len(py_files) >= 2, f"Fixture {task.task_id} must contain source and test files"


@pytest.mark.asyncio
async def test_benchmark_suite_execution():
    fixtures_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures"
    s = Settings(model_provider="mock")
    sandbox = SubprocessSandboxRunner()

    runner = BenchmarkRunner(fixtures_root=fixtures_dir, settings=s, sandbox_runner=sandbox)
    client = MockModelClient()

    # 1. Run Bounded Repair Loop mode
    report_loop = await runner.run_suite(
        model_client=client,
        max_attempts=3,
        mode_label="bounded_repair_loop",
    )

    assert report_loop.total_tasks == 10
    assert report_loop.metrics.total_tasks_evaluated == 10
    assert report_loop.metrics.tasks_eligible == 10
    assert report_loop.metrics.final_passed_count > 0
    assert "# RepoMedic AI Benchmark Evaluation Report" in report_loop.summary_markdown
