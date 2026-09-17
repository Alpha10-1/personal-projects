"""Write paths for /tasks.

Two things carry most of the risk here: the foreign-key checks in
_validate_refs (SQLite does not enforce them) and the status side effects
on completed_at and blocked_reason.
"""

from conftest import TODAY, days_ago


def make_project(client, name="P"):
    return client.post("/projects", json={"name": name}).json()


def create(client, **kw):
    payload = {"title": "Write the query"}
    payload.update(kw)
    response = client.post("/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- Creation and validation ------------------------------------------------


def test_create_applies_defaults(client):
    body = create(client)

    assert body["status"] == "todo"
    assert body["priority"] == "medium"
    assert body["project_id"] is None
    assert body["completed_at"] is None
    assert body["hours_logged"] == 0.0


def test_create_with_done_status_stamps_completed_at(client):
    assert create(client, status="done")["completed_at"] is not None


def test_create_rejects_blank_title(client):
    assert client.post("/tasks", json={"title": ""}).status_code == 422


def test_create_rejects_negative_estimate(client):
    response = client.post("/tasks", json={"title": "t", "estimate_hours": -1})
    assert response.status_code == 422


def test_create_rejects_unknown_project(client):
    response = client.post("/tasks", json={"title": "t", "project_id": 999})
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown project"


def test_create_rejects_unknown_milestone(client):
    response = client.post("/tasks", json={"title": "t", "milestone_id": 999})
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown milestone"


def test_create_rejects_unknown_parent_task(client):
    response = client.post("/tasks", json={"title": "t", "parent_task_id": 999})
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown parent task"


def test_create_rejects_milestone_from_a_different_project(client):
    owner = make_project(client, "owner")
    other = make_project(client, "other")
    milestone = client.post(
        "/projects/%d/milestones" % owner["id"], json={"title": "m"}
    ).json()

    response = client.post(
        "/tasks",
        json={"title": "t", "project_id": other["id"], "milestone_id": milestone["id"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Milestone belongs to a different project"


def test_create_accepts_a_milestone_from_its_own_project(client):
    project = make_project(client)
    milestone = client.post(
        "/projects/%d/milestones" % project["id"], json={"title": "m"}
    ).json()

    body = create(client, project_id=project["id"], milestone_id=milestone["id"])

    assert body["milestone_id"] == milestone["id"]


# --- Updates ----------------------------------------------------------------


def test_patch_leaves_omitted_fields_alone(client):
    task = create(client, notes="keep me", priority="high")

    body = client.patch("/tasks/%d" % task["id"], json={"priority": "low"}).json()

    assert body["priority"] == "low"
    assert body["notes"] == "keep me"


def test_patch_to_done_stamps_completed_at_and_reopening_clears_it(client):
    task = create(client)
    url = "/tasks/%d" % task["id"]

    assert client.patch(url, json={"status": "done"}).json()["completed_at"] is not None
    assert client.patch(url, json={"status": "todo"}).json()["completed_at"] is None


def test_leaving_blocked_clears_the_blocked_reason(client):
    task = create(client, status="blocked", blocked_reason="waiting on the extract")

    body = client.patch("/tasks/%d" % task["id"], json={"status": "in_progress"}).json()

    assert body["status"] == "in_progress"
    assert body["blocked_reason"] is None


def test_staying_blocked_keeps_the_blocked_reason(client):
    task = create(client, status="blocked", blocked_reason="waiting on the extract")

    body = client.patch("/tasks/%d" % task["id"], json={"priority": "high"}).json()

    assert body["blocked_reason"] == "waiting on the extract"


def test_a_task_cannot_become_its_own_parent(client):
    task = create(client)

    response = client.patch(
        "/tasks/%d" % task["id"], json={"parent_task_id": task["id"]}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "A task cannot be its own parent"


def test_patch_revalidates_against_the_tasks_existing_project(client):
    """Moving a task to a new project must not silently keep a milestone
    belonging to the old one."""
    owner = make_project(client, "owner")
    other = make_project(client, "other")
    milestone = client.post(
        "/projects/%d/milestones" % owner["id"], json={"title": "m"}
    ).json()
    task = create(client, project_id=owner["id"], milestone_id=milestone["id"])

    response = client.patch(
        "/tasks/%d" % task["id"], json={"project_id": other["id"]}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Milestone belongs to a different project"


def test_patch_unknown_task_is_404(client):
    assert client.patch("/tasks/999", json={"priority": "low"}).status_code == 404


# --- Deletion ---------------------------------------------------------------


def test_delete_removes_subtasks_but_keeps_logged_hours(client):
    parent = create(client, title="parent")
    create(client, title="child", parent_task_id=parent["id"])
    client.post(
        "/time-logs",
        json={"work_date": TODAY.isoformat(), "hours": 2, "task_id": parent["id"]},
    )

    assert client.delete("/tasks/%d" % parent["id"]).status_code == 204

    assert client.get("/tasks").json() == []
    # The hours were really spent, so the log survives and just loses its link.
    logs = client.get("/time-logs").json()
    assert len(logs) == 1
    assert logs[0]["task_id"] is None
    assert logs[0]["hours"] == 2


def test_delete_unknown_task_is_404(client):
    assert client.delete("/tasks/999").status_code == 404


# --- Listing filters that the write paths feed ------------------------------


def test_overdue_filter_excludes_done_tasks(client):
    create(client, title="late", due_date=days_ago(3).isoformat())
    create(client, title="late_but_done", due_date=days_ago(3).isoformat(),
           status="done")

    rows = client.get("/tasks", params={"overdue": True}).json()

    assert [t["title"] for t in rows] == ["late"]


def test_subtask_counts_appear_on_the_parent(client):
    parent = create(client, title="parent")
    create(client, title="a", parent_task_id=parent["id"], status="done")
    create(client, title="b", parent_task_id=parent["id"])

    body = client.get("/tasks/%d" % parent["id"]).json()

    assert body["subtask_total"] == 2
    assert body["subtask_done"] == 1


def test_patch_title_to_null_is_rejected_not_a_server_error(client):
    task = create(client)

    assert client.patch("/tasks/%d" % task["id"], json={"title": None}).status_code == 422
