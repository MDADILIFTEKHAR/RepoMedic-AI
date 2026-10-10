"""Tests for the Evidence-Gated Repair Orchestrator state machine and feedback loop."""

from pathlib import Path
import pytest
from repomedic.config import Settings
from repomedic.models.mock_client import MockModelClient
from repomedic.orchestrator import EvidenceGatedOrchestrator, RepairState
from repomedic.sandbox.runner import SubprocessSandboxRunner


@pytest.mark.asyncio
async def test_baseline_already_passing(tmp_path):
    # Setup repository with already passing tests
    (tmp_path / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text("from calc import add\ndef test(): assert add(1, 2) == 3\n", encoding="utf-8")

    client = MockModelClient()
    orch = EvidenceGatedOrchestrator(
        model_client=client,
        sandbox_runner=SubprocessSandboxRunner(),
    )

    report = await orch.run_repair(
        repo_path=tmp_path,
        issue_description="None",
        is_predefined_fixture=True,
    )

    assert report.success is True
    assert report.terminal_state == RepairState.VERIFIED
    assert report.attempts_count == 0


@pytest.mark.asyncio
async def test_one_shot_repair_success(tmp_path):
    # Setup repo with broken code
    (tmp_path / "discount.py").write_text("def apply(p, r): return p * r\n", encoding="utf-8")
    (tmp_path / "test_discount.py").write_text("from discount import apply\ndef test(): assert apply(100.0, 0.2) == 80.0\n", encoding="utf-8")

    golden_diff = (
        "--- a/discount.py\n"
        "+++ b/discount.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def apply(p, r): return p * r\n"
        "+def apply(p, r): return p * (1.0 - r)\n"
    )

    client = MockModelClient(responses=[golden_diff])
    orch = EvidenceGatedOrchestrator(
        model_client=client,
        sandbox_runner=SubprocessSandboxRunner(),
    )

    report = await orch.run_repair(
        repo_path=tmp_path,
        issue_description="Discount returns discount amount instead of final price",
        is_predefined_fixture=True,
    )

    assert report.success is True
    assert report.terminal_state == RepairState.VERIFIED
    assert report.attempts_count == 1
    assert report.verified_patch.strip() == golden_diff.strip()


@pytest.mark.asyncio
async def test_state_machine_full_sequence(tmp_path):
    """Test that the orchestrator transitions through the required real-world state machine."""
    (tmp_path / "discount.py").write_text("def apply(p, r): return p * r\n", encoding="utf-8")
    (tmp_path / "test_discount.py").write_text("from discount import apply\ndef test(): assert apply(100.0, 0.2) == 80.0\n", encoding="utf-8")

    golden_diff = (
        "--- a/discount.py\n"
        "+++ b/discount.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def apply(p, r): return p * r\n"
        "+def apply(p, r): return p * (1.0 - r)\n"
    )

    states_visited = []

    def on_state(state: RepairState, msg: str):
        states_visited.append(state)

    client = MockModelClient(responses=[golden_diff])
    orch = EvidenceGatedOrchestrator(
        model_client=client,
        sandbox_runner=SubprocessSandboxRunner(),
        on_state_change=on_state,
    )

    report = await orch.run_repair(
        repo_path=tmp_path,
        issue_description="Fix discount price calculation",
        is_predefined_fixture=True,
    )

    assert report.success is True
    expected_sequence = [
        RepairState.CREATED,
        RepairState.REPOSITORY_VALIDATED,
        RepairState.BASELINE_TESTED,
        RepairState.ANALYZED,
        RepairState.PATCH_PROPOSED,
        RepairState.PATCH_VALIDATED,
        RepairState.PATCH_APPLIED,
        RepairState.TESTED,
        RepairState.VERIFIED,
    ]
    assert states_visited == expected_sequence


@pytest.mark.asyncio
async def test_multi_attempt_feedback_recovery(tmp_path):
    # Setup repo
    (tmp_path / "service.py").write_text("def calc(x): return x - 1\n", encoding="utf-8")
    (tmp_path / "test_service.py").write_text("from service import calc\ndef test(): assert calc(5) == 10\n", encoding="utf-8")

    # Attempt 1 produces wrong patch (calc returns x * 1 = 5)
    bad_diff = (
        "--- a/service.py\n"
        "+++ b/service.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def calc(x): return x - 1\n"
        "+def calc(x): return x * 1\n"
    )
    # Attempt 2 produces correct patch (calc returns x * 2 = 10)
    good_diff = (
        "--- a/service.py\n"
        "+++ b/service.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def calc(x): return x - 1\n"
        "+def calc(x): return x * 2\n"
    )

    client = MockModelClient(responses=[bad_diff, good_diff])
    orch = EvidenceGatedOrchestrator(
        model_client=client,
        sandbox_runner=SubprocessSandboxRunner(),
    )

    report = await orch.run_repair(
        repo_path=tmp_path,
        issue_description="Expected calc(5) == 10",
        is_predefined_fixture=True,
    )

    assert report.success is True
    assert report.attempts_count == 2
    assert len(report.audit_history) == 2
    assert report.audit_history[0].test_passed is False
    assert report.audit_history[1].test_passed is True


@pytest.mark.asyncio
async def test_max_three_attempts_bound(tmp_path):
    (tmp_path / "app.py").write_text("x = 0\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("from app import x\ndef test(): assert x == 100\n", encoding="utf-8")

    # Ineffective patch
    bad_diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 0\n"
        "+x = 1\n"
    )

    client = MockModelClient(responses=[bad_diff])
    orch = EvidenceGatedOrchestrator(
        model_client=client,
        settings=Settings(max_repair_attempts=3),
        sandbox_runner=SubprocessSandboxRunner(),
    )

    report = await orch.run_repair(
        repo_path=tmp_path,
        issue_description="x must be 100",
        is_predefined_fixture=True,
    )

    assert report.success is False
    assert report.terminal_state == RepairState.FAILED
    assert report.attempts_count == 3
    assert len(report.audit_history) == 3
