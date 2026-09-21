"""Reading a project out of its commit history.

The timeline is arithmetic and is tested as such. What the model makes of it
is not tested here -- what *is* tested is that it only ever receives facts,
and that it is told when those facts are incomplete.
"""

import json
from datetime import datetime

import pytest

from app import github, history, models


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    # Egress is off by default, so a test that exercises the assistant has to
    # say it means to -- the same deliberate step the user takes.
    monkeypatch.setenv("PP_AI_EGRESS", "on")
    # The review and the history both read the README now. A test that wants
    # to see one says so; this default keeps the guard meaningful by making
    # "no README" the explicit, ordinary case rather than a network call.
    monkeypatch.setattr(github, "fetch_readme", lambda repo, max_chars=8000: "")


def at(day, month=6, year=2026):
    return datetime(year, month, day, 12, 0)


def stats(files, additions=10, deletions=2):
    return json.dumps(
        {
            "files": [
                {"path": p, "status": "modified", "additions": 5, "deletions": 1}
                for p in files
            ],
            "additions": additions,
            "deletions": deletions,
        }
    )


@pytest.fixture
def repo_project(db, make):
    project = make.project(name="OMS", repo="me/oms", workspace="personal")

    def commit(sha, when, title, files=None, actor="me"):
        return make.event(
            repo="me/oms",
            external_id=sha,
            kind="commit",
            title=title,
            occurred_at=when,
            project_id=project.id,
            actor=actor,
            raw=json.dumps({"sha": sha}),
            file_stats=stats(files) if files else None,
        )

    return project, commit


# --- The arithmetic ----------------------------------------------------------


def test_an_area_is_the_meaningful_part_of_a_path():
    assert history.area_of("backend/app/routes/tasks.py") == "backend/app"
    assert history.area_of("src/components/ui.js") == "src/components"
    assert history.area_of("README.md") == "(root)"
    assert history.area_of("backend/main.py") == "backend"


def test_build_artefacts_are_not_where_the_work_went(db, repo_project):
    """Counting __pycache__ as an area would make every Python project look
    like its compiler output was the point."""
    project, commit = repo_project
    commit("a", at(1), "work", ["backend/app/core/search.py", "backend/app/__pycache__/x.pyc"])

    data = history.timeline(db, project)

    areas = [a["area"] for a in data["areas"]]
    assert "backend/app" in areas
    assert not any("__pycache__" in a for a in areas)


def test_the_timeline_groups_by_month_and_tracks_churn(db, repo_project):
    project, commit = repo_project
    commit("a", at(3, month=4), "first", ["src/a.py"])
    commit("b", at(9, month=4), "second", ["src/a.py"])
    commit("c", at(2, month=6), "later", ["src/b.py"])

    data = history.timeline(db, project)

    months = {p["month"]: p for p in data["periods"]}
    assert list(months) == ["2026-04", "2026-06"]
    assert months["2026-04"]["commits"] == 2
    assert months["2026-04"]["additions"] == 20
    assert months["2026-06"]["commits"] == 1


def test_an_area_records_when_it_appeared_and_was_last_touched(db, repo_project):
    """The whole point of "how a project changed throughout": an area touched
    once in April and never again is a different story from one worked on all
    year."""
    project, commit = repo_project
    commit("a", at(1, month=4), "early", ["portal/auth.py"])
    commit("b", at(1, month=8), "late", ["portal/auth.py"])
    commit("c", at(1, month=5), "once", ["legacy/old.py"])

    data = history.timeline(db, project)

    by_area = {a["area"]: a for a in data["areas"]}
    assert by_area["portal"]["first"].month == 4
    assert by_area["portal"]["last"].month == 8
    assert by_area["legacy"]["first"] == by_area["legacy"]["last"]


def test_a_commit_counts_once_per_area_it_touches(db, repo_project):
    project, commit = repo_project
    commit("a", at(1), "spans two", ["backend/app/x.py", "backend/app/y.py", "src/z.js"])

    data = history.timeline(db, project)

    by_area = {a["area"]: a["commits"] for a in data["areas"]}
    assert by_area["backend/app"] == 1  # two files, one commit
    assert by_area["src"] == 1


def test_the_most_changed_files_come_out_on_top(db, repo_project):
    project, commit = repo_project
    for i in range(3):
        commit(f"c{i}", at(i + 1), "touching the hot file", ["core/engine.py"])
    commit("other", at(9), "once", ["docs/readme.md"])

    data = history.timeline(db, project)

    assert data["files"][0]["path"] == "core/engine.py"
    assert data["files"][0]["commits"] == 3


def test_contributors_are_counted(db, repo_project):
    project, commit = repo_project
    commit("a", at(1), "x", ["a.py"], actor="alice")
    commit("b", at(2), "y", ["a.py"], actor="bob")
    commit("c", at(3), "z", ["a.py"], actor="alice")

    data = history.timeline(db, project)

    assert data["contributors"][0] == {"actor": "alice", "commits": 2}


def test_a_history_with_no_commits_says_so(db, make):
    project = make.project(name="Empty", repo="me/empty")

    data = history.timeline(db, project)

    assert data["commits"] == 0
    assert "No commits recorded" in history.as_text(data, project)


# --- Saying how much is actually known ---------------------------------------


def test_commits_without_detail_still_count_but_are_declared(db, repo_project):
    """A summary built from 2 commits of 5 is a different claim from one built
    on all of them, and the model has to be told which it has."""
    project, commit = repo_project
    commit("a", at(1), "with detail", ["src/a.py"])
    commit("b", at(2), "with detail", ["src/a.py"])
    for i in range(3):
        commit(f"plain{i}", at(i + 3), "message only")

    data = history.timeline(db, project)
    text = history.as_text(data, project)

    assert data["commits"] == 5
    assert data["detailed"] == 2
    assert "detail is available for 2 of them" in text
    assert "the other 3" in text


def test_a_complete_history_does_not_apologise(db, repo_project):
    project, commit = repo_project
    commit("a", at(1), "x", ["src/a.py"])

    text = history.as_text(history.timeline(db, project), project)

    assert "detail is available for" not in text


def test_the_text_carries_the_facts_the_model_narrates(db, repo_project):
    project, commit = repo_project
    commit("a", at(1, month=4), "add the portal", ["portal/auth.py"])

    text = history.as_text(history.timeline(db, project), project)

    assert "me/oms" in text
    assert "add the portal" in text
    assert "portal" in text
    assert "TIMELINE BY MONTH" in text
    assert "WHERE THE WORK WENT" in text


# --- The deep sync -----------------------------------------------------------


def test_a_deep_sync_only_fetches_what_is_missing(db, repo_project):
    project, commit = repo_project
    commit("have", at(1), "already detailed", ["src/a.py"])
    commit("want", at(2), "message only")

    asked = []

    def fake(repo, shas):
        asked.extend(shas)
        return {s: {"files": [{"path": "src/new.py", "additions": 1, "deletions": 0}],
                    "additions": 1, "deletions": 0} for s in shas}

    result = github.deep_sync(db, "me/oms", fetcher=fake)

    assert asked == ["want"]
    assert result == {"repo": "me/oms", "requested": 1, "filled": 1, "still_missing": 0}


def test_a_deep_sync_walks_back_a_chunk_at_a_time(db, repo_project):
    """A long history should not be one enormous call; running it again picks
    up where it left off."""
    project, commit = repo_project
    for i in range(5):
        commit(f"c{i}", at(i + 1), "message only")

    def fake(repo, shas):
        return {s: {"files": [], "additions": 0, "deletions": 0} for s in shas}

    first = github.deep_sync(db, "me/oms", limit=2, fetcher=fake)
    second = github.deep_sync(db, "me/oms", limit=2, fetcher=fake)

    assert first["filled"] == 2
    assert first["still_missing"] == 3
    assert second["filled"] == 2
    assert second["still_missing"] == 1


def test_a_commit_that_cannot_be_read_does_not_lose_the_others(db, repo_project):
    project, commit = repo_project
    commit("good", at(1), "message only")
    commit("bad", at(2), "message only")

    def partial(repo, shas):
        return {"good": {"files": [], "additions": 0, "deletions": 0}}

    result = github.deep_sync(db, "me/oms", fetcher=partial)

    assert result["requested"] == 2
    assert result["filled"] == 1
    assert result["still_missing"] == 1


def test_the_deep_sync_route_needs_a_repo(client):
    response = client.post("/activity/deep-sync")

    assert response.status_code == 400
    assert "No repos to sync" in response.json()["detail"]


# --- The endpoints -----------------------------------------------------------


def test_the_timeline_needs_no_key(client, db, repo_project):
    """It is arithmetic, and free. Requiring a key for it would be a lie
    about where the cost is."""
    project, commit = repo_project
    commit("a", at(1), "work", ["src/a.py"])

    body = client.get(f"/ai/projects/{project.id}/timeline").json()

    assert body["commits"] == 1
    assert body["repo"] == "me/oms"
    assert body["span"]["first"] is not None


def test_a_project_with_no_repo_has_no_history(client, make):
    project = make.project(name="Unlinked")

    response = client.get(f"/ai/projects/{project.id}/timeline")

    assert response.status_code == 400
    assert "isn't linked to a repo" in response.json()["detail"]


def test_a_repo_that_was_never_synced_says_so(client, make):
    project = make.project(name="Linked", repo="me/oms")

    response = client.get(f"/ai/projects/{project.id}/timeline")

    assert response.status_code == 400
    assert "Sync it first" in response.json()["detail"]


def test_the_history_note_is_written_as_agent_work(
    client, db, repo_project, configured, monkeypatch
):
    from app import assistant

    project, commit = repo_project
    commit("a", at(1, month=4), "add the portal", ["portal/auth.py"])

    async def fake(timeline_text, **kw):
        assert "portal" in timeline_text  # it got the facts, not the question
        return {
            "what_it_is": "A management system for a services firm.",
            "phases": [
                {"period": "Apr 2026", "title": "Portal", "what_changed": "Added portal/auth.py."}
            ],
            "where_the_work_went": [
                {"area": "portal", "what_it_does": "Client sign-in", "activity": "April only"}
            ],
            "observations": ["Only one month of activity."],
        }

    monkeypatch.setattr(assistant, "summarise_history", fake)

    body = client.post(f"/ai/projects/{project.id}/history").json()

    note = db.query(models.Note).one()
    assert note.source == "agent"
    assert note.title.startswith("Project history")
    assert "A management system for a services firm." in note.body
    assert "Apr 2026 — Portal" in note.body
    assert "portal: Client sign-in" in note.body
    assert body["commits"] == 1


def test_the_note_records_how_complete_the_history_was(
    client, db, repo_project, configured, monkeypatch
):
    from app import assistant

    project, commit = repo_project
    commit("a", at(1), "detailed", ["src/a.py"])
    commit("b", at(2), "message only")

    async def fake(timeline_text, **kw):
        return {"what_it_is": "Something."}

    monkeypatch.setattr(assistant, "summarise_history", fake)

    client.post(f"/ai/projects/{project.id}/history")

    body = db.query(models.Note).one().body
    assert "2 commits" in body
    assert "file-level detail for 1 of them" in body


def test_a_summary_read_from_history_is_proposed_not_applied(
    client, db, repo_project, configured, monkeypatch
):
    from app import assistant

    project, commit = repo_project
    project.summary = "What I typed months ago."
    db.commit()
    commit("a", at(1), "work", ["src/a.py"])

    async def fake(timeline_text, **kw):
        return {"what_it_is": "x", "suggested_summary": "What the history says it is."}

    monkeypatch.setattr(assistant, "summarise_history", fake)

    body = client.post(f"/ai/projects/{project.id}/history").json()

    assert body["summary_suggested"] is True
    db.expire_all()
    assert db.get(models.Project, project.id).summary == "What I typed months ago."
    suggestion = db.query(models.Suggestion).one()
    assert suggestion.rule == "history_summary"
    assert suggestion.proposed_value == "What the history says it is."


def test_running_it_again_replaces_the_proposal(
    client, db, repo_project, configured, monkeypatch
):
    from app import assistant

    project, commit = repo_project
    commit("a", at(1), "work", ["src/a.py"])

    async def first(timeline_text, **kw):
        return {"what_it_is": "x", "suggested_summary": "First reading."}

    monkeypatch.setattr(assistant, "summarise_history", first)
    client.post(f"/ai/projects/{project.id}/history")

    async def second(timeline_text, **kw):
        return {"what_it_is": "x", "suggested_summary": "Second reading."}

    monkeypatch.setattr(assistant, "summarise_history", second)
    client.post(f"/ai/projects/{project.id}/history")

    suggestions = db.query(models.Suggestion).all()
    assert len(suggestions) == 1
    assert suggestions[0].proposed_value == "Second reading."


# --- Asking questions --------------------------------------------------------


def sse_events(text):
    out = []
    for frame in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_a_question_is_answered_from_the_history(
    client, db, repo_project, configured, monkeypatch
):
    from app import ai

    project, commit = repo_project
    commit("a", at(1, month=4), "add the client portal", ["portal/auth.py"])
    seen = {}

    async def capture(*, system, messages, **_kw):
        seen["system"] = system
        seen["messages"] = messages
        yield "The portal arrived in April."

    monkeypatch.setattr(ai, "stream", capture)

    response = client.post(
        f"/ai/projects/{project.id}/ask", json={"question": "When did the portal appear?"}
    )

    assert response.status_code == 200
    assert [d for e, d in sse_events(response.text) if e == "delta"] == [
        "The portal arrived in April."
    ]
    # Grounded in the computed history, not in the question.
    assert "add the client portal" in seen["system"]
    assert "WHERE THE WORK WENT" in seen["system"]
    assert seen["messages"] == [{"role": "user", "content": "When did the portal appear?"}]


def test_the_question_prompt_admits_it_cannot_see_the_code(
    client, db, repo_project, configured, monkeypatch
):
    """A model asked about code it has not been given will describe what such
    code usually looks like unless it is told not to."""
    from app import ai

    project, commit = repo_project
    commit("a", at(1), "work", ["src/a.py"])
    seen = {}

    async def capture(*, system, messages, **_kw):
        seen["system"] = system
        yield "ok"

    monkeypatch.setattr(ai, "stream", capture)
    client.post(f"/ai/projects/{project.id}/ask", json={"question": "How does auth work?"})

    assert "not the source code" in seen["system"]


def test_asking_needs_a_key(client, db, repo_project, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    project, commit = repo_project
    commit("a", at(1), "work", ["src/a.py"])

    response = client.post(f"/ai/projects/{project.id}/ask", json={"question": "x"})

    assert response.status_code == 503


# --- Cleaning up after the model ---------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('A dashboard."]', "A dashboard."),
        ('A dashboard."  ]', "A dashboard."),
        ("A list: one, two,", "A list: one, two"),
        ('Ends in a brace."}', "Ends in a brace."),
        ("Nothing to strip.", "Nothing to strip."),
        # An unbalanced trailing quote is debris: a real closing quote has an
        # opening one. Observed twice from the same prompt, in both forms.
        ('A system with reporting."', "A system with reporting."),
        # A quotation mark someone meant is not debris.
        ('He said "hello"', 'He said "hello"'),
        ('She replied "yes" and "no"', 'She replied "yes" and "no"'),
        ('It is a "smart" system.', 'It is a "smart" system.'),
        ("", ""),
        (None, ""),
    ],
)
def test_json_debris_is_stripped_but_real_punctuation_is_not(raw, expected):
    """Observed for real: a summary came back ending `..." ]`, which then read
    as a typo in a note. Stripping every trailing quote would have been the
    easy fix and would have quietly damaged real prose."""
    from app import assistant

    assert assistant.clean_prose(raw) == expected


def test_a_summary_is_cut_at_a_sentence_not_mid_word():
    """A hard slice produced "...migrations and tests " -- which looks like
    the system lost the end of the sentence, because it had."""
    from app import assistant

    text = "A client system with a FastAPI backend. It also does reporting, at length."

    assert assistant.fit(text, 60) == "A client system with a FastAPI backend."


def test_something_with_no_sentence_break_still_ends_cleanly():
    from app import assistant

    out = assistant.fit("alpha beta gamma delta epsilon zeta eta theta", 20)

    assert not out.endswith(" ")
    assert len(out) <= 21  # the ellipsis
    assert "\u2026" in out


def test_text_within_the_limit_is_left_exactly_alone():
    from app import assistant

    assert assistant.fit("short enough", 60) == "short enough"


def test_the_stored_note_carries_no_debris(
    client, db, repo_project, configured, monkeypatch
):
    from app import assistant

    project, commit = repo_project
    commit("a", at(1), "work", ["src/a.py"])

    async def messy(timeline_text, **kw):
        return {
            "what_it_is": 'A management system."]',
            "observations": ['It stalled in May."]'],
            "suggested_summary": 'A system."]',
        }

    monkeypatch.setattr(assistant, "summarise_history", messy)

    client.post(f"/ai/projects/{project.id}/history")

    body = db.query(models.Note).one().body
    assert '"]' not in body
    assert "A management system." in body
    assert db.query(models.Suggestion).one().proposed_value == "A system."


def test_the_rankings_say_they_are_rankings(db, repo_project):
    """A file missing from a top-15 list still exists.

    Left unsaid, a model reads absence from the list as absence from the
    repository and plans work to build what is already built -- which is
    exactly what happened before this line was added.
    """
    project, commit = repo_project
    for index in range(20):
        commit(f"s{index}", at(index + 1, month=7), "work", [f"src/pages/p{index}.jsx"])

    text = history.as_text(history.timeline(db, project), project)

    assert "not every part" in text
    assert "does not mean the file does not exist" in text
    assert "says nothing about whether a feature was built" in text
