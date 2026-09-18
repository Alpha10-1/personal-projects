"""The personal side: workspace separation, repo import, scaffolding,
brainstorms.

The separation is the part worth testing hardest. A personal project that
starts turning up in the analyst's findings would quietly undo the whole
point of having two sides.
"""

import json

import pytest
from conftest import days_ago

from app import models


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")


def repo_payload(name="weather_etl", owner="Alpha10-1", **kw):
    payload = {
        "full_name": f"{owner}/{name}",
        "name": name,
        "description": "Pulls weather data on a schedule.",
        "language": "Python",
        "private": False,
        "fork": False,
        "archived": False,
        "stargazers_count": 3,
        "html_url": f"https://github.com/{owner}/{name}",
        "topics": ["etl"],
        "pushed_at": "2026-07-28T10:00:00Z",
        "created_at": "2025-01-01T10:00:00Z",
    }
    payload.update(kw)
    return payload


# --- The separation ----------------------------------------------------------


def test_a_project_is_work_unless_it_says_otherwise(client):
    """Every project that already exists predates this field, so the default
    has to be the side they were already on."""
    body = client.post("/projects", json={"name": "Something"}).json()

    assert body["workspace"] == "work"


def test_projects_can_be_asked_for_one_side(client, make):
    make.project(name="Work thing", workspace="work")
    make.project(name="Side thing", workspace="personal")

    personal = client.get("/projects", params={"workspace": "personal"}).json()
    work = client.get("/projects", params={"workspace": "work"}).json()
    both = client.get("/projects").json()

    assert [p["name"] for p in personal] == ["Side thing"]
    assert [p["name"] for p in work] == ["Work thing"]
    assert len(both) == 2


def test_the_analyst_leaves_personal_work_alone(client, make):
    """A side project you pick up every few months is not stalled, and being
    told it is would train you to ignore the findings that matter."""
    work = make.project(name="Work", workspace="work")
    side = make.project(name="Side", workspace="personal")
    make.task(title="Work task overdue", project_id=work.id, due_date=days_ago(10))
    make.task(title="Side task overdue", project_id=side.id, due_date=days_ago(10))

    findings = client.get("/review").json()["findings"]
    titles = " ".join(f["title"] for f in findings)

    assert "Work task overdue" in titles
    assert "Side task overdue" not in titles


def test_a_task_with_no_project_is_still_reviewed(client, make):
    """Unfiled is not personal."""
    make.task(title="Loose overdue task", due_date=days_ago(10))

    findings = client.get("/review").json()["findings"]

    assert any("Loose overdue task" in f["title"] for f in findings)


def test_personal_projects_raise_no_suggestions(client, db, make):
    side = make.project(name="Side", repo="me/side", workspace="personal")
    task = make.task(title="Tidy", project_id=side.id, status="todo")
    db.add(
        models.ActivityEvent(
            provider="github",
            external_id="c1",
            kind="commit",
            repo="me/side",
            title="a commit",
            occurred_at=models.utcnow(),
            project_id=side.id,
            task_id=task.id,
            linked_by="convention",
        )
    )
    db.commit()

    raised = client.post("/suggestions/refresh").json()

    assert raised == []


def test_a_personal_project_past_its_date_is_not_a_finding(client, make):
    make.project(
        name="Side", workspace="personal", status="active", target_date=days_ago(30)
    )

    findings = client.get("/review").json()["findings"]

    assert not any(f["rule"] == "project_past_target" for f in findings)


# --- Repos -------------------------------------------------------------------


def test_listing_repos_needs_to_know_whose(client):
    response = client.get("/personal/repos")

    assert response.status_code == 400
    assert "PP_GITHUB_USER" in response.json()["detail"]


def test_the_account_is_inferred_from_a_mapped_repo(client, make, monkeypatch):
    """The database already knows the owner; asking again would be rude."""
    from app import github

    make.project(name="Work", repo="Alpha10-1/personal-projects")
    monkeypatch.setattr(github, "fetch_user_repos", lambda user, limit=100: [repo_payload()])

    body = client.get("/personal/repos").json()

    assert body["user"] == "Alpha10-1"
    assert body["count"] == 1


def test_repos_say_which_are_already_imported(client, make, monkeypatch):
    from app import github

    make.project(name="Already", repo="Alpha10-1/weather_etl")
    monkeypatch.setattr(
        github,
        "fetch_user_repos",
        lambda user, limit=100: [repo_payload(), repo_payload(name="glam-glow")],
    )

    repos = client.get("/personal/repos", params={"user": "Alpha10-1"}).json()["repos"]

    by_name = {r["name"]: r for r in repos}
    assert by_name["weather_etl"]["imported"] is True
    assert by_name["glam-glow"]["imported"] is False


def test_importing_creates_personal_projects(client, db, monkeypatch):
    from app import github

    monkeypatch.setattr(github, "fetch_user_repos", lambda user, limit=100: [repo_payload()])

    created = client.post(
        "/personal/repos/import", json={"repos": ["Alpha10-1/weather_etl"]}
    ).json()

    assert len(created) == 1
    project = db.query(models.Project).one()
    assert project.workspace == "personal"
    assert project.repo == "Alpha10-1/weather_etl"
    assert project.summary == "Pulls weather data on a schedule."
    assert project.tech_stack == "Python"


def test_importing_the_same_repo_twice_is_a_no_op(client, db, monkeypatch):
    """The obvious thing to do after importing five repos is to come back and
    import the sixth."""
    from app import github

    monkeypatch.setattr(github, "fetch_user_repos", lambda user, limit=100: [repo_payload()])
    client.post("/personal/repos/import", json={"repos": ["Alpha10-1/weather_etl"]})

    again = client.post(
        "/personal/repos/import", json={"repos": ["Alpha10-1/weather_etl"]}
    ).json()

    assert again == []
    assert db.query(models.Project).count() == 1


def test_importing_survives_a_failed_listing(client, db, monkeypatch):
    """The name is all that is strictly needed; losing the description is
    better than losing the import."""
    from app import github

    def unavailable(user, limit=100):
        raise RuntimeError("GitHub 403: rate limited")

    monkeypatch.setattr(github, "fetch_user_repos", unavailable)

    created = client.post(
        "/personal/repos/import", json={"repos": ["Alpha10-1/weather_etl"]}
    ).json()

    assert len(created) == 1
    assert db.query(models.Project).one().name == "weather_etl"


def test_importing_nothing_is_a_mistake(client):
    assert client.post("/personal/repos/import", json={"repos": []}).status_code == 400


# --- Scaffolding -------------------------------------------------------------

PLAN = {
    "name": "Weather ETL v2",
    "summary": "Rewrite the pull to be incremental.",
    "objective": "Stop re-downloading a year of data every night.",
    "category": "build",
    "priority": "high",
    "milestones": [
        {"title": "Incremental pull working", "detail": "Only new rows."},
        {"title": "Backfill verified"},
    ],
    "tasks": [
        {
            "title": "Add a watermark column",
            "estimate_hours": 2,
            "milestone": "Incremental pull working",
        },
        {"title": "Compare a backfill run", "milestone": "Backfill verified"},
        {"title": "Unfiled cleanup task"},
    ],
    "first_step": "Add the watermark column.",
}


def fake_scaffold(plan):
    async def _call(idea, repo=None):
        return plan

    return _call


def test_a_preview_writes_nothing(client, db, configured, monkeypatch):
    from app import assistant

    monkeypatch.setattr(assistant, "scaffold", fake_scaffold(PLAN))

    body = client.post("/ai/scaffold", json={"idea": "Rewrite the weather ETL"}).json()

    assert body["applied"] is False
    assert body["plan"]["name"] == "Weather ETL v2"
    assert db.query(models.Project).count() == 0
    assert db.query(models.Task).count() == 0


def test_applying_builds_the_whole_thing(client, db, configured, monkeypatch):
    from app import assistant

    monkeypatch.setattr(assistant, "scaffold", fake_scaffold(PLAN))

    body = client.post(
        "/ai/scaffold", json={"idea": "Rewrite the weather ETL", "apply": True}
    ).json()

    project = db.get(models.Project, body["project_id"])
    assert project.workspace == "personal"
    assert project.name == "Weather ETL v2"
    assert db.query(models.Milestone).count() == 2
    assert db.query(models.Task).count() == 3


def test_generated_tasks_are_stamped_as_agent_work(client, db, configured, monkeypatch):
    """A board filled in thirty seconds still has to be distinguishable from
    one you typed."""
    from app import assistant

    monkeypatch.setattr(assistant, "scaffold", fake_scaffold(PLAN))

    client.post("/ai/scaffold", json={"idea": "x" * 20, "apply": True})

    assert {t.source for t in db.query(models.Task).all()} == {"agent"}


def test_tasks_are_filed_under_the_milestone_they_name(client, db, configured, monkeypatch):
    from app import assistant

    monkeypatch.setattr(assistant, "scaffold", fake_scaffold(PLAN))

    client.post("/ai/scaffold", json={"idea": "x" * 20, "apply": True})

    by_title = {t.title: t for t in db.query(models.Task).all()}
    milestones = {m.title: m.id for m in db.query(models.Milestone).all()}
    assert by_title["Add a watermark column"].milestone_id == milestones["Incremental pull working"]
    assert by_title["Compare a backfill run"].milestone_id == milestones["Backfill verified"]
    # Named no milestone -- filed under the project alone.
    assert by_title["Unfiled cleanup task"].milestone_id is None


def test_a_task_naming_a_milestone_that_does_not_exist_is_still_created(
    client, db, configured, monkeypatch
):
    """Losing a task to a typo in the model's own output would be the worst
    possible failure here."""
    from app import assistant

    plan = {
        "name": "P",
        "milestones": [{"title": "Real milestone"}],
        "tasks": [{"title": "Orphan", "milestone": "Milestone that was never listed"}],
    }
    monkeypatch.setattr(assistant, "scaffold", fake_scaffold(plan))

    client.post("/ai/scaffold", json={"idea": "x" * 20, "apply": True})

    task = db.query(models.Task).one()
    assert task.title == "Orphan"
    assert task.milestone_id is None


def test_an_edited_plan_can_be_built_without_asking_again(client, db, configured):
    """What the preview button posts back after you drop the tasks you did
    not want. No model call, so it cannot come back different."""
    trimmed = {**PLAN, "tasks": PLAN["tasks"][:1]}

    body = client.post("/ai/scaffold/apply", json={"plan": trimmed}).json()

    assert body["applied"] is True
    assert db.query(models.Task).count() == 1
    assert db.get(models.Project, body["project_id"]).workspace == "personal"


def test_scaffolding_needs_something_to_work_from(client, configured):
    assert client.post("/ai/scaffold", json={"idea": "  "}).status_code == 400


def test_scaffolding_a_repo_reads_its_readme(client, db, configured, monkeypatch):
    from app import assistant, github

    readme = "# Weather ETL\nDone: the nightly pull."
    monkeypatch.setattr(github, "fetch_readme", lambda repo, max_chars=8000: readme)
    seen = {}

    async def capture(idea, repo=None):
        seen["idea"] = idea
        seen["repo"] = repo
        return PLAN

    monkeypatch.setattr(assistant, "scaffold", capture)

    client.post("/ai/scaffold", json={"repo": "Alpha10-1/weather_etl"})

    assert seen["repo"]["full_name"] == "Alpha10-1/weather_etl"
    assert "nightly pull" in seen["repo"]["readme"]


# --- Brainstorms -------------------------------------------------------------


def test_a_brainstorm_starts_empty(client):
    body = client.post("/personal/brainstorms", json={"topic": "A running app"}).json()

    assert body["topic"] == "A running app"
    assert body["messages"] == []
    assert body["message_count"] == 0


def test_a_brainstorm_can_hang_off_nothing(client):
    """The best ideas start before there is a project to file them under."""
    body = client.post("/personal/brainstorms", json={"topic": "Half an idea"}).json()

    assert body["project_id"] is None


def test_a_brainstorm_on_a_project_that_does_not_exist_is_rejected(client):
    response = client.post(
        "/personal/brainstorms", json={"topic": "x", "project_id": 999}
    )

    assert response.status_code == 404


def test_deleting_a_brainstorm_takes_its_messages(client, db):
    session = client.post("/personal/brainstorms", json={"topic": "x"}).json()
    db.add(
        models.BrainstormMessage(
            brainstorm_id=session["id"], role="user", content="hello"
        )
    )
    db.commit()

    assert client.delete(f"/personal/brainstorms/{session['id']}").status_code == 204

    assert db.query(models.BrainstormMessage).count() == 0
    assert db.query(models.Brainstorm).count() == 0


def test_deleting_a_project_keeps_the_thinking(client, db, make):
    """Your thinking outlives the project it was filed under."""
    project = make.project(name="Doomed")
    session = client.post(
        "/personal/brainstorms", json={"topic": "x", "project_id": project.id}
    ).json()

    assert client.delete(f"/projects/{project.id}").status_code == 204

    row = db.get(models.Brainstorm, session["id"])
    assert row is not None
    assert row.project_id is None


def sse_events(text):
    out = []
    for frame in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def stream_of(*chunks):
    async def _stream(**_kwargs):
        for chunk in chunks:
            yield chunk

    return _stream


def test_a_turn_persists_both_sides(client, db, configured, monkeypatch):
    from app import ai

    session = client.post("/personal/brainstorms", json={"topic": "A running app"}).json()
    monkeypatch.setattr(ai, "stream", stream_of("Start with ", "the data model."))

    response = client.post(
        f"/ai/brainstorms/{session['id']}/turn", json={"content": "Where do I start?"}
    )

    assert response.status_code == 200
    assert [d for e, d in sse_events(response.text) if e == "delta"] == [
        "Start with ",
        "the data model.",
    ]
    saved = client.get(f"/personal/brainstorms/{session['id']}").json()["messages"]
    assert [(m["role"], m["content"]) for m in saved] == [
        ("user", "Where do I start?"),
        ("assistant", "Start with the data model."),
    ]


def test_a_failed_turn_still_keeps_the_question(client, db, configured, monkeypatch):
    """Yours is saved before the call, so an abandoned reply does not take
    the half worth keeping with it."""
    from app import ai

    session = client.post("/personal/brainstorms", json={"topic": "x"}).json()

    async def failing(**_kwargs):
        raise ai.AIFailed("Rate limited by the API. Try again in a moment.")
        yield  # pragma: no cover

    monkeypatch.setattr(ai, "stream", failing)

    response = client.post(
        f"/ai/brainstorms/{session['id']}/turn", json={"content": "Where do I start?"}
    )

    assert ("error", "Rate limited by the API. Try again in a moment.") in sse_events(
        response.text
    )
    saved = client.get(f"/personal/brainstorms/{session['id']}").json()["messages"]
    assert [m["role"] for m in saved] == ["user"]


def test_the_brainstorm_knows_its_project(client, db, make, configured, monkeypatch):
    from app import ai

    project = make.project(name="Weather ETL", summary="Nightly pull.")
    make.task(title="Add a watermark column", project_id=project.id)
    session = client.post(
        "/personal/brainstorms", json={"topic": "x", "project_id": project.id}
    ).json()
    seen = {}

    async def capture(*, system, messages, **_kw):
        seen["system"] = system
        yield "ok"

    monkeypatch.setattr(ai, "stream", capture)

    client.post(f"/ai/brainstorms/{session['id']}/turn", json={"content": "hi"})

    assert "Weather ETL" in seen["system"]
    assert "Add a watermark column" in seen["system"]


def test_harvesting_needs_a_conversation(client, configured):
    session = client.post("/personal/brainstorms", json={"topic": "x"}).json()

    response = client.post(f"/ai/brainstorms/{session['id']}/harvest", json={})

    assert response.status_code == 400
    assert "Nothing said yet" in response.json()["detail"]


def test_harvesting_turns_a_conversation_into_tasks(
    client, db, make, configured, monkeypatch
):
    from app import assistant

    project = make.project(name="Weather ETL", workspace="personal")
    session = client.post(
        "/personal/brainstorms", json={"topic": "x", "project_id": project.id}
    ).json()
    db.add(
        models.BrainstormMessage(
            brainstorm_id=session["id"], role="user", content="Should I use a watermark?"
        )
    )
    db.commit()

    async def fake(topic, messages):
        return {
            "tasks": [{"title": "Add a watermark column", "estimate_hours": 2}],
            "decisions": ["Incremental beats full reload."],
            "open": ["Which column to watermark on."],
        }

    monkeypatch.setattr(assistant, "harvest", fake)

    body = client.post(
        f"/ai/brainstorms/{session['id']}/harvest", json={"apply": True}
    ).json()

    assert body["tasks_created"] == 1
    task = db.query(models.Task).one()
    assert task.title == "Add a watermark column"
    assert task.project_id == project.id
    assert task.source == "agent"


def test_harvesting_without_applying_writes_nothing(
    client, db, make, configured, monkeypatch
):
    from app import assistant

    project = make.project(name="P", workspace="personal")
    session = client.post(
        "/personal/brainstorms", json={"topic": "x", "project_id": project.id}
    ).json()
    db.add(
        models.BrainstormMessage(brainstorm_id=session["id"], role="user", content="hi")
    )
    db.commit()

    async def fake(topic, messages):
        return {"tasks": [{"title": "Something"}]}

    monkeypatch.setattr(assistant, "harvest", fake)

    body = client.post(f"/ai/brainstorms/{session['id']}/harvest", json={}).json()

    assert body["applied"] is False
    assert db.query(models.Task).count() == 0


def test_harvested_tasks_need_somewhere_to_go(client, db, configured, monkeypatch):
    from app import assistant

    session = client.post("/personal/brainstorms", json={"topic": "x"}).json()
    db.add(
        models.BrainstormMessage(brainstorm_id=session["id"], role="user", content="hi")
    )
    db.commit()

    async def fake(topic, messages):
        return {"tasks": [{"title": "Something"}]}

    monkeypatch.setattr(assistant, "harvest", fake)

    response = client.post(
        f"/ai/brainstorms/{session['id']}/harvest", json={"apply": True}
    )

    assert response.status_code == 400
    assert "No project" in response.json()["detail"]


def test_the_personal_routes_are_unavailable_without_a_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    session = client.post("/personal/brainstorms", json={"topic": "x"}).json()

    assert client.post("/ai/scaffold", json={"idea": "x" * 20}).status_code == 503
    assert (
        client.post(f"/ai/brainstorms/{session['id']}/turn", json={"content": "x"}).status_code
        == 503
    )
