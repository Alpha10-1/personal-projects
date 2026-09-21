"""Starting, polling, applying and discarding runs over HTTP.

The background half is driven directly with `asyncio.run(carry_out(...))`
rather than through the client. Waiting on a task the test did not start is
a race, and a racy test of the thing that writes to your repository is worse
than no test.
"""

import asyncio
import json
import subprocess

import pytest

from app import agent, ai, models, workspace
from app.routes.agent import carry_out


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    root.mkdir()
    write(root / "a.py", "x = 1\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@example.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


@pytest.fixture
def configured(monkeypatch):
    """An API key that is present but never used -- every test fakes the run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(ai, "is_configured", lambda: True)


def proposal(**kw):
    base = {
        "changes": [{"path": "a.py", "action": "modify", "content": "x = 2\n"}],
        "diff": "--- a/a.py\n+++ b/a.py\n-x = 1\n+x = 2\n",
        "summary": "Changed x. Untested.",
        "error": None,
        "turns": 3,
        "base_sha": "abc1234",
        "review_required": False,
        "review_reason": None,
    }
    return {**base, **kw}


def fake_execute(result):
    async def run(_project, _instruction, **_kw):
        return result

    return run


# --- starting -------------------------------------------------------------


def test_a_project_with_no_folder_cannot_start_a_run(client, make, configured):
    project = make.project("No folder")
    response = client.post(
        f"/projects/{project.id}/agent/runs", json={"instruction": "Do the thing please"}
    )
    assert response.status_code == 400
    assert "no local folder" in response.json()["detail"]


def test_without_an_api_key_the_route_says_so(client, make, repo, monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: False)
    project = make.project("Demo", local_path=str(repo))
    response = client.post(
        f"/projects/{project.id}/agent/runs", json={"instruction": "Do the thing please"}
    )
    assert response.status_code == 503


def test_a_second_run_on_the_same_project_is_refused(client, db, make, repo, configured):
    project = make.project("Demo", local_path=str(repo))
    db.add(
        models.AgentRun(project_id=project.id, instruction="first", status="running")
    )
    db.commit()
    response = client.post(
        f"/projects/{project.id}/agent/runs", json={"instruction": "Do the thing please"}
    )
    assert response.status_code == 409
    assert "still going" in response.json()["detail"]


def test_a_very_short_instruction_is_rejected(client, make, repo, configured):
    project = make.project("Demo", local_path=str(repo))
    assert (
        client.post(f"/projects/{project.id}/agent/runs", json={"instruction": "no"}).status_code
        == 422
    )


# --- the background half --------------------------------------------------


def started(db, project, **kw):
    run = models.AgentRun(
        project_id=project.id, instruction="Do the thing.", status="running", **kw
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_a_finished_run_is_proposed_and_keeps_its_diff(db, make, repo, monkeypatch):
    project = make.project("Demo", local_path=str(repo))
    run = started(db, project)
    monkeypatch.setattr(agent, "execute", fake_execute(proposal()))
    asyncio.run(carry_out(run.id))
    db.expire_all()
    run = db.get(models.AgentRun, run.id)
    assert run.status == "proposed"
    assert run.summary == "Changed x. Untested."
    assert "+x = 2" in run.diff
    assert run.finished_at is not None
    # Nothing written yet.
    assert (repo / "a.py").read_bytes() == b"x = 1\n"


def test_auto_apply_writes_the_files(db, make, repo, monkeypatch):
    project = make.project("Demo", local_path=str(repo))
    run = started(db, project, auto_apply=True)
    monkeypatch.setattr(agent, "execute", fake_execute(proposal()))
    asyncio.run(carry_out(run.id))
    db.expire_all()
    run = db.get(models.AgentRun, run.id)
    assert run.status == "applied"
    assert run.applied_at is not None
    assert (repo / "a.py").read_bytes() == b"x = 2\n"


def test_auto_apply_is_overridden_by_a_protected_path(db, make, repo, monkeypatch):
    project = make.project("Demo", local_path=str(repo))
    run = started(db, project, auto_apply=True)
    monkeypatch.setattr(
        agent,
        "execute",
        fake_execute(
            proposal(review_required=True, review_reason="Touches models.py.")
        ),
    )
    asyncio.run(carry_out(run.id))
    db.expire_all()
    run = db.get(models.AgentRun, run.id)
    assert run.status == "proposed"
    assert run.review_required is True
    assert (repo / "a.py").read_bytes() == b"x = 1\n"


def test_a_run_that_changed_nothing_and_errored_is_failed(db, make, repo, monkeypatch):
    project = make.project("Demo", local_path=str(repo))
    run = started(db, project)
    monkeypatch.setattr(
        agent, "execute", fake_execute(proposal(changes=[], error="Ran out of turns."))
    )
    asyncio.run(carry_out(run.id))
    db.expire_all()
    assert db.get(models.AgentRun, run.id).status == "failed"


def test_an_agent_error_leaves_a_failed_row_not_a_running_one(db, make, repo, monkeypatch):
    project = make.project("Demo", local_path=str(repo))
    run = started(db, project)

    async def boom(*_a, **_kw):
        raise agent.AgentError("The folder moved.")

    monkeypatch.setattr(agent, "execute", boom)
    asyncio.run(carry_out(run.id))
    db.expire_all()
    run = db.get(models.AgentRun, run.id)
    assert run.status == "failed"
    assert run.error == "The folder moved."


def test_a_timeout_leaves_a_failed_row(db, make, repo, monkeypatch):
    project = make.project("Demo", local_path=str(repo))
    run = started(db, project)
    monkeypatch.setattr("app.routes.agent.RUN_TIMEOUT", 0.01)

    async def slow(*_a, **_kw):
        await asyncio.sleep(1)

    monkeypatch.setattr(agent, "execute", slow)
    asyncio.run(carry_out(run.id))
    db.expire_all()
    run = db.get(models.AgentRun, run.id)
    assert run.status == "failed"
    assert "did not finish in time" in run.error


# --- applying and discarding ---------------------------------------------


def proposed_run(db, project, **kw):
    fields = {
        "changes_json": json.dumps(
            [{"path": "a.py", "action": "modify", "content": "x = 9\n"}]
        ),
        "diff": "-x = 1\n+x = 9\n",
        "status": "proposed",
        "base_sha": None,
        **kw,
    }
    run = models.AgentRun(project_id=project.id, instruction="Do it.", **fields)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_apply_writes_and_marks_the_run_applied(client, db, make, repo):
    project = make.project("Demo", local_path=str(repo))
    run = proposed_run(db, project)
    body = client.post(f"/agent/runs/{run.id}/apply").json()
    assert body["applied"] == ["a.py"]
    assert body["stale_base"] is False
    assert (repo / "a.py").read_bytes() == b"x = 9\n"
    db.expire_all()
    assert db.get(models.AgentRun, run.id).status == "applied"


def test_apply_flags_a_base_that_has_moved(client, db, make, repo):
    project = make.project("Demo", local_path=str(repo))
    run = proposed_run(db, project, base_sha="0000000")
    body = client.post(f"/agent/runs/{run.id}/apply").json()
    assert body["stale_base"] is True
    assert "new commits" in body["note"]


def test_applying_twice_is_refused(client, db, make, repo):
    project = make.project("Demo", local_path=str(repo))
    run = proposed_run(db, project)
    client.post(f"/agent/runs/{run.id}/apply")
    again = client.post(f"/agent/runs/{run.id}/apply")
    assert again.status_code == 409
    assert "applied" in again.json()["detail"]


def test_applying_a_run_with_no_changes_is_refused(client, db, make, repo):
    project = make.project("Demo", local_path=str(repo))
    run = proposed_run(db, project, changes_json="[]")
    assert client.post(f"/agent/runs/{run.id}/apply").status_code == 409


def test_discard_leaves_the_tree_alone(client, db, make, repo):
    project = make.project("Demo", local_path=str(repo))
    run = proposed_run(db, project)
    assert client.post(f"/agent/runs/{run.id}/discard").json()["status"] == "discarded"
    assert (repo / "a.py").read_bytes() == b"x = 1\n"


def test_an_applied_run_cannot_be_discarded(client, db, make, repo):
    project = make.project("Demo", local_path=str(repo))
    run = proposed_run(db, project)
    client.post(f"/agent/runs/{run.id}/apply")
    response = client.post(f"/agent/runs/{run.id}/discard")
    assert response.status_code == 409
    assert "git" in response.json()["detail"]


def test_reading_a_run_includes_the_diff_but_the_list_does_not(client, db, make, repo):
    project = make.project("Demo", local_path=str(repo))
    run = proposed_run(db, project)
    detail = client.get(f"/agent/runs/{run.id}").json()
    assert detail["diff"] == "-x = 1\n+x = 9\n"
    assert detail["changes"][0]["path"] == "a.py"

    listed = client.get(f"/projects/{project.id}/agent/runs").json()
    assert listed[0]["file_count"] == 1
    assert "diff" not in listed[0]


def test_an_unknown_run_is_a_404(client):
    assert client.get("/agent/runs/999").status_code == 404
