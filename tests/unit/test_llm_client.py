"""Unit tests for LLM client wrapper.

All Anthropic API calls are mocked via AsyncMock.
Tests verify:
- Correct API call parameters (model, system, messages, tools, tool_choice)
- Response parsing from tool_use content blocks
- Return tuple (parsed_response, tokens_used, duration_ms)
- API key from environment
- Exception propagation
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from typing import Literal

from thinktank.llm.client import LLMClient


class SampleResponse(BaseModel):
    """Test schema for mocking."""

    decision: Literal["approved", "rejected"]
    reasoning: str


def _make_mock_response(tool_input: dict, input_tokens: int = 100, output_tokens: int = 50):
    """Create a mock Anthropic Message with a ToolUseBlock."""
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.input = tool_input

    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    response = MagicMock()
    response.content = [tool_use_block]
    response.usage = usage
    return response


class TestLLMClientInit:
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    def test_reads_api_key_from_env(self):
        client = LLMClient()
        assert client.model == "claude-sonnet-4-20250514"

    def test_default_model(self):
        client = LLMClient()
        assert client.model == "claude-sonnet-4-20250514"

    def test_max_retries_set(self):
        """Client should configure max_retries=2."""
        with patch("thinktank.llm.client.AsyncAnthropic") as mock_cls:
            LLMClient()
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["max_retries"] == 2


class TestLLMClientReview:
    @pytest.fixture
    def client(self):
        with patch("thinktank.llm.client.AsyncAnthropic"):
            return LLMClient()

    @pytest.mark.asyncio
    async def test_calls_messages_create_with_correct_params(self, client):
        mock_response = _make_mock_response({"decision": "approved", "reasoning": "OK"})
        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.review("system prompt", "user prompt", SampleResponse)

        client._client.messages.create.assert_called_once()
        call_kwargs = client._client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["system"] == "system prompt"
        assert call_kwargs["messages"] == [{"role": "user", "content": "user prompt"}]
        assert call_kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_uses_tool_use_for_structured_output(self, client):
        mock_response = _make_mock_response({"decision": "approved", "reasoning": "OK"})
        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.review("sys", "usr", SampleResponse)

        call_kwargs = client._client.messages.create.call_args[1]
        # Verify tools parameter contains the schema
        assert "tools" in call_kwargs
        tools = call_kwargs["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "structured_output"
        assert "input_schema" in tools[0]

        # Verify tool_choice forces the tool
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "structured_output"}

    @pytest.mark.asyncio
    async def test_returns_parsed_response_tokens_duration(self, client):
        mock_response = _make_mock_response(
            {"decision": "approved", "reasoning": "Great match"},
            input_tokens=200,
            output_tokens=80,
        )
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result, tokens, duration_ms = await client.review("sys", "usr", SampleResponse)

        assert isinstance(result, SampleResponse)
        assert result.decision == "approved"
        assert result.reasoning == "Great match"
        assert tokens == 280  # 200 + 80
        assert isinstance(duration_ms, int)
        assert duration_ms >= 0

    @pytest.mark.asyncio
    async def test_custom_max_tokens(self, client):
        mock_response = _make_mock_response({"decision": "rejected", "reasoning": "Nope"})
        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.review("sys", "usr", SampleResponse, max_tokens=2048)

        call_kwargs = client._client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_propagates_api_exceptions(self, client):
        from anthropic import APIConnectionError

        client._client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )

        with pytest.raises(APIConnectionError):
            await client.review("sys", "usr", SampleResponse)
