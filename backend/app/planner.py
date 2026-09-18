"""Everything known about a project, gathered before anyone plans anything.

The same split as `history.py` and `review.py`: this module computes facts.
What they mean, and what to do about them, is the model's job -- and it can
only be trusted with that if what it was given is visible here rather than
assembled inside a prompt string.

Four sources, in descending order of how much they can be trusted:

  the README      what the project says it is
  the timeline    what the commits show was actually done, and when
  the board       what is already planned, so a plan does not re-plan it
  the record      notes and feedback: what was decided, what was asked for

Scheduling is arithmetic and stays here. The model estimates effort in hours,
because that is a judgement about work; turning hours into dates is division,
and doing it in code means the schedule can be recomputed for a different
week without asking the model anything.
"""

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import history, models

README_LIMIT = 6_000
NOTE_LIMIT = 8
FEEDBACK_LIMIT = 10
OPEN_TASK_LIMIT = 40

# A working week's worth of attention on a side project, used when the caller
# does not say. Deliberately not 40: this is the time left after a job.
DEFAULT_HOURS_PER_WEEK = 10.0

# Below this, a repo is being worked on; above it, it is something you are
# coming back to. The difference changes what the first task should be.
DORMANT_DAYS = 45


def open_tasks(db: Session, project_id: int) -> list[models.Task]:
    return list(
        db.execute(
            select(models.Task)
            .where(
                models.Task.project_id == project_id,
                models.Task.status != "done",
            )
            .order_by(models.Task.created_at)
            .limit(OPEN_TASK_LIMIT)
        ).scalars()
    )


def milestones(db: Session, project_id: int) -> list[models.Milestone]:
    return list(
        db.execute(
            select(models.Milestone)
            .where(models.Milestone.project_id == project_id)
            .order_by(models.Milestone.position, models.Milestone.id)
        ).scalars()
    )


def estimate_calibration(db: Session) -> Optional[dict]:
    """How this person's past estimates compared with the hours they logged.

    Returns None when there is nothing to compare, which is the honest answer
    for a tracker with no completed estimated work in it yet -- and the prompt
    says so rather than letting the model imply its numbers are calibrated
    when nothing has ever been measured.
    """
    rows = db.execute(
        select(models.Task.id, models.Task.estimate_hours).where(
            models.Task.status == "done",
            models.Task.estimate_hours.is_not(None),
            models.Task.estimate_hours > 0,
        )
    ).all()
    if not rows:
        return None

    logged = {}
    for task_id, hours in db.execute(
        select(models.TimeLog.task_id, models.TimeLog.hours).where(
            models.TimeLog.task_id.is_not(None)
        )
    ).all():
        logged[task_id] = logged.get(task_id, 0.0) + float(hours or 0)

    pairs = [
        (float(estimate), logged[task_id])
        for task_id, estimate in rows
        if logged.get(task_id)
    ]
    if not pairs:
        return None

    ratios = sorted(actual / estimate for estimate, actual in pairs)
    middle = ratios[len(ratios) // 2]
    return {
        "tasks": len(pairs),
        "median_ratio": round(middle, 2),
        "estimated_hours": round(sum(e for e, _ in pairs), 1),
        "actual_hours": round(sum(a for _, a in pairs), 1),
    }


def cadence(timeline: dict) -> dict:
    """How this project actually gets worked on.

    Not how fast it could go -- how fast it has gone. A plan that assumes
    steady weekly progress on a repo whose whole history is three weekend
    bursts is a plan that will be wrong by Wednesday.
    """
    periods = timeline.get("periods") or []
    if not periods:
        return {"months_active": 0, "busiest_month": None, "commits_per_active_month": 0}

    counts = [p["commits"] for p in periods]
    busiest = max(periods, key=lambda p: p["commits"])
    span = timeline.get("span")
    last = span[1] if span else None
    idle = (date.today() - last.date()).days if last else None

    return {
        "months_active": len(periods),
        "commits_per_active_month": round(sum(counts) / len(counts), 1),
        "busiest_month": {"month": busiest["month"], "commits": busiest["commits"]},
        "quiet_months": [p["month"] for p in periods if p["commits"] <= 2],
        "days_since_last_commit": idle,
        "dormant": bool(idle is not None and idle > DORMANT_DAYS),
    }


def context(
    db: Session,
    project: models.Project,
    *,
    readme: Optional[str] = None,
    timeline: Optional[dict] = None,
) -> dict:
    """Everything worth knowing, as data."""
    timeline = timeline if timeline is not None else history.timeline(db, project)

    notes = list(
        db.execute(
            select(models.Note)
            .where(models.Note.project_id == project.id)
            .order_by(models.Note.pinned.desc(), models.Note.created_at.desc())
            .limit(NOTE_LIMIT)
        ).scalars()
    )
    feedback = list(
        db.execute(
            select(models.Feedback)
            .where(
                models.Feedback.project_id == project.id,
                models.Feedback.status == "open",
            )
            .order_by(models.Feedback.occurred_at.desc())
            .limit(FEEDBACK_LIMIT)
        ).scalars()
    )

    return {
        "project": project,
        "readme": (readme or "")[:README_LIMIT],
        "timeline": timeline,
        "cadence": cadence(timeline),
        "milestones": milestones(db, project.id),
        "open_tasks": open_tasks(db, project.id),
        "notes": notes,
        "feedback": feedback,
        "calibration": estimate_calibration(db),
    }


def as_text(data: dict) -> str:
    """The context as the model sees it.

    Sections are labelled by where they came from, because "the README says"
    and "the commits show" carry different weight and an answer that conflates
    them cannot be checked.
    """
    project = data["project"]
    lines = [f"PROJECT: {project.name}"]
    if project.repo:
        lines.append(f"Repository: {project.repo}")
    if project.summary:
        lines.append(f"Current summary: {project.summary}")
    if project.objective:
        lines.append(f"Stated objective: {project.objective}")
    if project.definition_of_done:
        lines.append(f"Definition of done: {project.definition_of_done}")
    lines.append(f"Status: {project.status}")

    if data["readme"]:
        lines.append(
            "\n--- THE README (what the project says about itself) ---\n"
            + data["readme"]
        )
    else:
        lines.append(
            "\n--- THE README ---\nThere is none, or it could not be read. "
            "Do not assume what it would have said."
        )

    timeline = data["timeline"]
    if timeline.get("commits"):
        lines.append(
            "\n--- THE COMMIT HISTORY (what was actually done) ---\n"
            + history.as_text(timeline, project)
        )
    else:
        lines.append(
            "\n--- THE COMMIT HISTORY ---\nNo commits are recorded for this "
            "project, so nothing here is known about what has been built."
        )

    pace = data["cadence"]
    if pace["months_active"]:
        line = (
            f"\n--- HOW IT GETS WORKED ON ---\n"
            f"{pace['months_active']} month(s) with commits, averaging "
            f"{pace['commits_per_active_month']} commits in each."
        )
        if pace.get("busiest_month"):
            line += (
                f" The busiest was {pace['busiest_month']['month']} with "
                f"{pace['busiest_month']['commits']}."
            )
        if pace.get("days_since_last_commit") is not None:
            line += f" Last commit was {pace['days_since_last_commit']} days ago"
            line += "; this project is dormant." if pace["dormant"] else "."
        lines.append(line)

    if data["milestones"]:
        lines.append("\n--- MILESTONES ALREADY SET ---")
        for milestone in data["milestones"]:
            due = f", due {milestone.due_date}" if milestone.due_date else ""
            lines.append(f"- {milestone.title}{due}")
    if data["open_tasks"]:
        lines.append("\n--- WORK ALREADY PLANNED (do not plan it again) ---")
        for task in data["open_tasks"]:
            estimate = f" [{task.estimate_hours}h]" if task.estimate_hours else ""
            lines.append(f"- [{task.status}] {task.title}{estimate}")
    else:
        lines.append("\n--- WORK ALREADY PLANNED ---\nNothing. The board is empty.")

    if data["notes"]:
        lines.append("\n--- WRITTEN RECORD (decisions, results, blockers) ---")
        for note in data["notes"]:
            body = " ".join((note.body or "").split())[:400]
            lines.append(f"- [{note.kind}] {note.title or 'untitled'}: {body}")
    if data["feedback"]:
        lines.append("\n--- OPEN FEEDBACK FROM PEOPLE ---")
        for item in data["feedback"]:
            body = " ".join((item.body or "").split())[:300]
            lines.append(f"- {item.author_login or 'someone'}: {body}")

    calibration = data["calibration"]
    if calibration:
        lines.append(
            f"\n--- HOW THIS PERSON'S ESTIMATES HAVE GONE ---\n"
            f"Across {calibration['tasks']} finished estimated task(s), actual "
            f"time was a median of {calibration['median_ratio']}x the estimate "
            f"({calibration['actual_hours']}h logged against "
            f"{calibration['estimated_hours']}h estimated). Bias your "
            f"estimates accordingly."
        )
    else:
        lines.append(
            "\n--- HOW THIS PERSON'S ESTIMATES HAVE GONE ---\n"
            "Unknown: no finished task has both an estimate and logged hours. "
            "Say that your estimates are unvalidated rather than implying they "
            "are calibrated."
        )

    return "\n".join(lines)


# --- Effort into dates -------------------------------------------------------


def schedule(
    option: dict,
    *,
    hours_per_week: float = DEFAULT_HOURS_PER_WEEK,
    start: Optional[date] = None,
) -> dict:
    """Give each milestone a target date from the work in front of it.

    Deliberately not the model's job. The model says how many hours a task is;
    how long that takes depends on how many hours a week you have, which is a
    fact about you and not about the work. Doing it here means changing 10
    hours a week to 4 is instant and costs nothing.

    Milestones are dated in plan order, each carrying the tasks assigned to it
    plus its share of the unassigned ones, so the dates are cumulative rather
    than every milestone landing on the same day.
    """
    start = start or date.today()
    rate = max(float(hours_per_week), 0.5)

    by_milestone: dict[str, float] = {}
    loose = 0.0
    for task in option.get("tasks") or []:
        hours = float(task.get("estimate_hours") or 0)
        named = (task.get("milestone") or "").strip().lower()
        if named:
            by_milestone[named] = by_milestone.get(named, 0.0) + hours
        else:
            loose += hours

    names = [(m.get("title") or "").strip() for m in option.get("milestones") or []]
    names = [n for n in names if n]
    # Unassigned work has to land somewhere or the last milestone's date is a
    # lie; spreading it evenly is the least wrong assumption available.
    share = loose / len(names) if names else 0.0

    cumulative = 0.0
    dated = []
    for milestone in option.get("milestones") or []:
        title = (milestone.get("title") or "").strip()
        hours = by_milestone.get(title.lower(), 0.0) + share
        cumulative += hours
        weeks = cumulative / rate
        dated.append(
            {
                **milestone,
                "estimated_hours": round(hours, 1),
                "cumulative_hours": round(cumulative, 1),
                "due_date": (start + timedelta(days=round(weeks * 7))).isoformat(),
            }
        )

    total = sum(float(t.get("estimate_hours") or 0) for t in option.get("tasks") or [])
    return {
        **option,
        "milestones": dated,
        "total_hours": round(total, 1),
        "hours_per_week": rate,
        "finishes": (start + timedelta(days=round(total / rate * 7))).isoformat(),
        "starts": start.isoformat(),
    }
