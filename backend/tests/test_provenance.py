"""Whether a row was entered by a person or written by an agent.

The distinction only earns its keep if it is impossible to lose: an agent has
to declare itself, and everything that does not counts as human.
"""

import pytest
from conftest import TODAY

from app.deps import SOURCE_HEADER

AGENT = {SOURCE_HEADER: "agent"}


def create_calls(client):
    """Every create endpoint that records provenance, as (label, callable)."""
    return {
        "task": lambda headers: client.post(
            "/tasks", json={"title": "t"}, headers=headers
        ),
        "time_log": lambda headers: client.post(
            "/time-logs",
            json={"work_date": TODAY.isoformat(), "hours": 1},
            headers=headers,
        ),
        "note": lambda headers: client.post(
            "/notes", json={"body": "b"}, headers=headers
        ),
    }


@pytest.mark.parametrize("label", ["task", "time_log", "note"])
def test_a_write_with_no_header_counts_as_human(client, label):
    response = create_calls(client)[label]({})

    assert response.status_code == 201
    assert response.json()["source"] == "human"


@pytest.mark.parametrize("label", ["task", "time_log", "note"])
def test_a_write_declaring_itself_an_agent_is_recorded_as_one(client, label):
    response = create_calls(client)[label](AGENT)

    assert response.status_code == 201
    assert response.json()["source"] == "agent"


@pytest.mark.parametrize("label", ["task", "time_log", "note"])
def test_an_unrecognised_source_is_refused(client, label):
    response = create_calls(client)[label]({SOURCE_HEADER: "robot"})

    assert response.status_code == 400
    assert SOURCE_HEADER in response.json()["detail"]


def test_the_header_is_case_insensitive(client):
    response = client.post("/tasks", json={"title": "t"}, headers={SOURCE_HEADER: "AGENT"})

    assert response.json()["source"] == "agent"


def test_provenance_survives_into_the_listings(client):
    client.post("/tasks", json={"title": "by hand"})
    client.post("/tasks", json={"title": "by agent"}, headers=AGENT)

    rows = {t["title"]: t["source"] for t in client.get("/tasks").json()}

    assert rows == {"by hand": "human", "by agent": "agent"}


def test_an_agent_cannot_disguise_a_write_through_the_payload(client):
    """source is taken from the header, not the body, so a payload field
    claiming otherwise is ignored rather than believed."""
    response = client.post("/tasks", json={"title": "t", "source": "human"}, headers=AGENT)

    assert response.status_code == 201
    assert response.json()["source"] == "agent"


def test_updating_a_row_does_not_change_who_created_it(client):
    task = client.post("/tasks", json={"title": "t"}, headers=AGENT).json()

    updated = client.patch(f"/tasks/{task['id']}", json={"priority": "high"}).json()

    assert updated["source"] == "agent"
