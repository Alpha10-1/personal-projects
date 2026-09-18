"""The assistant.

No test here reaches the real API: conftest refuses the client outright, so
anything that forgets to patch fails loudly rather than spending money. What
is actually under test is the layer around the model -- when it is called at
all, what it is allowed to send, and what happens when it fails.
"""

import json

import pytest

from app import ai, assistant, github


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    # The review and the history both read the README now. A test that wants
    # to see one says so; this default keeps the guard meaningful by making
    # "no README" the explicit, ordinary case rather than a network call.
    monkeypatch.setattr(github, "fetch_readme", lambda repo, max_chars=8000: "")
    return True


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return True


def fake_structured(result, spy=None):
    async def _call(**kwargs):
        if spy is not None:
            spy.append(kwargs)
        return result

    return _call


# --- Switched off by default -------------------------------------------------


def test_status_says_it_is_off_without_a_key(client, unconfigured):
    body = client.get("/ai/status").json()

    assert body["configured"] is False
    assert "ANTHROPIC_API_KEY" in body["reason"]


def test_status_never_returns_the_key(client, configured):
    body = client.get("/ai/status").json()

    assert body["configured"] is True
    assert "sk-test-not-a-real-key" not in json.dumps(body)


@pytest.mark.parametrize(
    "path", ["/ai/suggest/project", "/ai/suggest/task", "/ai/chat"]
)
def test_the_routes_are_unavailable_without_a_key(client, unconfigured, path):
    response = client.post(path, json={"draft": {"name": "x" * 40}, "messages": []})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


# --- Not spending a call on nothing -----------------------------------------


def test_a_bare_draft_is_not_sent_to_the_model(client, configured):
    """The guard is the point: without it every keystroke in an empty form
    would bill."""
    response = client.post("/ai/suggest/project", json={"draft": {"name": "Rep"}})

    assert response.status_code == 200
    assert response.json() == {"fields": {}, "skipped": "draft too short"}


def test_draft_text_ignores_the_fields_that_are_always_set():
    assert assistant.draft_text({"status": "planning", "priority": "high"}) == ""
    assert "forecast" in assistant.draft_text({"name": "Shift forecast"})


# --- Suggestions -------------------------------------------------------------


def test_suggestions_come_back_as_fields(client, configured, monkeypatch):
    monkeypatch.setattr(
        assistant.ai,
        "structured",
        fake_structured({"summary": "A model of shift output.", "priority": "high"}),
    )

    body = client.post(
        "/ai/suggest/project",
        json={"draft": {"name": "Shift-scheduling forecast model"}},
    ).json()

    assert body["fields"]["summary"] == "A model of shift output."
    assert body["fields"]["priority"] == "high"


def test_empty_suggestions_are_dropped(client, configured, monkeypatch):
    """A model declining a field sends "" or []; the UI should never be
    offered a blank to apply."""
    monkeypatch.setattr(
        assistant.ai,
        "structured",
        fake_structured(
            {"summary": "Real.", "objective": "", "tasks": [], "priority": None}
        ),
    )

    fields = client.post(
        "/ai/suggest/project", json={"draft": {"name": "Shift forecast model"}}
    ).json()["fields"]

    assert fields == {"summary": "Real."}


def test_a_task_draft_carries_its_project_as_context(client, configured, make, monkeypatch):
    project = make.project(name="Ore tracking", objective="Know where the ore is.")
    spy = []
    monkeypatch.setattr(assistant.ai, "structured", fake_structured({"notes": "n"}, spy))

    client.post(
        "/ai/suggest/task",
        json={"draft": {"title": "Pull the shift data"}, "project_id": project.id},
    )

    prompt = spy[0]["prompt"]
    assert "Ore tracking" in prompt
    assert "Know where the ore is." in prompt


def test_a_model_failure_is_a_502_not_a_500(client, configured, monkeypatch):
    async def boom(**_kwargs):
        raise ai.AIFailed("Rate limited by the API. Try again in a moment.")

    monkeypatch.setattr(assistant.ai, "structured", boom)

    response = client.post(
        "/ai/suggest/project", json={"draft": {"name": "Shift forecast model"}}
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Rate limited by the API. Try again in a moment."


# --- Chat --------------------------------------------------------------------


def stream_of(*chunks):
    async def _stream(**_kwargs):
        for chunk in chunks:
            yield chunk

    return _stream


def sse_events(text):
    """Parse an SSE body into (event, data) pairs."""
    out = []
    for frame in text.strip().split("\n\n"):
        lines = dict(
            line.split(": ", 1) for line in frame.splitlines() if ": " in line
        )
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_chat_streams_the_answer(client, configured, monkeypatch):
    monkeypatch.setattr(ai, "stream", stream_of("Two tasks ", "are overdue."))

    response = client.post(
        "/ai/chat", json={"messages": [{"role": "user", "content": "what's late?"}]}
    )

    assert response.status_code == 200
    events = sse_events(response.text)
    assert [d for e, d in events if e == "delta"] == ["Two tasks ", "are overdue."]
    assert events[-1][0] == "done"


def test_a_mid_stream_failure_arrives_as_an_event(client, configured, monkeypatch):
    """Once the 200 is out there is no status code left to use, so the error
    has to travel in-band or the UI just sees a truncated answer."""

    async def failing(**_kwargs):
        yield "Looking at "
        raise ai.AIFailed("Couldn't reach the API. Check the connection.")

    monkeypatch.setattr(ai, "stream", failing)

    response = client.post(
        "/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )

    assert response.status_code == 200
    events = sse_events(response.text)
    assert ("error", "Couldn't reach the API. Check the connection.") in events
    assert events[-1][0] == "done"


def test_chat_is_given_the_tracker_not_the_browser_s_version(
    client, configured, make, monkeypatch
):
    """Context is built server-side, so a caller can't widen what the model
    sees by stuffing the request."""
    make.project(name="Ore tracking")
    spy = {}

    async def capture(*, system, messages, **_kw):
        spy["system"] = system
        spy["messages"] = messages
        yield "ok"

    monkeypatch.setattr(ai, "stream", capture)

    client.post(
        "/ai/chat",
        json={"messages": [{"role": "user", "content": "what am I working on?"}]},
    )

    assert "Ore tracking" in spy["system"]
    assert spy["messages"] == [{"role": "user", "content": "what am I working on?"}]


def test_an_empty_conversation_is_rejected(client, configured):
    assert client.post("/ai/chat", json={"messages": []}).status_code == 400
    assert (
        client.post(
            "/ai/chat", json={"messages": [{"role": "user", "content": "   "}]}
        ).status_code
        == 400
    )


# --- Repo review -------------------------------------------------------------


def test_repo_review_needs_a_project(client, configured):
    assert client.post("/ai/projects/999/repo-review").status_code == 404


def test_repo_review_needs_a_linked_repo(client, configured, make):
    project = make.project(name="Unlinked")

    response = client.post(f"/ai/projects/{project.id}/repo-review")

    assert response.status_code == 400
    assert "isn't linked to a repo" in response.json()["detail"]


def test_repo_review_needs_activity_first(client, configured, make):
    project = make.project(name="Linked", repo="owner/name")

    response = client.post(f"/ai/projects/{project.id}/repo-review")

    assert response.status_code == 400
    assert "Sync it first" in response.json()["detail"]


def test_repo_review_reads_the_stored_activity(client, configured, db, make, monkeypatch):
    from app import github, models

    project = make.project(name="Tracker", repo="owner/name")
    db.add(
        models.ActivityEvent(
            provider="github",
            external_id="c1",
            kind="commit",
            repo="owner/name",
            title="Fix the off-by-one in the loader",
            occurred_at=models.utcnow(),
            project_id=project.id,
            raw=json.dumps({"sha": "abc123"}),
        )
    )
    db.commit()

    monkeypatch.setattr(github, "fetch_diffs", lambda repo, shas, **kw: "--- a.py\n+bug")
    spy = []

    async def fake_review(**kwargs):
        spy.append(kwargs)
        return {"summary": "Loader fixes.", "risks": []}

    monkeypatch.setattr(assistant, "review_repo", fake_review)

    body = client.post(f"/ai/projects/{project.id}/repo-review").json()

    assert body["summary"] == "Loader fixes."
    assert body["commits_reviewed"] == 1
    assert body["diffs_included"] is True
    assert "off-by-one" in spy[0]["events"][0].title


def test_a_review_still_runs_when_the_diffs_cannot_be_fetched(
    client, configured, db, make, monkeypatch
):
    """A rate limit on the diff endpoint shouldn't cost you the summary the
    commit messages alone can support."""
    from app import github, models

    project = make.project(name="Tracker", repo="owner/name")
    db.add(
        models.ActivityEvent(
            provider="github",
            external_id="c1",
            kind="commit",
            repo="owner/name",
            title="Fix the loader",
            occurred_at=models.utcnow(),
            raw=json.dumps({"sha": "abc123"}),
        )
    )
    db.commit()

    def unavailable(repo, shas, **kw):
        raise RuntimeError("GitHub 403: rate limited")

    monkeypatch.setattr(github, "fetch_diffs", unavailable)

    async def fake_review(**kwargs):
        assert kwargs["diffs"] == ""
        return {"summary": "From messages only."}

    monkeypatch.setattr(assistant, "review_repo", fake_review)

    body = client.post(f"/ai/projects/{project.id}/repo-review").json()

    assert body["diffs_included"] is False
    assert body["summary"] == "From messages only."


# --- Bounding what leaves the machine ---------------------------------------


def test_clip_marks_where_it_cut():
    clipped = ai.clip("x" * 100, limit=10)

    assert clipped.startswith("x" * 10)
    assert "truncated at 10 characters" in clipped


def test_clip_leaves_short_text_alone():
    assert ai.clip("short", limit=100) == "short"
    assert ai.clip(None) == ""


def test_the_tracker_context_is_bounded(db, make):
    for i in range(60):
        make.project(name=f"Project {i}", summary="s" * 400)

    context = assistant.tracker_context(db)

    assert len(context) <= ai.MAX_CONTEXT_CHARS + 60
