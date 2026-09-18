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
import time
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
# A plan carries several whole options, each with milestones and tasks, so it
# needs more room than a review of the same project would.
MAX_PLAN_TOKENS = 8_000
MAX_RESEARCH_TOKENS = 3_000
REQUEST_TIMEOUT = 60.0

# Searching is the expensive part: each result set comes back as input tokens,
# so one search costs roughly what a whole ordinary call does. Capped, and
# never on unless asked for.
MAX_SEARCHES = 5
RESEARCH_TIMEOUT = 180.0


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


def _client(timeout: float = REQUEST_TIMEOUT):
    """Built per call rather than held as a module global, so that setting the
    key in the environment takes effect without a restart.

    The timeout is per call because the jobs differ by an order of magnitude:
    a suggestion that takes a minute is broken, while a research call that
    runs five searches legitimately takes several.
    """
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
    return anthropic.AsyncAnthropic(api_key=api_key(), timeout=timeout)


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


def _meter(
    feature: str,
    model: str,
    usage,
    project_id: Optional[int],
    started: float,
    *,
    ok: bool = True,
    error: Optional[str] = None,
) -> None:
    """Record one call in the ledger.

    Imported here rather than at module level: `spend` opens the database,
    and this module is otherwise free of it -- the import stays inside the
    one function that needs it so the dependency is visible at the point of
    use, and so importing `ai` never pulls in the database by itself.
    """
    try:
        from app import spend

        spend.record(
            feature=feature,
            model=model,
            usage=usage,
            project_id=project_id,
            ok=ok,
            error=error,
            seconds=time.monotonic() - started,
        )
    except Exception:  # pragma: no cover - accounting never breaks a feature
        pass


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
    timeout: float = REQUEST_TIMEOUT,
    feature: Optional[str] = None,
    project_id: Optional[int] = None,
) -> dict[str, Any]:
    """Ask for JSON and actually get JSON.

    Forcing a tool call rather than asking for JSON in prose is what makes
    this safe to parse: there is no prose to strip, no fenced block, and the
    shape is the schema rather than whatever the model felt like emitting.

    The ledger entry is labelled with `tool_name` unless the caller says
    otherwise, because every caller already names its tool after the job it
    is doing. Deriving it means a feature added later is attributed correctly
    without anyone having to remember a second argument.
    """
    feature = feature or tool_name
    client = _client(timeout)
    started = time.monotonic()
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
        reason = _unwrap(exc)
        _meter(feature, model, None, project_id, started, ok=False, error=reason)
        raise AIFailed(reason) from exc

    _meter(feature, model, message.usage, project_id, started)

    for block in message.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input or {})
    raise AIFailed("The model returned nothing usable.")


async def research(
    *,
    system: str,
    prompt: str,
    max_searches: int = MAX_SEARCHES,
    model: str = CHAT_MODEL,
    max_tokens: int = MAX_RESEARCH_TOKENS,
    timeout: float = RESEARCH_TIMEOUT,
    feature: str = "research",
    project_id: Optional[int] = None,
) -> dict[str, Any]:
    """Let the model look things up, and record what it read.

    This is the only call that reaches beyond the Anthropic API: the search
    runs on their side, but the queries are derived from your project, so a
    repository name or a problem description can end up in a search engine.
    It is never on by default anywhere -- the caller has to ask for it.

    Comes back as prose plus the sources behind it. The citations are the
    point: an improvement suggested because a real page says so can be
    checked, and one the model remembered cannot.
    """
    client = _client(timeout)
    started = time.monotonic()
    try:
        message = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": max(1, int(max_searches)),
                }
            ],
        )
    except Exception as exc:
        reason = _unwrap(exc)
        _meter(feature, model, None, project_id, started, ok=False, error=reason)
        raise AIFailed(reason) from exc

    _meter(feature, model, getattr(message, "usage", None), project_id, started)

    parts: list[str] = []
    sources: dict[str, str] = {}
    for block in message.content:
        if getattr(block, "type", None) != "text":
            continue
        parts.append(block.text)
        for citation in getattr(block, "citations", None) or []:
            url = getattr(citation, "url", None)
            if url and url not in sources:
                sources[url] = getattr(citation, "title", None) or url

    usage = getattr(message, "usage", None)
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0)

    return {
        "text": "".join(parts).strip(),
        "sources": [{"url": url, "title": title} for url, title in sources.items()],
        "searches": searches or 0,
    }


async def stream(
    *,
    system: str,
    messages: list[dict],
    model: str = CHAT_MODEL,
    max_tokens: int = MAX_CHAT_TOKENS,
    feature: str = "chat",
    project_id: Optional[int] = None,
) -> AsyncIterator[str]:
    """Yield the answer as it is written.

    Chat is streamed because a paragraph that arrives all at once after five
    seconds reads as a hang, and the floater is meant to feel like a
    conversation.
    """
    client = _client()
    started = time.monotonic()
    try:
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as streamed:
            async for chunk in streamed.text_stream:
                yield chunk
            # Only available once the stream is drained, which is why this
            # sits inside the context manager rather than after it.
            final = await streamed.get_final_message()
    except Exception as exc:
        reason = _unwrap(exc)
        _meter(feature, model, None, project_id, started, ok=False, error=reason)
        raise AIFailed(reason) from exc

    _meter(feature, model, getattr(final, "usage", None), project_id, started)


def sse(event: str, data: Any) -> str:
    """One Server-Sent Events frame.

    Errors travel as an event of their own rather than as an HTTP status: by
    the time a stream fails the 200 and the headers are long gone, so the only
    way to tell the client is in-band.
    """
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
