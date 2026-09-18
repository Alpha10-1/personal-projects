"""Tests for the MCP server.

The tools are exercised against the real FastAPI app over an in-process ASGI
transport, so what they send is what the API would actually receive -- the
point being to catch a tool that builds a request the API rejects.
"""

import json
from datetime import datetime

import httpx
import pytest
from conftest import TODAY
from mcp.server.mcpserver.exceptions import ToolError

import mcp_server
from app.main import app


@pytest.fixture
def mcp(monkeypatch, tmp_path, db):
    """Point the server's HTTP client at the app, and its audit log at tmp."""
    real_client = httpx.AsyncClient

    def in_process(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=app)
        kwargs.setdefault("base_url", "http://testserver")
        return real_client(*args, **kwargs)

    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", in_process)
    monkeypatch.setattr(mcp_server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mcp_server, "AUDIT_LOG", tmp_path / "agent-audit.jsonl")
    return mcp_server


def audit_entries(mcp):
    if not mcp.AUDIT_LOG.exists():
        return []
    return [json.loads(line) for line in mcp.AUDIT_LOG.read_text().splitlines()]


# --- Registration -----------------------------------------------------------


@pytest.mark.anyio
async def test_every_tool_is_registered_with_a_description():
    tools = await mcp_server.server.list_tools()
    by_name = {t.name: t for t in tools}

    assert {
        "today",
        "insights",
        "list_projects",
        "list_tasks",
        "list_milestones",
        "list_time_logs",
        "list_notes",
        "create_task",
        "update_task",
        "log_time",
        "add_note",
        "update_project",
        "project_timeline",
        "deep_sync_commits",
        "list_people",
        "add_person",
        "project_team",
        "add_member",
        "list_feedback",
        "add_feedback",
        "update_feedback",
        "list_repos",
        "import_repos",
        "list_brainstorms",
        "read_brainstorm",
        "list_dashboards",
        "add_dashboard",
    } <= set(by_name)
    assert all(t.description for t in tools)


@pytest.mark.anyio
async def test_read_and_write_tools_are_annotated_distinctly():
    """A client has to be able to tell which tools change data."""
    by_name = {t.name: t for t in await mcp_server.server.list_tools()}

    assert by_name["today"].annotations.read_only_hint is True
    assert by_name["list_tasks"].annotations.read_only_hint is True
    assert by_name["create_task"].annotations.read_only_hint is False
    assert by_name["update_task"].annotations.read_only_hint is False
    assert by_name["project_timeline"].annotations.read_only_hint is True
    assert by_name["import_repos"].annotations.read_only_hint is False


def test_prune_drops_unset_arguments_but_keeps_falsy_ones():
    """An omitted argument must not travel as null -- the API rejects null for
    anything backed by a NOT NULL column."""
    assert mcp_server._prune({"a": None, "b": 0, "c": False, "d": ""}) == {
        "b": 0,
        "c": False,
        "d": "",
    }


# --- Reading ----------------------------------------------------------------


@pytest.mark.anyio
async def test_today_returns_the_dashboard_payload(mcp):
    body = await mcp.today()

    assert body["date"] == TODAY.isoformat()
    assert "counts" in body and "hours" in body


@pytest.mark.anyio
async def test_insights_accepts_a_window(mcp):
    body = await mcp.insights(days=14)

    assert body["window"]["days"] == 14


@pytest.mark.anyio
async def test_list_tasks_filters_are_passed_through(mcp):
    await mcp.create_task(title="open one")
    await mcp.create_task(title="finished", status="done")

    assert {t["title"] for t in await mcp.list_tasks(open_only=True)} == {"open one"}
    assert len(await mcp.list_tasks()) == 2


@pytest.mark.anyio
async def test_reads_are_not_written_to_the_audit_log(mcp):
    await mcp.today()
    await mcp.list_tasks()

    assert audit_entries(mcp) == []


# --- Writing ----------------------------------------------------------------


@pytest.mark.anyio
async def test_time_logged_against_a_task_rolls_up_to_its_project(mcp, client):
    project = client.post("/projects", json={"name": "Forecasting"}).json()
    task = await mcp.create_task(
        title="write the query", project_id=project["id"], priority="high"
    )

    log = await mcp.log_time(
        work_date=TODAY.isoformat(), hours=2.5, task_id=task["id"], category="build"
    )

    assert task["priority"] == "high"
    assert log["hours"] == 2.5
    assert log["task_title"] == "write the query"
    # The API fills the project in from the task, so the agent never has to.
    assert log["project_id"] == project["id"]
    assert log["project_name"] == "Forecasting"

    rolled_up = (await mcp.list_projects())[0]
    assert rolled_up["hours_logged"] == 2.5


@pytest.mark.anyio
async def test_update_task_changes_only_what_it_is_given(mcp):
    task = await mcp.create_task(title="t", notes="keep me", priority="low")

    updated = await mcp.update_task(task_id=task["id"], status="in_progress")

    assert updated["status"] == "in_progress"
    assert updated["notes"] == "keep me"
    assert updated["priority"] == "low"


@pytest.mark.anyio
async def test_add_note_records_a_decision(mcp):
    note = await mcp.add_note(
        body="Chose Prophet over ARIMA: weekly seasonality was the whole problem.",
        title="Model choice",
        kind="decision",
    )

    assert note["kind"] == "decision"
    assert (await mcp.list_notes(kind="decision"))[0]["id"] == note["id"]


@pytest.mark.anyio
async def test_writes_are_appended_to_the_audit_log(mcp):
    await mcp.create_task(title="audited")

    entries = audit_entries(mcp)
    assert len(entries) == 1
    assert entries[0]["method"] == "POST"
    assert entries[0]["path"] == "/tasks"
    assert entries[0]["payload"]["title"] == "audited"
    assert entries[0]["outcome"].startswith("ok")


# --- Failure modes ----------------------------------------------------------


@pytest.mark.anyio
async def test_validation_errors_name_the_field(mcp):
    with pytest.raises(ToolError) as caught:
        await mcp.log_time(work_date=TODAY.isoformat(), hours=0)

    assert "hours" in str(caught.value)


@pytest.mark.anyio
async def test_unknown_reference_surfaces_the_api_message(mcp):
    with pytest.raises(ToolError) as caught:
        await mcp.create_task(title="t", project_id=999)

    assert "Unknown project" in str(caught.value)


@pytest.mark.anyio
async def test_a_failed_write_is_still_audited(mcp):
    with pytest.raises(ToolError):
        await mcp.create_task(title="t", project_id=999)

    entries = audit_entries(mcp)
    assert len(entries) == 1
    assert entries[0]["outcome"].startswith("error")


@pytest.mark.anyio
async def test_an_empty_update_is_refused_before_it_reaches_the_api(mcp):
    task = await mcp.create_task(title="t")

    with pytest.raises(ToolError, match="Nothing to update"):
        await mcp.update_task(task_id=task["id"])


@pytest.mark.anyio
async def test_an_unreachable_api_says_so_plainly(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(mcp_server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mcp_server, "API_URL", "http://127.0.0.1:9")

    with pytest.raises(ToolError, match="Is the backend running"):
        await mcp_server.today()


# --- How a project was built ------------------------------------------------


@pytest.fixture
def repo_project(make):
    """A project with a repo and two commits, one of them detailed."""
    project = make.project(name="OMS", repo="me/oms")
    make.event(
        repo="me/oms",
        external_id="aaa",
        title="add the search module",
        occurred_at=datetime(2026, 7, 4, 12, 0),
        project_id=project.id,
        actor="me",
        raw=json.dumps({"sha": "aaa"}),
        file_stats=json.dumps(
            {
                "files": [
                    {
                        "path": "backend/app/search.py",
                        "status": "added",
                        "additions": 90,
                        "deletions": 0,
                    }
                ],
                "additions": 90,
                "deletions": 0,
            }
        ),
    )
    make.event(
        repo="me/oms",
        external_id="bbb",
        title="tidy up",
        occurred_at=datetime(2026, 8, 9, 12, 0),
        project_id=project.id,
        actor="me",
        raw=json.dumps({"sha": "bbb"}),
    )
    return project


@pytest.mark.anyio
async def test_the_timeline_is_the_arithmetic_of_the_history(mcp, repo_project):
    body = await mcp.project_timeline(project_id=repo_project.id)

    assert body["commits"] == 2
    assert [p["month"] for p in body["periods"]] == ["2026-07", "2026-08"]
    assert "backend/app" in {a["area"] for a in body["areas"]}


@pytest.mark.anyio
async def test_the_timeline_says_how_much_detail_it_has(mcp, repo_project):
    """A summary built from 1 of 2 commits is a different claim from one built
    on both, so the agent is told which it is holding."""
    body = await mcp.project_timeline(project_id=repo_project.id)

    assert body["commits"] == 2
    assert body["detailed"] == 1


@pytest.mark.anyio
async def test_a_project_with_no_repo_says_so_rather_than_returning_nothing(mcp, make):
    make.project(name="No repo")

    with pytest.raises(ToolError, match="isn't linked to a repo"):
        await mcp.project_timeline(project_id=1)


@pytest.mark.anyio
async def test_deep_sync_fills_in_the_missing_detail(mcp, repo_project, monkeypatch):
    from app import github

    monkeypatch.setattr(
        github,
        "fetch_commit_stats",
        lambda repo, shas: {
            sha: {"files": [{"path": "README.md", "additions": 1, "deletions": 0}],
                  "additions": 1, "deletions": 0}
            for sha in shas
        },
    )

    result = await mcp.deep_sync_commits(repo="me/oms")

    assert result[0]["filled"] == 1
    assert result[0]["still_missing"] == 0
    assert (await mcp.project_timeline(project_id=repo_project.id))["detailed"] == 2


# --- People and collaboration -----------------------------------------------


@pytest.mark.anyio
async def test_a_person_added_with_a_login_picks_up_their_existing_work(mcp, make):
    """Adding someone who has been committing for weeks should show those
    weeks, not start them from zero."""
    project = make.project(name="OMS", repo="me/oms")
    make.event(repo="me/oms", external_id="c1", project_id=project.id, actor="dana")

    person = await mcp.add_person(name="Dana", github_login="dana")

    assert person["contribution_count"] == 1


@pytest.mark.anyio
async def test_project_team_gathers_members_contributors_and_feedback(mcp, make):
    project = make.project(name="OMS", repo="me/oms")
    make.event(repo="me/oms", external_id="c1", project_id=project.id, actor="dana")
    person = await mcp.add_person(name="Dana", github_login="dana")
    await mcp.add_member(project_id=project.id, person_id=person["id"], role="contributor")
    await mcp.add_feedback(
        body="The export should stream, not buffer.",
        project_id=project.id,
        person_id=person["id"],
    )

    team = await mcp.project_team(project_id=project.id)

    assert [m["role"] for m in team["members"]] == ["contributor"]
    assert [c["login"] for c in team["contributors"]] == ["dana"]
    assert team["feedback_open"] == 1


@pytest.mark.anyio
async def test_feedback_typed_in_cannot_claim_to_be_a_review_comment(mcp):
    """The other sources mean "this came from GitHub and has an id there"."""
    item = await mcp.add_feedback(body="Said in standup: drop the CSV path.")

    assert item["source"] == "manual"


@pytest.mark.anyio
async def test_feedback_can_be_closed_once_it_is_dealt_with(mcp):
    item = await mcp.add_feedback(body="Rename the column.")

    await mcp.update_feedback(feedback_id=item["id"], status="actioned")

    assert await mcp.list_feedback() == []
    assert len(await mcp.list_feedback(status="actioned")) == 1


@pytest.mark.anyio
async def test_an_empty_feedback_update_is_refused_before_it_reaches_the_api(mcp):
    item = await mcp.add_feedback(body="Something.")

    with pytest.raises(ToolError, match="Nothing to update"):
        await mcp.update_feedback(feedback_id=item["id"])


# --- The GitHub account -----------------------------------------------------


def _repo_payload(full_name="me/oms", **kw):
    return {
        "full_name": full_name,
        "name": full_name.split("/")[-1],
        "description": "An organisation management system",
        "language": "Python",
        "html_url": f"https://github.com/{full_name}",
        **kw,
    }


@pytest.mark.anyio
async def test_repos_are_listed_with_whether_they_are_already_tracked(
    mcp, make, monkeypatch
):
    from app import github

    make.project(name="OMS", repo="me/oms")
    monkeypatch.setattr(
        github,
        "fetch_user_repos",
        lambda user, limit=100, **kw: [_repo_payload(), _repo_payload("me/other")],
    )

    body = await mcp.list_repos(user="me")

    assert {r["full_name"]: r["imported"] for r in body["repos"]} == {
        "me/oms": True,
        "me/other": False,
    }


@pytest.mark.anyio
async def test_importing_the_same_repo_twice_adds_nothing(mcp, monkeypatch):
    from app import github

    monkeypatch.setattr(
        github, "fetch_user_repos", lambda user, limit=100, **kw: [_repo_payload()]
    )

    first = await mcp.import_repos(repos=["me/oms"])
    second = await mcp.import_repos(repos=["me/oms"])

    assert [p["repo"] for p in first] == ["me/oms"]
    assert second == []
    assert len(await mcp.list_projects()) == 1


@pytest.mark.anyio
async def test_importing_nothing_is_refused_before_it_reaches_the_api(mcp):
    with pytest.raises(ToolError, match="at least one repo"):
        await mcp.import_repos(repos=[])


@pytest.mark.anyio
async def test_a_brainstorm_is_listed_without_its_transcript_and_read_with_it(
    mcp, client
):
    session = client.post(
        "/personal/brainstorms", json={"topic": "Where to take the tracker"}
    ).json()

    listed = await mcp.list_brainstorms()
    read = await mcp.read_brainstorm(brainstorm_id=session["id"])

    assert [s["topic"] for s in listed] == ["Where to take the tracker"]
    assert listed[0]["messages"] == []
    assert read["id"] == session["id"]


# --- Dashboards -------------------------------------------------------------


@pytest.mark.anyio
async def test_a_dashboard_can_be_recorded_and_found_by_project(mcp, make):
    project = make.project(name="OMS")
    await mcp.add_dashboard(
        name="Delivery", url="https://example.com/report", project_id=project.id
    )
    await mcp.add_dashboard(name="Loose", url="https://example.com/other")

    assert [d["name"] for d in await mcp.list_dashboards(project_id=project.id)] == [
        "Delivery"
    ]
    assert [d["name"] for d in await mcp.list_dashboards(unlinked_only=True)] == ["Loose"]
