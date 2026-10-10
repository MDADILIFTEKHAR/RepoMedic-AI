"""Tests for FastAPI endpoints and request validation."""

import pytest
from fastapi.testclient import TestClient
from repomedic.api import app
from repomedic.config import Settings, reset_settings


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
    # Missing both fixture_id and local_repo_path
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


def test_serve_root(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "RepoMedic" in res.text
