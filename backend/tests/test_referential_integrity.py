"""Deletes with foreign keys actually enforced.

SQLite ignores foreign keys unless asked not to, so these relationships used
to be enforced only by the route code. With the pragma on, a delete that runs
in the wrong order fails loudly instead of leaving a row pointing at something
that no longer exists -- which is what these tests pin down.
"""

from conftest import TODAY
from sqlalchemy import text

from app import models


def test_foreign_keys_are_enforced_on_the_connection(db):
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_journal_mode_is_wal(db):
    assert db.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"


def no_dangling_rows(db):
    return db.execute(text("PRAGMA foreign_key_check")).fetchall()


# --- Deleting a project -----------------------------------------------------


def test_deleting_a_project_with_logged_time_on_its_tasks(client, db):
    """The original order deleted tasks before the time logs pointing at
    them."""
    project = client.post("/projects", json={"name": "P"}).json()
    task = client.post(
        "/tasks", json={"title": "t", "project_id": project["id"]}
    ).json()
    client.post(
        "/time-logs",
        json={"work_date": TODAY.isoformat(), "hours": 2, "task_id": task["id"]},
    )

    assert client.delete(f"/projects/{project['id']}").status_code == 204
    assert no_dangling_rows(db) == []


def test_deleting_a_project_clears_links_held_by_outside_rows(client, db):
    """A subtask filed under another project keeps existing, and loses only
    its link to the deleted parent."""
    doomed = client.post("/projects", json={"name": "doomed"}).json()
    keeper = client.post("/projects", json={"name": "keeper"}).json()
    parent = client.post(
        "/tasks", json={"title": "parent", "project_id": doomed["id"]}
    ).json()
    outsider = client.post(
        "/tasks",
        json={
            "title": "outsider",
            "project_id": keeper["id"],
            "parent_task_id": parent["id"],
        },
    ).json()

    assert client.delete(f"/projects/{doomed['id']}").status_code == 204

    survivor = client.get(f"/tasks/{outsider['id']}").json()
    assert survivor["title"] == "outsider"
    assert survivor["parent_task_id"] is None
    assert no_dangling_rows(db) == []


def test_deleting_a_project_with_interlinked_tasks_and_milestones(client, db):
    project = client.post("/projects", json={"name": "P"}).json()
    pid = project["id"]
    milestone = client.post(
        f"/projects/{pid}/milestones", json={"title": "m"}
    ).json()
    parent = client.post(
        "/tasks",
        json={"title": "parent", "project_id": pid, "milestone_id": milestone["id"]},
    ).json()
    client.post(
        "/tasks",
        json={"title": "child", "project_id": pid, "parent_task_id": parent["id"]},
    )

    assert client.delete(f"/projects/{pid}").status_code == 204

    assert client.get("/tasks").json() == []
    assert no_dangling_rows(db) == []


# --- Deleting a task --------------------------------------------------------


def test_deleting_a_task_keeps_hours_logged_against_its_subtasks(client, db):
    """Previously only the parent's own logs were unlinked, so a subtask's
    logs were left pointing at a deleted row."""
    parent = client.post("/tasks", json={"title": "parent"}).json()
    child = client.post(
        "/tasks", json={"title": "child", "parent_task_id": parent["id"]}
    ).json()
    client.post(
        "/time-logs",
        json={"work_date": TODAY.isoformat(), "hours": 3, "task_id": child["id"]},
    )

    assert client.delete(f"/tasks/{parent['id']}").status_code == 204

    logs = client.get("/time-logs").json()
    assert len(logs) == 1
    assert logs[0]["hours"] == 3
    assert logs[0]["task_id"] is None
    assert no_dangling_rows(db) == []


def test_deleting_a_task_promotes_nested_subtasks(client, db):
    """Only direct subtasks go with the task; anything below them becomes
    top-level rather than pointing at a deleted row."""
    parent = client.post("/tasks", json={"title": "parent"}).json()
    child = client.post(
        "/tasks", json={"title": "child", "parent_task_id": parent["id"]}
    ).json()
    grandchild = client.post(
        "/tasks", json={"title": "grandchild", "parent_task_id": child["id"]}
    ).json()

    assert client.delete(f"/tasks/{parent['id']}").status_code == 204

    remaining = {t["title"]: t for t in client.get("/tasks").json()}
    assert set(remaining) == {"grandchild"}
    assert remaining["grandchild"]["parent_task_id"] is None
    assert grandchild["id"] == remaining["grandchild"]["id"]
    assert no_dangling_rows(db) == []


def test_deleting_a_milestone_leaves_its_tasks_on_the_project(client, db):
    project = client.post("/projects", json={"name": "P"}).json()
    milestone = client.post(
        f"/projects/{project['id']}/milestones", json={"title": "m"}
    ).json()
    task = client.post(
        "/tasks",
        json={
            "title": "t",
            "project_id": project["id"],
            "milestone_id": milestone["id"],
        },
    ).json()

    assert client.delete(f"/milestones/{milestone['id']}").status_code == 204

    survivor = client.get(f"/tasks/{task['id']}").json()
    assert survivor["milestone_id"] is None
    assert survivor["project_id"] == project["id"]
    assert no_dangling_rows(db) == []


# --- Activity events ----------------------------------------------------------


def _add_activity(db, project, task=None, external_id="github:commit:x"):
    db.add(
        models.ActivityEvent(
            provider="github",
            external_id=external_id,
            kind="commit",
            repo="owner/name",
            title="some commit",
            occurred_at=models.utcnow(),
            project_id=project.id,
            task_id=task.id if task else None,
            linked_by="convention" if task else "repo",
        )
    )
    db.commit()


def test_deleting_a_task_keeps_its_activity_and_clears_the_link(client, db, make):
    """Activity is a record of something that happened elsewhere, so it
    outlives the task it was attributed to."""
    project = make.project(name="Tracker", repo="owner/name")
    task = make.task(title="Tidy", project_id=project.id)
    _add_activity(db, project, task)

    assert client.delete(f"/tasks/{task.id}").status_code == 204

    events = client.get("/activity").json()
    assert len(events) == 1
    assert events[0]["task_id"] is None
    assert events[0]["project_id"] == project.id
    assert no_dangling_rows(db) == []


def test_deleting_a_project_takes_its_activity_with_it(client, db, make):
    project = make.project(name="Tracker", repo="owner/name")
    task = make.task(title="Tidy", project_id=project.id)
    _add_activity(db, project, task)

    assert client.delete(f"/projects/{project.id}").status_code == 204

    assert client.get("/activity").json() == []
    assert no_dangling_rows(db) == []
