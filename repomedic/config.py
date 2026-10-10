"""Configuration management for RepoMedic AI.

Loads and validates runtime configuration from environment variables
with safe development defaults and strict protection of secrets.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for RepoMedic AI harness."""

    model_config = SettingsConfigDict(
        env_prefix="REPOMEDIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime Environment
    environment: Literal["development", "production", "test"] = "development"
    debug: bool = False
    hosted_mode: bool = False  # When True, disables arbitrary repos and enforces predefined fixtures only

    # Server configuration
    host: str = "0.0.0.0"
    port: int = Field(default=8000, description="Server port. Can also be overridden by PORT env var")

    # Model Provider Settings
    # Supported: "ollama", "openai_compatible", "mock"
    model_provider: Literal["ollama", "openai_compatible", "mock"] = "ollama"

    # Ollama (Local open-weight inference)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"

    # OpenAI-Compatible API (Cloud/Hosted open-weight models, vLLM, OpenRouter, etc.)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "qwen2.5-coder:7b"
    openai_api_key: Optional[SecretStr] = None

    # Model inference limits
    model_timeout_seconds: float = 45.0
    model_max_retries: int = 2
    model_temperature: float = 0.1

    # GitHub Integration Settings
    github_client_id: Optional[str] = None
    github_client_secret: Optional[SecretStr] = None
    github_redirect_uri: Optional[str] = None
    github_default_token: Optional[SecretStr] = None

    # Orchestrator & Repair Loop Controls
    max_repair_attempts: int = 3
    test_timeout_seconds: float = 30.0

    # Repository Scanner Limits
    max_context_bytes: int = 65_536  # Max 64KB code context sent to model
    max_file_size_bytes: int = 262_144  # Max 256KB per inspected file
    max_files_scanned: int = 200

    # Sandboxing & Execution Strategy
    # Supported strategies: 'auto' (Docker if present, restricted subprocess fallback for Render/demo), 'docker' (strictly require container), 'subprocess' (local restricted execution)
    sandbox_mode: Literal["auto", "docker", "subprocess"] = Field(
        default="auto",
        description="Sandbox strategy: 'auto' (Docker if available, otherwise restricted subprocess), 'docker' (strictly containerized), 'subprocess' (local restricted execution).",
    )
    docker_enabled: bool = True
    docker_image: str = "python:3.11-slim"
    docker_memory_limit: str = "512m"
    docker_cpu_limit: float = 1.0
    docker_pids_limit: int = 64
    allow_host_execution: bool = Field(
        default=True,
        description="Explicit developer override to allow local subprocess execution for repositories when Docker is unavailable.",
    )

    # Storage & Temporary Paths
    workspace_root: Path = Field(default_factory=lambda: Path(os.getcwd()))
    temp_worktree_dir: Optional[Path] = None

    @field_validator("port", mode="before")
    @classmethod
    def parse_port(cls, v: object) -> int:
        """Allow PORT env var (used by Render, Heroku, etc.) to override repomedic_port."""
        port_from_env = os.environ.get("PORT")
        if port_from_env and port_from_env.isdigit():
            return int(port_from_env)
        if isinstance(v, str) and v.isdigit():
            return int(v)
        if isinstance(v, int):
            return v
        return 8000

    @property
    def is_docker_available(self) -> bool:
        """Dynamically detect whether the docker CLI is present."""
        if not self.docker_enabled:
            return False
        import shutil
        return shutil.which("docker") is not None

    @property
    def is_sandbox_ready(self) -> bool:
        """Check whether at least one execution sandbox is ready."""
        if self.sandbox_mode == "docker":
            return self.is_docker_available
        if self.sandbox_mode in ("auto", "subprocess"):
            return True
        return self.is_docker_available or self.allow_host_execution

    @property
    def active_sandbox_type(self) -> str:
        """Get the identifier of the active execution sandbox."""
        if self.is_docker_available and self.sandbox_mode in ("docker", "auto"):
            return "docker"
        return "restricted_subprocess"

    def safe_dict(self) -> dict:
        """Produce a serialized config dict with masked secrets for telemetry and logs."""
        d = self.model_dump(exclude={"openai_api_key", "github_client_secret", "github_default_token"})
        d["openai_api_key_configured"] = bool(self.openai_api_key and self.openai_api_key.get_secret_value())
        d["github_oauth_configured"] = bool(self.github_client_id and self.github_client_secret)
        d["github_token_configured"] = bool(self.github_default_token and self.github_default_token.get_secret_value())
        return d


# Global settings singleton accessor
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Retrieve or initialize the cached application settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(new_settings: Optional[Settings] = None) -> None:
    """Reset the settings singleton (useful in automated tests)."""
    global _settings
    _settings = new_settings
