"""Tests for disposable workspace and rollback isolation."""

from pathlib import Path
from repomedic.repository.worktree import DisposableWorkspace


def test_disposable_workspace_isolation(tmp_path):
    orig_file = tmp_path / "calc.py"
    orig_file.write_text("def subtract(a, b): return a - b\n", encoding="utf-8")

    diff = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def subtract(a, b): return a - b\n"
        "+def subtract(a, b): return a + b\n"
    )

    with DisposableWorkspace(tmp_path) as ws:
        applied = ws.apply_patch(diff)
        assert applied is True

        # Modified inside disposable workspace
        wt_file = ws.workspace_path / "calc.py"
        assert "return a + b" in wt_file.read_text(encoding="utf-8")

        # Original repo is completely unchanged!
        assert "return a - b" in orig_file.read_text(encoding="utf-8")

    # Workspace directory cleaned up on exit
    assert not ws.temp_dir or not ws.temp_dir.exists()


def test_rollback(tmp_path):
    orig_file = tmp_path / "app.py"
    orig_file.write_text("version = '1.0'\n", encoding="utf-8")

    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-version = '1.0'\n"
        "+version = '2.0'\n"
    )

    with DisposableWorkspace(tmp_path) as ws:
        ws.apply_patch(diff)
        assert "2.0" in (ws.workspace_path / "app.py").read_text(encoding="utf-8")

        ws.rollback()
        assert "1.0" in (ws.workspace_path / "app.py").read_text(encoding="utf-8")
