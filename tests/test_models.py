"""Tests for model provider abstractions and clients."""

import pytest
from repomedic.config import Settings
from repomedic.models import (
    CodeContextItem,
    MockModelClient,
    ModelConnectionError,
    ModelPrompt,
    ModelTimeoutError,
    create_model_client,
    extract_unified_diff,
)


def test_extract_unified_diff_markdown():
    text = (
        "Here is the fix:\n"
        "```diff\n"
        "--- a/calculator.py\n"
        "+++ b/calculator.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-    return a - b\n"
        "+    return a + b\n"
        "```\n"
        "Hope this helps!"
    )
    diff = extract_unified_diff(text)
    assert "--- a/calculator.py" in diff
    assert "+++ b/calculator.py" in diff
    assert "@@ -1,2 +1,2 @@" in diff
    assert "Here is the fix" not in diff


def test_extract_unified_diff_raw():
    text = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    diff = extract_unified_diff(text)
    assert diff.startswith("--- a/app.py")


def test_extract_unified_diff_empty():
    assert extract_unified_diff("") == ""
    assert extract_unified_diff("Just some text without diff") == ""


@pytest.mark.asyncio
async def test_mock_model_client_basic():
    client = MockModelClient()
    prompt = ModelPrompt(
        repository_name="test_repo",
        issue_description="Fix addition bug",
        context_files=[CodeContextItem(rel_path="calc.py", content="return a - b", total_lines=1)],
        baseline_test_output="AssertionError: 2 != 0",
    )
    res = await client.generate_patch(prompt)
    assert "calc.py" in res.extracted_diff
    assert res.provider == "mock"
    assert len(client.call_history) == 1


@pytest.mark.asyncio
async def test_mock_model_client_connection_error():
    client = MockModelClient(simulate_connection_error=True)
    prompt = ModelPrompt(
        repository_name="test_repo",
        issue_description="test",
        baseline_test_output="err",
    )
    with pytest.raises(ModelConnectionError):
        await client.generate_patch(prompt)


@pytest.mark.asyncio
async def test_mock_model_client_timeout():
    client = MockModelClient(simulate_timeout=True)
    prompt = ModelPrompt(
        repository_name="test_repo",
        issue_description="test",
        baseline_test_output="err",
    )
    with pytest.raises(ModelTimeoutError):
        await client.generate_patch(prompt)


def test_create_model_client_factory():
    s_mock = Settings(model_provider="mock")
    c_mock = create_model_client(s_mock)
    assert isinstance(c_mock, MockModelClient)

    s_ollama = Settings(model_provider="ollama")
    c_ollama = create_model_client(s_ollama)
    assert c_ollama.model_name == s_ollama.ollama_model

    s_openai = Settings(model_provider="openai_compatible")
    c_openai = create_model_client(s_openai)
    assert c_openai.model_name == s_openai.openai_model
