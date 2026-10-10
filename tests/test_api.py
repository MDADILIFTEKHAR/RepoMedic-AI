"""Tests for FastAPI endpoints, request validation, downloads, and PR guards."""

import pytest
from fastapi.testclient import TestClient
from repomedic.api import app, repair_jobs
from repomedic.config import Settings, reset_settings
from repomedic.orchestrator import RepairState


@pytest.fixture
def client():
    # Use mock model provider for API unit tests
    s = Settings(model_provider="mock", environment="test")
    reset_settings(s)
    with TestClient(app) as c:
        yield c
    reset_settings(None)


def test_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "version" in data
    assert "model_provider" in data
    assert "docker_available" in data


def test_benchmark_tasks_list(client):
    res = client.get("/api/benchmark/tasks")
    assert res.status_code == 200
    tasks = res.json()
    assert len(tasks) == 10
    task_ids = [t["task_id"] for t in tasks]
    assert "task_01_arithmetic_discount" in task_ids


def test_start_repair_invalid_payload(client):
    # Missing parameters
    res = client.post("/api/repair/start", json={})
    assert res.status_code == 400

    # Non-existent fixture
    res = client.post("/api/repair/start", json={"fixture_id": "non_existent_task"})
    assert res.status_code == 404


def test_start_repair_and_poll(client):
    res = client.post(
        "/api/repair/start",
        json={"fixture_id": "task_01_arithmetic_discount"},
    )
    assert res.status_code == 202
    data = res.json()
    assert "job_id" in data
    job_id = data["job_id"]

    # Poll status
    status_res = client.get(f"/api/repair/status/{job_id}")
    assert status_res.status_code == 200
    s_data = status_res.json()
    assert s_data["job_id"] == job_id


def test_github_status_endpoint(client):
    res = client.get("/api/github/status")
    assert res.status_code == 200
    data = res.json()
    assert "oauth_configured" in data


def test_github_auth_validation(client):
    res = client.post("/api/github/auth", json={"token": "   "})
    assert res.status_code == 400


def test_download_endpoints_and_pr_safeguards(client):
    # Setup mock job in repair_jobs
    dummy_job_id = "test_verified_job_123"
    repair_jobs[dummy_job_id] = {
        "job_id": dummy_job_id,
        "status": "completed",
        "current_state": RepairState.VERIFIED.value,
        "progress_message": "Repair verified successfully",
        "attempts_used": 1,
        "max_attempts": 3,
        "created_at": 100.0,
        "completed_at": 105.0,
        "report": {
            "repository_path": "octocat/Hello-World",
            "remote_clone_url": "https://github.com/octocat/Hello-World",
            "base_branch": "main",
            "disposable_branch": "repomedic/fix-test",
            "issue_description": "Fix arithmetic defect",
            "terminal_state": RepairState.VERIFIED.value,
            "success": True,
            "attempts_count": 1,
            "max_attempts": 3,
            "baseline_passed": False,
            "baseline_stdout": "FAILED",
            "baseline_stderr": "",
            "verified_patch": "--- a/main.py\n+++ b/main.py\n@@ -1,1 +1,1 @@\n-a\n+b\n",
            "changed_files": ["main.py"],
            "audit_history": [],
            "total_elapsed_seconds": 5.0,
        },
        "_remote_clone_url": "https://github.com/octocat/Hello-World",
        "_base_branch": "main",
    }

    # 1. Download patch
    res_patch = client.get(f"/api/repair/{dummy_job_id}/download/patch")
    assert res_patch.status_code == 200
    assert "--- a/main.py" in res_patch.text

    # 2. Download report
    res_rep = client.get(f"/api/repair/{dummy_job_id}/download/report")
    assert res_rep.status_code == 200
    assert res_rep.json()["success"] is True

    # 3. Publish PR without explicit confirmation must fail with HTTP 400
    res_pr_no_confirm = client.post(
        f"/api/repair/{dummy_job_id}/publish-pr",
        json={"confirm": False},
    )
    assert res_pr_no_confirm.status_code == 400
    assert "confirmation is required" in res_pr_no_confirm.json()["detail"].lower()

    # 4. Publish PR on non-existent job must return 404
    res_pr_not_found = client.post(
        "/api/repair/non_existent_job/publish-pr",
        json={"confirm": True},
    )
    assert res_pr_not_found.status_code == 404


def test_serve_root(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "RepoMedic" in res.text


def test_github_oauth_configure_and_callback(client):
    # Test runtime OAuth configuration
    conf_res = client.post(
        "/api/github/oauth/configure",
        json={"client_id": "test_cid_123", "client_secret": "test_csecret_456"},
    )
    assert conf_res.status_code == 200
    assert conf_res.json()["client_id"] == "test_cid_123"

    # Test OAuth URL generation now works
    url_res = client.get("/api/github/oauth/url")
    assert url_res.status_code == 200
    assert "authorization_url" in url_res.json()
    assert "test_cid_123" in url_res.json()["authorization_url"]

    # Test OAuth browser callback with error param
    err_res = client.get("/api/github/oauth/callback?error=access_denied&error_description=User+cancelled")
    assert err_res.status_code == 400
    assert "User cancelled" in err_res.text

