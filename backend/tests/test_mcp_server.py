"""Tests for the MCP server.

The tools are exercised against the real FastAPI app over an in-process ASGI
transport, so what they send is what the API would actually receive -- the
point being to catch a tool that builds a request the API rejects.
"""

import json

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
