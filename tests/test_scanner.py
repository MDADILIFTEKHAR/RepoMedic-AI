"""Tests for repository scanner and context extractor."""

from pathlib import Path
from repomedic.repository.scanner import (
    RepositoryScanner,
    is_sensitive_or_ignored_file,
    is_test_file,
)


def test_is_sensitive_or_ignored_file():
    assert is_sensitive_or_ignored_file(Path(".env")) is True
    assert is_sensitive_or_ignored_file(Path(".env.production")) is True
    assert is_sensitive_or_ignored_file(Path("secrets.key")) is True
    assert is_sensitive_or_ignored_file(Path("id_rsa")) is True
    assert is_sensitive_or_ignored_file(Path(".git/config")) is True
    assert is_sensitive_or_ignored_file(Path(".venv/lib/site-packages/pkg.py")) is True
    assert is_sensitive_or_ignored_file(Path("__pycache__/foo.cpython-311.pyc")) is True
    assert is_sensitive_or_ignored_file(Path("binary.exe")) is True

    # Legitimate python file
    assert is_sensitive_or_ignored_file(Path("app/main.py")) is False
    assert is_sensitive_or_ignored_file(Path("tests/test_app.py")) is False


def test_is_test_file():
    assert is_test_file(Path("test_calc.py")) is True
    assert is_test_file(Path("calc_test.py")) is True
    assert is_test_file(Path("tests/utils.py")) is True
    assert is_test_file(Path("src/calculator.py")) is False


def test_scanner_traversal(tmp_path):
    # Create sample repository tree
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_calc.py").write_text("from src.calc import add\ndef test(): assert add(1, 2) == 3\n", encoding="utf-8")

    # Sensitive/ignored files
    (tmp_path / ".env").write_text("SECRET_KEY=12345\n", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    scanner = RepositoryScanner(repo_root=tmp_path)
    result = scanner.scan()

    assert len(result.python_files) == 1
    assert result.python_files[0].rel_path == "src/calc.py"

    assert len(result.test_files) == 1
    assert result.test_files[0].rel_path == "tests/test_calc.py"

    assert result.excluded_files_count >= 1


def test_context_extraction(tmp_path):
    (tmp_path / "auth.py").write_text("def authenticate_user(): pass\n", encoding="utf-8")
    (tmp_path / "billing.py").write_text("def calculate_subscription(): pass\n", encoding="utf-8")

    scanner = RepositoryScanner(repo_root=tmp_path)
    items = scanner.extract_relevant_context("Bug in subscription discount calculation inside billing")

    assert len(items) >= 1
    assert items[0].rel_path == "billing.py"
