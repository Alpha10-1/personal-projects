"""What may leave this machine, and what may never.

The tracker is local: SQLite on your own disk, a localhost API, a browser on
the same machine. Two things reach outside it -- GitHub, which is where the
commits already are, and the model API, which is the only path carrying
*your* material rather than fetching someone else's.

There are two separate controls here, and they answer different questions.

**The master switch** (`PP_AI_EGRESS=off`) turns the whole assistant off. It
exists so there is one thing to set on a machine where nothing may go out at
all, and it is checked at the single function that builds the API client, so
a route written next year inherits it without knowing it exists.

**The sensitive-source rule** is not a switch, because it is not a
preference. Power BI reports are backed by datasets that may carry
row-level security: what a person is allowed to see depends on who they are,
and the whole point of that is defeated the moment the contents are copied
somewhere the permission model does not reach. No flag enables it. Nothing
identifying a dataset, workspace or report may appear in anything sent.

That second rule is enforced as a tripwire on the outgoing text rather than
as a promise about how prompts are built. Today no context builder includes
a dashboard, which is the real protection; the tripwire is there for the day
somebody adds one without thinking, because "we were careful" is not a
property you can test and this is.

**The tripwire matches identifiers, never names.** A report called "Sales"
would otherwise block every prompt containing the word sales, the check
would be turned off within a week, and the guarantee would be worth nothing.
Ids and URLs are unambiguous, so those are what it looks for.
"""

import os
from collections.abc import Iterable
from typing import Optional

ENV_VAR = "PP_AI_EGRESS"

# Anything not in here means off. Deliberately not `bool(value)`: a typo
# should fail closed, and "false" is truthy.
_OFF = {"off", "0", "false", "no", "deny", "block"}

REASON_OFF = (
    "Sending anything to the model is switched off, so the assistant is "
    "unavailable. Nothing about your projects leaves this machine. Remove "
    f"{ENV_VAR}=off from backend/.env to allow it."
)

# Short strings match too much. A real Power BI id is a GUID; a URL is
# longer still. Anything shorter than this is not distinctive enough to be
# worth the false positives.
MIN_TERM = 12


def egress_allowed() -> bool:
    """Whether the assistant may run at all.

    On unless explicitly switched off. The per-source rule below is what
    protects the material that must never go, and it does not depend on this.
    """
    return (os.getenv(ENV_VAR) or "").strip().lower() not in _OFF


def blocked_reason() -> str:
    return REASON_OFF


def state() -> dict:
    """The switch, for the UI and for `/ai/status`.

    Carries no `reason` of its own. It did, and because the caller spreads
    this into the status dict after computing one, a missing key came back
    with its reason overwritten by None -- a 503 that said only "Service
    Unavailable". One field, one source.
    """
    return {
        "egress": "on" if egress_allowed() else "off",
        "egress_env": ENV_VAR,
        "never_sent": ["Power BI datasets, workspaces and reports"],
    }


# --- What may never be sent --------------------------------------------------


class SensitiveDataBlocked(RuntimeError):
    """Something that must never leave was about to.

    Raised instead of sending. Subclasses nothing on purpose -- callers map
    it explicitly, because a privacy stop is not a model failure and should
    not read like a transient error someone retries.
    """


def sensitive_terms(db=None) -> list[str]:
    """The identifiers that must never appear in an outgoing prompt.

    Ids and URLs only. Names are deliberately excluded: a dashboard called
    "Delivery" would block every sentence containing the word, and a check
    that cries wolf is a check that gets removed.
    """
    from app import models
    from app.db import SessionLocal

    own = db is None
    session = SessionLocal() if own else db
    try:
        from sqlalchemy import select

        rows = session.execute(
            select(
                models.Dashboard.external_id,
                models.Dashboard.workspace_id,
                models.Dashboard.dataset_id,
                models.Dashboard.url,
            )
        ).all()
    except Exception:  # pragma: no cover - a missing table must not block work
        return []
    finally:
        if own:
            session.close()

    terms = {
        value.strip()
        for row in rows
        for value in row
        if value and len(value.strip()) >= MIN_TERM
    }
    return sorted(terms)


def find_sensitive(text: str, terms: Optional[Iterable[str]] = None) -> list[str]:
    """Which forbidden identifiers appear in this text."""
    if not text:
        return []
    haystack = text.lower()
    return [t for t in (terms if terms is not None else sensitive_terms()) if t.lower() in haystack]


def check_outgoing(*parts: Optional[str], terms: Optional[Iterable[str]] = None) -> None:
    """Refuse to send anything carrying a Power BI identifier.

    The message names the kind of thing found and how many, never the value:
    this string reaches the UI and logs, and repeating a workspace id into a
    log to announce that it must not be shared would be its own small joke.
    """
    text = "\n".join(p for p in parts if p)
    found = find_sensitive(text, terms)
    if not found:
        return
    raise SensitiveDataBlocked(
        f"Blocked: this would have sent {len(found)} Power BI "
        f"identifier{'' if len(found) == 1 else 's'} to the model. Dataset "
        "contents and the reports over them stay on this machine, because "
        "what they contain depends on who is allowed to see it."
    )


# --- Reading a dataset as the person, not as the application -----------------

DELEGATION_NOTE = (
    "Power BI is connected with a service principal, which is an application "
    "identity: row-level security does not apply to it in the way it applies "
    "to a person, so anything read that way is read with the application's "
    "access rather than yours. Reading as you requires delegated sign-in. "
    "See docs/powerbi-delegated-access.md."
)
