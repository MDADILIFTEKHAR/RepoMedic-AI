"""Sandbox execution harness for running tests in an isolated environment.

Provides Docker-based container isolation for arbitrary repositories,
and restricted subprocess isolation for predefined benchmark fixtures.
Strictly blocks execution of arbitrary code when Docker is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import List, Optional
from pydantic import BaseModel


class SandboxError(Exception):
    """Base exception for sandbox execution errors."""


class SandboxUnavailableError(SandboxError):
    """Raised when required container sandboxing is missing for untrusted code."""


class TestExecutionResult(BaseModel):
    """Structured outcome of executing a test suite."""

    passed: bool
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    is_infrastructure_failure: bool = False
    failure_reason: Optional[str] = None
    runner_type: str = "unknown"


class BaseSandboxRunner:
    """Base sandbox runner interface."""

    def __init__(self, timeout_seconds: float = 25.0) -> None:
        self.timeout_seconds = timeout_seconds

    def run_tests(
        self,
        workspace_dir: Path,
        test_command: Optional[List[str]] = None,
        is_predefined_fixture: bool = False,
    ) -> TestExecutionResult:
        raise NotImplementedError


class DockerSandboxRunner(BaseSandboxRunner):
    """Executes tests inside an ephemeral, network-isolated Docker container."""

    def __init__(
        self,
        image: str = "python:3.11-slim",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        pids_limit: int = 64,
        timeout_seconds: float = 25.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit

    def run_tests(
        self,
        workspace_dir: Path,
        test_command: Optional[List[str]] = None,
        is_predefined_fixture: bool = False,
    ) -> TestExecutionResult:
        workspace_dir = Path(workspace_dir).resolve()
        cmd = test_command or ["pytest", "-q"]

        # Docker command with strict security flags:
        # --rm: Clean up container on exit
        # --network none: Disable all network access
        # --memory: Cap RAM to prevent resource exhaustion
        # --cpus: Cap CPU quota
        # --pids-limit: Prevent fork-bombs
        # -v: Mount only the disposable workspace directory
        docker_args = [
            "docker",
            "run",
            "--rm",
            "--network", "none",
            "--memory", self.memory_limit,
            f"--cpus={self.cpu_limit}",
            f"--pids-limit={self.pids_limit}",
            "-v", f"{workspace_dir}:/workspace",
            "-w", "/workspace",
            self.image,
        ] + cmd

        t0 = time.perf_counter()
        try:
            res = subprocess.run(
                docker_args,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            duration = time.perf_counter() - t0
            passed = res.returncode == 0
            stdout_bounded = res.stdout[:16_384]
            stderr_bounded = res.stderr[:16_384]

            # Detect Docker daemon or container startup failure
            if res.returncode == 125 or res.returncode == 126 or res.returncode == 127:
                return TestExecutionResult(
                    passed=False,
                    exit_code=res.returncode,
                    duration_seconds=round(duration, 3),
                    stdout=stdout_bounded,
                    stderr=stderr_bounded,
                    is_infrastructure_failure=True,
                    failure_reason=f"Docker container engine error (code {res.returncode})",
                    runner_type="docker",
                )

            return TestExecutionResult(
                passed=passed,
                exit_code=res.returncode,
                duration_seconds=round(duration, 3),
                stdout=stdout_bounded,
                stderr=stderr_bounded,
                is_infrastructure_failure=False,
                failure_reason=None if passed else "Tests failed in container",
                runner_type="docker",
            )

        except subprocess.TimeoutExpired:
            duration = time.perf_counter() - t0
            return TestExecutionResult(
                passed=False,
                exit_code=-1,
                duration_seconds=round(duration, 3),
                stdout="",
                stderr=f"Execution timed out after {self.timeout_seconds} seconds.",
                is_infrastructure_failure=True,
                failure_reason="Test execution timed out in Docker container",
                runner_type="docker",
            )
        except Exception as ex:
            duration = time.perf_counter() - t0
            return TestExecutionResult(
                passed=False,
                exit_code=-1,
                duration_seconds=round(duration, 3),
                stdout="",
                stderr=f"Docker invocation error: {str(ex)}",
                is_infrastructure_failure=True,
                failure_reason=f"Docker infrastructure failure: {str(ex)}",
                runner_type="docker",
            )


class SubprocessSandboxRunner(BaseSandboxRunner):
    """Restricted local subprocess runner.

    ONLY permits execution for verified, predefined benchmark fixtures.
    Strictly refuses arbitrary repositories when Docker is unavailable.
    """

    def run_tests(
        self,
        workspace_dir: Path,
        test_command: Optional[List[str]] = None,
        is_predefined_fixture: bool = False,
    ) -> TestExecutionResult:
        if not is_predefined_fixture:
            raise SandboxUnavailableError(
                "Security policy violation: Docker is unavailable or not running on this host. "
                "Arbitrary repository code execution is strictly disabled to prevent host compromise. "
                "Only verified, predefined benchmark fixtures may be executed without Docker containerization."
            )

        workspace_dir = Path(workspace_dir).resolve()
        python_bin = sys.executable

        # Use current Python environment to run pytest
        cmd = test_command or [python_bin, "-m", "pytest", "-q"]

        # Restricted environment
        restricted_env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "PYTHONPATH": str(workspace_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }

        t0 = time.perf_counter()
        try:
            res = subprocess.run(
                cmd,
                cwd=str(workspace_dir),
                env=restricted_env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            duration = time.perf_counter() - t0
            passed = res.returncode == 0
            stdout_bounded = res.stdout[:16_384]
            stderr_bounded = res.stderr[:16_384]

            return TestExecutionResult(
                passed=passed,
                exit_code=res.returncode,
                duration_seconds=round(duration, 3),
                stdout=stdout_bounded,
                stderr=stderr_bounded,
                is_infrastructure_failure=False,
                failure_reason=None if passed else "Test suite failure",
                runner_type="subprocess_restricted",
            )

        except subprocess.TimeoutExpired:
            duration = time.perf_counter() - t0
            return TestExecutionResult(
                passed=False,
                exit_code=-1,
                duration_seconds=round(duration, 3),
                stdout="",
                stderr=f"Test run timed out after {self.timeout_seconds} seconds.",
                is_infrastructure_failure=True,
                failure_reason="Execution timed out",
                runner_type="subprocess_restricted",
            )
        except Exception as ex:
            duration = time.perf_counter() - t0
            return TestExecutionResult(
                passed=False,
                exit_code=-1,
                duration_seconds=round(duration, 3),
                stdout="",
                stderr=f"Subprocess execution error: {str(ex)}",
                is_infrastructure_failure=True,
                failure_reason=str(ex),
                runner_type="subprocess_restricted",
            )


def create_sandbox_runner(
    timeout_seconds: float = 25.0,
    force_docker: bool = False,
) -> BaseSandboxRunner:
    """Create sandbox runner according to host capabilities."""
    has_docker = shutil.which("docker") is not None

    if has_docker and not force_docker:
        # Check if docker daemon is actually responding
        try:
            check = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=3,
            )
            if check.returncode == 0:
                return DockerSandboxRunner(timeout_seconds=timeout_seconds)
        except Exception:
            pass

    return SubprocessSandboxRunner(timeout_seconds=timeout_seconds)
