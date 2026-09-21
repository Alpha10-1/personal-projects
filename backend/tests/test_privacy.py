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


# --- The master switch -------------------------------------------------------


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no", " off "])
def test_one_setting_turns_the_whole_assistant_off(monkeypatch, keyed, value):
    monkeypatch.setenv(privacy.ENV_VAR, value)

    assert privacy.egress_allowed() is False
    assert ai.is_configured() is False


def test_with_a_key_and_no_setting_the_assistant_runs(monkeypatch, keyed):
    """Repos, notes and tasks may go; the per-source rule below is what
    protects the material that may not."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    assert privacy.egress_allowed() is True
    assert ai.is_configured() is True


# --- Enforcement -------------------------------------------------------------


def test_the_client_itself_refuses_so_no_route_can_slip_past(monkeypatch, keyed, real_ai):
    """The gate is at the one function that builds the API client. A route
    that forgets to ask permission still cannot send anything."""
    monkeypatch.setenv(privacy.ENV_VAR, "off")

    with pytest.raises(ai.AINotConfigured, match="Nothing about your projects"):
        ai._client()


@pytest.mark.anyio
async def test_a_structured_call_cannot_be_made(monkeypatch, keyed, real_ai):
    monkeypatch.setenv(privacy.ENV_VAR, "off")

    with pytest.raises(ai.AINotConfigured):
        await ai.structured(system="s", prompt="p", schema={}, tool_name="t")


@pytest.mark.anyio
async def test_a_streamed_call_cannot_be_made(monkeypatch, keyed, real_ai):
    monkeypatch.setenv(privacy.ENV_VAR, "off")

    with pytest.raises(ai.AINotConfigured):
        [chunk async for chunk in ai.stream(system="s", messages=[])]


@pytest.mark.anyio
async def test_web_research_cannot_be_made(monkeypatch, keyed, real_ai):
    """The one call that reaches past the model API to a search engine."""
    monkeypatch.setenv(privacy.ENV_VAR, "off")

    with pytest.raises(ai.AINotConfigured):
        await ai.research(system="s", prompt="p")


# --- What the app says about it ----------------------------------------------


def test_status_says_why_rather_than_just_no(client, keyed, monkeypatch):
    monkeypatch.setenv(privacy.ENV_VAR, "off")

    body = client.get("/ai/status").json()

    assert body["configured"] is False
    assert body["egress"] == "off"
    assert "Nothing about your projects leaves this machine" in body["reason"]
    assert privacy.ENV_VAR in body["reason"]


def test_the_switch_being_off_outranks_a_missing_key(client, monkeypatch):
    """Both are true; the stronger statement is the useful one."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv(privacy.ENV_VAR, "off")

    assert "leaves this machine" in client.get("/ai/status").json()["reason"]


def test_a_missing_key_is_still_reported_when_sending_is_allowed(client, monkeypatch):
    """The regression that made this worth testing: the switch's own state
    dict carried a `reason` and overwrote this one, leaving a 503 that said
    only "Service Unavailable"."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    body = client.get("/ai/status").json()

    assert body["reason"] is not None
    assert "ANTHROPIC_API_KEY" in body["reason"]


def test_status_never_leaks_the_key(client, keyed, monkeypatch):
    import json

    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

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
    monkeypatch.setenv(privacy.ENV_VAR, "off")

    response = client.post(path, json=payload)

    assert response.status_code == 503
    assert "leaves this machine" in response.json()["detail"]


# --- What still works --------------------------------------------------------


def test_the_tracker_itself_is_untouched(client, make, monkeypatch):
    """None of this ever needed the network. Switching the model off must not
    take the actual tracker with it."""
    monkeypatch.setenv(privacy.ENV_VAR, "off")
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
    monkeypatch.setenv(privacy.ENV_VAR, "off")

    assert client.get("/ai/spend").status_code == 200


# --- What may never be sent, switch or no switch -----------------------------
#
# Power BI reports sit over datasets that may carry row-level security: what
# a person may see depends on who they are. Copying the contents somewhere
# that permission model does not reach defeats the whole mechanism, so this
# is not a preference and there is no flag for it.

GUID = "b1e7c0de-1234-4a5b-9c8d-0f1e2a3b4c5d"


@pytest.fixture
def dashboard(db, make):
    from app import models

    project = make.project(name="Reporting")
    row = models.Dashboard(
        name="Delivery",
        project_id=project.id,
        url="https://app.powerbi.com/groups/me/reports/" + GUID,
        source="powerbi",
        external_id=GUID,
        workspace_id="9f8e7d6c-1111-2222-3333-444455556666",
        dataset_id="0a1b2c3d-aaaa-bbbb-cccc-ddddeeeeffff",
        workspace_name="Finance",
        dataset_name="Sales",
    )
    db.add(row)
    db.commit()
    return row


def test_identifiers_are_collected_but_names_are_not(db, dashboard):
    """A dashboard called "Sales" must not blocklist the word sales -- a
    check that cries wolf gets switched off, and then it protects nothing."""
    terms = privacy.sensitive_terms(db)

    assert dashboard.external_id in terms
    assert dashboard.workspace_id in terms
    assert dashboard.dataset_id in terms
    assert dashboard.url in terms
    assert "Sales" not in terms
    assert "Finance" not in terms
    assert "Delivery" not in terms


def test_ordinary_prose_about_a_project_is_not_blocked(db, dashboard):
    """The common case. Repos, notes and tasks may go; this must not become
    a check that makes the assistant unusable."""
    terms = privacy.sensitive_terms(db)

    privacy.check_outgoing(
        "Summarise the delivery work and the sales pipeline notes.", terms=terms
    )


def test_a_dataset_id_is_refused(db, dashboard):
    terms = privacy.sensitive_terms(db)

    with pytest.raises(privacy.SensitiveDataBlocked, match="Power BI identifier"):
        privacy.check_outgoing(
            f"The dataset {dashboard.dataset_id} shows 4 rows.", terms=terms
        )


def test_a_report_url_is_refused(db, dashboard):
    terms = privacy.sensitive_terms(db)

    with pytest.raises(privacy.SensitiveDataBlocked):
        privacy.check_outgoing(f"See {dashboard.url} for the numbers.", terms=terms)


def test_the_refusal_never_repeats_the_thing_it_is_protecting(db, dashboard):
    """The message reaches the UI and the logs. Printing a workspace id in
    order to announce that it must not be shared would be its own small joke."""
    terms = privacy.sensitive_terms(db)

    with pytest.raises(privacy.SensitiveDataBlocked) as caught:
        privacy.check_outgoing(dashboard.workspace_id, terms=terms)

    assert dashboard.workspace_id not in str(caught.value)
    assert GUID not in str(caught.value)


def test_it_matches_however_the_id_is_cased(db, dashboard):
    terms = privacy.sensitive_terms(db)

    with pytest.raises(privacy.SensitiveDataBlocked):
        privacy.check_outgoing(dashboard.dataset_id.upper(), terms=terms)


def test_short_values_are_not_treated_as_identifiers(db, make):
    """A two-character id would match half of everything."""
    from app import models

    db.add(models.Dashboard(name="Tiny", source="manual", external_id="ab"))
    db.commit()

    assert "ab" not in privacy.sensitive_terms(db)


@pytest.mark.anyio
async def test_a_model_call_carrying_one_is_stopped_before_it_is_sent(
    db, dashboard, keyed, monkeypatch, real_ai
):
    """The tripwire is on the outgoing text, so it holds even if some future
    context builder starts including dashboards without anyone noticing."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    with pytest.raises(privacy.SensitiveDataBlocked):
        await ai.structured(
            system="You are helpful.",
            prompt=f"Report {dashboard.external_id} refreshed today.",
            schema={},
            tool_name="t",
        )


@pytest.mark.anyio
async def test_the_chat_stream_is_checked_too(db, dashboard, keyed, monkeypatch, real_ai):
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    with pytest.raises(privacy.SensitiveDataBlocked):
        [
            chunk
            async for chunk in ai.stream(
                system="s",
                messages=[{"role": "user", "content": f"what is {dashboard.dataset_id}"}],
            )
        ]


def test_the_api_answers_403_rather_than_500(client, db, dashboard, keyed, monkeypatch):
    """A refusal is not a fault. It should not read like one, and it should
    not invite a retry."""
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)

    response = client.post(
        "/ai/chat",
        json={"messages": [{"role": "user", "content": f"tell me about {GUID}"}]},
    )

    assert response.status_code == 403
    assert "stay on this machine" in response.json()["detail"]


def test_no_context_builder_includes_a_dashboard(db, dashboard, make):
    """The real protection: nothing assembling a prompt looks at the
    dashboards table at all. The tripwire exists for the day that changes."""
    from app import assistant

    text = assistant.tracker_context(db)

    assert dashboard.external_id not in text
    assert dashboard.url not in text
    assert dashboard.dataset_id not in text


def test_the_status_says_what_is_never_sent(client, keyed):
    assert "Power BI datasets, workspaces and reports" in str(
        client.get("/ai/status").json()["never_sent"]
    )
