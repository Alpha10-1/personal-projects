"""Whether anything about your work may leave this machine.

The tracker is local: SQLite on your own disk, a localhost API, a browser on
the same machine. Two things reach outside it -- GitHub, which is where the
commits already are, and the Anthropic API, which is the only path that
carries *your* material rather than fetching someone else's.

This module is the switch on that second path, and it is off by default.

**Enforced, not agreed.** The check lives at the one function that builds the
API client, so a route added next year cannot forget it -- there is no code
path to the model that does not pass through here. A rule kept in a prompt,
a comment or a habit is a rule that lasts until the next feature.

**Off is the default.** A tracker that silently starts sending project
summaries because an environment variable was missing is worse than one that
refuses to. When it is off, `ai.is_configured()` is False, every AI route
answers 503 with the reason, and the buttons are not rendered -- the same
path the app already takes when there is no key at all.

What is *not* affected: everything computed locally. The commit timeline, the
review rules, the findings, the spend ledger, the whole tracker. Those never
touched the network and still do not.
"""

import os

ENV_VAR = "PP_AI_EGRESS"

# Anything not in here means off. Deliberately not `bool(value)`: a typo in
# an environment variable should fail closed, and "false" is truthy.
_ALLOWED = {"on", "1", "true", "yes", "allow"}

REASON_OFF = (
    "Sending anything to the model is switched off, so the assistant is "
    "unavailable. Nothing about your projects leaves this machine. Set "
    f"{ENV_VAR}=on in backend/.env to allow it."
)


def egress_allowed() -> bool:
    """Whether this machine may send your project data to the model API."""
    return (os.getenv(ENV_VAR) or "").strip().lower() in _ALLOWED


def blocked_reason() -> str:
    return REASON_OFF


def state() -> dict:
    """The switch, for the UI and for `/ai/status`.

    Deliberately carries no `reason` of its own. It did, and because the
    caller spreads this into the status dict after computing one, a missing
    key came back with the reason silently overwritten by None -- a 503 that
    said "Service Unavailable" and nothing else. One field, one source.
    """
    return {
        "egress": "on" if egress_allowed() else "off",
        "egress_env": ENV_VAR,
    }
