"""Model provider clients and factory for RepoMedic AI."""

from __future__ import annotations

from repomedic.config import Settings, get_settings
from repomedic.models.base import (
    BaseModelClient,
    CodeContextItem,
    ModelConnectionError,
    ModelError,
    ModelMetadata,
    ModelPrompt,
    ModelResponse,
    ModelResponseFormatError,
    ModelTimeoutError,
    extract_unified_diff,
)
from repomedic.models.mock_client import MockModelClient
from repomedic.models.ollama_client import OllamaClient
from repomedic.models.openai_compatible import OpenAICompatibleClient


def create_model_client(settings: Settings | None = None) -> BaseModelClient:
    """Factory creating the appropriate model client instance based on configuration."""
    cfg = settings or get_settings()

    if cfg.model_provider == "ollama":
        return OllamaClient(
            model_name=cfg.ollama_model,
            base_url=cfg.ollama_base_url,
            timeout_seconds=cfg.model_timeout_seconds,
            max_retries=cfg.model_max_retries,
            temperature=cfg.model_temperature,
        )
    elif cfg.model_provider == "openai_compatible":
        return OpenAICompatibleClient(
            model_name=cfg.openai_model,
            base_url=cfg.openai_base_url,
            api_key=cfg.openai_api_key,
            timeout_seconds=cfg.model_timeout_seconds,
            max_retries=cfg.model_max_retries,
            temperature=cfg.model_temperature,
        )
    elif cfg.model_provider == "mock":
        return MockModelClient()
    else:
        raise ValueError(f"Unsupported model provider: {cfg.model_provider}")


__all__ = [
    "BaseModelClient",
    "CodeContextItem",
    "ModelConnectionError",
    "ModelError",
    "ModelMetadata",
    "ModelPrompt",
    "ModelResponse",
    "ModelResponseFormatError",
    "ModelTimeoutError",
    "MockModelClient",
    "OllamaClient",
    "OpenAICompatibleClient",
    "create_model_client",
    "extract_unified_diff",
]
