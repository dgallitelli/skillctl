"""Claude CLI utilities for evaluation grading.

Thin wrappers around AgentRunner for backward compatibility.
"""

from __future__ import annotations

from typing import Optional

from skillctl.eval.agent_runner import ClaudeRunner

_runner = ClaudeRunner()


def check_claude_available() -> None:
    """Verify that the claude CLI is available."""
    _runner.check_available()


def run_claude_prompt(
    prompt: str,
    skill_path: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    timeout: int = 120,
    output_format: str = "text",
) -> tuple[str, str, int, float]:
    """Invoke ``claude -p`` and return (stdout, stderr, returncode, elapsed_seconds)."""
    return _runner.run_prompt(
        prompt,
        skill_path=skill_path,
        workspace_dir=workspace_dir,
        timeout=timeout,
        output_format=output_format,
    )
