"""Abstract model provider interface and common data structures for RepoMedic AI."""

from __future__ import annotations

import abc
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ModelError(Exception):
    """Base exception for all model provider failures."""


class ModelConnectionError(ModelError):
    """Raised when model provider endpoint is unreachable or connection refused."""


class ModelTimeoutError(ModelError):
    """Raised when model inference exceeds configured execution timeout."""


class ModelResponseFormatError(ModelError):
    """Raised when model outputs an empty or unparseable response."""


class ModelMetadata(BaseModel):
    """Metadata regarding the model and provider."""

    provider: str
    model_name: str
    endpoint_url: str
    is_open_weight: bool = True
    context_window_tokens: Optional[int] = None


class CodeContextItem(BaseModel):
    """A relevant source file or snippet provided as context to the model."""

    rel_path: str
    content: str
    start_line: int = 1
    total_lines: int = 1


class ModelPrompt(BaseModel):
    """Structured repair prompt sent to the coding model."""

    repository_name: str
    issue_description: str
    context_files: List[CodeContextItem] = Field(default_factory=list)
    baseline_test_output: str
    prior_failure_feedback: Optional[str] = None
    attempt_number: int = 1
    max_attempts: int = 3


class ModelResponse(BaseModel):
    """Normalized response from a model provider."""

    raw_output: str
    extracted_diff: str
    model_name: str
    provider: str
    latency_seconds: float
    usage: Dict[str, Any] = Field(default_factory=dict)


def extract_unified_diff(text: str) -> str:
    """Safely extract unified diff block from markdown fences or raw output.

    Handles ```diff, ```patch, generic ``` fences, or raw unified diff streams.
    Strips conversational text before and after the diff.
    """
    if not text:
        return ""

    # 1. Search for fenced code blocks with diff or patch tags
    code_block_match = re.search(r"```(?:diff|patch)?\s*\n([\s\S]*?)```", text, re.IGNORECASE)
    if code_block_match:
        candidate = code_block_match.group(1).strip()
        if "---" in candidate and "+++" in candidate and "@@" in candidate:
            return candidate

    # 2. Check if text contains raw unified diff headers
    diff_start = text.find("--- ")
    if diff_start != -1:
        # Search backwards or from diff_start to find potential diff end
        diff_candidate = text[diff_start:].strip()
        # If there's an ending code block fence, trim at that
        fence_end = diff_candidate.find("```")
        if fence_end != -1:
            diff_candidate = diff_candidate[:fence_end].strip()
        if "+++" in diff_candidate and "@@" in diff_candidate:
            return diff_candidate

    # Return raw text if it contains basic diff markers
    if "--- " in text and "+++ " in text:
        return text.strip()

    return ""


class BaseModelClient(abc.ABC):
    """Abstract interface implemented by all model providers."""

    def __init__(self, model_name: str, base_url: str, timeout_seconds: float = 45.0) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @abc.abstractmethod
    async def generate_patch(self, prompt: ModelPrompt) -> ModelResponse:
        """Send prompt to the model and return structured response with extracted diff."""
        raise NotImplementedError

    @abc.abstractmethod
    async def check_health(self) -> Dict[str, Any]:
        """Perform a quick health and connectivity check on the model endpoint."""
        raise NotImplementedError

    def build_system_prompt(self) -> str:
        """Construct the standardized instruction prompt enforcing unified diff output."""
        return (
            "You are RepoMedic AI, an expert open-weight Python bug repair engine.\n"
            "Your objective is to propose a minimal, safe, and precise unified-diff patch that fixes the bug.\n\n"
            "MANDATORY INSTRUCTIONS:\n"
            "1. Output ONLY a valid unified diff (starting with '--- a/path/to/file.py' and '+++ b/path/to/file.py').\n"
            "2. Wrap your diff in a standard ```diff code fence.\n"
            "3. Do NOT modify test files, configuration files, CI workflows, or dependency manifests.\n"
            "4. Do NOT include conversational explanations or apologies before or after the diff.\n"
            "5. Keep modifications minimal: only change the lines necessary to resolve the bug.\n"
            "6. Ensure the patch adheres strictly to Python syntax and PEP 8 guidelines."
        )

    def build_user_prompt(self, prompt: ModelPrompt) -> str:
        """Format the user prompt with context files, test failure evidence, and feedback."""
        lines = [
            f"# BUG REPAIR REQUEST (Attempt {prompt.attempt_number} of {prompt.max_attempts})",
            f"Repository: {prompt.repository_name}",
            "",
            "## ISSUE DESCRIPTION",
            prompt.issue_description.strip(),
            "",
            "## RELEVANT CODE CONTEXT",
        ]

        if not prompt.context_files:
            lines.append("No specific source files provided.")
        else:
            for item in prompt.context_files:
                lines.append(f"### File: {item.rel_path} (lines {item.start_line}..{item.start_line + item.total_lines - 1})")
                lines.append("```python")
                lines.append(item.content.strip())
                lines.append("```")
                lines.append("")

        lines.extend([
            "## BASELINE TEST FAILURE LOGS",
            "```text",
            prompt.baseline_test_output.strip() or "No baseline test output available.",
            "```",
        ])

        if prompt.prior_failure_feedback:
            lines.extend([
                "",
                "## PREVIOUS ATTEMPT FAILURE EVIDENCE",
                "Your previous patch did not pass verification. Examine the failure logs below and correct your approach:",
                "```text",
                prompt.prior_failure_feedback.strip(),
                "```",
            ])

        lines.extend([
            "",
            "## TASK",
            "Provide the minimal unified diff that resolves this issue while preserving all passing test contracts.",
        ])

        return "\n".join(lines)
