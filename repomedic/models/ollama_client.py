"""Ollama client adapter for local open-weight model inference in RepoMedic AI."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional
import httpx

from repomedic.models.base import (
    BaseModelClient,
    ModelConnectionError,
    ModelPrompt,
    ModelResponse,
    ModelResponseFormatError,
    ModelTimeoutError,
    extract_unified_diff,
)


class OllamaClient(BaseModelClient):
    """Client for local Ollama server running models such as qwen2.5-coder:7b."""

    def __init__(
        self,
        model_name: str = "qwen2.5-coder:7b",
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        temperature: float = 0.1,
    ) -> None:
        super().__init__(model_name=model_name, base_url=base_url, timeout_seconds=timeout_seconds)
        self.max_retries = max_retries
        self.temperature = temperature

    async def check_health(self) -> Dict[str, Any]:
        """Check if the local Ollama daemon is active and whether the target model is loaded."""
        url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return {
                        "available": False,
                        "status": f"Ollama HTTP error {resp.status_code}",
                        "model_present": False,
                    }
                data = resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                model_found = any(self.model_name in m for m in models)
                return {
                    "available": True,
                    "status": "Ollama service online",
                    "model_present": model_found,
                    "installed_models": models,
                    "target_model": self.model_name,
                }
        except httpx.ConnectError:
            return {
                "available": False,
                "status": f"Cannot connect to Ollama at {self.base_url}. Ensure 'ollama serve' is running.",
                "model_present": False,
            }
        except Exception as ex:
            return {
                "available": False,
                "status": f"Ollama health check error: {str(ex)}",
                "model_present": False,
            }

    async def generate_patch(self, prompt: ModelPrompt) -> ModelResponse:
        """Query Ollama /api/chat with bounded retries and prompt formatting."""
        system_content = self.build_system_prompt()
        user_content = self.build_user_prompt(prompt)

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "options": {
                "temperature": self.temperature,
            },
        }

        url = f"{self.base_url}/api/chat"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(url, json=payload)
                    latency = time.perf_counter() - t0

                    if resp.status_code != 200:
                        raise ModelResponseFormatError(
                            f"Ollama returned HTTP status {resp.status_code}: {resp.text[:300]}"
                        )

                    data = resp.json()
                    message = data.get("message", {})
                    content = message.get("content", "")

                    if not content or not content.strip():
                        raise ModelResponseFormatError("Ollama returned an empty completion content.")

                    diff = extract_unified_diff(content)
                    return ModelResponse(
                        raw_output=content,
                        extracted_diff=diff,
                        model_name=self.model_name,
                        provider="ollama",
                        latency_seconds=round(latency, 3),
                        usage={
                            "total_duration": data.get("total_duration"),
                            "load_duration": data.get("load_duration"),
                            "eval_count": data.get("eval_count"),
                        },
                    )

            except httpx.ConnectError as exc:
                last_error = ModelConnectionError(
                    f"Connection refused to Ollama at {self.base_url}. Is Ollama running?"
                )
            except httpx.TimeoutException as exc:
                last_error = ModelTimeoutError(
                    f"Inference timed out after {self.timeout_seconds}s on Ollama {self.model_name}."
                )
            except ModelResponseFormatError:
                raise
            except Exception as exc:
                last_error = ModelConnectionError(f"Unexpected error communicating with Ollama: {exc}")

            if attempt < self.max_retries:
                await asyncio.sleep(1.0 * (attempt + 1))

        if last_error:
            raise last_error
        raise ModelConnectionError("Failed to communicate with Ollama after retries.")
