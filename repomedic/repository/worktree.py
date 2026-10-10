"""Disposable working-tree and isolated workspace management.

Guarantees that patch trials and test runs never modify the original repository,
and provides secure remote cloning with credential redaction and disposable branch isolation.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
from typing import Optional

from repomedic.github.client import redact_token, validate_github_identifier
from repomedic.repository.scanner import EXCLUDED_DIRECTORIES

GITHUB_URL_RE = re.compile(r"^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)(\.git)?$")


class WorkspaceError(Exception):
    """Base exception for workspace operations."""


class DisposableWorkspace:
    """Manages an isolated temporary copy of a repository for safe patch experimentation."""

    def __init__(
        self,
        original_repo: Optional[Path] = None,
        remote_clone_url: Optional[str] = None,
        base_branch: Optional[str] = "main",
        auth_token: Optional[str] = None,
    ) -> None:
        self.original_repo = Path(original_repo).resolve() if original_repo else None
        self.remote_clone_url = remote_clone_url
        self.base_branch = validate_github_identifier(base_branch) if base_branch else "main"
        self.auth_token = auth_token
        self.temp_dir: Optional[Path] = None
        self.is_git_worktree: bool = False
        self.is_remote_clone: bool = False
        self.token: str = uuid.uuid4().hex[:8]
        self.disposable_branch: str = f"repomedic/fix-{self.token}"

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
        """Create an isolated worktree or secure remote clone."""
        temp_base = tempfile.gettempdir()
        target_dir = Path(temp_base) / f"repomedic_wt_{self.token}"

        # 1. Remote GitHub repository clone
        if self.remote_clone_url:
            return self._create_from_remote(target_dir)

        # 2. Local repository
        if not self.original_repo or not self.original_repo.exists():
            raise WorkspaceError("Cannot initialize workspace: no original_repo or remote_clone_url supplied.")

        return self._create_from_local(target_dir)

    def _create_from_remote(self, target_dir: Path) -> Path:
        """Clone remote GitHub repository into an ephemeral workspace directory."""
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        m = GITHUB_URL_RE.match(self.remote_clone_url.strip())
        if not m:
            raise WorkspaceError(
                f"Security error: Invalid remote repository URL: '{self.remote_clone_url}'. "
                "Only standard HTTPS GitHub URLs (https://github.com/owner/repo) are permitted."
            )

        owner, repo = m.group(1), m.group(2)
        if self.auth_token:
            auth_clone_url = f"https://x-access-token:{self.auth_token}@github.com/{owner}/{repo}.git"
        else:
            auth_clone_url = f"https://github.com/{owner}/{repo}.git"

        # Clone shallowly with timeout
        cmd = [
            "git", "clone",
            "--depth", "50",
            "--branch", self.base_branch,
            auth_clone_url,
            str(target_dir),
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if res.returncode != 0:
                clean_err = redact_token(res.stderr or res.stdout)
                raise WorkspaceError(f"Failed to clone remote repository branch '{self.base_branch}': {clean_err}")

            # Create disposable working branch
            branch_cmd = ["git", "checkout", "-b", self.disposable_branch]
            subprocess.run(branch_cmd, cwd=str(target_dir), capture_output=True, timeout=10)

            self.temp_dir = target_dir
            self.is_remote_clone = True
            self.is_git_worktree = False
            return target_dir

        except subprocess.TimeoutExpired:
            raise WorkspaceError("Remote git clone timed out after 45 seconds.")
        except Exception as ex:
            clean_ex = redact_token(str(ex))
            raise WorkspaceError(f"Error during repository clone: {clean_ex}")

    def _create_from_local(self, target_dir: Path) -> Path:
        """Create isolated local worktree or sanitized file copy."""
        git_dir = self.original_repo / ".git"
        if git_dir.exists():
            # Attempt git worktree creation (detach or branch)
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
                    self.is_remote_clone = False
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
        self.is_remote_clone = False
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

    def commit_patch(
        self,
        message: str,
        author_name: str = "RepoMedic AI",
        author_email: str = "repomedic@users.noreply.github.com",
    ) -> bool:
        """Commit modified tracked files inside the disposable branch."""
        if not self.temp_dir or not self.temp_dir.exists():
            return False

        try:
            # Stage modified tracked files
            subprocess.run(["git", "add", "-u"], cwd=str(self.temp_dir), capture_output=True, timeout=10)

            # Commit
            commit_res = subprocess.run(
                [
                    "git",
                    "-c", f"user.name={author_name}",
                    "-c", f"user.email={author_email}",
                    "commit",
                    "-m", message.strip(),
                ],
                cwd=str(self.temp_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return commit_res.returncode == 0
        except Exception:
            return False

    def push_disposable_branch(self) -> str:
        """Push the disposable branch to the remote repository. Requires explicit user approval."""
        if not self.temp_dir or not self.temp_dir.exists():
            raise WorkspaceError("Workspace not active.")

        if not self.is_remote_clone:
            raise WorkspaceError("Cannot push branch: current workspace is local, not a remote clone.")

        cmd = ["git", "push", "-u", "origin", self.disposable_branch]
        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.temp_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode != 0:
                err = redact_token(res.stderr or res.stdout)
                raise WorkspaceError(f"Git push failed: {err}")
            return self.disposable_branch
        except subprocess.TimeoutExpired:
            raise WorkspaceError("Git push timed out.")
        except Exception as ex:
            raise WorkspaceError(f"Error during git push: {redact_token(str(ex))}")

    def rollback(self) -> None:
        """Discard uncommitted modifications inside the disposable workspace."""
        if not self.temp_dir or not self.temp_dir.exists():
            return

        if (self.is_git_worktree or self.is_remote_clone) and (self.temp_dir / ".git").exists():
            try:
                subprocess.run(["git", "checkout", "-f"], cwd=str(self.temp_dir), capture_output=True, timeout=10)
                subprocess.run(["git", "clean", "-fd"], cwd=str(self.temp_dir), capture_output=True, timeout=10)
                return
            except Exception:
                pass

        # If file copy, recreate workspace cleanly
        self.cleanup()
        self.create()

    def cleanup(self) -> None:
        """Remove worktree and clean up all temporary directories."""
        if self.is_git_worktree and self.temp_dir and self.original_repo:
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
                start_idx = max(0, hunk.old_start - 1)
                del_count = hunk.old_length

                new_chunk = []
                for line in hunk.lines:
                    if line.startswith("+"):
                        new_chunk.append(line[1:] + "\n")
                    elif line.startswith(" "):
                        new_chunk.append(line[1:] + "\n")

                lines[start_idx : start_idx + del_count] = new_chunk

            target_file.write_text("".join(lines), encoding="utf-8")

        return True
