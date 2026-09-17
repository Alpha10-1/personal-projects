"""Calls to the model.

Everything the rest of the app does works without this module. There is no
API key in the repo and none is required to run the tracker: when one isn't
configured, `is_configured()` is False, the /ai routes answer 503 with a
plain reason, and the UI hides the affordances rather than offering buttons
that fail. That is deliberate -- the deterministic review in `review.py` is
the part that must always work, and this is the part that costs money.

Two models, because the jobs are different. Suggestions fire while you type,
so they go to the fast one and are capped hard; chat and repo review are
asked for explicitly and can afford the better model.

**This is the one place in the system that sends your data off the machine.**
Everything else is localhost-only. Each call here is built from an explicit
context object rather than reaching into the database itself, so what leaves
is visible at the call site instead of buried in a prompt.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any, Optional

# Live suggestions are asked for on a pause in typing, so latency is the
# constraint and Haiku is the right trade. Chat and repo review are deliberate
# actions where the answer's quality matters more than a second of waiting.
FAST_MODEL = os.getenv("PP_AI_FAST_MODEL", "claude-haiku-4-5-20251001")
CHAT_MODEL = os.getenv("PP_AI_MODEL", "claude-sonnet-5")

# A draft that has barely been started gives the model nothing to work with,
# and asking anyway spends a call to be told so.
MIN_DRAFT_CHARS = 12

# Cost guards. The suggestion path is the one that can run away -- it fires
# repeatedly while a form is open -- so it gets the tighter cap.
MAX_CONTEXT_CHARS = 6_000
MAX_SUGGEST_TOKENS = 900
MAX_CHAT_TOKENS = 2_000
MAX_REVIEW_TOKENS = 2_500
REQUEST_TIMEOUT = 60.0


class AINotConfigured(RuntimeError):
    """No API key, or the SDK isn't installed."""


class AIFailed(RuntimeError):
    """The call was made and did not come back usable."""


def api_key() -> str:
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(api_key())


def status() -> dict:
    """What the frontend needs to decide whether to offer any of this."""
    configured = is_configured()
    return {
        "configured": configured,
        "fast_model": FAST_MODEL if configured else None,
        "chat_model": CHAT_MODEL if configured else None,
        "reason": None
        if configured
        else "ANTHROPIC_API_KEY is not set, so the assistant is switched off.",
    }


def _client():
    """Built per call rather than held as a module global, so that setting the
    key in the environment takes effect without a restart."""
    if not is_configured():
        raise AINotConfigured(
            "ANTHROPIC_API_KEY is not set. Put it in backend/.env to switch the "
            "assistant on."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise AINotConfigured(
            "The anthropic package isn't installed. `pip install -r "
            "requirements.txt` in backend/."
        ) from exc
    return anthropic.AsyncAnthropic(api_key=api_key(), timeout=REQUEST_TIMEOUT)


def clip(text: Optional[str], limit: int = MAX_CONTEXT_CHARS) -> str:
    """Bound what goes into a prompt.

    Truncation is marked rather than silent: a model given a diff that stops
    mid-hunk should be able to tell that it did, instead of reasoning
    confidently about code it never saw the end of.
    """
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated at {limit} characters]"


def _unwrap(exc: Exception) -> str:
    """Turn an SDK error into something worth showing a person.

    The key and the full request are deliberately not echoed: this string ends
    up in the UI.
    """
    name = type(exc).__name__
    if "Authentication" in name:
        return "The API key was rejected. Check ANTHROPIC_API_KEY."
    if "RateLimit" in name:
        return "Rate limited by the API. Try again in a moment."
    if "NotFound" in name:
        return "That model name isn't available on this key."
    if "APIConnection" in name or isinstance(exc, asyncio.TimeoutError):
        return "Couldn't reach the API. Check the connection."
    return f"The model call failed ({name})."


async def structured(
    *,
    system: str,
    prompt: str,
    schema: dict,
    tool_name: str = "respond",
    model: str = FAST_MODEL,
    max_tokens: int = MAX_SUGGEST_TOKENS,
) -> dict[str, Any]:
    """Ask for JSON and actually get JSON.

    Forcing a tool call rather than asking for JSON in prose is what makes
    this safe to parse: there is no prose to strip, no fenced block, and the
    shape is the schema rather than whatever the model felt like emitting.
    """
    client = _client()
    try:
        message = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "name": tool_name,
                    "description": "Return the suggestions.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
    except Exception as exc:
        raise AIFailed(_unwrap(exc)) from exc

    for block in message.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input or {})
    raise AIFailed("The model returned nothing usable.")


async def stream(
    *,
    system: str,
    messages: list[dict],
    model: str = CHAT_MODEL,
    max_tokens: int = MAX_CHAT_TOKENS,
) -> AsyncIterator[str]:
    """Yield the answer as it is written.

    Chat is streamed because a paragraph that arrives all at once after five
    seconds reads as a hang, and the floater is meant to feel like a
    conversation.
    """
    client = _client()
    try:
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as streamed:
            async for chunk in streamed.text_stream:
                yield chunk
    except Exception as exc:
        raise AIFailed(_unwrap(exc)) from exc


def sse(event: str, data: Any) -> str:
    """One Server-Sent Events frame.

    Errors travel as an event of their own rather than as an HTTP status: by
    the time a stream fails the 200 and the headers are long gone, so the only
    way to tell the client is in-band.
    """
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
