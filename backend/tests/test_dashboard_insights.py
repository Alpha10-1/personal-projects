"""Tests for GET /dashboard/insights.

These aggregations fail quietly -- a wrong median or a misfiled week still
renders as a plausible number -- so the assertions pin exact values rather
than just shapes.
"""

from datetime import date, timedelta

from conftest import TODAY, WEEK_START, at, days_ago


def get_insights(client, **params):
    response = client.get("/dashboard/insights", params=params)
    assert response.status_code == 200
    return response.json()


def test_empty_database_reports_empty_aggregates(client):
    body = get_insights(client)

    assert body["categories"] == []
    assert body["hours_by_category"] == []
    assert body["hours_by_project"] == []
    assert body["totals"] == {"hours": 0, "tasks_closed": 0, "tasks_opened": 0}
    assert body["cycle_time_days"] == {"median": None, "p90": None, "sample": 0}
    # The trend still spans the window, so an empty chart has an x-axis.
    assert body["hours_trend"] != []
    assert all(week["total"] == 0 for week in body["hours_trend"])


def test_window_reflects_the_days_parameter(client):
    body = get_insights(client, days=14)

    assert body["window"] == {
        "start": (TODAY - timedelta(days=13)).isoformat(),
        "end": TODAY.isoformat(),
        "days": 14,
    }


def test_hours_are_totalled_by_category_and_sorted_by_size(client, make):
    make.log(work_date=TODAY, hours=1.0, category="research")
    make.log(work_date=TODAY, hours=4.0, category="build")
    make.log(work_date=days_ago(2), hours=2.5, category="build")

    body = get_insights(client)

    assert body["categories"] == ["build", "research"]
    assert body["hours_by_category"] == [
        {"category": "build", "hours": 6.5},
        {"category": "research", "hours": 1.0},
    ]
    assert body["totals"]["hours"] == 7.5


def test_logs_outside_the_window_are_ignored(client, make):
    make.log(work_date=TODAY, hours=2.0, category="build")
    make.log(work_date=days_ago(100), hours=90.0, category="build")

    body = get_insights(client, days=14)

    assert body["totals"]["hours"] == 2.0


def test_hours_trend_buckets_by_monday_and_totals_match_categories(client, make):
    make.log(work_date=WEEK_START, hours=3.0, category="build")
    make.log(work_date=WEEK_START, hours=1.5, category="admin")
    make.log(work_date=WEEK_START - timedelta(days=7), hours=2.0, category="build")

    body = get_insights(client, days=28)
    trend = {week["week"]: week for week in body["hours_trend"]}

    # Every bucket key is a Monday, and the buckets run week by week.
    weeks = [date.fromisoformat(w["week"]) for w in body["hours_trend"]]
    assert all(w.weekday() == 0 for w in weeks)
    assert weeks == sorted(weeks)
    assert all(b - a == timedelta(days=7) for a, b in zip(weeks, weeks[1:]))

    this_week = trend[WEEK_START.isoformat()]
    assert this_week["build"] == 3.0
    assert this_week["admin"] == 1.5
    assert this_week["total"] == 4.5

    last_week = trend[(WEEK_START - timedelta(days=7)).isoformat()]
    assert last_week["total"] == 2.0
    assert last_week["admin"] == 0


def test_unassigned_hours_are_labelled_rather_than_dropped(client, make):
    project = make.project(name="Forecasting")
    make.log(work_date=TODAY, hours=2.0, project_id=project.id)
    make.log(work_date=TODAY, hours=5.0)  # no project

    body = get_insights(client)

    # Sorted by hours descending, so the unassigned block leads here.
    assert body["hours_by_project"] == [
        {"project_id": None, "name": "Unassigned", "hours": 5.0},
        {"project_id": project.id, "name": "Forecasting", "hours": 2.0},
    ]


def test_throughput_counts_opened_and_closed_per_week(client, make):
    make.task(title="opened_and_closed", created_at=at(WEEK_START), completed_at=at(TODAY))
    make.task(title="still_open", created_at=at(WEEK_START))
    make.task(
        title="closed_last_week",
        created_at=at(WEEK_START - timedelta(days=10)),
        completed_at=at(WEEK_START - timedelta(days=7)),
    )

    body = get_insights(client, days=28)
    rows = {week["week"]: week for week in body["throughput"]}

    assert rows[WEEK_START.isoformat()]["opened"] == 2
    assert rows[WEEK_START.isoformat()]["closed"] == 1
    assert rows[(WEEK_START - timedelta(days=7)).isoformat()]["closed"] == 1
    assert body["totals"]["tasks_closed"] == 2
    assert body["totals"]["tasks_opened"] == 3


def test_cycle_time_percentiles(client, make):
    # Ages of 0, 1, 2, 3 and 10 days, all closed inside the window.
    for age in (0, 1, 2, 3, 10):
        make.task(
            title=f"aged_{age}",
            created_at=at(days_ago(age)),
            completed_at=at(TODAY),
        )

    body = get_insights(client, days=28)

    assert body["cycle_time_days"]["sample"] == 5
    assert body["cycle_time_days"]["median"] == 2
    assert body["cycle_time_days"]["p90"] == 10


def test_backdated_completion_clamps_cycle_time_to_zero(client, make):
    """A hand-edited completed_at can land before created_at; the dashboard
    documents that it clamps rather than showing a negative age."""
    make.task(
        title="backdated",
        created_at=at(TODAY),
        completed_at=at(days_ago(4)),
    )

    body = get_insights(client, days=28)

    assert body["cycle_time_days"]["sample"] == 1
    assert body["cycle_time_days"]["median"] == 0


def test_open_status_mix_excludes_done(client, make):
    make.task(title="a", status="todo")
    make.task(title="b", status="todo")
    make.task(title="c", status="blocked")
    make.task(title="d", status="done", completed_at=at(TODAY))

    body = get_insights(client)

    assert body["open_status_mix"] == [
        {"status": "blocked", "count": 1},
        {"status": "todo", "count": 2},
    ]


def test_project_rows_carry_task_derived_progress(client, make):
    project = make.project(name="Pipeline", status="active")
    make.task(title="t1", project_id=project.id, status="done", completed_at=at(TODAY))
    make.task(title="t2", project_id=project.id, status="todo")
    make.log(work_date=TODAY, hours=2.5, project_id=project.id)

    row = get_insights(client)["projects"][0]

    assert row["name"] == "Pipeline"
    assert row["task_total"] == 2
    assert row["task_done"] == 1
    assert row["progress"] == 50
    assert row["hours"] == 2.5


def test_archived_projects_are_excluded_from_project_rows(client, make):
    make.project(name="visible", status="active")
    make.project(name="archived", status="active", archived_at=at(days_ago(1)))

    body = get_insights(client)

    assert [p["name"] for p in body["projects"]] == ["visible"]
