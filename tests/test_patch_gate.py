"""Tests for Patch Gate deterministic security and policy filter."""

from pathlib import Path
import pytest
from repomedic.repository.patch_gate import (
    PatchGate,
    PatchGatePolicy,
    parse_unified_diff,
)


def test_parse_unified_diff_valid():
    diff = (
        "--- a/service.py\n"
        "+++ b/service.py\n"
        "@@ -10,3 +10,3 @@\n"
        " def run():\n"
        "-    x = 1\n"
        "+    x = 2\n"
        "     return x\n"
    )
    patches = parse_unified_diff(diff)
    assert len(patches) == 1
    p = patches[0]
    assert p.new_path == "service.py"
    assert p.lines_added == 1
    assert p.lines_deleted == 1
    assert len(p.hunks) == 1


def test_parse_unified_diff_malformed():
    with pytest.raises(ValueError):
        parse_unified_diff("")

    with pytest.raises(ValueError):
        parse_unified_diff("random invalid text without diff headers")


def test_gate_valid_patch(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    gate = PatchGate(repo_root=tmp_path, allowed_files={"app.py"})

    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    res = gate.validate_patch(diff)
    assert res.is_valid is True
    assert res.affected_files == ["app.py"]
    assert res.lines_added == 1
    assert res.lines_deleted == 1


def test_gate_rejects_path_traversal(tmp_path):
    gate = PatchGate(repo_root=tmp_path)
    diff = (
        "--- a/../../etc/passwd\n"
        "+++ b/../../etc/passwd\n"
        "@@ -1,1 +1,1 @@\n"
        "-root\n"
        "+hacked\n"
    )
    res = gate.validate_patch(diff)
    assert res.is_valid is False
    assert "path traversal" in res.rejection_reason.lower()


def test_gate_rejects_disallowed_manifests(tmp_path):
    gate = PatchGate(repo_root=tmp_path)
    diff = (
        "--- a/requirements.txt\n"
        "+++ b/requirements.txt\n"
        "@@ -1,1 +1,2 @@\n"
        "+evil-pkg==1.0\n"
    )
    res = gate.validate_patch(diff)
    assert res.is_valid is False
    assert "protected file/manifest" in res.rejection_reason.lower()


def test_gate_rejects_test_tampering(tmp_path):
    gate = PatchGate(repo_root=tmp_path)
    diff = (
        "--- a/test_service.py\n"
        "+++ b/test_service.py\n"
        "@@ -1,2 +1,1 @@\n"
        "-assert False\n"
        "+assert True\n"
    )
    res = gate.validate_patch(diff)
    assert res.is_valid is False
    assert "test files may not be modified" in res.rejection_reason.lower()


def test_gate_rejects_unlisted_files(tmp_path):
    gate = PatchGate(repo_root=tmp_path, allowed_files={"service.py"})
    diff = (
        "--- a/unknown.py\n"
        "+++ b/unknown.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-a\n"
        "+b\n"
    )
    res = gate.validate_patch(diff)
    assert res.is_valid is False
    assert "not in scanned repository allowlist" in res.rejection_reason.lower()
