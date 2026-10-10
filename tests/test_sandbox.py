"""Tests for sandbox test runner and isolation boundaries."""

import pytest
from repomedic.sandbox.runner import (
    SandboxUnavailableError,
    SubprocessSandboxRunner,
)


def test_subprocess_blocks_arbitrary_repo(tmp_path):
    runner = SubprocessSandboxRunner()
    # Attempting to run arbitrary repository without fixture flag
    with pytest.raises(SandboxUnavailableError):
        runner.run_tests(workspace_dir=tmp_path, is_predefined_fixture=False)


def test_subprocess_runs_fixture(tmp_path):
    # Create simple passing test
    (tmp_path / "test_simple.py").write_text("def test_ok(): assert 1 == 1\n", encoding="utf-8")

    runner = SubprocessSandboxRunner()
    res = runner.run_tests(workspace_dir=tmp_path, is_predefined_fixture=True)

    assert res.passed is True
    assert res.exit_code == 0
    assert res.is_infrastructure_failure is False


def test_subprocess_reports_assertion_failure(tmp_path):
    # Create failing test
    (tmp_path / "test_failing.py").write_text("def test_bad(): assert 1 == 2\n", encoding="utf-8")

    runner = SubprocessSandboxRunner()
    res = runner.run_tests(workspace_dir=tmp_path, is_predefined_fixture=True)

    assert res.passed is False
    assert res.exit_code != 0
    assert res.is_infrastructure_failure is False
    assert res.failure_reason == "Test suite failure"


def test_timeout_handling(tmp_path):
    # Test script with infinite loop
    (tmp_path / "test_loop.py").write_text("import time\ndef test_hang(): time.sleep(5)\n", encoding="utf-8")

    # Short timeout 1.0s
    runner = SubprocessSandboxRunner(timeout_seconds=1.0)
    res = runner.run_tests(workspace_dir=tmp_path, is_predefined_fixture=True)

    assert res.passed is False
    assert res.is_infrastructure_failure is True
    assert "timed out" in res.stderr.lower()
