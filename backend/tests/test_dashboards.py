"""Dashboards, and the Power BI Service behind them.

No test here reaches Power BI: the whole point of the design is that the
manual half works with nothing configured, and the synced half is driven
through an injected fetcher.
"""

import pytest

from app import models, powerbi
from app.routes.dashboards import report_id_from_url

REPORT_URL = (
    "https://app.powerbi.com/groups/me/reports/"
    "3f2504e0-4f89-11d3-9a0c-0305e82c3301/ReportSection"
)
REPORT_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


@pytest.fixture(autouse=True)
def no_powerbi_credentials(monkeypatch):
    """Off by default, like the real thing on a fresh machine."""
    for name in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET", "PBI_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setenv("PBI_TENANT_ID", "tenant")
    monkeypatch.setenv("PBI_CLIENT_ID", "client")
    monkeypatch.setenv("PBI_CLIENT_SECRET", "secret")


def report(**kw):
    payload = {
        "external_id": REPORT_ID,
        "name": "Plant availability",
        "url": REPORT_URL,
        "workspace_id": "ws-1",
        "workspace_name": "Operations",
        "dataset_id": "ds-1",
        "dataset_name": "Plant telemetry",
        "last_refresh_at": None,
        "refresh_status": "Completed",
        "refresh_error": None,
    }
    payload.update(kw)
    return payload


# --- Off by default ----------------------------------------------------------


def test_power_bi_is_off_until_it_is_configured(client):
    body = client.get("/powerbi/status").json()

    assert body["configured"] is False
    assert body["mode"] is None
    assert "PBI_TENANT_ID" in body["missing"]
    assert "PBI_TENANT_ID" in body["reason"]


def test_syncing_without_credentials_says_why(client):
    response = client.post("/powerbi/sync")

    assert response.status_code == 503
    assert "PBI_TENANT_ID" in response.json()["detail"]


def test_a_pasted_token_is_enough_to_try_it(client, monkeypatch):
    """An escape hatch for proving the rest works before anyone raises a
    ticket for a service principal."""
    monkeypatch.setenv("PBI_ACCESS_TOKEN", "eyJ0eXAiOi-not-real")

    body = client.get("/powerbi/status").json()

    assert body["configured"] is True
    assert body["mode"] == "token"


def test_the_status_never_echoes_the_secret(client, connected, monkeypatch):
    monkeypatch.setenv("PBI_CLIENT_SECRET", "super-secret-value")

    body = client.get("/powerbi/status").json()

    assert "super-secret-value" not in str(body)
    assert body["mode"] == "service_principal"


# --- Pasting a link works with nothing configured ----------------------------


def test_a_link_can_be_added_with_power_bi_switched_off(client, make):
    project = make.project(name="Plant reporting")

    body = client.post(
        "/dashboards",
        json={"name": "Plant availability", "url": REPORT_URL, "project_id": project.id},
    ).json()

    assert body["source"] == "manual"
    assert body["project_name"] == "Plant reporting"
    # Unknown, honestly, rather than pretending it is fine.
    assert body["refresh_status"] is None


def test_a_report_url_gives_up_its_id(client):
    assert report_id_from_url(REPORT_URL) == REPORT_ID
    assert report_id_from_url("https://example.com/not-a-report") is None
    assert report_id_from_url(None) is None


def test_the_same_report_is_not_added_twice(client):
    client.post("/dashboards", json={"name": "First", "url": REPORT_URL})

    response = client.post("/dashboards", json={"name": "Second", "url": REPORT_URL})

    assert response.status_code == 409
    assert "already here as 'First'" in response.json()["detail"]


def test_a_link_without_a_report_id_is_still_allowed(client):
    """Not everything worth linking is a Power BI report."""
    first = client.post("/dashboards", json={"name": "A", "url": "https://example.com/a"})
    second = client.post("/dashboards", json={"name": "B", "url": "https://example.com/b"})

    assert first.status_code == 201
    assert second.status_code == 201


def test_dashboards_filter_by_project(client, make):
    project = make.project(name="P")
    client.post("/dashboards", json={"name": "Mine", "project_id": project.id})
    client.post("/dashboards", json={"name": "Loose"})

    mine = client.get("/dashboards", params={"project_id": project.id}).json()
    loose = client.get("/dashboards", params={"unlinked_only": True}).json()

    assert [d["name"] for d in mine] == ["Mine"]
    assert [d["name"] for d in loose] == ["Loose"]


def test_a_dashboard_must_point_at_a_real_project(client):
    response = client.post("/dashboards", json={"name": "X", "project_id": 999})

    assert response.status_code == 404


def test_deleting_a_project_takes_its_dashboards(client, db, make):
    project = make.project(name="Doomed")
    client.post("/dashboards", json={"name": "Report", "project_id": project.id})

    assert client.delete(f"/projects/{project.id}").status_code == 204

    assert db.query(models.Dashboard).count() == 0


# --- Syncing -----------------------------------------------------------------


def test_a_sync_records_what_the_service_says(client, db, connected, monkeypatch):
    monkeypatch.setattr(powerbi, "fetch_reports", lambda workspace=None: [report()])

    body = client.post("/powerbi/sync").json()

    assert body == {
        "reports_seen": 1,
        "added": 1,
        "adopted": 0,
        "updated": 0,
        "failing": 0,
    }
    row = db.query(models.Dashboard).one()
    assert row.source == "powerbi"
    assert row.workspace_name == "Operations"
    assert row.dataset_name == "Plant telemetry"


def test_a_sync_adopts_a_link_you_already_pasted(client, db, make, connected, monkeypatch):
    """The whole reason the id is pulled out of the URL: a pasted link and a
    synced report are the same report, and it should not appear twice."""
    project = make.project(name="Plant reporting")
    client.post(
        "/dashboards",
        json={
            "name": "My name for it",
            "url": REPORT_URL,
            "note": "The one Ops actually opens",
            "project_id": project.id,
        },
    )
    monkeypatch.setattr(powerbi, "fetch_reports", lambda workspace=None: [report()])

    body = client.post("/powerbi/sync").json()

    assert body["added"] == 0
    assert body["adopted"] == 1
    row = db.query(models.Dashboard).one()
    # Yours is kept; the Service's is taken.
    assert row.project_id == project.id
    assert row.note == "The one Ops actually opens"
    assert row.name == "Plant availability"
    assert row.source == "powerbi"


def test_syncing_twice_changes_nothing_new(client, db, connected, monkeypatch):
    monkeypatch.setattr(powerbi, "fetch_reports", lambda workspace=None: [report()])
    client.post("/powerbi/sync")

    again = client.post("/powerbi/sync").json()

    assert again["added"] == 0
    assert again["updated"] == 1
    assert db.query(models.Dashboard).count() == 1


def test_a_sync_keeps_the_project_link_across_runs(client, db, make, connected, monkeypatch):
    project = make.project(name="P")
    monkeypatch.setattr(powerbi, "fetch_reports", lambda workspace=None: [report()])
    client.post("/powerbi/sync")
    row = db.query(models.Dashboard).one()
    client.patch(f"/dashboards/{row.id}", json={"project_id": project.id})

    client.post("/powerbi/sync")

    db.expire_all()
    assert db.query(models.Dashboard).one().project_id == project.id


def test_a_service_failure_is_a_502(client, connected, monkeypatch):
    def broken(workspace=None):
        raise powerbi.PowerBIError("Power BI 403: service principals are not enabled.")

    monkeypatch.setattr(powerbi, "fetch_reports", broken)

    response = client.post("/powerbi/sync")

    assert response.status_code == 502
    assert "service principals" in response.json()["detail"]


# --- The finding -------------------------------------------------------------


def test_a_failed_refresh_becomes_a_finding(client, db, make):
    project = make.project(name="Plant reporting", status="active")
    db.add(
        models.Dashboard(
            name="Plant availability",
            project_id=project.id,
            source="powerbi",
            external_id=REPORT_ID,
            workspace_name="Operations",
            dataset_name="Plant telemetry",
            refresh_status="Failed",
            last_refresh_at=models.utcnow(),
        )
    )
    db.commit()

    findings = client.get("/review").json()["findings"]

    stale = [f for f in findings if f["rule"] == "dashboard_refresh_failed"]
    assert len(stale) == 1
    assert "Plant availability is showing stale data" == stale[0]["title"]
    assert "Operations" in stale[0]["evidence"]


def test_a_healthy_report_is_not_a_finding(client, db, make):
    project = make.project(name="P", status="active")
    db.add(
        models.Dashboard(
            name="Fine", project_id=project.id, source="powerbi",
            external_id=REPORT_ID, refresh_status="Completed",
        )
    )
    db.commit()

    findings = client.get("/review").json()["findings"]

    assert not any(f["rule"] == "dashboard_refresh_failed" for f in findings)


def test_a_report_nobody_linked_is_not_a_finding(client, db, make):
    """An unlinked report belongs to nobody here, so there is no project for
    the finding to be about."""
    make.project(name="P", status="active")
    db.add(
        models.Dashboard(
            name="Orphan", source="powerbi", external_id=REPORT_ID, refresh_status="Failed"
        )
    )
    db.commit()

    findings = client.get("/review").json()["findings"]

    assert not any(f["rule"] == "dashboard_refresh_failed" for f in findings)


def test_a_personal_project_s_report_is_out_of_scope(client, db, make):
    project = make.project(name="Side", status="active", workspace="personal")
    db.add(
        models.Dashboard(
            name="Side report", project_id=project.id, source="powerbi",
            external_id=REPORT_ID, refresh_status="Failed",
        )
    )
    db.commit()

    findings = client.get("/review").json()["findings"]

    assert not any(f["rule"] == "dashboard_refresh_failed" for f in findings)


def test_a_report_on_a_finished_project_is_not_nagged_about(client, db, make):
    project = make.project(name="Done", status="done", completed_at=models.utcnow())
    db.add(
        models.Dashboard(
            name="Old report", project_id=project.id, source="powerbi",
            external_id=REPORT_ID, refresh_status="Failed",
        )
    )
    db.commit()

    findings = client.get("/review").json()["findings"]

    assert not any(f["rule"] == "dashboard_refresh_failed" for f in findings)


# --- Token handling ----------------------------------------------------------


def test_asking_for_a_token_without_credentials_raises_clearly():
    with pytest.raises(powerbi.NotConfigured, match="PBI_TENANT_ID"):
        powerbi.access_token()


def test_a_pasted_token_is_used_as_is(monkeypatch):
    monkeypatch.setenv("PBI_ACCESS_TOKEN", "pasted-token")

    assert powerbi.access_token() == "pasted-token"


def test_unused_dates_parse_safely():
    assert powerbi._parse_time(None) is None
    assert powerbi._parse_time("not a date") is None
    assert powerbi._parse_time("2026-09-18T07:30:00Z").year == 2026

