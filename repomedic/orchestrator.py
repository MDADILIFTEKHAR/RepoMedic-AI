"""Evidence-Gated Repair Orchestrator for RepoMedic AI.

Implements the finite state machine:
CREATED → REPOSITORY_VALIDATED → BASELINE_TESTED → ANALYZED → PATCH_PROPOSED →
PATCH_VALIDATED → PATCH_APPLIED → TESTED → VERIFIED or FAILED.
Coordinates repository scanning, baseline test execution, open-weight model patch generation,
deterministic policy gating, disposable worktree execution, failure evidence feedback,
and bounded 3-attempt retries.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
import time
from typing import Callable, List, Optional
from pydantic import BaseModel, Field

from repomedic.config import Settings, get_settings
from repomedic.github.client import redact_token
from repomedic.models.base import BaseModelClient, ModelPrompt
from repomedic.repository.patch_gate import PatchGate, PatchGatePolicy, PatchValidationResult
from repomedic.repository.scanner import RepositoryScanner
from repomedic.repository.worktree import DisposableWorkspace, WorkspaceError
from repomedic.sandbox.runner import (
    BaseSandboxRunner,
    SandboxUnavailableError,
    TestExecutionResult,
    create_sandbox_runner,
)


class RepairState(str, Enum):
    """Explicit states of the Evidence-Gated Repair Harness."""

    CREATED = "CREATED"
    REPOSITORY_VALIDATED = "REPOSITORY_VALIDATED"
    BASELINE_TESTED = "BASELINE_TESTED"
    ANALYZED = "ANALYZED"
    PATCH_PROPOSED = "PATCH_PROPOSED"
    PATCH_VALIDATED = "PATCH_VALIDATED"
    PATCH_APPLIED = "PATCH_APPLIED"
    TESTED = "TESTED"
    # Terminal outcomes
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    PATCH_REJECTED = "PATCH_REJECTED"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    SANDBOX_BLOCKED = "SANDBOX_BLOCKED"

    # Compatibility aliases
    COMPLETED_VERIFIED = "VERIFIED"
    FAILED_MAX_ATTEMPTS = "FAILED"
    INITIALIZING = "CREATED"
    COLLECTING_CONTEXT = "ANALYZED"
    RUNNING_BASELINE_TESTS = "BASELINE_TESTED"
    GENERATING_PATCH = "PATCH_PROPOSED"
    VALIDATING_PATCH_GATE = "PATCH_VALIDATED"
    APPLYING_PATCH = "PATCH_APPLIED"
    RUNNING_VERIFICATION_TESTS = "TESTED"


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
    remote_clone_url: Optional[str] = None
    base_branch: Optional[str] = "main"
    disposable_branch: Optional[str] = None
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
    pull_request_url: Optional[str] = None


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
        self.current_state = RepairState.CREATED

    def _set_state(self, state: RepairState, message: str = "") -> None:
        self.current_state = state
        if self.on_state_change:
            self.on_state_change(state, message)

    async def run_repair(
        self,
        repo_path: Optional[Path] = None,
        remote_clone_url: Optional[str] = None,
        branch: Optional[str] = "main",
        auth_token: Optional[str] = None,
        issue_description: str = "",
        is_predefined_fixture: bool = False,
    ) -> RepairSessionReport:
        """Execute the end-to-end evidence-gated repair loop."""
        t_start = time.perf_counter()
        target_name = repo_path.name if repo_path else (remote_clone_url or "repository")
        self._set_state(RepairState.CREATED, f"Task created for {target_name}")

        audit_history: List[RepairAttemptRecord] = []
        max_attempts = self.settings.max_repair_attempts

        # 1. Initialize Disposable Workspace & Validate Repository
        self._set_state(RepairState.REPOSITORY_VALIDATED, f"Validating and cloning workspace for {target_name}")

        try:
            active_ws = DisposableWorkspace(
                original_repo=repo_path,
                remote_clone_url=remote_clone_url,
                base_branch=branch,
                auth_token=auth_token,
            )
            working_dir = active_ws.create()
        except WorkspaceError as err:
            clean_err = redact_token(str(err))
            self._set_state(RepairState.INFRASTRUCTURE_ERROR, clean_err)
            return RepairSessionReport(
                repository_path=str(repo_path or remote_clone_url or ""),
                remote_clone_url=remote_clone_url,
                base_branch=branch,
                issue_description=issue_description,
                terminal_state=RepairState.INFRASTRUCTURE_ERROR,
                success=False,
                attempts_count=0,
                max_attempts=max_attempts,
                baseline_passed=False,
                baseline_stdout="",
                baseline_stderr=clean_err,
                summary_message=clean_err,
                total_elapsed_seconds=round(time.perf_counter() - t_start, 3),
            )

        try:
            # 2. Scan Repository and discover Python source files
            scanner = RepositoryScanner(
                repo_root=working_dir,
                max_file_size_bytes=self.settings.max_file_size_bytes,
                max_context_bytes=self.settings.max_context_bytes,
            )
            scan_result = scanner.scan()
            allowed_source_rel_paths = {f.rel_path for f in scan_result.python_files}

            # 3. Run Baseline Tests in isolated disposable workspace
            self._set_state(
                RepairState.BASELINE_TESTED,
                "Running baseline test suite to observe initial bug failure",
            )
            try:
                baseline_test_result = self.sandbox_runner.run_tests(
                    workspace_dir=working_dir,
                    is_predefined_fixture=is_predefined_fixture,
                )
            except SandboxUnavailableError as err:
                self._set_state(RepairState.SANDBOX_BLOCKED, str(err))
                return RepairSessionReport(
                    repository_path=str(repo_path or remote_clone_url or ""),
                    remote_clone_url=remote_clone_url,
                    base_branch=branch,
                    disposable_branch=active_ws.disposable_branch,
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
                self._set_state(
                    RepairState.INFRASTRUCTURE_ERROR,
                    baseline_test_result.failure_reason or "Infrastructure failure",
                )
                return RepairSessionReport(
                    repository_path=str(repo_path or remote_clone_url or ""),
                    remote_clone_url=remote_clone_url,
                    base_branch=branch,
                    disposable_branch=active_ws.disposable_branch,
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
                self._set_state(RepairState.VERIFIED, "Baseline tests already pass cleanly.")
                return RepairSessionReport(
                    repository_path=str(repo_path or remote_clone_url or ""),
                    remote_clone_url=remote_clone_url,
                    base_branch=branch,
                    disposable_branch=active_ws.disposable_branch,
                    issue_description=issue_description,
                    terminal_state=RepairState.VERIFIED,
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

            # 4. Analyze Code Context
            self._set_state(
                RepairState.ANALYZED,
                "Analyzing repository structure and scoring relevant source files",
            )
            context_files = scanner.extract_relevant_context(
                issue_description=issue_description,
                test_failure_output=baseline_evidence,
            )

            # 5. Bounded Repair Loop (max_attempts iterations)
            prior_feedback: Optional[str] = None
            verified_patch: Optional[str] = None
            changed_files: List[str] = []

            for attempt in range(1, max_attempts + 1):
                # State: PATCH_PROPOSED
                self._set_state(
                    RepairState.PATCH_PROPOSED,
                    f"Querying model for repair patch (Attempt {attempt}/{max_attempts})",
                )

                prompt = ModelPrompt(
                    repository_name=target_name,
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
                        repository_path=str(repo_path or remote_clone_url or ""),
                        remote_clone_url=remote_clone_url,
                        base_branch=branch,
                        disposable_branch=active_ws.disposable_branch,
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

                # State: PATCH_VALIDATED
                self._set_state(
                    RepairState.PATCH_VALIDATED,
                    f"Validating patch security policy (Attempt {attempt})",
                )
                gate = PatchGate(
                    repo_root=working_dir,
                    policy=PatchGatePolicy(allow_test_modifications=False, allow_new_files=False),
                    allowed_files=allowed_source_rel_paths,
                )
                gate_result: PatchValidationResult = gate.validate_patch(diff_candidate)

                if not gate_result.is_valid:
                    rejection_msg = gate_result.rejection_reason or "Policy rejection"
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

                # State: PATCH_APPLIED
                self._set_state(
                    RepairState.PATCH_APPLIED,
                    f"Applying patch to isolated disposable workspace (Attempt {attempt})",
                )
                # Rollback any changes from prior failed attempts in same workspace
                active_ws.rollback()
                applied = active_ws.apply_patch(diff_candidate)
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

                # State: TESTED
                self._set_state(
                    RepairState.TESTED,
                    f"Running verification test suite inside sandbox (Attempt {attempt})",
                )
                post_test_result = self.sandbox_runner.run_tests(
                    workspace_dir=working_dir,
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
                        repository_path=str(repo_path or remote_clone_url or ""),
                        remote_clone_url=remote_clone_url,
                        base_branch=branch,
                        disposable_branch=active_ws.disposable_branch,
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
                    # Verified!
                    self._set_state(
                        RepairState.VERIFIED,
                        f"Patch verified! All tests passed on attempt {attempt}.",
                    )
                    verified_patch = diff_candidate
                    changed_files = gate_result.affected_files

                    # If this is a git workspace, commit the patch to the disposable branch
                    active_ws.commit_patch(f"fix(repomedic): resolve {issue_description[:60]}")

                    return RepairSessionReport(
                        repository_path=str(repo_path or remote_clone_url or ""),
                        remote_clone_url=remote_clone_url,
                        base_branch=branch,
                        disposable_branch=active_ws.disposable_branch,
                        issue_description=issue_description,
                        terminal_state=RepairState.VERIFIED,
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
                terminal = RepairState.FAILED
                msg = f"Repair failed: verification tests failed after maximum {max_attempts} attempts."

            self._set_state(terminal, msg)
            return RepairSessionReport(
                repository_path=str(repo_path or remote_clone_url or ""),
                remote_clone_url=remote_clone_url,
                base_branch=branch,
                disposable_branch=active_ws.disposable_branch,
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

        finally:
            active_ws.cleanup()
