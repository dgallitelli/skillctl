"""Unit tests for skillctl.optimize.llm_client."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from skillctl.errors import SkillctlError
from skillctl.optimize.llm_client import LLMClient, _MAX_RETRIES, _RETRY_DELAYS
from skillctl.optimize.types import LLMResponse

# Create a fake anthropic module so @patch("anthropic.Anthropic") works
# even when the real package isn't installed.
_fake_anthropic = types.ModuleType("anthropic")
_fake_anthropic.Anthropic = MagicMock  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _ensure_anthropic_importable(monkeypatch):
    """Ensure 'anthropic' is importable for tests that mock it."""
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic)
    yield


class TestLLMClientInit:
    """Tests for LLMClient.__init__."""

    @patch("boto3.client")
    def test_bedrock_default_provider(self, mock_boto_client):
        client = LLMClient()
        assert client.provider == "bedrock"
        assert client.model == "us.anthropic.claude-sonnet-4-6"
        mock_boto_client.assert_called_once_with("bedrock-runtime", region_name="us-east-1")

    @patch("boto3.client")
    def test_bedrock_custom_model_and_region(self, mock_boto_client):
        client = LLMClient(provider="bedrock", model="custom-model", region="us-west-2")
        assert client.model == "custom-model"
        mock_boto_client.assert_called_once_with("bedrock-runtime", region_name="us-west-2")

    def test_anthropic_provider(self):
        mock_cls = MagicMock()
        _fake_anthropic.Anthropic = mock_cls  # type: ignore[attr-defined]
        client = LLMClient(provider="anthropic")
        assert client.provider == "anthropic"
        assert client.model == "claude-sonnet-4-6"
        mock_cls.assert_called_once()

    def test_anthropic_custom_model(self):
        _fake_anthropic.Anthropic = MagicMock()  # type: ignore[attr-defined]
        client = LLMClient(provider="anthropic", model="custom-anthropic-model")
        assert client.model == "custom-anthropic-model"

    def test_unsupported_provider_raises_skillctl_error(self):
        with pytest.raises(SkillctlError) as exc_info:
            LLMClient(provider="openai")
        err = exc_info.value
        assert err.code == "E_BAD_PROVIDER"
        assert "openai" in err.what
        assert "bedrock" in err.fix.lower() or "anthropic" in err.fix.lower()


class TestLLMClientCallBedrock:
    """Tests for LLMClient.call with Bedrock backend."""

    @patch("boto3.client")
    def test_call_bedrock_returns_llm_response(self, mock_boto_client):
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock
        mock_bedrock.converse.return_value = {
            "output": {"message": {"content": [{"text": "Hello from Bedrock"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 20},
        }

        client = LLMClient(provider="bedrock")
        resp = client.call(system="You are helpful.", prompt="Say hello")

        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello from Bedrock"
        assert resp.input_tokens == 10
        assert resp.output_tokens == 20
        mock_bedrock.converse.assert_called_once_with(
            modelId="us.anthropic.claude-sonnet-4-6",
            system=[{"text": "You are helpful."}],
            messages=[{"role": "user", "content": [{"text": "Say hello"}]}],
            inferenceConfig={"maxTokens": 4096},
        )


class TestLLMClientCallAnthropic:
    """Tests for LLMClient.call with Anthropic backend."""

    def test_call_anthropic_returns_llm_response(self):
        mock_anthropic = MagicMock()
        _fake_anthropic.Anthropic = MagicMock(return_value=mock_anthropic)  # type: ignore[attr-defined]

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Hello from Anthropic")]
        mock_msg.usage.input_tokens = 15
        mock_msg.usage.output_tokens = 25
        mock_anthropic.messages.create.return_value = mock_msg

        client = LLMClient(provider="anthropic")
        resp = client.call(system="You are helpful.", prompt="Say hello", max_tokens=2048)

        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello from Anthropic"
        assert resp.input_tokens == 15
        assert resp.output_tokens == 25
        mock_anthropic.messages.create.assert_called_once_with(
            model="claude-sonnet-4-6",
            system="You are helpful.",
            messages=[{"role": "user", "content": "Say hello"}],
            max_tokens=2048,
        )


class TestLLMClientRetry:
    """Tests for retry logic with exponential backoff."""

    @patch("skillctl.optimize.llm_client.time.sleep")
    @patch("boto3.client")
    def test_retries_on_failure_then_succeeds(self, mock_boto_client, mock_sleep):
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock
        mock_bedrock.converse.side_effect = [
            RuntimeError("transient error"),
            {
                "output": {"message": {"content": [{"text": "ok"}]}},
                "usage": {"inputTokens": 5, "outputTokens": 5},
            },
        ]

        client = LLMClient(provider="bedrock")
        resp = client.call(system="sys", prompt="p")

        assert resp.content == "ok"
        mock_sleep.assert_called_once_with(1)  # first retry delay

    @patch("skillctl.optimize.llm_client.time.sleep")
    @patch("boto3.client")
    def test_raises_after_max_retries(self, mock_boto_client, mock_sleep):
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock
        mock_bedrock.converse.side_effect = RuntimeError("persistent error")

        client = LLMClient(provider="bedrock")
        with pytest.raises(RuntimeError, match="persistent error"):
            client.call(system="sys", prompt="p")

        # Should have slept 3 times (delays: 1, 4, 16)
        assert mock_sleep.call_count == _MAX_RETRIES
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(4)
        mock_sleep.assert_any_call(16)

    @patch("skillctl.optimize.llm_client.time.sleep")
    @patch("boto3.client")
    def test_retry_backoff_delays_are_correct(self, mock_boto_client, mock_sleep):
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock
        mock_bedrock.converse.side_effect = [
            RuntimeError("err1"),
            RuntimeError("err2"),
            RuntimeError("err3"),
            {
                "output": {"message": {"content": [{"text": "finally"}]}},
                "usage": {"inputTokens": 1, "outputTokens": 1},
            },
        ]

        client = LLMClient(provider="bedrock")
        resp = client.call(system="sys", prompt="p")

        assert resp.content == "finally"
        assert mock_sleep.call_args_list == [
            ((1,),),
            ((4,),),
            ((16,),),
        ]
