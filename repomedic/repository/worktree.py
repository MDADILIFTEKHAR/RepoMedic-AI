"""Disposable working-tree and isolated workspace management.

Guarantees that patch trials and test runs never modify the original repository.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
from typing import Optional

from repomedic.repository.scanner import EXCLUDED_DIRECTORIES


class DisposableWorkspace:
    """Manages an isolated temporary copy of a repository for safe patch experimentation."""

    def __init__(self, original_repo: Path) -> None:
        self.original_repo = Path(original_repo).resolve()
        self.temp_dir: Optional[Path] = None
        self.is_git_worktree: bool = False
        self.token: str = uuid.uuid4().hex[:8]

    def __enter__(self) -> "DisposableWorkspace":
        self.create()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()

    @property
    def workspace_path(self) -> Path:
        if not self.temp_dir or not self.temp_dir.exists():
            raise RuntimeError("Disposable workspace is not currently active.")
        return self.temp_dir

    def create(self) -> Path:
        """Create an isolated worktree or isolated file copy."""
        temp_base = tempfile.gettempdir()
        target_dir = Path(temp_base) / f"repomedic_wt_{self.token}"

        # Check if original is a valid git repository
        git_dir = self.original_repo / ".git"
        if git_dir.exists():
            # Attempt git worktree creation
            try:
                res = subprocess.run(
                    ["git", "worktree", "add", "--detach", str(target_dir), "HEAD"],
                    cwd=str(self.original_repo),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if res.returncode == 0:
                    self.temp_dir = target_dir
                    self.is_git_worktree = True
                    return target_dir
            except Exception:
                pass

        # Fallback: Copy repository files excluding sensitive/virtualenv directories
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        def _ignore_patterns(path: str, names: list[str]) -> set[str]:
            return {n for n in names if n in EXCLUDED_DIRECTORIES}

        shutil.copytree(
            self.original_repo,
            target_dir,
            ignore=_ignore_patterns,
            dirs_exist_ok=True,
        )
        self.temp_dir = target_dir
        self.is_git_worktree = False
        return target_dir

    def apply_patch(self, diff_text: str) -> bool:
        """Apply unified diff to the isolated workspace. Returns True on success."""
        if not self.temp_dir or not self.temp_dir.exists():
            raise RuntimeError("Workspace not active.")

        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as tf:
            tf.write(diff_text)
            patch_file = tf.name

        try:
            # 1. Try git apply if git worktree or git available
            res = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", patch_file],
                cwd=str(self.temp_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0:
                return True

            # 2. Try patch CLI command if available
            patch_bin = shutil.which("patch")
            if patch_bin:
                p_res = subprocess.run(
                    [patch_bin, "-p1", "-i", patch_file],
                    cwd=str(self.temp_dir),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if p_res.returncode == 0:
                    return True

            # 3. Fallback: Python native minimal unified diff applier
            return self._apply_diff_natively(diff_text)

        finally:
            try:
                os.remove(patch_file)
            except OSError:
                pass

    def rollback(self) -> None:
        """Discard uncommitted modifications inside the disposable workspace."""
        if not self.temp_dir or not self.temp_dir.exists():
            return

        if self.is_git_worktree:
            try:
                subprocess.run(
                    ["git", "checkout", "-f"],
                    cwd=str(self.temp_dir),
                    capture_output=True,
                    timeout=10,
                )
                subprocess.run(
                    ["git", "clean", "-fd"],
                    cwd=str(self.temp_dir),
                    capture_output=True,
                    timeout=10,
                )
                return
            except Exception:
                pass

        # If file copy, recreate workspace cleanly
        self.cleanup()
        self.create()

    def cleanup(self) -> None:
        """Remove worktree and clean up all temporary directories."""
        if self.is_git_worktree and self.temp_dir:
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(self.temp_dir)],
                    cwd=str(self.original_repo),
                    capture_output=True,
                    timeout=10,
                )
                subprocess.run(
                    ["git", "worktree", "prune"],
                    cwd=str(self.original_repo),
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass

        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None

    def _apply_diff_natively(self, diff_text: str) -> bool:
        """Native pure-Python unified diff applier for single/multi file patches."""
        from repomedic.repository.patch_gate import parse_unified_diff

        try:
            file_patches = parse_unified_diff(diff_text)
        except Exception:
            return False

        for fp in file_patches:
            target_rel = fp.new_path or fp.old_path
            target_file = self.temp_dir / target_rel
            if not target_file.exists():
                return False

            try:
                content = target_file.read_text(encoding="utf-8")
                lines = content.splitlines(keepends=True)
            except Exception:
                return False

            # Apply hunks in reverse order (by old_start) so line offsets remain stable
            sorted_hunks = sorted(fp.hunks, key=lambda h: h.old_start, reverse=True)
            for hunk in sorted_hunks:
                # 1-indexed to 0-indexed start
                start_idx = max(0, hunk.old_start - 1)
                del_count = hunk.old_length

                # Construct new replacement lines
                new_chunk = []
                for line in hunk.lines:
                    if line.startswith("+"):
                        new_chunk.append(line[1:] + "\n")
                    elif line.startswith(" "):
                        new_chunk.append(line[1:] + "\n")

                lines[start_idx : start_idx + del_count] = new_chunk

            target_file.write_text("".join(lines), encoding="utf-8")

        return True
