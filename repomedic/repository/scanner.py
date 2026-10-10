"""Repository scanner for RepoMedic AI.

Safely discovers Python source files, filters out sensitive data and caches,
enforces strict context limits, and identifies relevant source files for a bug description.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import List, Optional, Set
from pydantic import BaseModel

from repomedic.models.base import CodeContextItem

# Disallowed directory names (never inspected)
EXCLUDED_DIRECTORIES: Set[str] = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".eggs",
}

# File extensions to ignore
EXCLUDED_EXTENSIONS: Set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".whl",
    ".tar",
    ".gz",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".pdf",
    ".db",
    ".sqlite",
    ".sqlite3",
}

# Regex patterns identifying sensitive secret files
SECRET_FILE_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r".*\.(pem|key|pkcs12|pfx|crt|cer)$", re.IGNORECASE),
    re.compile(r".*(id_rsa|id_ed25519|id_ecdsa)(\..+)?$", re.IGNORECASE),
    re.compile(r".*(password|secret|credential|token|api_key).*\.(json|yaml|yml|txt|toml)$", re.IGNORECASE),
]


class ScannedFileInfo(BaseModel):
    """Metadata for a discovered Python file in the repository."""

    rel_path: str
    absolute_path: Path
    size_bytes: int
    is_test_file: bool
    line_count: int


class RepositoryScanResult(BaseModel):
    """Result of a complete repository scan."""

    root_path: Path
    python_files: List[ScannedFileInfo]
    test_files: List[ScannedFileInfo]
    excluded_files_count: int
    total_size_bytes: int


def is_sensitive_or_ignored_file(rel_path: Path) -> bool:
    """Check whether a relative path matches excluded dirs, binary exts, or secret patterns."""
    parts = set(rel_path.parts)
    # Check if any parent component is in excluded directories
    if any(part in EXCLUDED_DIRECTORIES for part in parts):
        return True

    file_name = rel_path.name
    file_ext = rel_path.suffix.lower()

    if file_ext in EXCLUDED_EXTENSIONS:
        return True

    for pattern in SECRET_FILE_PATTERNS:
        if pattern.match(file_name):
            return True

    return False


def is_python_source(path: Path) -> bool:
    """Check if file is a Python source file."""
    return path.suffix.lower() == ".py"


def is_test_file(rel_path: Path) -> bool:
    """Check if file is a pytest/unittest test file."""
    name = rel_path.name.lower()
    parts = [p.lower() for p in rel_path.parts]
    if "tests" in parts or "test" in parts:
        return True
    return name.startswith("test_") or name.endswith("_test.py")


class RepositoryScanner:
    """Inspects a repository safely without reading sensitive or unneeded files."""

    def __init__(
        self,
        repo_root: Path,
        max_file_size_bytes: int = 262_144,
        max_context_bytes: int = 65_536,
        max_files_scanned: int = 200,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_file_size_bytes = max_file_size_bytes
        self.max_context_bytes = max_context_bytes
        self.max_files_scanned = max_files_scanned

    def scan(self) -> RepositoryScanResult:
        """Traverse the repository directory tree safely."""
        if not self.repo_root.is_dir():
            raise FileNotFoundError(f"Repository directory does not exist: {self.repo_root}")

        python_files: List[ScannedFileInfo] = []
        test_files: List[ScannedFileInfo] = []
        excluded_count = 0
        total_size = 0

        for root, dirs, files in os.walk(self.repo_root):
            # Prune excluded directories in-place so os.walk does not descend into them
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRECTORIES]

            for fname in files:
                full_path = Path(root) / fname
                try:
                    rel_path = full_path.relative_to(self.repo_root)
                except ValueError:
                    continue

                if is_sensitive_or_ignored_file(rel_path):
                    excluded_count += 1
                    continue

                if not is_python_source(full_path):
                    continue

                try:
                    stat = full_path.stat()
                except OSError:
                    excluded_count += 1
                    continue

                size = stat.st_size
                if size > self.max_file_size_bytes:
                    excluded_count += 1
                    continue

                # Count lines safely
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = len(f.readlines())
                except Exception:
                    excluded_count += 1
                    continue

                info = ScannedFileInfo(
                    rel_path=str(rel_path).replace("\\", "/"),
                    absolute_path=full_path,
                    size_bytes=size,
                    is_test_file=is_test_file(rel_path),
                    line_count=lines,
                )

                if info.is_test_file:
                    test_files.append(info)
                else:
                    python_files.append(info)

                total_size += size

                if len(python_files) + len(test_files) >= self.max_files_scanned:
                    break

        return RepositoryScanResult(
            root_path=self.repo_root,
            python_files=python_files,
            test_files=test_files,
            excluded_files_count=excluded_count,
            total_size_bytes=total_size,
        )

    def extract_relevant_context(
        self,
        issue_description: str,
        test_failure_output: Optional[str] = None,
        max_files: int = 5,
    ) -> List[CodeContextItem]:
        """Extract relevant source files based on issue description and test failures."""
        scan_result = self.scan()
        all_sources = scan_result.python_files

        if not all_sources:
            return []

        # Keywords from issue description and test output
        text_corpus = (issue_description + " " + (test_failure_output or "")).lower()
        tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text_corpus))

        # Score candidate files
        scored_files = []
        for file_info in all_sources:
            score = 0
            file_name_lower = Path(file_info.rel_path).name.lower()
            rel_path_lower = file_info.rel_path.lower()

            # Direct mention of file name in issue or test traceback
            if file_name_lower in text_corpus or rel_path_lower in text_corpus:
                score += 50

            # Read content to check token matches
            try:
                content = file_info.absolute_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            content_lower = content.lower()
            for token in tokens:
                if token in content_lower:
                    score += 1

            scored_files.append((score, file_info, content))

        # Sort descending by score
        scored_files.sort(key=lambda x: x[0], reverse=True)

        context_items: List[CodeContextItem] = []
        current_bytes = 0

        for score, file_info, content in scored_files[:max_files]:
            file_bytes = len(content.encode("utf-8"))
            if current_bytes + file_bytes > self.max_context_bytes:
                # Truncate content to fit remaining budget
                remaining = self.max_context_bytes - current_bytes
                if remaining < 500:
                    break
                content = content[:remaining] + "\n# ... [TRUNCATED DUE TO CONTEXT LIMIT] ..."
                file_bytes = len(content.encode("utf-8"))

            context_items.append(
                CodeContextItem(
                    rel_path=file_info.rel_path,
                    content=content,
                    start_line=1,
                    total_lines=file_info.line_count,
                )
            )
            current_bytes += file_bytes
            if current_bytes >= self.max_context_bytes:
                break

        # If no file scored high, include top source file if available
        if not context_items and all_sources:
            top_file = all_sources[0]
            try:
                c = top_file.absolute_path.read_text(encoding="utf-8", errors="replace")
                context_items.append(
                    CodeContextItem(
                        rel_path=top_file.rel_path,
                        content=c[: self.max_context_bytes],
                        start_line=1,
                        total_lines=top_file.line_count,
                    )
                )
            except Exception:
                pass

        return context_items
