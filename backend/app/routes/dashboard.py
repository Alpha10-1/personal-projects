"""Aggregations behind the Today view and the Insights page.

Everything here is read-only and computed from the same rows the rest of the
app writes, so there is nothing to keep in sync.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.enrich import enrich_projects, enrich_tasks, serialize

router = APIRouter(tags=["dashboard"])

ACTIVE_STATUSES = ("active", "planning", "idea", "on_hold")


@router.get("/dashboard/today")
def today_view(db: Session = Depends(get_db), horizon_days: int = Query(7, ge=1, le=60)):
    today = date.today()
    horizon = today + timedelta(days=horizon_days)

    open_tasks = list(
        db.execute(select(models.Task).where(models.Task.status != "done")).scalars()
    )

    overdue = [t for t in open_tasks if t.due_date and t.due_date < today]
    due_today = [t for t in open_tasks if t.due_date == today]
    upcoming = [t for t in open_tasks if t.due_date and today < t.due_date <= horizon]
    in_progress = [t for t in open_tasks if t.status == "in_progress"]
    blocked = [t for t in open_tasks if t.status == "blocked"]

    projects = list(
        db.execute(
            select(models.Project).where(
                models.Project.archived_at.is_(None),
                models.Project.status.in_(ACTIVE_STATUSES),
            )
        ).scalars()
    )
    enriched_projects = enrich_projects(db, projects)
    enriched_projects.sort(
        key=lambda pair: (-pair[1]["open_overdue"], -pair[1]["progress"])
    )

    milestones = list(
        db.execute(
            select(models.Milestone)
            .where(
                models.Milestone.status == "pending",
                models.Milestone.due_date.is_not(None),
                models.Milestone.due_date <= horizon,
            )
            .order_by(models.Milestone.due_date)
        ).scalars()
    )
    project_names = {p.id: p.name for p in db.execute(select(models.Project)).scalars()}

    week_start = today - timedelta(days=today.weekday())
    week_hours = sum(
        float(row[0] or 0)
        for row in db.execute(
            select(models.TimeLog.hours).where(models.TimeLog.work_date >= week_start)
        ).all()
    )
    today_hours = sum(
        float(row[0] or 0)
        for row in db.execute(
            select(models.TimeLog.hours).where(models.TimeLog.work_date == today)
        ).all()
    )

    def tasks_out(items):
        return serialize(schemas.TaskOut, enrich_tasks(db, sorted(
            items, key=lambda t: (t.due_date or date.max, t.id)
        )))

    return {
        "date": today.isoformat(),
        "counts": {
            "overdue": len(overdue),
            "due_today": len(due_today),
            "upcoming": len(upcoming),
            "in_progress": len(in_progress),
            "blocked": len(blocked),
            "open_total": len(open_tasks),
            "active_projects": len(projects),
        },
        "hours": {
            "today": round(today_hours, 2),
            "this_week": round(week_hours, 2),
        },
        "overdue": tasks_out(overdue),
        "due_today": tasks_out(due_today),
        "upcoming": tasks_out(upcoming),
        "in_progress": tasks_out(in_progress),
        "blocked": tasks_out(blocked),
        "projects": serialize(schemas.ProjectOut, enriched_projects),
        "milestones": [
            {
                "id": m.id,
                "project_id": m.project_id,
                "project_name": project_names.get(m.project_id),
                "title": m.title,
                "due_date": m.due_date.isoformat() if m.due_date else None,
                "overdue": bool(m.due_date and m.due_date < today),
            }
            for m in milestones
        ],
    }


@router.get("/dashboard/insights")
def insights(db: Session = Depends(get_db), days: int = Query(56, ge=7, le=365)):
    """Where the time went, and whether work is actually closing.

    The window defaults to eight weeks: long enough for a trend to mean
    something, short enough that it still describes how you work now.
    """
    today = date.today()
    start = today - timedelta(days=days - 1)
    start_dt = datetime.combine(start, time.min)

    logs = list(
        db.execute(
            select(models.TimeLog).where(models.TimeLog.work_date >= start)
        ).scalars()
    )
    project_names = {p.id: p.name for p in db.execute(select(models.Project)).scalars()}

    # Weeks are keyed by their Monday so the buckets line up with a working
    # week rather than with an arbitrary offset from today.
    def week_key(day: date) -> str:
        return (day - timedelta(days=day.weekday())).isoformat()

    weeks = []
    cursor = start - timedelta(days=start.weekday())
    while cursor <= today:
        weeks.append(cursor.isoformat())
        cursor += timedelta(days=7)

    hours_by_week_category = defaultdict(float)
    hours_by_category = defaultdict(float)
    hours_by_project = defaultdict(float)
    for log in logs:
        hours = float(log.hours or 0)
        hours_by_week_category[(week_key(log.work_date), log.category)] += hours
        hours_by_category[log.category] += hours
        hours_by_project[log.project_id] += hours

    categories = sorted(hours_by_category, key=lambda c: -hours_by_category[c])
    hours_trend = [
        {
            "week": week,
            "total": round(sum(hours_by_week_category[(week, c)] for c in categories), 2),
            **{c: round(hours_by_week_category[(week, c)], 2) for c in categories},
        }
        for week in weeks
    ]

    completed = list(
        db.execute(
            select(models.Task).where(
                models.Task.completed_at.is_not(None),
                models.Task.completed_at >= start_dt,
            )
        ).scalars()
    )
    created = list(
        db.execute(
            select(models.Task).where(models.Task.created_at.is_not(None), models.Task.created_at >= start_dt)
        ).scalars()
    )
    closed_by_week = defaultdict(int)
    opened_by_week = defaultdict(int)
    for t in completed:
        closed_by_week[week_key(t.completed_at.date())] += 1
    for t in created:
        opened_by_week[week_key(t.created_at.date())] += 1

    throughput = [
        {
            "week": week,
            "opened": opened_by_week.get(week, 0),
            "closed": closed_by_week.get(week, 0),
        }
        for week in weeks
    ]

    open_tasks = list(
        db.execute(select(models.Task).where(models.Task.status != "done")).scalars()
    )
    status_mix = defaultdict(int)
    for t in open_tasks:
        status_mix[t.status] += 1

    # Cycle time says how long a task sat before it closed -- the number that
    # usually explains a backlog better than the backlog size does.
    #
    # Clamped at zero: a task cannot take negative time, but back-dated rows
    # (imported history, or a completed_at edited by hand) can produce one, and
    # a negative median would read as nonsense on the dashboard.
    cycle_days = [
        max(0, (t.completed_at.date() - t.created_at.date()).days)
        for t in completed
        if t.created_at and t.completed_at
    ]
    cycle_days.sort()

    def percentile(values, fraction):
        if not values:
            return None
        index = min(len(values) - 1, int(round(fraction * (len(values) - 1))))
        return values[index]

    projects = list(
        db.execute(
            select(models.Project).where(models.Project.archived_at.is_(None))
        ).scalars()
    )
    project_rows = [
        {
            "id": p.id,
            "name": p.name,
            "status": p.status,
            "category": p.category,
            "progress": data["progress"],
            "hours": data["hours_logged"],
            "open_overdue": data["open_overdue"],
            "task_total": data["task_total"],
            "task_done": data["task_done"],
        }
        for p, data in enrich_projects(db, projects)
    ]

    return {
        "window": {"start": start.isoformat(), "end": today.isoformat(), "days": days},
        "categories": categories,
        "hours_trend": hours_trend,
        "hours_by_category": [
            {"category": c, "hours": round(hours_by_category[c], 2)} for c in categories
        ],
        "hours_by_project": sorted(
            [
                {
                    "project_id": pid,
                    "name": project_names.get(pid, "Unassigned") if pid else "Unassigned",
                    "hours": round(hours, 2),
                }
                for pid, hours in hours_by_project.items()
            ],
            key=lambda row: -row["hours"],
        ),
        "throughput": throughput,
        "open_status_mix": [
            {"status": status, "count": count} for status, count in sorted(status_mix.items())
        ],
        "cycle_time_days": {
            "median": percentile(cycle_days, 0.5),
            "p90": percentile(cycle_days, 0.9),
            "sample": len(cycle_days),
        },
        "totals": {
            "hours": round(sum(hours_by_category.values()), 2),
            "tasks_closed": len(completed),
            "tasks_opened": len(created),
        },
        "projects": project_rows,
    }
