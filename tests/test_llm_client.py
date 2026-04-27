"""Tests for skillctl.optimize.llm_client.

Unit tests mock litellm.completion for fast CI.
Integration tests (marked @pytest.mark.integration) call real providers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from skillctl.optimize.llm_client import LLMClient, DEFAULT_MODEL, _MAX_RETRIES
from skillctl.optimize.types import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_litellm_response(content="Hello", input_tokens=10, output_tokens=20):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.prompt_tokens = input_tokens
    resp.usage.completion_tokens = output_tokens
    return resp


# ===================================================================
# Unit tests
# ===================================================================


class TestLLMClientInit:
    def test_default_model(self):
        client = LLMClient()
        assert client.model == DEFAULT_MODEL
        assert "bedrock/" in client.model

    def test_custom_model(self):
        client = LLMClient(model="openai/gpt-4o")
        assert client.model == "openai/gpt-4o"


class TestLLMClientCall:
    @patch("litellm.completion")
    def test_call_returns_llm_response(self, mock_completion):
        mock_completion.return_value = _mock_litellm_response(
            "Hello from LiteLLM",
            10,
            20,
        )

        client = LLMClient()
        resp = client.call(system="You are helpful.", prompt="Say hello")

        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello from LiteLLM"
        assert resp.input_tokens == 10
        assert resp.output_tokens == 20
        mock_completion.assert_called_once_with(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Say hello"},
            ],
            max_tokens=4096,
        )

    @patch("litellm.completion")
    def test_call_custom_max_tokens(self, mock_completion):
        mock_completion.return_value = _mock_litellm_response()

        client = LLMClient()
        client.call(system="sys", prompt="p", max_tokens=2048)

        call_kwargs = mock_completion.call_args
        assert call_kwargs.kwargs["max_tokens"] == 2048


class TestLLMClientRetry:
    @patch("skillctl.optimize.llm_client.time.sleep")
    @patch("litellm.completion")
    def test_retries_on_failure_then_succeeds(self, mock_completion, mock_sleep):
        mock_completion.side_effect = [
            RuntimeError("transient error"),
            _mock_litellm_response("ok"),
        ]

        client = LLMClient()
        resp = client.call(system="sys", prompt="p")

        assert resp.content == "ok"
        mock_sleep.assert_called_once_with(1)

    @patch("skillctl.optimize.llm_client.time.sleep")
    @patch("litellm.completion")
    def test_raises_after_max_retries(self, mock_completion, mock_sleep):
        mock_completion.side_effect = RuntimeError("persistent error")

        client = LLMClient()
        with pytest.raises(RuntimeError, match="persistent error"):
            client.call(system="sys", prompt="p")

        assert mock_sleep.call_count == _MAX_RETRIES

    @patch("skillctl.optimize.llm_client.time.sleep")
    @patch("litellm.completion")
    def test_retry_backoff_delays(self, mock_completion, mock_sleep):
        mock_completion.side_effect = [
            RuntimeError("err1"),
            RuntimeError("err2"),
            RuntimeError("err3"),
            _mock_litellm_response("finally"),
        ]

        client = LLMClient()
        resp = client.call(system="sys", prompt="p")

        assert resp.content == "finally"
        assert mock_sleep.call_args_list == [((1,),), ((4,),), ((16,),)]


# ===================================================================
# Integration tests — call real providers
# ===================================================================


@pytest.mark.integration
class TestLLMClientIntegration:
    def test_bedrock_simple_call(self):
        client = LLMClient()
        resp = client.call(
            system="Reply with exactly one word.",
            prompt="Say 'hello'.",
            max_tokens=16,
        )
        assert len(resp.content.strip()) > 0
        assert resp.input_tokens > 0
        assert resp.output_tokens > 0

    def test_bedrock_json_response(self):
        import json

        client = LLMClient()
        resp = client.call(
            system="Return only valid JSON. No markdown fences.",
            prompt='Return {"status": "ok", "count": 42}',
            max_tokens=64,
        )
        data = json.loads(resp.content)
        assert data["status"] == "ok"

    def test_default_model_is_bedrock_opus(self):
        client = LLMClient()
        assert client.model.startswith("bedrock/")
        assert "opus" in client.model
