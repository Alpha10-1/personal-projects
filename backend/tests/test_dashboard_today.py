"""Tests for GET /dashboard/today.

The endpoint's job is classification: every open task lands in the right
bucket, and nothing closed or archived leaks in.
"""

from datetime import timedelta

from conftest import TODAY, WEEK_START, at, days_ago, days_ahead


def get_today(client, **params):
    response = client.get("/dashboard/today", params=params)
    assert response.status_code == 200
    return response.json()


def titles(rows):
    return {row["title"] for row in rows}


def test_empty_database_reports_zeros(client):
    body = get_today(client)

    assert body["date"] == TODAY.isoformat()
    assert body["counts"] == {
        "overdue": 0,
        "due_today": 0,
        "upcoming": 0,
        "in_progress": 0,
        "blocked": 0,
        "open_total": 0,
        "active_projects": 0,
    }
    assert body["hours"] == {"today": 0, "this_week": 0}
    assert body["overdue"] == []
    assert body["projects"] == []
    assert body["milestones"] == []


def test_due_date_buckets_are_mutually_exclusive(client, make):
    make.task(title="late", due_date=days_ago(1))
    make.task(title="now", due_date=TODAY)
    make.task(title="soon", due_date=days_ahead(3))
    make.task(title="later", due_date=days_ahead(30))  # beyond the 7-day horizon
    make.task(title="undated")

    body = get_today(client)

    assert titles(body["overdue"]) == {"late"}
    assert titles(body["due_today"]) == {"now"}
    assert titles(body["upcoming"]) == {"soon"}
    assert body["counts"]["open_total"] == 5


def test_horizon_days_widens_the_upcoming_window(client, make):
    make.task(title="day_ten", due_date=days_ahead(10))

    assert get_today(client)["counts"]["upcoming"] == 0
    assert get_today(client, horizon_days=14)["counts"]["upcoming"] == 1


def test_done_tasks_are_excluded_even_when_overdue(client, make):
    make.task(title="finished", status="done", due_date=days_ago(5))
    make.task(title="open", status="todo", due_date=days_ago(5))

    body = get_today(client)

    assert titles(body["overdue"]) == {"open"}
    assert body["counts"]["open_total"] == 1


def test_status_buckets_count_in_progress_and_blocked(client, make):
    make.task(title="doing", status="in_progress")
    make.task(title="stuck", status="blocked", blocked_reason="waiting on data")
    make.task(title="queued", status="todo")

    body = get_today(client)

    assert titles(body["in_progress"]) == {"doing"}
    assert titles(body["blocked"]) == {"stuck"}
    assert body["counts"]["in_progress"] == 1
    assert body["counts"]["blocked"] == 1


def test_overdue_tasks_are_ordered_by_due_date(client, make):
    make.task(title="older", due_date=days_ago(9))
    make.task(title="newer", due_date=days_ago(2))

    assert [t["title"] for t in get_today(client)["overdue"]] == ["older", "newer"]


def test_only_active_unarchived_projects_are_listed(client, make):
    make.project(name="live", status="active")
    make.project(name="idea_stage", status="idea")
    make.project(name="shipped", status="done")
    make.project(name="put_away", status="active", archived_at=at(days_ago(1)))

    body = get_today(client)

    assert {p["name"] for p in body["projects"]} == {"live", "idea_stage"}
    assert body["counts"]["active_projects"] == 2


def test_hours_this_week_excludes_the_previous_week(client, make):
    make.log(work_date=TODAY, hours=3.0)
    make.log(work_date=WEEK_START, hours=2.0)
    make.log(work_date=WEEK_START - timedelta(days=1), hours=5.0)

    body = get_today(client)

    # On a Monday, TODAY and WEEK_START are the same day, so the 2.0 lands in
    # today's total too. The week total is 5.0 either way; last week's 5.0 is
    # never included.
    expected_today = 5.0 if TODAY == WEEK_START else 3.0
    assert body["hours"]["today"] == expected_today
    assert body["hours"]["this_week"] == 5.0


def test_milestones_within_horizon_are_flagged_overdue(client, make):
    project = make.project(name="Sales model")
    make.milestone(project, title="was_due", due_date=days_ago(2))
    make.milestone(project, title="coming", due_date=days_ahead(3))
    make.milestone(project, title="far_off", due_date=days_ahead(40))
    make.milestone(project, title="already_done", status="done", due_date=TODAY)
    make.milestone(project, title="undated")

    rows = {m["title"]: m for m in get_today(client)["milestones"]}

    assert set(rows) == {"was_due", "coming"}
    assert rows["was_due"]["overdue"] is True
    assert rows["coming"]["overdue"] is False
    assert rows["was_due"]["project_name"] == "Sales model"
