"""Write paths for /time-logs.

The interesting rule is _resolve_project: a log attached to a task must roll
up to that task's project, so the two can never drift apart.
"""

from conftest import TODAY, days_ago


def create(client, **kw):
    payload = {"work_date": TODAY.isoformat(), "hours": 1.5}
    payload.update(kw)
    response = client.post("/time-logs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- Validation -------------------------------------------------------------


def test_hours_must_be_positive(client):
    for value in (0, -1):
        response = client.post(
            "/time-logs", json={"work_date": TODAY.isoformat(), "hours": value}
        )
        assert response.status_code == 422, value


def test_hours_cannot_exceed_a_day(client):
    response = client.post(
        "/time-logs", json={"work_date": TODAY.isoformat(), "hours": 25}
    )
    assert response.status_code == 422


def test_work_date_is_required(client):
    assert client.post("/time-logs", json={"hours": 1}).status_code == 422


def test_unknown_category_is_rejected(client):
    response = client.post(
        "/time-logs",
        json={"work_date": TODAY.isoformat(), "hours": 1, "category": "napping"},
    )
    assert response.status_code == 422


def test_unknown_project_is_rejected(client):
    response = client.post(
        "/time-logs",
        json={"work_date": TODAY.isoformat(), "hours": 1, "project_id": 999},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown project"


def test_unknown_task_is_rejected(client):
    response = client.post(
        "/time-logs", json={"work_date": TODAY.isoformat(), "hours": 1, "task_id": 999}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown task"


# --- Project resolution -----------------------------------------------------


def test_a_log_against_a_task_inherits_that_tasks_project(client):
    project = client.post("/projects", json={"name": "Forecasting"}).json()
    task = client.post(
        "/tasks", json={"title": "t", "project_id": project["id"]}
    ).json()

    body = create(client, task_id=task["id"])

    assert body["project_id"] == project["id"]
    assert body["project_name"] == "Forecasting"
    assert body["task_title"] == "t"


def test_the_tasks_project_overrides_a_conflicting_project_id(client):
    owner = client.post("/projects", json={"name": "owner"}).json()
    other = client.post("/projects", json={"name": "other"}).json()
    task = client.post("/tasks", json={"title": "t", "project_id": owner["id"]}).json()

    body = create(client, task_id=task["id"], project_id=other["id"])

    assert body["project_id"] == owner["id"]


def test_a_log_against_a_projectless_task_keeps_no_project(client):
    task = client.post("/tasks", json={"title": "loose"}).json()

    body = create(client, task_id=task["id"])

    assert body["project_id"] is None
    assert body["task_title"] == "loose"


def test_a_log_can_belong_to_neither_a_task_nor_a_project(client):
    body = create(client)

    assert body["project_id"] is None
    assert body["task_id"] is None
    assert body["category"] == "build"


# --- Updates and deletion ---------------------------------------------------


def test_patch_updates_hours_and_leaves_the_rest(client):
    log = create(client, note="morning session", category="research")

    body = client.patch("/time-logs/%d" % log["id"], json={"hours": 3.25}).json()

    assert body["hours"] == 3.25
    assert body["note"] == "morning session"
    assert body["category"] == "research"


def test_patch_rejects_out_of_range_hours(client):
    log = create(client)

    response = client.patch("/time-logs/%d" % log["id"], json={"hours": 0})

    assert response.status_code == 422


def test_repointing_a_log_at_a_new_task_moves_its_project(client):
    first = client.post("/projects", json={"name": "first"}).json()
    second = client.post("/projects", json={"name": "second"}).json()
    task_a = client.post(
        "/tasks", json={"title": "a", "project_id": first["id"]}
    ).json()
    task_b = client.post(
        "/tasks", json={"title": "b", "project_id": second["id"]}
    ).json()
    log = create(client, task_id=task_a["id"])
    assert log["project_id"] == first["id"]

    body = client.patch(
        "/time-logs/%d" % log["id"], json={"task_id": task_b["id"]}
    ).json()

    assert body["project_id"] == second["id"]


def test_patch_unknown_log_is_404(client):
    assert client.patch("/time-logs/999", json={"hours": 1}).status_code == 404


def test_delete_removes_the_log(client):
    log = create(client)

    assert client.delete("/time-logs/%d" % log["id"]).status_code == 204
    assert client.get("/time-logs").json() == []


def test_delete_unknown_log_is_404(client):
    assert client.delete("/time-logs/999").status_code == 404


# --- Listing ----------------------------------------------------------------


def test_logs_are_listed_newest_first(client):
    create(client, work_date=days_ago(5).isoformat(), note="older")
    create(client, work_date=TODAY.isoformat(), note="newer")

    assert [row["note"] for row in client.get("/time-logs").json()] == [
        "newer",
        "older",
    ]


def test_date_range_filters_are_inclusive(client):
    create(client, work_date=days_ago(10).isoformat(), note="outside")
    create(client, work_date=days_ago(5).isoformat(), note="edge")
    create(client, work_date=TODAY.isoformat(), note="inside")

    rows = client.get(
        "/time-logs",
        params={"date_from": days_ago(5).isoformat(), "date_to": TODAY.isoformat()},
    ).json()

    assert {row["note"] for row in rows} == {"edge", "inside"}
