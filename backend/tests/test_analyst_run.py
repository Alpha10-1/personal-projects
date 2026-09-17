"""The unattended pass.

Driven against the real app over an in-process transport, so what the script
sends is what the API would actually receive.
"""

import json

import httpx
import pytest
from conftest import TODAY

import analyst_run
from app.main import app


@pytest.fixture
def runner(monkeypatch, tmp_path, db):
    """Point the script's HTTP client at the app, in process."""
    real_client = httpx.AsyncClient

    def in_process(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=app)
        kwargs.setdefault("base_url", "http://testserver")
        return real_client(*args, **kwargs)

    monkeypatch.setattr(analyst_run.httpx, "AsyncClient", in_process)
    monkeypatch.setattr(analyst_run, "DATA_DIR", tmp_path)
    monkeypatch.setattr(analyst_run, "RUN_LOG", tmp_path / "analyst-runs.jsonl")
    return analyst_run


def run_log(runner):
    if not runner.RUN_LOG.exists():
        return []
    return [json.loads(line) for line in runner.RUN_LOG.read_text().splitlines()]


# --- The digest --------------------------------------------------------------


def test_digest_reports_an_empty_board_plainly():
    text = analyst_run.digest([], [])

    assert "No findings." in text
    assert "No suggestions awaiting a decision." in text


def test_digest_lists_findings_and_suggestions():
    findings = [{"severity": "warn", "title": "Something stalled", "detail": "9 days"}]
    suggestions = [
        {
            "target_title": "Tidy the loader",
            "target_type": "task",
            "target_id": 1,
            "field": "status",
            "current_value": "todo",
            "proposed_value": "in_progress",
            "rationale": "There are commits against it.",
        }
    ]

    text = analyst_run.digest(findings, suggestions)

    assert "[warn] Something stalled" in text
    assert "9 days" in text
    assert "Tidy the loader: status todo -> in_progress" in text
    assert "There are commits against it." in text


# --- The run -----------------------------------------------------------------


@pytest.mark.anyio
async def test_a_quiet_board_runs_clean(runner):
    summary = await runner.run()

    assert summary["repos"] == []
    assert summary["findings"] == 0
    assert summary["suggestions_pending"] == 0


@pytest.mark.anyio
async def test_the_run_reports_findings_without_changing_anything(runner, client, make):
    make.task(title="Late", due_date=TODAY.replace(day=1))

    summary = await runner.run()

    assert summary["findings"] >= 1
    assert client.get("/tasks").json()[0]["status"] == "todo"


@pytest.mark.anyio
async def test_a_dry_run_does_not_propose(runner, db, make):
    """The repo is mapped here on purpose: a dry run must not sync either,
    which the network guard would catch if it did."""
    from app import models

    project = make.project(name="Tracker", repo="owner/name")
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    db.add(
        models.ActivityEvent(
            provider="github", external_id="c1", kind="commit", repo="owner/name",
            title="a commit", occurred_at=models.utcnow(),
            project_id=project.id, task_id=task.id, linked_by="convention",
        )
    )
    db.commit()

    summary = await runner.run(dry_run=True)

    assert summary["suggestions_raised"] == 0
    assert db.query(models.Suggestion).count() == 0


@pytest.mark.anyio
async def test_the_run_raises_suggestions_from_real_evidence(runner, db, make):
    """No repo is mapped, so nothing is fetched -- the suggestion comes
    entirely from activity already recorded."""
    from app import models

    project = make.project(name="Tracker")
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    db.add(
        models.ActivityEvent(
            provider="github", external_id="c1", kind="commit", repo="owner/name",
            title="a commit", occurred_at=models.utcnow(),
            project_id=project.id, task_id=task.id, linked_by="convention",
        )
    )
    db.commit()

    summary = await runner.run()

    assert summary["suggestions_raised"] == 1
    assert summary["suggestions_pending"] == 1
    # Raised, not applied.
    assert db.get(models.Task, task.id).status == "todo"


@pytest.mark.anyio
async def test_the_digest_note_is_written_as_agent_work(runner, client, make):
    make.task(title="Late", due_date=TODAY.replace(day=1))

    await runner.run(write_note=True)

    notes = client.get("/notes").json()
    assert len(notes) == 1
    assert notes[0]["source"] == "agent"
    assert notes[0]["title"].startswith("Analyst digest")


@pytest.mark.anyio
async def test_no_note_is_written_unless_asked(runner, client):
    await runner.run()

    assert client.get("/notes").json() == []


@pytest.mark.anyio
async def test_an_unreachable_api_fails_without_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(analyst_run, "DATA_DIR", tmp_path)
    monkeypatch.setattr(analyst_run, "RUN_LOG", tmp_path / "runs.jsonl")
    monkeypatch.setattr(analyst_run, "API_URL", "http://127.0.0.1:9")

    with pytest.raises(analyst_run.RunFailed, match="Is the backend running"):
        await analyst_run.run()


def test_the_failure_is_recorded_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(analyst_run, "DATA_DIR", tmp_path)
    monkeypatch.setattr(analyst_run, "RUN_LOG", tmp_path / "runs.jsonl")
    monkeypatch.setattr(analyst_run, "API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr("sys.argv", ["analyst_run.py"])

    code = analyst_run.main()

    assert code == 1
    assert "analyst run failed" in capsys.readouterr().err
    entries = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert entries[-1]["ok"] is False


def test_main_records_a_successful_run(runner, monkeypatch):
    """The run log is written by main(), which is what the scheduled task
    actually invokes."""
    monkeypatch.setattr("sys.argv", ["analyst_run.py", "--quiet"])

    assert analyst_run.main() == 0

    entries = run_log(runner)
    assert entries[-1]["ok"] is True
    assert "findings" in entries[-1]
