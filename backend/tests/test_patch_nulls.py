"""PATCH null handling across every update schema.

An update schema is all-optional so a PATCH can omit fields, which made it
possible to send an explicit null for a field backed by a NOT NULL column.
That reached the database and surfaced as a 500. Fields carrying `NoNull`
are now rejected as a 422 instead, while genuinely nullable fields still
clear as before.
"""

import pytest
from conftest import TODAY


@pytest.fixture
def rows(client):
    """One row of every kind, with the URL that patches it."""
    project = client.post("/projects", json={"name": "P"}).json()
    task = client.post("/tasks", json={"title": "T"}).json()
    milestone = client.post(
        f"/projects/{project['id']}/milestones", json={"title": "M"}
    ).json()
    log = client.post(
        "/time-logs", json={"work_date": TODAY.isoformat(), "hours": 1}
    ).json()
    note = client.post("/notes", json={"body": "B"}).json()
    link = client.post("/links", json={"title": "L", "url": "x.com"}).json()
    return {
        "project": f"/projects/{project['id']}",
        "task": f"/tasks/{task['id']}",
        "milestone": f"/milestones/{milestone['id']}",
        "time_log": f"/time-logs/{log['id']}",
        "note": f"/notes/{note['id']}",
        "link": f"/links/{link['id']}",
    }


# Every update field whose column is NOT NULL.
NON_NULLABLE = [
    ("project", "name"),
    ("project", "status"),
    ("project", "category"),
    ("project", "priority"),
    ("task", "title"),
    ("task", "status"),
    ("task", "priority"),
    ("milestone", "title"),
    ("milestone", "status"),
    ("milestone", "position"),
    ("time_log", "work_date"),
    ("time_log", "hours"),
    ("time_log", "category"),
    ("note", "body"),
    ("note", "kind"),
    ("note", "pinned"),
    ("link", "title"),
    ("link", "url"),
    ("link", "kind"),
]

# Fields that are genuinely nullable and must still clear on an explicit null.
NULLABLE = [
    ("project", "summary"),
    ("project", "target_date"),
    ("project", "progress_override"),
    ("task", "notes"),
    ("task", "due_date"),
    ("task", "estimate_hours"),
    ("task", "project_id"),
    ("milestone", "detail"),
    ("milestone", "due_date"),
    ("time_log", "note"),
    ("note", "title"),
    ("link", "note"),
]


@pytest.mark.parametrize("resource,field", NON_NULLABLE)
def test_explicit_null_is_rejected_with_422(client, rows, resource, field):
    response = client.patch(rows[resource], json={field: None})

    assert response.status_code == 422, f"{resource}.{field}"


@pytest.mark.parametrize("resource,field", NON_NULLABLE)
def test_rejection_names_the_offending_field(client, rows, resource, field):
    """The frontend shows detail[0].loc[-1], so the error has to point at the
    field rather than at the body as a whole."""
    detail = client.patch(rows[resource], json={field: None}).json()["detail"]

    assert detail[0]["loc"][-1] == field


@pytest.mark.parametrize("resource,field", NULLABLE)
def test_nullable_fields_still_clear(client, rows, resource, field):
    response = client.patch(rows[resource], json={field: None})

    assert response.status_code == 200, f"{resource}.{field}: {response.text}"
    assert response.json()[field] is None


@pytest.mark.parametrize("resource,field", NON_NULLABLE)
def test_omitting_a_guarded_field_still_leaves_it_alone(client, rows, resource, field):
    """The guard must not turn these into required fields: a PATCH that does
    not mention them has to keep working."""
    before = client.patch(rows[resource], json={}).json()

    assert before[field] is not None


def test_a_valid_value_for_a_guarded_field_still_applies(client, rows):
    assert client.patch(rows["project"], json={"priority": "high"}).json()[
        "priority"
    ] == "high"
    assert client.patch(rows["note"], json={"pinned": False}).json()["pinned"] is False
    assert client.patch(rows["milestone"], json={"position": 0}).json()["position"] == 0


def test_falsy_values_are_not_mistaken_for_null(client, rows):
    """position 0 and pinned False are real values, not omissions -- the guard
    checks for None rather than falsiness."""
    assert client.patch(rows["milestone"], json={"position": 0}).status_code == 200
    assert client.patch(rows["note"], json={"pinned": False}).status_code == 200


def test_create_still_rejects_a_null_for_a_required_field(client):
    """The guard is on the update schemas; create keeps its own requirements."""
    assert client.post("/projects", json={"name": None}).status_code == 422
    assert client.post("/tasks", json={"title": None}).status_code == 422
