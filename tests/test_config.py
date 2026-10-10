"""Tests for repomedic/config.py."""

import os
from repomedic.config import Settings, get_settings, reset_settings


def test_default_settings():
    s = Settings()
    assert s.environment == "development"
    assert s.max_repair_attempts == 3
    assert s.model_timeout_seconds == 45.0
    assert s.test_timeout_seconds == 30.0
    assert s.max_context_bytes == 65_536


def test_env_override(monkeypatch):
    monkeypatch.setenv("REPOMEDIC_MAX_REPAIR_ATTEMPTS", "2")
    monkeypatch.setenv("REPOMEDIC_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("REPOMEDIC_OLLAMA_MODEL", "qwen-custom")
    monkeypatch.setenv("PORT", "9090")

    s = Settings()
    assert s.max_repair_attempts == 2
    assert s.model_provider == "openai_compatible"
    assert s.ollama_model == "qwen-custom"
    assert s.port == 9090


def test_secret_masking():
    s = Settings(openai_api_key="sk-secret-12345")
    d = s.safe_dict()
    assert "openai_api_key" not in d
    assert d["openai_api_key_configured"] is True


def test_settings_singleton():
    reset_settings(None)
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    reset_settings(None)


def test_sandbox_config():
    s = Settings(sandbox_mode="auto", allow_host_execution=True)
    assert s.is_sandbox_ready is True
    assert s.active_sandbox_type in ("docker", "restricted_subprocess")

