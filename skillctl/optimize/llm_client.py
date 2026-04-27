"""LLM client for skillctl — provider-agnostic via LiteLLM.

Supports any model backend: Amazon Bedrock, OpenAI, Anthropic, Google,
Ollama, Azure, and 100+ others. See https://docs.litellm.ai/docs/providers
for the full list and model name prefixes.

Default: bedrock/us.anthropic.claude-opus-4-6-v1 (Claude Opus on Bedrock).
"""

from __future__ import annotations

import time

from skillctl.optimize.types import LLMResponse

DEFAULT_MODEL = "bedrock/us.anthropic.claude-opus-4-6-v1"

_RETRY_DELAYS = [1, 4, 16]
_MAX_RETRIES = 3


class LLMClient:
    """Provider-agnostic LLM client via LiteLLM."""

    def __init__(self, model: str | None = None):
        self.model = model or DEFAULT_MODEL

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
        import litellm  # type: ignore[import-not-found]

        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
        )
        choice = response.choices[0]  # type: ignore[union-attr]
        usage = response.usage  # type: ignore[union-attr]
        return LLMResponse(
            content=choice.message.content or "",
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
        )
