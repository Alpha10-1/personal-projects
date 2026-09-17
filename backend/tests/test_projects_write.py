"""Write paths for /projects: validation, status side effects, and the
archive / duplicate / delete behaviours the UI depends on."""

import pytest

from conftest import TODAY, days_ago, days_ahead


def create(client, **kw):
    payload = {"name": "Forecasting"}
    payload.update(kw)
    response = client.post("/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- Creation ---------------------------------------------------------------


def test_create_applies_documented_defaults(client):
    body = create(client)

    assert body["status"] == "planning"
    assert body["category"] == "other"
    assert body["priority"] == "medium"
    assert body["completed_at"] is None
    assert body["archived_at"] is None
    assert body["progress"] == 0


def test_create_with_done_status_stamps_completed_at(client):
    body = create(client, status="done")

    assert body["completed_at"] is not None
    # No tasks to count, so progress falls through to the status.
    assert body["progress"] == 100


def test_create_rejects_blank_name(client):
    assert client.post("/projects", json={"name": ""}).status_code == 422


def test_create_rejects_unknown_status(client):
    response = client.post("/projects", json={"name": "P", "status": "nearly_done"})
    assert response.status_code == 422


def test_create_rejects_progress_override_outside_0_100(client):
    for value in (-1, 101):
        response = client.post(
            "/projects", json={"name": "P", "progress_override": value}
        )
        assert response.status_code == 422, value


# --- Updates ----------------------------------------------------------------


def test_patch_leaves_omitted_fields_alone(client):
    project = create(client, summary="original", priority="high")

    body = client.patch("/projects/%d" % project["id"], json={"priority": "low"}).json()

    assert body["priority"] == "low"
    assert body["summary"] == "original"


def test_patch_with_explicit_null_clears_the_field(client):
    project = create(client, summary="original")

    body = client.patch("/projects/%d" % project["id"], json={"summary": None}).json()

    assert body["summary"] is None


def test_patch_to_done_stamps_completed_at_and_reopening_clears_it(client):
    project = create(client, status="active")
    url = "/projects/%d" % project["id"]

    done = client.patch(url, json={"status": "done"}).json()
    assert done["completed_at"] is not None

    reopened = client.patch(url, json={"status": "active"}).json()
    assert reopened["completed_at"] is None


def test_patch_to_the_same_status_does_not_restamp_completed_at(client):
    project = create(client, status="done")
    first = project["completed_at"]

    body = client.patch("/projects/%d" % project["id"], json={"status": "done"}).json()

    assert body["completed_at"] == first


def test_progress_override_wins_over_task_counts(client):
    project = create(client)
    client.post("/tasks", json={"title": "a", "project_id": project["id"]})
    client.post("/tasks", json={"title": "b", "project_id": project["id"]})

    body = client.patch(
        "/projects/%d" % project["id"], json={"progress_override": 80}
    ).json()

    assert body["task_total"] == 2
    assert body["task_done"] == 0
    assert body["progress"] == 80


def test_patch_unknown_project_is_404(client):
    assert client.patch("/projects/999", json={"priority": "low"}).status_code == 404


# --- Archive / unarchive ----------------------------------------------------


def test_archive_sets_status_and_timestamp(client):
    project = create(client, status="active")

    body = client.post("/projects/%d/archive" % project["id"]).json()

    assert body["status"] == "archived"
    assert body["archived_at"] is not None


def test_unarchive_returns_unfinished_work_to_on_hold(client):
    project = create(client, status="active")
    client.post("/projects/%d/archive" % project["id"])

    body = client.post("/projects/%d/unarchive" % project["id"]).json()

    assert body["archived_at"] is None
    assert body["status"] == "on_hold"


def test_unarchive_returns_finished_work_to_done(client):
    project = create(client, status="done")
    client.post("/projects/%d/archive" % project["id"])

    body = client.post("/projects/%d/unarchive" % project["id"]).json()

    assert body["status"] == "done"


def test_archived_projects_are_hidden_from_the_default_listing(client):
    project = create(client)
    client.post("/projects/%d/archive" % project["id"])

    assert client.get("/projects").json() == []
    assert len(client.get("/projects", params={"include_archived": True}).json()) == 1


# --- Deletion ---------------------------------------------------------------


def test_delete_removes_the_project_and_all_attached_rows(client):
    project = create(client)
    pid = project["id"]
    milestone = client.post("/projects/%d/milestones" % pid, json={"title": "m"}).json()
    client.post(
        "/tasks",
        json={"title": "t", "project_id": pid, "milestone_id": milestone["id"]},
    )
    client.post(
        "/time-logs",
        json={"work_date": TODAY.isoformat(), "hours": 1, "project_id": pid},
    )
    client.post("/notes", json={"body": "n", "project_id": pid})
    client.post("/links", json={"title": "l", "url": "example.com", "project_id": pid})

    assert client.delete("/projects/%d" % pid).status_code == 204

    assert client.get("/projects/%d" % pid).status_code == 404
    assert client.get("/tasks").json() == []
    assert client.get("/time-logs").json() == []
    assert client.get("/notes").json() == []
    assert client.get("/links").json() == []
    assert client.get("/projects/%d/milestones" % pid).json() == []


def test_delete_leaves_other_projects_untouched(client):
    doomed = create(client, name="doomed")
    keeper = create(client, name="keeper")
    client.post("/tasks", json={"title": "survivor", "project_id": keeper["id"]})

    client.delete("/projects/%d" % doomed["id"])

    assert [t["title"] for t in client.get("/tasks").json()] == ["survivor"]


def test_delete_unknown_project_is_404(client):
    assert client.delete("/projects/999").status_code == 404


# --- Duplication ------------------------------------------------------------


def test_duplicate_copies_the_plan_and_resets_progress(client):
    project = create(client, name="Pipeline", status="active", tech_stack="dbt")
    pid = project["id"]
    milestone = client.post(
        "/projects/%d/milestones" % pid,
        json={"title": "Phase 1", "due_date": days_ahead(5).isoformat()},
    ).json()
    client.post(
        "/tasks",
        json={
            "title": "build",
            "project_id": pid,
            "milestone_id": milestone["id"],
            "status": "done",
            "due_date": days_ago(1).isoformat(),
        },
    )
    client.post(
        "/time-logs",
        json={"work_date": TODAY.isoformat(), "hours": 4, "project_id": pid},
    )

    clone = client.post("/projects/%d/duplicate" % pid).json()

    assert clone["name"] == "Pipeline (copy)"
    assert clone["status"] == "planning"
    assert clone["tech_stack"] == "dbt"
    # The structure comes across; the history does not.
    assert clone["task_total"] == 1
    assert clone["task_done"] == 0
    assert clone["hours_logged"] == 0.0

    clone_tasks = client.get("/tasks", params={"project_id": clone["id"]}).json()
    assert [t["status"] for t in clone_tasks] == ["todo"]
    assert clone_tasks[0]["due_date"] is None

    clone_milestones = client.get("/projects/%d/milestones" % clone["id"]).json()
    assert [m["title"] for m in clone_milestones] == ["Phase 1"]
    assert clone_milestones[0]["due_date"] is None
    # The copied task points at the copied milestone, not the original.
    assert clone_tasks[0]["milestone_id"] == clone_milestones[0]["id"]


def test_duplicate_rewires_subtasks_to_their_copied_parent(client):
    project = create(client)
    pid = project["id"]
    parent = client.post("/tasks", json={"title": "parent", "project_id": pid}).json()
    client.post(
        "/tasks",
        json={"title": "child", "project_id": pid, "parent_task_id": parent["id"]},
    )

    clone = client.post("/projects/%d/duplicate" % pid).json()

    tasks = {
        t["title"]: t
        for t in client.get("/tasks", params={"project_id": clone["id"]}).json()
    }
    assert tasks["child"]["parent_task_id"] == tasks["parent"]["id"]
    assert tasks["child"]["parent_task_id"] != parent["id"]


def test_duplicate_can_skip_tasks_and_milestones(client):
    project = create(client)
    pid = project["id"]
    client.post("/projects/%d/milestones" % pid, json={"title": "m"})
    client.post("/tasks", json={"title": "t", "project_id": pid})

    clone = client.post(
        "/projects/%d/duplicate" % pid,
        params={"copy_tasks": False, "copy_milestones": False},
    ).json()

    assert client.get("/tasks", params={"project_id": clone["id"]}).json() == []
    assert client.get("/projects/%d/milestones" % clone["id"]).json() == []


def test_duplicate_unknown_project_is_404(client):
    assert client.post("/projects/999/duplicate").status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="Known bug: ProjectUpdate.name is Optional, so an explicit null "
    "reaches a NOT NULL column and raises IntegrityError (500) instead of "
    "being rejected as a 422.",
)
def test_patch_name_to_null_is_rejected_not_a_server_error(client):
    project = create(client)

    assert client.patch("/projects/%d" % project["id"], json={"name": None}).status_code == 422
