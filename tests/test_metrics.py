"""Tests for evaluation metrics calculation and statistical reporting."""

from repomedic.evaluation.metrics import calculate_metrics
from repomedic.orchestrator import RepairAttemptRecord, RepairSessionReport, RepairState


def test_metrics_empty():
    m = calculate_metrics([])
    assert m.total_tasks_evaluated == 0
    assert m.tasks_eligible == 0
    assert m.one_shot_pass_rate_pct == 0.0
    assert m.final_pass_rate_pct == 0.0


def test_metrics_calculation():
    # Report 1: Passed on attempt 1 (one-shot)
    r1 = RepairSessionReport(
        repository_path="repo1",
        issue_description="issue1",
        terminal_state=RepairState.COMPLETED_VERIFIED,
        success=True,
        attempts_count=1,
        max_attempts=3,
        baseline_passed=False,
        baseline_stdout="",
        baseline_stderr="",
        total_elapsed_seconds=2.0,
    )

    # Report 2: Passed on attempt 2 (recovered via loop)
    r2 = RepairSessionReport(
        repository_path="repo2",
        issue_description="issue2",
        terminal_state=RepairState.COMPLETED_VERIFIED,
        success=True,
        attempts_count=2,
        max_attempts=3,
        baseline_passed=False,
        baseline_stdout="",
        baseline_stderr="",
        total_elapsed_seconds=4.0,
    )

    # Report 3: Failed after 3 attempts
    r3 = RepairSessionReport(
        repository_path="repo3",
        issue_description="issue3",
        terminal_state=RepairState.FAILED_MAX_ATTEMPTS,
        success=False,
        attempts_count=3,
        max_attempts=3,
        baseline_passed=False,
        baseline_stdout="",
        baseline_stderr="",
        total_elapsed_seconds=6.0,
    )

    # Report 4: Policy gate rejected
    r4 = RepairSessionReport(
        repository_path="repo4",
        issue_description="issue4",
        terminal_state=RepairState.PATCH_REJECTED,
        success=False,
        attempts_count=1,
        max_attempts=3,
        baseline_passed=False,
        baseline_stdout="",
        baseline_stderr="",
        total_elapsed_seconds=1.0,
    )

    reports = [r1, r2, r3, r4]
    metrics = calculate_metrics(reports)

    assert metrics.total_tasks_evaluated == 4
    assert metrics.tasks_eligible == 4
    # 1 out of 4 passed on attempt 1 => 25%
    assert metrics.one_shot_passed_count == 1
    assert metrics.one_shot_pass_rate_pct == 25.0

    # 2 out of 4 passed eventually => 50%
    assert metrics.final_passed_count == 2
    assert metrics.final_pass_rate_pct == 50.0

    # Loop recovered count = 1, delta = 50 - 25 = +25%
    assert metrics.loop_recovered_count == 1
    assert metrics.loop_improvement_delta_pct == 25.0

    # 1 out of 4 rejected => 25%
    assert metrics.patch_rejections_count == 1
    assert metrics.patch_rejection_rate_pct == 25.0

    # Latencies: [2.0, 4.0, 6.0, 1.0] -> mean = 3.25
    assert metrics.latency_mean_seconds == 3.25
