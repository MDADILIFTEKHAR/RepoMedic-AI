"""Deterministic mock model client for testing, CI, and simulated scenarios."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from repomedic.models.base import (
    BaseModelClient,
    ModelConnectionError,
    ModelPrompt,
    ModelResponse,
    ModelTimeoutError,
    extract_unified_diff,
)


class MockModelClient(BaseModelClient):
    """Deterministic mock client that yields pre-configured responses or programmatic patches."""

    def __init__(
        self,
        model_name: str = "mock-qwen2.5-coder",
        responses: Optional[List[str]] = None,
        simulate_connection_error: bool = False,
        simulate_timeout: bool = False,
        response_generator: Optional[Callable[[ModelPrompt], str]] = None,
    ) -> None:
        super().__init__(model_name=model_name, base_url="mock://localhost")
        self.responses = list(responses or [])
        self.simulate_connection_error = simulate_connection_error
        self.simulate_timeout = simulate_timeout
        self.response_generator = response_generator
        self.call_history: List[ModelPrompt] = []

    def set_responses(self, responses: List[str]) -> None:
        """Queue responses for successive calls."""
        self.responses = list(responses)

    async def check_health(self) -> Dict[str, Any]:
        """Return simulated health check status."""
        if self.simulate_connection_error:
            return {"available": False, "status": "Simulated mock offline", "model_present": False}
        return {
            "available": True,
            "status": "Mock model ready (deterministic test mode)",
            "model_present": True,
            "target_model": self.model_name,
        }

    async def generate_patch(self, prompt: ModelPrompt) -> ModelResponse:
        """Return next scripted response or generated response."""
        self.call_history.append(prompt)

        if self.simulate_connection_error:
            raise ModelConnectionError("Simulated mock model connection failure")

        if self.simulate_timeout:
            raise ModelTimeoutError("Simulated mock model timeout")

        if self.response_generator:
            content = self.response_generator(prompt)
        elif self.responses:
            # Pop next scripted response; if only 1 remains, keep using it
            if len(self.responses) > 1:
                content = self.responses.pop(0)
            else:
                content = self.responses[0]
        else:
            content = (
                "```diff\n"
                "--- a/calc.py\n"
                "+++ b/calc.py\n"
                "@@ -1,2 +1,2 @@\n"
                "-    return a - b\n"
                "+    return a + b\n"
                "```"
            )

        diff = extract_unified_diff(content)
        return ModelResponse(
            raw_output=content,
            extracted_diff=diff,
            model_name=self.model_name,
            provider="mock",
            latency_seconds=0.01,
            usage={"prompt_tokens": 120, "completion_tokens": 40},
        )
