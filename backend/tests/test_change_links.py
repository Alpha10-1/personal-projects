"""Attaching a code change to the work it belongs to.

Two halves. The guess -- which open task an edit is probably for, read off
the file path and the note -- and the link itself, which is only ever set
by a person choosing one.
"""

import subprocess

import pytest

from app import board, models


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    write(root / "powerbi.py", "def refresh():\n    return None\n")
    write(root / "schemas.py", "class Payload:\n    pass\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@e.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


@pytest.fixture
def project(make, repo):
    return make.project("Demo", local_path=str(repo))


# --- the guess ------------------------------------------------------------


def test_a_path_matching_a_task_is_offered(db, project, make):
    task = make.task("Power BI delegated sign-in", project_id=project.id)
    found = board.likely_tasks(db, project, "backend/app/powerbi.py")
    assert [row["task_id"] for row in found] == [task.id]
    # Reported as the title's words, which is what explains the match.
    assert found[0]["matched"] == ["bi", "power"]


def test_the_note_is_read_as_well_as_the_path(db, project, make):
    task = make.task("Rotate the Anthropic API key", project_id=project.id)
    assert board.likely_tasks(db, project, "backend/app/ai.py") == []
    found = board.likely_tasks(db, project, "backend/app/ai.py", "swap the anthropic key")
    assert [row["task_id"] for row in found] == [task.id]


def test_an_unrelated_file_matches_nothing(db, project, make):
    make.task("Power BI delegated sign-in", project_id=project.id)
    assert board.likely_tasks(db, project, "src/components/ui.js") == []


def test_directory_names_alone_do_not_match(db, project, make):
    """Otherwise every change under src/ ties itself to every task."""
    make.task("Rework the components", project_id=project.id)
    assert board.likely_tasks(db, project, "src/components/Shell.js") == []


def test_finished_work_is_not_offered(db, project, make):
    """Attaching an edit to a done task is nearly always the wrong match."""
    make.task("Power BI delegated sign-in", project_id=project.id, status="done")
    assert board.likely_tasks(db, project, "backend/app/powerbi.py") == []


def test_another_project_is_not_offered(db, project, make):
    other = make.project("Other")
    make.task("Power BI delegated sign-in", project_id=other.id)
    assert board.likely_tasks(db, project, "backend/app/powerbi.py") == []


def test_the_best_match_comes_first(db, project, make):
    make.task("Power BI sign-in and refresh", project_id=project.id)
    exact = make.task("Power BI", project_id=project.id)
    found = board.likely_tasks(db, project, "powerbi.py", "power bi")
    assert found[0]["task_id"] == exact.id


def test_the_guess_route_answers_with_what_it_matched(client, db, project, make):
    make.task("Power BI delegated sign-in", project_id=project.id)
    body = client.get(
        f"/projects/{project.id}/code/likely-task", params={"path": "powerbi.py"}
    ).json()
    assert body["candidates"][0]["matched"] == ["bi", "power"]


# --- the link -------------------------------------------------------------


def raise_change(client, project, task_id=None):
    return client.post(
        f"/projects/{project.id}/code/changes",
        json={
            "path": "schemas.py",
            "content": "class Payload:\n    ok = True\n",
            "note": "add a field",
            **({"task_id": task_id} if task_id is not None else {}),
        },
    )


def test_a_change_can_be_raised_against_a_task(client, db, project, make):
    task = make.task("Add a field", project_id=project.id)
    body = raise_change(client, project, task.id).json()
    assert body["task_id"] == task.id
    assert body["task_title"] == "Add a field"


def test_a_change_belongs_to_nothing_by_default(client, db, project):
    """Most edits do not belong to a task, and that must stay easy."""
    body = raise_change(client, project).json()
    assert body["task_id"] is None


def test_a_task_from_another_project_is_refused(client, db, project, make):
    other = make.project("Other")
    task = make.task("Elsewhere", project_id=other.id)
    response = raise_change(client, project, task.id)
    assert response.status_code == 400
    assert "not on this project" in response.json()["detail"]


def test_the_link_can_be_set_afterwards(client, db, project, make):
    """Which work an edit was for is often only obvious later."""
    change = raise_change(client, project).json()
    task = make.task("Add a field", project_id=project.id)

    body = client.post(
        f"/code/changes/{change['id']}/task", json={"task_id": task.id}
    ).json()
    assert body["task_id"] == task.id


def test_the_link_can_be_removed(client, db, project, make):
    task = make.task("Add a field", project_id=project.id)
    change = raise_change(client, project, task.id).json()

    body = client.post(f"/code/changes/{change['id']}/task", json={"task_id": None}).json()
    assert body["task_id"] is None


def test_changes_can_be_listed_for_a_task(client, db, project, make):
    task = make.task("Add a field", project_id=project.id)
    raise_change(client, project, task.id)

    rows = client.get(f"/tasks/{task.id}/code/changes").json()
    assert [row["path"] for row in rows] == ["schemas.py"]


def test_a_task_with_no_changes_answers_with_an_empty_list(client, db, project, make):
    task = make.task("Nothing yet", project_id=project.id)
    assert client.get(f"/tasks/{task.id}/code/changes").json() == []


def test_the_change_list_can_be_filtered_by_task(client, db, project, make):
    task = make.task("Add a field", project_id=project.id)
    raise_change(client, project, task.id)

    rows = client.get(
        f"/projects/{project.id}/code/changes", params={"task_id": task.id}
    ).json()
    assert len(rows) == 1
    assert client.get(
        f"/projects/{project.id}/code/changes", params={"task_id": task.id + 99}
    ).json() == []
