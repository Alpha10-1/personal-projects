"""Nothing about your work leaves this machine unless you say so.

The point of these tests is that the guarantee is structural. It is not that
each route remembers to check -- it is that there is no way to reach the
model without passing the check, so a feature written next year inherits it
without knowing it exists.
"""

import pytest

from app import ai, privacy

# Captured at import, before the suite's autouse network guard replaces them.
# These tests have to run the real functions: the whole claim is that the
# real gate refuses, and a test double refusing proves only that the double
# was installed. Safe to restore, because the gate raises before anything is
# sent -- if it ever stopped doing so, these tests would reach the guard
# again and fail loudly rather than quietly calling out.
REAL_CLIENT = ai._client
REAL_RESEARCH = ai.research


@pytest.fixture
def real_ai(monkeypatch):
    monkeypatch.setattr(ai, "_client", REAL_CLIENT)
    monkeypatch.setattr(ai, "research", REAL_RESEARCH)


@pytest.fixture
def keyed(monkeypatch):
    """A key is present. Whether anything can be sent is then purely a
    question of the switch, which is what these tests are about."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    return True


# --- The default -------------------------------------------------------------


def test_nothing_may_be_sent_unless_it_was_switched_on(monkeypatch, keyed):
    """A tracker that starts sending project summaries because a variable was
    missing is worse than one that refuses to."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    assert privacy.egress_allowed() is False
    assert ai.is_configured() is False


@pytest.mark.parametrize("value", ["on", "ON", "1", "true", "yes", " on "])
def test_it_can_be_switched_on_deliberately(monkeypatch, keyed, value):
    monkeypatch.setenv(privacy.ENV_VAR, value)

    assert privacy.egress_allowed() is True
    assert ai.is_configured() is True


@pytest.mark.parametrize("value", ["off", "false", "no", "0", "", "onn", "maybe"])
def test_anything_unrecognised_means_off(monkeypatch, keyed, value):
    """Fails closed: a typo in the variable must not be read as consent, and
    `bool("false")` is True."""
    monkeypatch.setenv(privacy.ENV_VAR, value)

    assert privacy.egress_allowed() is False


# --- Enforcement -------------------------------------------------------------


def test_the_client_itself_refuses_so_no_route_can_slip_past(monkeypatch, keyed, real_ai):
    """The gate is at the one function that builds the API client. A route
    that forgets to ask permission still cannot send anything."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    with pytest.raises(ai.AINotConfigured, match="Nothing about your projects"):
        ai._client()


@pytest.mark.anyio
async def test_a_structured_call_cannot_be_made(monkeypatch, keyed, real_ai):
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    with pytest.raises(ai.AINotConfigured):
        await ai.structured(system="s", prompt="p", schema={}, tool_name="t")


@pytest.mark.anyio
async def test_a_streamed_call_cannot_be_made(monkeypatch, keyed, real_ai):
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    with pytest.raises(ai.AINotConfigured):
        [chunk async for chunk in ai.stream(system="s", messages=[])]


@pytest.mark.anyio
async def test_web_research_cannot_be_made(monkeypatch, keyed, real_ai):
    """The one call that reaches past the model API to a search engine."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    with pytest.raises(ai.AINotConfigured):
        await ai.research(system="s", prompt="p")


# --- What the app says about it ----------------------------------------------


def test_status_says_why_rather_than_just_no(client, keyed, monkeypatch):
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    body = client.get("/ai/status").json()

    assert body["configured"] is False
    assert body["egress"] == "off"
    assert "Nothing about your projects leaves this machine" in body["reason"]
    assert privacy.ENV_VAR in body["reason"]


def test_the_switch_being_off_outranks_a_missing_key(client, monkeypatch):
    """Both are true; the stronger statement is the useful one."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    assert "leaves this machine" in client.get("/ai/status").json()["reason"]


def test_a_missing_key_is_still_reported_when_sending_is_allowed(client, monkeypatch):
    """The regression that made this worth testing: the switch's own state
    dict carried a `reason` and overwrote this one, leaving a 503 that said
    only "Service Unavailable"."""
    monkeypatch.setenv(privacy.ENV_VAR, "on")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    body = client.get("/ai/status").json()

    assert body["reason"] is not None
    assert "ANTHROPIC_API_KEY" in body["reason"]


def test_status_never_leaks_the_key(client, keyed, monkeypatch):
    import json

    monkeypatch.setenv(privacy.ENV_VAR, "on")

    assert "sk-test-not-a-real-key" not in json.dumps(client.get("/ai/status").json())


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/ai/chat", {"messages": [{"role": "user", "content": "hi"}]}),
        ("/ai/suggest/project", {"draft": {"name": "x" * 40}}),
        ("/ai/scaffold", {"idea": "something worth building"}),
    ],
)
def test_every_model_route_refuses_and_says_why(client, keyed, monkeypatch, path, payload):
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    response = client.post(path, json=payload)

    assert response.status_code == 503
    assert "leaves this machine" in response.json()["detail"]


# --- What still works --------------------------------------------------------


def test_the_tracker_itself_is_untouched(client, make, monkeypatch):
    """None of this ever needed the network. Switching the model off must not
    take the actual tracker with it."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)
    project = make.project(name="Local only")
    make.task(title="A task", project_id=project.id)

    assert client.get("/projects").status_code == 200
    assert client.get("/tasks").status_code == 200
    assert client.get("/review").status_code == 200
    assert client.get("/dashboard/today").status_code == 200


def test_the_computed_timeline_still_works_with_sending_off(client, db, make):
    """It is arithmetic over commits already on disk, and was always free."""
    import json as _json
    from datetime import datetime

    project = make.project(name="OMS", repo="me/oms")
    make.event(
        repo="me/oms",
        external_id="c1",
        kind="commit",
        title="a commit",
        occurred_at=datetime(2026, 7, 1, 12, 0),
        project_id=project.id,
        raw=_json.dumps({"sha": "c1"}),
    )

    body = client.get(f"/ai/projects/{project.id}/timeline").json()

    assert body["commits"] == 1


def test_the_spend_ledger_is_readable_with_sending_off(client, monkeypatch):
    """Reading the bill is most wanted exactly when the assistant is off."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    assert client.get("/ai/spend").status_code == 200
