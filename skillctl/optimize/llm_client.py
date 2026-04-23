"""LLM client for skillctl — uses Amazon Bedrock via the Anthropic SDK."""

from __future__ import annotations

import time

from skillctl.errors import SkillctlError
from skillctl.optimize.types import LLMResponse

DEFAULT_MODEL = "us.anthropic.claude-opus-4-6-v1"

_RETRY_DELAYS = [1, 4, 16]  # exponential backoff: 1s, 4s, 16s
_MAX_RETRIES = 3


class LLMClient:
    """LLM client that calls Claude via Amazon Bedrock (AnthropicBedrock SDK)."""

    def __init__(self, model: str | None = None, region: str = "us-east-1"):
        import anthropic

        self.model = model or DEFAULT_MODEL
        self.client = anthropic.AnthropicBedrock(aws_region=region)

    def call(self, system: str, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        """Send a prompt and return structured response with usage stats.

        Retries up to 3 times with exponential backoff (1s, 4s, 16s).
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._call(system, prompt, max_tokens)
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAYS[attempt])
        raise last_exc  # type: ignore[misc]

    def _call(self, system: str, prompt: str, max_tokens: int) -> LLMResponse:
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
