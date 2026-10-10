"""Evidence-Gated Repair Orchestrator for RepoMedic AI.

Implements the finite state machine that coordinates repository scanning,
baseline test execution, open-weight model patch generation, deterministic policy gating,
disposable worktree execution, failure evidence feedback, and bounded 3-attempt retries.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
import time
from typing import Callable, List, Optional
from pydantic import BaseModel, Field

from repomedic.config import Settings, get_settings
from repomedic.models.base import BaseModelClient, ModelPrompt
from repomedic.repository.patch_gate import PatchGate, PatchGatePolicy, PatchValidationResult
from repomedic.repository.scanner import RepositoryScanner
from repomedic.repository.worktree import DisposableWorkspace
from repomedic.sandbox.runner import (
    BaseSandboxRunner,
    SandboxUnavailableError,
    TestExecutionResult,
    create_sandbox_runner,
)


class RepairState(str, Enum):
    """Explicit states of the Evidence-Gated Repair Harness."""

    INITIALIZING = "INITIALIZING"
    COLLECTING_CONTEXT = "COLLECTING_CONTEXT"
    RUNNING_BASELINE_TESTS = "RUNNING_BASELINE_TESTS"
    GENERATING_PATCH = "GENERATING_PATCH"
    VALIDATING_PATCH_GATE = "VALIDATING_PATCH_GATE"
    APPLYING_PATCH = "APPLYING_PATCH"
    RUNNING_VERIFICATION_TESTS = "RUNNING_VERIFICATION_TESTS"
    EVALUATING_OUTCOME = "EVALUATING_OUTCOME"
    # Terminal states
    COMPLETED_VERIFIED = "COMPLETED_VERIFIED"
    FAILED_MAX_ATTEMPTS = "FAILED_MAX_ATTEMPTS"
    PATCH_REJECTED = "PATCH_REJECTED"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    SANDBOX_BLOCKED = "SANDBOX_BLOCKED"


class RepairAttemptRecord(BaseModel):
    """Detailed audit record of a single repair loop iteration."""

    attempt_number: int
    raw_model_response: str
    extracted_diff: str
    gate_valid: bool
    gate_rejection_reason: Optional[str] = None
    affected_files: List[str] = []
    lines_added: int = 0
    lines_deleted: int = 0
    test_passed: bool = False
    test_exit_code: Optional[int] = None
    test_stdout: str = ""
    test_stderr: str = ""
    is_infrastructure_failure: bool = False
    model_latency_seconds: float = 0.0


class RepairSessionReport(BaseModel):
    """Complete summary and audit trace for a bug repair session."""

    repository_path: str
    issue_description: str
    terminal_state: RepairState
    success: bool
    attempts_count: int
    max_attempts: int
    baseline_passed: bool
    baseline_stdout: str
    baseline_stderr: str
    verified_patch: Optional[str] = None
    changed_files: List[str] = []
    audit_history: List[RepairAttemptRecord] = Field(default_factory=list)
    total_elapsed_seconds: float = 0.0
    summary_message: str = ""


class EvidenceGatedOrchestrator:
    """Coordinates the deterministic repair pipeline and enforces the 3-attempt bound."""

    def __init__(
        self,
        model_client: BaseModelClient,
        settings: Optional[Settings] = None,
        sandbox_runner: Optional[BaseSandboxRunner] = None,
        on_state_change: Optional[Callable[[RepairState, str], None]] = None,
    ) -> None:
        self.model_client = model_client
        self.settings = settings or get_settings()
        self.sandbox_runner = sandbox_runner or create_sandbox_runner(
            timeout_seconds=self.settings.test_timeout_seconds
        )
        self.on_state_change = on_state_change
        self.current_state = RepairState.INITIALIZING

    def _set_state(self, state: RepairState, message: str = "") -> None:
        self.current_state = state
        if self.on_state_change:
            self.on_state_change(state, message)

    async def run_repair(
        self,
        repo_path: Path,
        issue_description: str,
        is_predefined_fixture: bool = False,
    ) -> RepairSessionReport:
        """Execute the end-to-end evidence-gated repair loop."""
        t_start = time.perf_counter()
        repo_path = Path(repo_path).resolve()
        self._set_state(RepairState.INITIALIZING, f"Inspecting repository at {repo_path}")

        audit_history: List[RepairAttemptRecord] = []
        max_attempts = self.settings.max_repair_attempts

        # 1. Scan Repository and discover Python source files
        scanner = RepositoryScanner(
            repo_root=repo_path,
            max_file_size_bytes=self.settings.max_file_size_bytes,
            max_context_bytes=self.settings.max_context_bytes,
        )
        scan_result = scanner.scan()
        allowed_source_rel_paths = {f.rel_path for f in scan_result.python_files}

        # 2. Run Baseline Tests in isolated disposable workspace
        self._set_state(RepairState.RUNNING_BASELINE_TESTS, "Running baseline test suite to observe initial bug failure")
        with DisposableWorkspace(repo_path) as initial_ws:
            try:
                baseline_test_result = self.sandbox_runner.run_tests(
                    workspace_dir=initial_ws.workspace_path,
                    is_predefined_fixture=is_predefined_fixture,
                )
            except SandboxUnavailableError as err:
                self._set_state(RepairState.SANDBOX_BLOCKED, str(err))
                return RepairSessionReport(
                    repository_path=str(repo_path),
                    issue_description=issue_description,
                    terminal_state=RepairState.SANDBOX_BLOCKED,
                    success=False,
                    attempts_count=0,
                    max_attempts=max_attempts,
                    baseline_passed=False,
                    baseline_stdout="",
                    baseline_stderr=str(err),
                    summary_message=str(err),
                    total_elapsed_seconds=round(time.perf_counter() - t_start, 3),
                )

        if baseline_test_result.is_infrastructure_failure:
            self._set_state(RepairState.INFRASTRUCTURE_ERROR, baseline_test_result.failure_reason or "Infrastructure failure")
            return RepairSessionReport(
                repository_path=str(repo_path),
                issue_description=issue_description,
                terminal_state=RepairState.INFRASTRUCTURE_ERROR,
                success=False,
                attempts_count=0,
                max_attempts=max_attempts,
                baseline_passed=False,
                baseline_stdout=baseline_test_result.stdout,
                baseline_stderr=baseline_test_result.stderr,
                summary_message=f"Infrastructure failure during baseline: {baseline_test_result.failure_reason}",
                total_elapsed_seconds=round(time.perf_counter() - t_start, 3),
            )

        if baseline_test_result.passed:
            # Baseline tests already pass! No bug observed.
            self._set_state(RepairState.COMPLETED_VERIFIED, "Baseline tests already pass cleanly.")
            return RepairSessionReport(
                repository_path=str(repo_path),
                issue_description=issue_description,
                terminal_state=RepairState.COMPLETED_VERIFIED,
                success=True,
                attempts_count=0,
                max_attempts=max_attempts,
                baseline_passed=True,
                baseline_stdout=baseline_test_result.stdout,
                baseline_stderr=baseline_test_result.stderr,
                summary_message="Baseline tests already pass; no code repair needed.",
                total_elapsed_seconds=round(time.perf_counter() - t_start, 3),
            )

        # Baseline failed as expected, capturing evidence
        baseline_evidence = (
            f"Exit Code: {baseline_test_result.exit_code}\n"
            f"STDOUT:\n{baseline_test_result.stdout}\n"
            f"STDERR:\n{baseline_test_result.stderr}"
        )

        # 3. Extract relevant context files
        self._set_state(RepairState.COLLECTING_CONTEXT, "Extracting relevant source files and failure traces")
        context_files = scanner.extract_relevant_context(
            issue_description=issue_description,
            test_failure_output=baseline_evidence,
        )

        # 4. Repair Loop (bounded to max_attempts)
        prior_feedback: Optional[str] = None
        verified_patch: Optional[str] = None
        changed_files: List[str] = []

        for attempt in range(1, max_attempts + 1):
            self._set_state(
                RepairState.GENERATING_PATCH,
                f"Querying model for repair patch (Attempt {attempt}/{max_attempts})",
            )

            prompt = ModelPrompt(
                repository_name=repo_path.name,
                issue_description=issue_description,
                context_files=context_files,
                baseline_test_output=baseline_evidence,
                prior_failure_feedback=prior_feedback,
                attempt_number=attempt,
                max_attempts=max_attempts,
            )

            try:
                model_resp = await self.model_client.generate_patch(prompt)
            except Exception as ex:
                # Model provider error
                self._set_state(
                    RepairState.INFRASTRUCTURE_ERROR,
                    f"Model provider communication failure: {str(ex)}",
                )
                audit_history.append(
                    RepairAttemptRecord(
                        attempt_number=attempt,
                        raw_model_response="",
                        extracted_diff="",
                        gate_valid=False,
                        gate_rejection_reason=f"Model error: {str(ex)}",
                        is_infrastructure_failure=True,
                    )
                )
                return RepairSessionReport(
                    repository_path=str(repo_path),
                    issue_description=issue_description,
                    terminal_state=RepairState.INFRASTRUCTURE_ERROR,
                    success=False,
                    attempts_count=attempt,
                    max_attempts=max_attempts,
                    baseline_passed=False,
                    baseline_stdout=baseline_test_result.stdout,
                    baseline_stderr=baseline_test_result.stderr,
                    audit_history=audit_history,
                    summary_message=f"Model provider failed: {str(ex)}",
                    total_elapsed_seconds=round(time.perf_counter() - t_start, 3),
                )

            diff_candidate = model_resp.extracted_diff

            # 5. Policy & Security Gate Check
            self._set_state(RepairState.VALIDATING_PATCH_GATE, f"Validating patch security policy (Attempt {attempt})")
            gate = PatchGate(
                repo_root=repo_path,
                policy=PatchGatePolicy(allow_test_modifications=False, allow_new_files=False),
                allowed_files=allowed_source_rel_paths,
            )
            gate_result: PatchValidationResult = gate.validate_patch(diff_candidate)

            if not gate_result.is_valid:
                # Patch rejected by security gate
                rejection_msg = gate_result.rejection_reason or "Unknown policy rejection"
                prior_feedback = (
                    f"Attempt {attempt} rejected by deterministic Patch Gate: {rejection_msg}.\n"
                    "Ensure your proposed patch is valid unified diff, targets allowed source files only, "
                    "does not touch tests or CI files, and contains no path traversal."
                )
                audit_history.append(
                    RepairAttemptRecord(
                        attempt_number=attempt,
                        raw_model_response=model_resp.raw_output,
                        extracted_diff=diff_candidate,
                        gate_valid=False,
                        gate_rejection_reason=rejection_msg,
                        affected_files=gate_result.affected_files,
                        lines_added=gate_result.lines_added,
                        lines_deleted=gate_result.lines_deleted,
                        model_latency_seconds=model_resp.latency_seconds,
                    )
                )
                continue

            # 6. Apply to isolated disposable workspace and execute verification tests
            self._set_state(RepairState.APPLYING_PATCH, f"Applying patch to isolated disposable workspace (Attempt {attempt})")
            with DisposableWorkspace(repo_path) as worktree:
                applied = worktree.apply_patch(diff_candidate)
                if not applied:
                    prior_feedback = (
                        f"Attempt {attempt}: Unified diff failed to apply cleanly to the codebase.\n"
                        "Check line numbers, offsets, and context lines."
                    )
                    audit_history.append(
                        RepairAttemptRecord(
                            attempt_number=attempt,
                            raw_model_response=model_resp.raw_output,
                            extracted_diff=diff_candidate,
                            gate_valid=False,
                            gate_rejection_reason="Failed to apply diff cleanly to workspace",
                            affected_files=gate_result.affected_files,
                            lines_added=gate_result.lines_added,
                            lines_deleted=gate_result.lines_deleted,
                            model_latency_seconds=model_resp.latency_seconds,
                        )
                    )
                    continue

                # Run Verification Tests in sandbox
                self._set_state(
                    RepairState.RUNNING_VERIFICATION_TESTS,
                    f"Running verification test suite inside sandbox (Attempt {attempt})",
                )
                post_test_result = self.sandbox_runner.run_tests(
                    workspace_dir=worktree.workspace_path,
                    is_predefined_fixture=is_predefined_fixture,
                )

                audit_history.append(
                    RepairAttemptRecord(
                        attempt_number=attempt,
                        raw_model_response=model_resp.raw_output,
                        extracted_diff=diff_candidate,
                        gate_valid=True,
                        affected_files=gate_result.affected_files,
                        lines_added=gate_result.lines_added,
                        lines_deleted=gate_result.lines_deleted,
                        test_passed=post_test_result.passed,
                        test_exit_code=post_test_result.exit_code,
                        test_stdout=post_test_result.stdout,
                        test_stderr=post_test_result.stderr,
                        is_infrastructure_failure=post_test_result.is_infrastructure_failure,
                        model_latency_seconds=model_resp.latency_seconds,
                    )
                )

                if post_test_result.is_infrastructure_failure:
                    self._set_state(
                        RepairState.INFRASTRUCTURE_ERROR,
                        post_test_result.failure_reason or "Infrastructure failure during test execution",
                    )
                    return RepairSessionReport(
                        repository_path=str(repo_path),
                        issue_description=issue_description,
                        terminal_state=RepairState.INFRASTRUCTURE_ERROR,
                        success=False,
                        attempts_count=attempt,
                        max_attempts=max_attempts,
                        baseline_passed=False,
                        baseline_stdout=baseline_test_result.stdout,
                        baseline_stderr=baseline_test_result.stderr,
                        audit_history=audit_history,
                        summary_message=f"Infrastructure failure: {post_test_result.failure_reason}",
                        total_elapsed_seconds=round(time.perf_counter() - t_start, 3),
                    )

                if post_test_result.passed:
                    # Verified! All tests passed!
                    self._set_state(
                        RepairState.COMPLETED_VERIFIED,
                        f"Patch verified! All tests passed on attempt {attempt}.",
                    )
                    verified_patch = diff_candidate
                    changed_files = gate_result.affected_files
                    return RepairSessionReport(
                        repository_path=str(repo_path),
                        issue_description=issue_description,
                        terminal_state=RepairState.COMPLETED_VERIFIED,
                        success=True,
                        attempts_count=attempt,
                        max_attempts=max_attempts,
                        baseline_passed=False,
                        baseline_stdout=baseline_test_result.stdout,
                        baseline_stderr=baseline_test_result.stderr,
                        verified_patch=verified_patch,
                        changed_files=changed_files,
                        audit_history=audit_history,
                        summary_message=f"Repair verified successfully on attempt {attempt}/{max_attempts}.",
                        total_elapsed_seconds=round(time.perf_counter() - t_start, 3),
                    )

                # Tests failed: build genuine failure evidence for next attempt
                prior_feedback = (
                    f"Attempt {attempt} verification tests FAILED (exit code {post_test_result.exit_code}).\n"
                    f"Test STDOUT:\n{post_test_result.stdout}\n"
                    f"Test STDERR:\n{post_test_result.stderr}\n"
                    "Carefully examine the failure traceback above and revise the patch."
                )

        # Attempts exhausted
        last_record = audit_history[-1] if audit_history else None
        if last_record and not last_record.gate_valid:
            terminal = RepairState.PATCH_REJECTED
            msg = f"Repair stopped: all proposals rejected by policy gate ({max_attempts}/{max_attempts} attempts)."
        else:
            terminal = RepairState.FAILED_MAX_ATTEMPTS
            msg = f"Repair failed: verification tests failed after maximum {max_attempts} attempts."

        self._set_state(terminal, msg)
        return RepairSessionReport(
            repository_path=str(repo_path),
            issue_description=issue_description,
            terminal_state=terminal,
            success=False,
            attempts_count=len(audit_history),
            max_attempts=max_attempts,
            baseline_passed=False,
            baseline_stdout=baseline_test_result.stdout,
            baseline_stderr=baseline_test_result.stderr,
            audit_history=audit_history,
            summary_message=msg,
            total_elapsed_seconds=round(time.perf_counter() - t_start, 3),
        )
