"""OpenAI-compatible client adapter for hosted open-weight models in RepoMedic AI."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional
import httpx
from pydantic import SecretStr

from repomedic.models.base import (
    BaseModelClient,
    ModelConnectionError,
    ModelPrompt,
    ModelResponse,
    ModelResponseFormatError,
    ModelTimeoutError,
    extract_unified_diff,
)


class OpenAICompatibleClient(BaseModelClient):
    """Client for any OpenAI-compatible API serving open-weight models (e.g. vLLM, OpenRouter)."""

    def __init__(
        self,
        model_name: str = "qwen2.5-coder:7b",
        base_url: str = "https://api.openai.com/v1",
        api_key: Optional[SecretStr] = None,
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        temperature: float = 0.1,
    ) -> None:
        super().__init__(model_name=model_name, base_url=base_url, timeout_seconds=timeout_seconds)
        self.api_key = api_key
        self.max_retries = max_retries
        self.temperature = temperature

    def _get_headers(self) -> Dict[str, str]:
        """Construct request headers. Never logs the key."""
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {self.api_key.get_secret_value()}"
        return headers

    async def check_health(self) -> Dict[str, Any]:
        """Check endpoint connectivity without exposing keys in logs."""
        url = f"{self.base_url}/models"
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m.get("id", "") for m in data.get("data", [])]
                    model_found = any(self.model_name in m for m in models) if models else True
                    return {
                        "available": True,
                        "status": "Hosted endpoint reachable",
                        "model_present": model_found,
                        "target_model": self.model_name,
                        "endpoint": self.base_url,
                    }
                elif resp.status_code in (401, 403):
                    return {
                        "available": False,
                        "status": "Authentication required or invalid API key",
                        "model_present": False,
                        "target_model": self.model_name,
                        "endpoint": self.base_url,
                    }
                else:
                    return {
                        "available": False,
                        "status": f"Endpoint responded with HTTP {resp.status_code}",
                        "model_present": False,
                        "target_model": self.model_name,
                        "endpoint": self.base_url,
                    }
        except httpx.ConnectError:
            return {
                "available": False,
                "status": f"Cannot connect to hosted endpoint at {self.base_url}",
                "model_present": False,
                "target_model": self.model_name,
                "endpoint": self.base_url,
            }
        except Exception as ex:
            return {
                "available": False,
                "status": f"Health check failed: {type(ex).__name__}",
                "model_present": False,
                "target_model": self.model_name,
                "endpoint": self.base_url,
            }

    async def generate_patch(self, prompt: ModelPrompt) -> ModelResponse:
        """Query OpenAI-compatible /chat/completions endpoint with retry logic."""
        system_content = self.build_system_prompt()
        user_content = self.build_user_prompt(prompt)

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": 2048,
        }

        url = f"{self.base_url}/chat/completions"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(url, json=payload, headers=self._get_headers())
                    latency = time.perf_counter() - t0

                    if resp.status_code != 200:
                        clean_error_preview = resp.text[:250].replace("\n", " ")
                        raise ModelResponseFormatError(
                            f"Hosted endpoint HTTP {resp.status_code}: {clean_error_preview}"
                        )

                    data = resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise ModelResponseFormatError("Hosted endpoint returned zero choices in response.")

                    choice = choices[0]
                    message = choice.get("message", {})
                    content = message.get("content", "")

                    if not content or not content.strip():
                        raise ModelResponseFormatError("Model returned empty text content.")

                    diff = extract_unified_diff(content)
                    return ModelResponse(
                        raw_output=content,
                        extracted_diff=diff,
                        model_name=self.model_name,
                        provider="openai_compatible",
                        latency_seconds=round(latency, 3),
                        usage=data.get("usage", {}),
                    )

            except httpx.ConnectError:
                last_error = ModelConnectionError(f"Connection failed to {self.base_url}")
            except httpx.TimeoutException:
                last_error = ModelTimeoutError(
                    f"Inference timed out after {self.timeout_seconds}s at {self.base_url}"
                )
            except ModelResponseFormatError:
                raise
            except Exception as exc:
                last_error = ModelConnectionError(f"Error communicating with hosted model: {exc}")

            if attempt < self.max_retries:
                await asyncio.sleep(1.0 * (attempt + 1))

        if last_error:
            raise last_error
        raise ModelConnectionError("Failed to reach hosted provider after retries.")
