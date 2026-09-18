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
    # --no-autostart, or this would try to start a backend on port 9 and the
    # test would be about that instead.
    monkeypatch.setattr("sys.argv", ["analyst_run.py", "--no-autostart"])

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


# --- Standing the backend up -------------------------------------------------
#
# The whole point of the scheduled run is that nobody is there. These cover the
# case that made it fail every morning: nothing was listening.


class FakeProcess:
    """A backend that behaves as told, without starting one."""

    def __init__(self, exits_with=None):
        self.exits_with = exits_with
        self.returncode = exits_with
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exits_with

    def terminate(self):
        self.terminated = True
        self.exits_with = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture
def unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(analyst_run, "DATA_DIR", tmp_path)
    monkeypatch.setattr(analyst_run, "RUN_LOG", tmp_path / "runs.jsonl")
    monkeypatch.setattr(analyst_run, "BACKEND_LOG", tmp_path / "backend.log")
    monkeypatch.setattr(analyst_run, "API_URL", "http://localhost:8000")
    monkeypatch.setattr(analyst_run, "STARTUP_POLL", 0.0)
    monkeypatch.setattr(analyst_run, "STARTUP_TIMEOUT", 0.2)


def answers_after(calls):
    """A health check that starts failing and then succeeds, as a server that
    is still binding its port does."""
    state = {"n": 0}

    async def probe(timeout=2.0):
        state["n"] += 1
        return state["n"] > calls

    return probe, state


@pytest.mark.anyio
async def test_nothing_is_started_when_something_is_already_serving(
    monkeypatch, unreachable
):
    """A backend the user is using must not be stopped at the end of the run."""
    monkeypatch.setattr(analyst_run, "api_is_up", lambda timeout=2.0: _true())
    started_one = []
    monkeypatch.setattr(
        analyst_run, "_start_backend", lambda: started_one.append(1) or FakeProcess()
    )

    async with analyst_run.serving() as started:
        assert started is False

    assert started_one == []


async def _true():
    return True


@pytest.mark.anyio
async def test_a_backend_is_started_and_stopped_again(monkeypatch, unreachable):
    probe, _ = answers_after(1)
    process = FakeProcess()
    monkeypatch.setattr(analyst_run, "api_is_up", probe)
    monkeypatch.setattr(analyst_run, "_start_backend", lambda: process)

    async with analyst_run.serving() as started:
        assert started is True
        assert not process.terminated  # still up while the work happens

    assert process.terminated


@pytest.mark.anyio
async def test_the_backend_is_stopped_even_when_the_run_fails(monkeypatch, unreachable):
    probe, _ = answers_after(1)
    process = FakeProcess()
    monkeypatch.setattr(analyst_run, "api_is_up", probe)
    monkeypatch.setattr(analyst_run, "_start_backend", lambda: process)

    with pytest.raises(analyst_run.RunFailed, match="the sync blew up"):
        async with analyst_run.serving():
            raise analyst_run.RunFailed("the sync blew up")

    assert process.terminated


@pytest.mark.anyio
async def test_a_backend_that_dies_on_startup_is_reported_with_its_own_log(
    monkeypatch, unreachable, tmp_path
):
    probe, _ = answers_after(99)
    (tmp_path / "backend.log").write_text(
        "ERROR: [Errno 10048] error while attempting to bind on address\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(analyst_run, "api_is_up", probe)
    monkeypatch.setattr(analyst_run, "_start_backend", lambda: FakeProcess(exits_with=1))

    with pytest.raises(analyst_run.RunFailed, match="exited immediately"):
        async with analyst_run.serving():
            pass


@pytest.mark.anyio
async def test_a_backend_that_never_answers_gives_up_rather_than_hanging(
    monkeypatch, unreachable
):
    probe, state = answers_after(10**6)
    monkeypatch.setattr(analyst_run, "api_is_up", probe)
    monkeypatch.setattr(analyst_run, "_start_backend", lambda: FakeProcess())

    with pytest.raises(analyst_run.RunFailed, match="wasn't answering"):
        async with analyst_run.serving():
            pass

    assert state["n"] > 1


@pytest.mark.anyio
async def test_a_remote_api_is_never_started_locally(monkeypatch, unreachable):
    """Starting a second server against someone else's address would at best
    do nothing and at worst write to the wrong database."""
    probe, _ = answers_after(10**6)
    monkeypatch.setattr(analyst_run, "api_is_up", probe)
    monkeypatch.setattr(analyst_run, "API_URL", "https://tracker.example.com")

    with pytest.raises(analyst_run.RunFailed, match="isn't on this machine"):
        async with analyst_run.serving():
            pass


@pytest.mark.anyio
async def test_no_autostart_keeps_the_old_loud_failure(monkeypatch, unreachable):
    probe, _ = answers_after(10**6)
    monkeypatch.setattr(analyst_run, "api_is_up", probe)

    with pytest.raises(analyst_run.RunFailed, match="Is the backend running"):
        async with analyst_run.serving(autostart=False):
            pass


def test_the_run_log_says_whether_it_had_to_start_one(runner, monkeypatch):
    """So a morning that went wrong can be told apart from a morning where
    the app happened to be open."""
    monkeypatch.setattr("sys.argv", ["analyst_run.py", "--quiet"])

    assert analyst_run.main() == 0

    assert run_log(runner)[-1]["started_api"] is False
