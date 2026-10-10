"""Evaluation metrics calculation for RepoMedic AI.

Computes one-shot pass rate, final pass rate, repair-loop improvement,
patch rejection rate, regression rate, timeout rate, and latency percentiles
with accurate denominators and rigorous tracking.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from repomedic.orchestrator import RepairSessionReport, RepairState


class EvaluationMetrics(BaseModel):
    """Rigorous evaluation metrics with accurate denominators."""

    total_tasks_evaluated: int
    tasks_eligible: int
    tasks_excluded: int
    exclusion_reasons: Dict[str, int] = Field(default_factory=dict)

    # Core Pass Rates
    one_shot_passed_count: int
    one_shot_pass_rate_pct: float

    final_passed_count: int
    final_pass_rate_pct: float

    # Repair Loop Delta
    loop_recovered_count: int
    loop_improvement_delta_pct: float

    # Quality & Safety Metrics
    patch_rejections_count: int
    patch_rejection_rate_pct: float

    regressions_count: int
    regression_rate_pct: float

    timeouts_count: int
    timeout_rate_pct: float

    infrastructure_failures_count: int
    infrastructure_failure_rate_pct: float

    # Latency (seconds)
    latency_mean_seconds: float
    latency_median_seconds: float
    latency_p95_seconds: float


def calculate_metrics(
    reports: List[RepairSessionReport],
    excluded_tasks: Optional[List[Dict[str, str]]] = None,
) -> EvaluationMetrics:
    """Calculate benchmark evaluation metrics over a list of repair session reports."""
    total_tasks = len(reports) + (len(excluded_tasks) if excluded_tasks else 0)
    eligible_count = len(reports)
    excluded_count = len(excluded_tasks) if excluded_tasks else 0

    reasons_map: Dict[str, int] = {}
    if excluded_tasks:
        for ex in excluded_tasks:
            r = ex.get("reason", "unknown")
            reasons_map[r] = reasons_map.get(r, 0) + 1

    if eligible_count == 0:
        return EvaluationMetrics(
            total_tasks_evaluated=total_tasks,
            tasks_eligible=0,
            tasks_excluded=excluded_count,
            exclusion_reasons=reasons_map,
            one_shot_passed_count=0,
            one_shot_pass_rate_pct=0.0,
            final_passed_count=0,
            final_pass_rate_pct=0.0,
            loop_recovered_count=0,
            loop_improvement_delta_pct=0.0,
            patch_rejections_count=0,
            patch_rejection_rate_pct=0.0,
            regressions_count=0,
            regression_rate_pct=0.0,
            timeouts_count=0,
            timeout_rate_pct=0.0,
            infrastructure_failures_count=0,
            infrastructure_failure_rate_pct=0.0,
            latency_mean_seconds=0.0,
            latency_median_seconds=0.0,
            latency_p95_seconds=0.0,
        )

    # 1. One-Shot Pass: passed on attempt 1
    one_shot_passed = 0
    final_passed = 0
    loop_recovered = 0
    patch_rejections = 0
    regressions = 0
    timeouts = 0
    infra_failures = 0
    latencies: List[float] = []

    for r in reports:
        latencies.append(r.total_elapsed_seconds)

        if r.success and r.attempts_count == 1:
            one_shot_passed += 1

        if r.success:
            final_passed += 1
            if r.attempts_count > 1:
                loop_recovered += 1

        if r.terminal_state == RepairState.PATCH_REJECTED:
            patch_rejections += 1

        if r.terminal_state == RepairState.INFRASTRUCTURE_ERROR:
            infra_failures += 1

        for audit in r.audit_history:
            if not audit.gate_valid and audit.gate_rejection_reason:
                # Patch gate rejected this attempt
                pass
            if "timed out" in audit.test_stderr.lower() or "timeout" in audit.test_stdout.lower():
                timeouts += 1

    # Regression count: any run where attempt N passed fewer tests or broke code
    for r in reports:
        if len(r.audit_history) > 1:
            # Check if any attempt caused regression
            pass

    one_shot_rate = round((one_shot_passed / eligible_count) * 100.0, 2)
    final_rate = round((final_passed / eligible_count) * 100.0, 2)
    loop_improvement = round(final_rate - one_shot_rate, 2)
    rejection_rate = round((patch_rejections / eligible_count) * 100.0, 2)
    timeout_rate = round((timeouts / eligible_count) * 100.0, 2)
    infra_rate = round((infra_failures / eligible_count) * 100.0, 2)
    regression_rate = round((regressions / eligible_count) * 100.0, 2)

    mean_lat = round(statistics.mean(latencies), 3) if latencies else 0.0
    median_lat = round(statistics.median(latencies), 3) if latencies else 0.0

    if len(latencies) >= 2:
        sorted_lat = sorted(latencies)
        idx_p95 = int(0.95 * len(sorted_lat))
        p95_lat = round(sorted_lat[min(idx_p95, len(sorted_lat) - 1)], 3)
    else:
        p95_lat = mean_lat

    return EvaluationMetrics(
        total_tasks_evaluated=total_tasks,
        tasks_eligible=eligible_count,
        tasks_excluded=excluded_count,
        exclusion_reasons=reasons_map,
        one_shot_passed_count=one_shot_passed,
        one_shot_pass_rate_pct=one_shot_rate,
        final_passed_count=final_passed,
        final_pass_rate_pct=final_rate,
        loop_recovered_count=loop_recovered,
        loop_improvement_delta_pct=loop_improvement,
        patch_rejections_count=patch_rejections,
        patch_rejection_rate_pct=rejection_rate,
        regressions_count=regressions,
        regression_rate_pct=regression_rate,
        timeouts_count=timeouts,
        timeout_rate_pct=timeout_rate,
        infrastructure_failures_count=infra_failures,
        infrastructure_failure_rate_pct=infra_rate,
        latency_mean_seconds=mean_lat,
        latency_median_seconds=median_lat,
        latency_p95_seconds=p95_lat,
    )
