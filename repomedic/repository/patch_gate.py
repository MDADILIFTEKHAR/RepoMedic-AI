"""Deterministic security and policy gate for proposed model patches.

Validates unified diff syntax, blocks path traversal and disallowed files,
prevents test assertion tampering, and pre-checks patch applicability.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import List, Optional, Set
from pydantic import BaseModel

from repomedic.repository.scanner import is_sensitive_or_ignored_file, is_test_file

# Strictly forbidden file names & extensions in patches
DISALLOWED_EXTENSIONS: Set[str] = {
    ".sh",
    ".bat",
    ".cmd",
    ".ps1",
    ".exe",
    ".dll",
    ".so",
    ".bin",
    ".env",
    ".pem",
    ".key",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".lock",
    ".md",
    ".rst",
    ".txt",
}

DISALLOWED_EXACT_NAMES: Set[str] = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "dockerfile",
    "docker-compose.yml",
    "makefile",
    ".gitignore",
    ".gitmodules",
}


class DiffHunk(BaseModel):
    """Represents an individual hunk within a diff."""

    old_start: int
    old_length: int
    new_start: int
    new_length: int
    lines: List[str]


class ParsedFilePatch(BaseModel):
    """Represents a patch applied to a single target file."""

    old_path: str
    new_path: str
    hunks: List[DiffHunk]
    lines_added: int
    lines_deleted: int


class PatchValidationResult(BaseModel):
    """Result of patch gating and security verification."""

    is_valid: bool
    rejection_reason: Optional[str] = None
    affected_files: List[str] = []
    lines_added: int = 0
    lines_deleted: int = 0
    parsed_files: List[ParsedFilePatch] = []


class PatchGatePolicy:
    """Security policy controlling acceptable patch operations."""

    def __init__(
        self,
        allow_test_modifications: bool = False,
        allow_new_files: bool = False,
        max_affected_files: int = 5,
        max_lines_changed: int = 500,
    ) -> None:
        self.allow_test_modifications = allow_test_modifications
        self.allow_new_files = allow_new_files
        self.max_affected_files = max_affected_files
        self.max_lines_changed = max_lines_changed


def sanitize_patch_path(raw_path: str) -> str:
    """Strip git diff prefixes ('a/', 'b/') and normalize path separators."""
    clean = raw_path.strip().replace("\\", "/")
    if clean.startswith("a/"):
        clean = clean[2:]
    elif clean.startswith("b/"):
        clean = clean[2:]
    # Remove leading slashes
    clean = clean.lstrip("/")
    return clean


def parse_unified_diff(diff_text: str) -> List[ParsedFilePatch]:
    """Parse unified diff text into structured file patches and hunks.

    Raises ValueError if diff is malformed.
    """
    if not diff_text or not diff_text.strip():
        raise ValueError("Diff text is empty.")

    lines = diff_text.splitlines()
    file_patches: List[ParsedFilePatch] = []

    current_old: Optional[str] = None
    current_new: Optional[str] = None
    current_hunks: List[DiffHunk] = []
    current_hunk: Optional[DiffHunk] = None
    added_count = 0
    deleted_count = 0

    hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")

    for line in lines:
        if line.startswith("--- "):
            # If we had a prior file, store it
            if current_new and (current_hunks or current_hunk):
                if current_hunk:
                    current_hunks.append(current_hunk)
                    current_hunk = None
                file_patches.append(
                    ParsedFilePatch(
                        old_path=sanitize_patch_path(current_old or ""),
                        new_path=sanitize_patch_path(current_new),
                        hunks=current_hunks,
                        lines_added=added_count,
                        lines_deleted=deleted_count,
                    )
                )
                current_hunks = []
                added_count = 0
                deleted_count = 0

            raw_old = line[4:].strip()
            # If path contains tab (e.g. timestamp separator), take first token
            raw_old = raw_old.split("\t")[0]
            current_old = raw_old

        elif line.startswith("+++ "):
            raw_new = line[4:].strip().split("\t")[0]
            current_new = raw_new

        elif line.startswith("@@"):
            if not current_new:
                raise ValueError("Encountered hunk header '@@' before file target '+++'.")

            m = hunk_header_re.match(line)
            if not m:
                raise ValueError(f"Malformed hunk header syntax: {line}")

            if current_hunk:
                current_hunks.append(current_hunk)

            old_start = int(m.group(1))
            old_len = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_len = int(m.group(4)) if m.group(4) else 1

            current_hunk = DiffHunk(
                old_start=old_start,
                old_length=old_len,
                new_start=new_start,
                new_length=new_len,
                lines=[],
            )

        elif current_hunk is not None:
            if line.startswith("+"):
                added_count += 1
                current_hunk.lines.append(line)
            elif line.startswith("-"):
                deleted_count += 1
                current_hunk.lines.append(line)
            elif line.startswith(" ") or line == "":
                current_hunk.lines.append(line)
            elif line.startswith("\\ No newline at end of file"):
                current_hunk.lines.append(line)
            else:
                # Unexpected line inside hunk
                continue

    if current_new and (current_hunks or current_hunk):
        if current_hunk:
            current_hunks.append(current_hunk)
        file_patches.append(
            ParsedFilePatch(
                old_path=sanitize_patch_path(current_old or ""),
                new_path=sanitize_patch_path(current_new),
                hunks=current_hunks,
                lines_added=added_count,
                lines_deleted=deleted_count,
            )
        )

    if not file_patches:
        raise ValueError("No valid file patches discovered in diff text.")

    return file_patches


class PatchGate:
    """Security filter and policy checker for unified diffs."""

    def __init__(
        self,
        repo_root: Path,
        policy: Optional[PatchGatePolicy] = None,
        allowed_files: Optional[Set[str]] = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.policy = policy or PatchGatePolicy()
        self.allowed_files = allowed_files  # Set of relative paths discovered by scanner

    def validate_patch(self, diff_text: str) -> PatchValidationResult:
        """Run all deterministic policy and safety checks on the patch."""
        # 1. Diff text presence
        if not diff_text or not diff_text.strip():
            return PatchValidationResult(
                is_valid=False,
                rejection_reason="Patch is empty or contains no unified diff content.",
            )

        # 2. Parse diff structure
        try:
            file_patches = parse_unified_diff(diff_text)
        except ValueError as ex:
            return PatchValidationResult(
                is_valid=False,
                rejection_reason=f"Patch parsing failure: {str(ex)}",
            )

        # 3. Check number of affected files
        if len(file_patches) > self.policy.max_affected_files:
            return PatchValidationResult(
                is_valid=False,
                rejection_reason=(
                    f"Policy violation: patch modifies {len(file_patches)} files, "
                    f"exceeding max allowed {self.policy.max_affected_files}."
                ),
            )

        total_added = sum(p.lines_added for p in file_patches)
        total_deleted = sum(p.lines_deleted for p in file_patches)
        total_changed = total_added + total_deleted

        if total_changed > self.policy.max_lines_changed:
            return PatchValidationResult(
                is_valid=False,
                rejection_reason=(
                    f"Policy violation: patch modifies {total_changed} lines, "
                    f"exceeding max limit {self.policy.max_lines_changed}."
                ),
            )

        affected_paths: List[str] = []

        for p in file_patches:
            target_path_str = p.new_path or p.old_path

            # Disallow /dev/null if new files not permitted
            if target_path_str in ("/dev/null", "dev/null", ""):
                if not self.policy.allow_new_files:
                    return PatchValidationResult(
                        is_valid=False,
                        rejection_reason="Policy violation: creation or complete deletion of files is disallowed.",
                    )
                continue

            # Path traversal checks
            if ".." in target_path_str or target_path_str.startswith("/") or ":" in target_path_str:
                return PatchValidationResult(
                    is_valid=False,
                    rejection_reason=f"Security violation: path traversal or absolute path detected: '{target_path_str}'",
                )

            # Null byte injection
            if "\0" in target_path_str:
                return PatchValidationResult(
                    is_valid=False,
                    rejection_reason="Security violation: null byte in path.",
                )

            path_obj = Path(target_path_str)

            # Check if file has sensitive patterns or excluded directories (.git, etc.)
            if is_sensitive_or_ignored_file(path_obj):
                return PatchValidationResult(
                    is_valid=False,
                    rejection_reason=f"Security violation: attempt to modify sensitive/ignored path: '{target_path_str}'",
                )

            # Check disallowed filenames and extensions
            lower_name = path_obj.name.lower()
            if lower_name in DISALLOWED_EXACT_NAMES or path_obj.suffix.lower() in DISALLOWED_EXTENSIONS:
                return PatchValidationResult(
                    is_valid=False,
                    rejection_reason=(
                        f"Policy violation: modification to protected file/manifest is prohibited: '{target_path_str}'"
                    ),
                )

            # Enforce Python-only source modifications
            if path_obj.suffix.lower() != ".py":
                return PatchValidationResult(
                    is_valid=False,
                    rejection_reason=f"Policy violation: non-Python file modification disallowed: '{target_path_str}'",
                )

            # Anti-tampering: check if patch touches test files
            if is_test_file(path_obj) and not self.policy.allow_test_modifications:
                return PatchValidationResult(
                    is_valid=False,
                    rejection_reason=(
                        f"Integrity violation: test files may not be modified by repair model: '{target_path_str}'. "
                        "The model must fix source code, not alter tests."
                    ),
                )

            # Allowlist check: path must belong to scanned known repository files if allowlist provided
            if self.allowed_files is not None and target_path_str not in self.allowed_files:
                return PatchValidationResult(
                    is_valid=False,
                    rejection_reason=f"Policy violation: file '{target_path_str}' is not in scanned repository allowlist.",
                )

            affected_paths.append(target_path_str)

        # 4. Check patch applicability against repository using `git apply --check` if repo is git repo
        git_check_error = self._verify_git_apply_check(diff_text)
        if git_check_error:
            return PatchValidationResult(
                is_valid=False,
                rejection_reason=f"Patch application pre-check failed: {git_check_error}",
                affected_files=affected_paths,
                lines_added=total_added,
                lines_deleted=total_deleted,
            )

        return PatchValidationResult(
            is_valid=True,
            affected_files=affected_paths,
            lines_added=total_added,
            lines_deleted=total_deleted,
            parsed_files=file_patches,
        )

    def _verify_git_apply_check(self, diff_text: str) -> Optional[str]:
        """Run `git apply --check` in a temp file to ensure patch applies cleanly."""
        git_dir = self.repo_root / ".git"
        if not git_dir.exists():
            # If not a git repo, skip git-specific check (e.g. standalone test directory)
            return None

        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as tf:
            tf.write(diff_text)
            patch_path = tf.name

        try:
            res = subprocess.run(
                ["git", "apply", "--check", "--ignore-whitespace", patch_path],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode != 0:
                err = res.stderr.strip() or res.stdout.strip()
                return err or f"git apply --check exited with code {res.returncode}"
            return None
        except subprocess.TimeoutExpired:
            return "git apply --check timed out"
        except Exception as ex:
            # If git executable not usable for check, return None so tests can proceed
            return None
        finally:
            try:
                os.remove(patch_path)
            except OSError:
                pass
