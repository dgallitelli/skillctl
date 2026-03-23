"""Unified LLM client wrapping Bedrock and Anthropic backends."""

from __future__ import annotations

import time

from skillctl.errors import SkillctlError
from skillctl.optimize.types import LLMResponse

_RETRY_DELAYS = [1, 4, 16]  # exponential backoff: 1s, 4s, 16s
_MAX_RETRIES = 3


class LLMClient:
    """Unified LLM client that wraps Bedrock or Anthropic."""

    def __init__(self, provider: str = "bedrock", model: str | None = None, region: str = "us-east-1"):
        self.provider = provider
        if provider == "bedrock":
            import boto3

            self.model = model or "us.anthropic.claude-sonnet-4-6"
            self.client = boto3.client("bedrock-runtime", region_name=region)
        elif provider == "anthropic":
            import anthropic

            self.model = model or "claude-sonnet-4-6"
            self.client = anthropic.Anthropic()
        else:
            raise SkillctlError(
                code="E_BAD_PROVIDER",
                what=f"Unknown LLM provider: {provider}",
                why="The optimizer needs an LLM backend for failure analysis and variant generation",
                fix="Use --provider bedrock (default) or --provider anthropic",
            )

    def call(self, system: str, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        """Send a prompt and return structured response with usage stats.

        Retries up to 3 times with exponential backoff (1s, 4s, 16s).
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):  # 0, 1, 2, 3 → first try + 3 retries
            try:
                if self.provider == "bedrock":
                    return self._call_bedrock(system, prompt, max_tokens)
                else:
                    return self._call_anthropic(system, prompt, max_tokens)
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAYS[attempt])
        raise last_exc  # type: ignore[misc]

    def _call_bedrock(self, system: str, prompt: str, max_tokens: int) -> LLMResponse:
        response = self.client.converse(
            modelId=self.model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens},
        )
        content = response["output"]["message"]["content"][0]["text"]
        usage = response["usage"]
        return LLMResponse(
            content=content,
            input_tokens=usage["inputTokens"],
            output_tokens=usage["outputTokens"],
        )

    def _call_anthropic(self, system: str, prompt: str, max_tokens: int) -> LLMResponse:
        response = self.client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return LLMResponse(
            content=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
