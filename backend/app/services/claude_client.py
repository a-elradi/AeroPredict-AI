"""
AeroPredict — Claude (Anthropic) client
Thin wrapper that asks Claude for a JSON answer matching a schema.
The API key is read from the ANTHROPIC_API_KEY environment variable.
"""
import json
import logging
import os

import anthropic

log = logging.getLogger(__name__)

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

_client: anthropic.Anthropic | None = None


class ClaudeUnavailable(Exception):
    """Claude could not produce an answer (not configured, busy, or declined)."""


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not is_configured():
            raise ClaudeUnavailable("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic(timeout=90.0, max_retries=2)
    return _client


def generate_json(system: str, prompt: str, schema: dict,
                  max_tokens: int = 8000, effort: str = "medium") -> dict:
    """Ask Claude for a JSON object that matches `schema`."""
    client = _get_client()
    try:
        response = client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            # If the model declines, the API retries on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as e:
        raise ClaudeUnavailable("Claude is busy (rate limited)") from e
    except anthropic.APIStatusError as e:
        log.error("Claude API error %s: %s", e.status_code, e.message)
        raise ClaudeUnavailable(f"Claude API error ({e.status_code})") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeUnavailable("Could not reach Claude") from e

    if response.stop_reason == "refusal":
        raise ClaudeUnavailable("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise ClaudeUnavailable("Claude's answer was cut off")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ClaudeUnavailable("Claude returned no text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeUnavailable("Claude returned invalid JSON") from e
