"""Derived values shared across routes.

These are computed on read rather than stored, so a task edit can never leave
a project's counters stale. The data volumes a single person generates make
that cheap, and correctness-by-construction is worth more here than caching.
"""

from datetime import date

from sqlalchemy import case, func, select

from app import models


def _count_done(status_column):
    return func.sum(case((status_column == "done", 1), else_=0))


def project_name_map(db, project_ids):
    ids = {pid for pid in project_ids if pid}
    if not ids:
        return {}
    rows = db.execute(
        select(models.Project.id, models.Project.name).where(models.Project.id.in_(ids))
    ).all()
    return {row[0]: row[1] for row in rows}


def compute_progress(project, task_total, task_done, milestone_total, milestone_done):
    """Percent complete, best available signal first.

    A manual override always wins -- it's the only way to express progress on
    work that hasn't been broken into tasks yet. Otherwise tasks, then
    milestones, then a flat read of the status.
    """
    if project.progress_override is not None:
        return project.progress_override
    if task_total:
        return round(task_done * 100 / task_total)
    if milestone_total:
        return round(milestone_done * 100 / milestone_total)
    return 100 if project.status == "done" else 0


def enrich_projects(db, projects):
    """Attach counts, hours and progress to a list of ORM projects."""
    if not projects:
        return []
    ids = [p.id for p in projects]
    today = date.today()

    task_rows = db.execute(
        select(
            models.Task.project_id,
            func.count(models.Task.id),
            _count_done(models.Task.status),
        )
        .where(models.Task.project_id.in_(ids))
        .group_by(models.Task.project_id)
    ).all()
    tasks_by_project = {r[0]: (int(r[1]), int(r[2] or 0)) for r in task_rows}

    # Overdue count and the next thing due both look only at open tasks, so
    # they come from one pass over the open ones rather than conditional sums.
    open_rows = db.execute(
        select(models.Task.project_id, models.Task.due_date)
        .where(
            models.Task.project_id.in_(ids),
            models.Task.status != "done",
            models.Task.due_date.is_not(None),
        )
    ).all()
    overdue_by_project = {}
    next_due_by_project = {}
    for project_id, due in open_rows:
        if due < today:
            overdue_by_project[project_id] = overdue_by_project.get(project_id, 0) + 1
        current = next_due_by_project.get(project_id)
        if current is None or due < current:
            next_due_by_project[project_id] = due

    ms_rows = db.execute(
        select(
            models.Milestone.project_id,
            func.count(models.Milestone.id),
            _count_done(models.Milestone.status),
        )
        .where(models.Milestone.project_id.in_(ids))
        .group_by(models.Milestone.project_id)
    ).all()
    ms_by_project = {r[0]: (int(r[1]), int(r[2] or 0)) for r in ms_rows}

    hour_rows = db.execute(
        select(models.TimeLog.project_id, func.sum(models.TimeLog.hours))
        .where(models.TimeLog.project_id.in_(ids))
        .group_by(models.TimeLog.project_id)
    ).all()
    hours_by_project = {r[0]: float(r[1] or 0) for r in hour_rows}

    out = []
    for p in projects:
        task_total, task_done = tasks_by_project.get(p.id, (0, 0))
        ms_total, ms_done = ms_by_project.get(p.id, (0, 0))
        out.append(
            (
                p,
                {
                    "task_total": task_total,
                    "task_done": task_done,
                    "milestone_total": ms_total,
                    "milestone_done": ms_done,
                    "open_overdue": overdue_by_project.get(p.id, 0),
                    "hours_logged": round(hours_by_project.get(p.id, 0.0), 2),
                    "progress": compute_progress(p, task_total, task_done, ms_total, ms_done),
                    "next_due": next_due_by_project.get(p.id),
                },
            )
        )
    return out


def enrich_tasks(db, tasks):
    """Attach project name, logged hours and subtask counts to tasks."""
    if not tasks:
        return []
    ids = [t.id for t in tasks]

    names = project_name_map(db, [t.project_id for t in tasks])

    hour_rows = db.execute(
        select(models.TimeLog.task_id, func.sum(models.TimeLog.hours))
        .where(models.TimeLog.task_id.in_(ids))
        .group_by(models.TimeLog.task_id)
    ).all()
    hours = {r[0]: float(r[1] or 0) for r in hour_rows}

    sub_rows = db.execute(
        select(
            models.Task.parent_task_id,
            func.count(models.Task.id),
            _count_done(models.Task.status),
        )
        .where(models.Task.parent_task_id.in_(ids))
        .group_by(models.Task.parent_task_id)
    ).all()
    subs = {r[0]: (int(r[1]), int(r[2] or 0)) for r in sub_rows}

    out = []
    for t in tasks:
        sub_total, sub_done = subs.get(t.id, (0, 0))
        out.append(
            (
                t,
                {
                    "project_name": names.get(t.project_id),
                    "hours_logged": round(hours.get(t.id, 0.0), 2),
                    "subtask_total": sub_total,
                    "subtask_done": sub_done,
                },
            )
        )
    return out


def serialize(schema, pairs):
    """Build response models from (orm_object, extra_fields) pairs."""
    return [schema.model_validate(obj).model_copy(update=extra) for obj, extra in pairs]
