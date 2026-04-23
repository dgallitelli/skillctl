"""Tests for skillctl.optimize.llm_client.

Unit tests mock the AnthropicBedrock client for fast CI.
Integration tests (marked @pytest.mark.integration) call real Bedrock.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from skillctl.optimize.llm_client import LLMClient, DEFAULT_MODEL, _MAX_RETRIES, _RETRY_DELAYS
from skillctl.optimize.types import LLMResponse

# ---------------------------------------------------------------------------
# Fake anthropic module for unit tests (no real SDK needed)
# ---------------------------------------------------------------------------

_fake_anthropic = types.ModuleType("anthropic")
_fake_anthropic.AnthropicBedrock = MagicMock  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _ensure_anthropic_importable(monkeypatch):
    """Ensure 'anthropic' is importable for unit tests that mock it."""
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic)
    yield


# ===================================================================
# Unit tests — mock the Anthropic Bedrock client
# ===================================================================

class TestLLMClientInit:
    """Tests for LLMClient.__init__."""

    def test_default_model(self):
        mock_cls = MagicMock()
        _fake_anthropic.AnthropicBedrock = mock_cls
        client = LLMClient()
        assert client.model == DEFAULT_MODEL
        assert client.model == "us.anthropic.claude-opus-4-6-v1"
        mock_cls.assert_called_once_with(aws_region="us-east-1")

    def test_custom_model_and_region(self):
        mock_cls = MagicMock()
        _fake_anthropic.AnthropicBedrock = mock_cls
        client = LLMClient(model="us.anthropic.claude-sonnet-4-6-v1:0", region="us-west-2")
        assert client.model == "us.anthropic.claude-sonnet-4-6-v1:0"
        mock_cls.assert_called_once_with(aws_region="us-west-2")


class TestLLMClientCall:
    """Tests for LLMClient.call — verifies messages.create is called correctly."""

    def test_call_returns_llm_response(self):
        mock_bedrock = MagicMock()
        _fake_anthropic.AnthropicBedrock = MagicMock(return_value=mock_bedrock)

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Hello from Bedrock")]
        mock_msg.usage.input_tokens = 10
        mock_msg.usage.output_tokens = 20
        mock_bedrock.messages.create.return_value = mock_msg

        client = LLMClient()
        resp = client.call(system="You are helpful.", prompt="Say hello")

        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello from Bedrock"
        assert resp.input_tokens == 10
        assert resp.output_tokens == 20
        mock_bedrock.messages.create.assert_called_once_with(
            model=DEFAULT_MODEL,
            system="You are helpful.",
            messages=[{"role": "user", "content": "Say hello"}],
            max_tokens=4096,
        )

    def test_call_custom_max_tokens(self):
        mock_bedrock = MagicMock()
        _fake_anthropic.AnthropicBedrock = MagicMock(return_value=mock_bedrock)

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="ok")]
        mock_msg.usage.input_tokens = 5
        mock_msg.usage.output_tokens = 5
        mock_bedrock.messages.create.return_value = mock_msg

        client = LLMClient()
        client.call(system="sys", prompt="p", max_tokens=2048)

        mock_bedrock.messages.create.assert_called_once_with(
            model=DEFAULT_MODEL,
            system="sys",
            messages=[{"role": "user", "content": "p"}],
            max_tokens=2048,
        )


class TestLLMClientRetry:
    """Tests for retry logic with exponential backoff."""

    @patch("skillctl.optimize.llm_client.time.sleep")
    def test_retries_on_failure_then_succeeds(self, mock_sleep):
        mock_bedrock = MagicMock()
        _fake_anthropic.AnthropicBedrock = MagicMock(return_value=mock_bedrock)

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="ok")]
        mock_msg.usage.input_tokens = 5
        mock_msg.usage.output_tokens = 5

        mock_bedrock.messages.create.side_effect = [
            RuntimeError("transient error"),
            mock_msg,
        ]

        client = LLMClient()
        resp = client.call(system="sys", prompt="p")

        assert resp.content == "ok"
        mock_sleep.assert_called_once_with(1)

    @patch("skillctl.optimize.llm_client.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep):
        mock_bedrock = MagicMock()
        _fake_anthropic.AnthropicBedrock = MagicMock(return_value=mock_bedrock)
        mock_bedrock.messages.create.side_effect = RuntimeError("persistent error")

        client = LLMClient()
        with pytest.raises(RuntimeError, match="persistent error"):
            client.call(system="sys", prompt="p")

        assert mock_sleep.call_count == _MAX_RETRIES
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(4)
        mock_sleep.assert_any_call(16)

    @patch("skillctl.optimize.llm_client.time.sleep")
    def test_retry_backoff_delays_are_correct(self, mock_sleep):
        mock_bedrock = MagicMock()
        _fake_anthropic.AnthropicBedrock = MagicMock(return_value=mock_bedrock)

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="finally")]
        mock_msg.usage.input_tokens = 1
        mock_msg.usage.output_tokens = 1

        mock_bedrock.messages.create.side_effect = [
            RuntimeError("err1"),
            RuntimeError("err2"),
            RuntimeError("err3"),
            mock_msg,
        ]

        client = LLMClient()
        resp = client.call(system="sys", prompt="p")

        assert resp.content == "finally"
        assert mock_sleep.call_args_list == [
            ((1,),),
            ((4,),),
            ((16,),),
        ]


# ===================================================================
# Integration tests — call real Amazon Bedrock
# ===================================================================
# Run with: pytest tests/test_llm_client.py -m integration
# Requires valid AWS credentials with Bedrock access.

@pytest.mark.integration
class TestLLMClientBedrockIntegration:
    """Integration tests that make real calls to Amazon Bedrock."""

    def _make_real_client(self):
        """Create a real LLMClient (bypass the fake anthropic module)."""
        import importlib
        import skillctl.optimize.llm_client as mod
        if "anthropic" in sys.modules and sys.modules["anthropic"] is _fake_anthropic:
            del sys.modules["anthropic"]
        importlib.reload(mod)
        return mod.LLMClient()

    def test_bedrock_simple_call(self):
        client = self._make_real_client()
        resp = client.call(
            system="You are a test assistant. Reply with exactly one word.",
            prompt="Say 'hello'.",
            max_tokens=16,
        )
        assert isinstance(resp, LLMResponse)
        assert len(resp.content) > 0
        assert resp.input_tokens > 0
        assert resp.output_tokens > 0

    def test_bedrock_json_response(self):
        client = self._make_real_client()
        resp = client.call(
            system="You are a JSON generator. Return only valid JSON, no markdown.",
            prompt='Return a JSON object with key "status" and value "ok".',
            max_tokens=64,
        )
        import json
        data = json.loads(resp.content)
        assert data["status"] == "ok"

    def test_bedrock_model_is_opus(self):
        client = self._make_real_client()
        assert "opus" in client.model.lower()
