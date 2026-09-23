"""The analyst's reading of the board.

Two kinds of output, deliberately separated:

  * **findings** -- observations. Read-only, computed fresh each time, never
    stored. "This has been in progress for eleven days with nothing logged."
  * **suggestions** -- proposed changes, persisted, waiting on a decision.
    Only raised where the evidence is strong enough that a specific field
    should take a specific value.

Everything here is deterministic. That is the point: a rule that fires can be
explained, tested, and turned off. The narrative -- the standup paragraph, the
judgement about what matters this week -- is the agent's job, written from
these findings rather than in place of them.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import models, transitions

# Defaults chosen to be boring rather than clever: a week is the unit people
# already think in, and anything shorter fires on a normal weekend.
STALE_DAYS = 7
BLOCKED_DAYS = 5
OVERRUN_RATIO = 1.5


@dataclass
class Finding:
    rule: str
    severity: str  # info | warn
    target_type: str
    target_id: Optional[int]
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)


def _cutoff(days: int) -> datetime:
    return datetime.combine(date.today() - timedelta(days=days), time.min)


def _last_activity(db: Session) -> dict[int, datetime]:
    rows = db.execute(
        select(
            models.ActivityEvent.task_id,
            func.max(models.ActivityEvent.occurred_at),
        )
        .where(models.ActivityEvent.task_id.is_not(None))
        .group_by(models.ActivityEvent.task_id)
    ).all()
    return {r[0]: r[1] for r in rows}


def _last_time_log(db: Session) -> dict[int, date]:
    rows = db.execute(
        select(models.TimeLog.task_id, func.max(models.TimeLog.work_date))
        .where(models.TimeLog.task_id.is_not(None))
        .group_by(models.TimeLog.task_id)
    ).all()
    return {r[0]: r[1] for r in rows}


def _hours_by_task(db: Session) -> dict[int, float]:
    rows = db.execute(
        select(models.TimeLog.task_id, func.sum(models.TimeLog.hours))
        .where(models.TimeLog.task_id.is_not(None))
        .group_by(models.TimeLog.task_id)
    ).all()
    return {r[0]: float(r[1] or 0) for r in rows}


# --- Findings ---------------------------------------------------------------


def _open_tasks(db: Session) -> list[models.Task]:
    """Open tasks the analyst is allowed to comment on.

    Personal projects are out of scope by design: a side project you touch
    every few months is not "stalled", and a review that says it is trains
    you to ignore the ones that matter. A task with no project at all is
    still in scope -- it is unfiled, not personal.
    """
    personal = select(models.Project.id).where(models.Project.workspace == "personal")
    return list(
        db.execute(
            select(models.Task).where(
                models.Task.status != "done",
                or_(
                    models.Task.project_id.is_(None),
                    models.Task.project_id.not_in(personal),
                ),
            )
        ).scalars()
    )


def find(db: Session, stale_days: int = STALE_DAYS) -> list[Finding]:
    """Everything worth a second look, newest concern first."""
    today = date.today()
    findings: list[Finding] = []

    open_tasks = _open_tasks(db)
    activity = _last_activity(db)
    logs = _last_time_log(db)
    hours = _hours_by_task(db)
    stale_before = _cutoff(stale_days)

    for task in open_tasks:
        if task.status == "in_progress":
            # The last sign of life, from either side: a commit, or an hour
            # logged. Either counts as the work being alive.
            marks = []
            if activity.get(task.id):
                marks.append(activity[task.id])
            if logs.get(task.id):
                marks.append(datetime.combine(logs[task.id], time.min))
            seen = max(marks) if marks else None

            if seen is None or seen < stale_before:
                days = (today - seen.date()).days if seen else None
                findings.append(
                    Finding(
                        rule="stale_in_progress",
                        severity="warn",
                        target_type="task",
                        target_id=task.id,
                        title=f"“{task.title}” has been in progress with nothing recorded",
                        detail=(
                            f"No commits or hours in the last {days} days."
                            if days is not None
                            else "No commits or hours have ever been recorded against it."
                        ),
                    )
                )

        if task.status == "blocked":
            since = task.updated_at or task.created_at
            if since and since < _cutoff(BLOCKED_DAYS):
                findings.append(
                    Finding(
                        rule="stale_blocker",
                        severity="warn",
                        target_type="task",
                        target_id=task.id,
                        title=(
                            f"“{task.title}” has been blocked for "
                            f"{(today - since.date()).days} days"
                        ),
                        detail=task.blocked_reason or "No reason was recorded.",
                    )
                )

        if task.due_date and task.due_date < today:
            findings.append(
                Finding(
                    rule="overdue_task",
                    severity="warn",
                    target_type="task",
                    target_id=task.id,
                    title=f"“{task.title}” is {(today - task.due_date).days} days overdue",
                    detail=f"Due {task.due_date.isoformat()}, still {task.status}.",
                )
            )

        spent = hours.get(task.id, 0.0)
        if task.estimate_hours and spent > task.estimate_hours * OVERRUN_RATIO:
            findings.append(
                Finding(
                    rule="estimate_overrun",
                    severity="info",
                    target_type="task",
                    target_id=task.id,
                    title=f"“{task.title}” has run well past its estimate",
                    detail=f"{spent:g}h logged against an estimate of {task.estimate_hours:g}h.",
                )
            )

    projects = list(
        db.execute(
            select(models.Project).where(
                models.Project.archived_at.is_(None),
                models.Project.status.in_(("active", "planning")),
                models.Project.workspace != "personal",
            )
        ).scalars()
    )
    for project in projects:
        if project.target_date and project.target_date < today:
            findings.append(
                Finding(
                    rule="project_past_target",
                    severity="warn",
                    target_type="project",
                    target_id=project.id,
                    title=f"{project.name} is past its target date",
                    detail=(
                        f"Target was {project.target_date.isoformat()} and it is "
                        f"still {project.status}."
                    ),
                )
            )

    # A report whose data stopped refreshing is reporting yesterday's numbers
    # to whoever opens it, and nothing in the tracker would otherwise say so.
    # Only reports attached to a live work project: an unlinked one belongs to
    # nobody here, and a personal one is out of scope like everything else.
    live_ids = {p.id for p in projects}
    for board in db.execute(
        select(models.Dashboard).where(
            models.Dashboard.refresh_status == "Failed",
            models.Dashboard.project_id.is_not(None),
        )
    ).scalars():
        if board.project_id not in live_ids:
            continue
        when = (
            f" Last tried {board.last_refresh_at:%Y-%m-%d}."
            if board.last_refresh_at
            else ""
        )
        findings.append(
            Finding(
                rule="dashboard_refresh_failed",
                severity="warn",
                target_type="dashboard",
                target_id=board.id,
                title=f"{board.name} is showing stale data",
                detail=f"Its last dataset refresh failed.{when}",
                evidence=[e for e in (board.workspace_name, board.dataset_name) if e],
            )
        )

    unlinked = db.execute(
        select(func.count(models.ActivityEvent.id)).where(
            models.ActivityEvent.project_id.is_(None)
        )
    ).scalar()
    if unlinked:
        findings.append(
            Finding(
                rule="unlinked_activity",
                severity="info",
                target_type="activity",
                target_id=None,
                title=f"{unlinked} activity events match no project",
                detail=(
                    "Set a project's `repo` field to attribute them, or review "
                    "them with ?unlinked_only=true."
                ),
            )
        )

    findings.extend(_hygiene(db))

    order = {"warn": 0, "info": 1}
    findings.sort(key=lambda f: (order.get(f.severity, 9), f.rule, f.target_id or 0))
    return findings


def _hygiene(db: Session) -> list[Finding]:
    """What has been committed to each repository that should not have been.

    Read out of the commit detail already stored, so this costs a query and
    nothing else. It reports and never offers to fix: rewriting history has
    to be coordinated with every clone, and a tracker is the wrong thing to
    be doing it for you.

    The wording insists on the part that is easy to get wrong -- deleting
    the file now does not remove it from history, and a key that was pushed
    is a key that must be rotated.
    """
    from pathlib import Path

    from app import hygiene

    findings: list[Finding] = []
    for report in hygiene.scan(db):
        project = db.get(models.Project, report.project_id) if report.project_id else None
        if project and project.local_path:
            try:
                hygiene.check_present(Path(project.local_path), report)
            except (OSError, ValueError):
                pass
        where = project.name if project else "unfiled commits"

        if report.secrets:
            named = ", ".join(entry.path for entry in report.secrets[:hygiene.MAX_NAMED])
            findings.append(
                Finding(
                    rule="committed_secret",
                    severity="warn",
                    target_type="project",
                    target_id=report.project_id,
                    title=f"{where} has credentials committed to its history",
                    detail=(
                        "Deleting them now does not remove them from history, "
                        "and anything that was pushed should be treated as "
                        "exposed and rotated."
                    ),
                    evidence=[
                        f"{entry.path} — first in {entry.first_sha}"
                        + (", still in the working copy" if entry.still_present else "")
                        for entry in report.secrets[: hygiene.MAX_NAMED]
                    ]
                    or [named],
                )
            )

        for entry in report.filled_templates:
            findings.append(
                Finding(
                    rule="template_has_real_values",
                    severity="warn",
                    target_type="project",
                    target_id=report.project_id,
                    title=f"{entry.path} looks like it has real values in it",
                    detail=(
                        "A template file is meant to be committed, which is "
                        "exactly why a real value left in one gets published "
                        "without anyone looking twice."
                    ),
                    evidence=entry.filled_keys[: hygiene.MAX_NAMED],
                )
            )

        habitual = [e for e in report.noise if e.commits >= hygiene.HABIT]
        if habitual:
            worst = habitual[0]
            findings.append(
                Finding(
                    rule="committed_build_output",
                    severity="info",
                    target_type="project",
                    target_id=report.project_id,
                    title=(
                        f"{where} commits build output — "
                        f"{len(report.noise)} such path(s)"
                    ),
                    detail=(
                        f"{worst.path} has been committed in {worst.commits} "
                        "commits. That is a missing .gitignore entry rather "
                        "than anything wrong with the code."
                    ),
                    evidence=[
                        f"{entry.path} — {entry.commits} commits"
                        for entry in habitual[: hygiene.MAX_NAMED]
                    ],
                )
            )

        # Coverage, not a finding about the repository: a scan over commits
        # whose file lists were never fetched has not looked at them, and
        # saying nothing would read as saying they are clean.
        if report.commits_without_detail and report.project_id:
            findings.append(
                Finding(
                    rule="hygiene_not_fully_scanned",
                    severity="info",
                    target_type="project",
                    target_id=report.project_id,
                    title=(
                        f"{report.commits_without_detail} of "
                        f"{report.commits_scanned} commits in {where} were not "
                        "checked for committed secrets"
                    ),
                    detail=(
                        "Their file lists have not been fetched. Run a deep "
                        "sync from the Repo tab to include them."
                    ),
                )
            )

    return findings


# --- Suggestions ------------------------------------------------------------


def _fingerprint(rule: str, target_type: str, target_id: int, proposed: str) -> str:
    return f"{rule}:{target_type}:{target_id}:{proposed}"


def _merged_pr_for(db: Session, task_id: int) -> Optional[models.ActivityEvent]:
    """A merged pull request that names this task, if there is one.

    `merged_at` was never given a column, so it is read back out of the stored
    payload -- which is exactly what keeping the raw response was for.
    """
    events = db.execute(
        select(models.ActivityEvent).where(
            models.ActivityEvent.task_id == task_id,
            models.ActivityEvent.kind == "pull_request",
        )
    ).scalars()
    for event in events:
        try:
            if (json.loads(event.raw or "{}")).get("merged_at"):
                return event
        except ValueError:
            continue
    return None


def propose(db: Session) -> list[models.Suggestion]:
    """Raise suggestions for anything the evidence clearly supports.

    Idempotent, and respects a dismissal: the fingerprint of a dismissed
    suggestion is still taken, so the same proposal is never made twice.
    """
    existing = set(db.execute(select(models.Suggestion.fingerprint)).scalars())
    created: list[models.Suggestion] = []

    def add(rule, task, field_name, proposed, rationale, evidence):
        mark = _fingerprint(rule, "task", task.id, proposed)
        if mark in existing:
            return
        existing.add(mark)
        suggestion = models.Suggestion(
            rule=rule,
            fingerprint=mark,
            target_type="task",
            target_id=task.id,
            field=field_name,
            current_value=getattr(task, field_name),
            proposed_value=proposed,
            rationale=rationale,
            evidence=json.dumps(evidence),
        )
        db.add(suggestion)
        created.append(suggestion)

    open_tasks = _open_tasks(db)

    for task in open_tasks:
        merged = _merged_pr_for(db, task.id)
        if merged is not None:
            add(
                "merged_pr_suggests_done",
                task,
                "status",
                "done",
                (
                    "A pull request naming this task has been merged, which "
                    "usually means the work landed."
                ),
                [f"{merged.title} ({merged.url})"],
            )
            continue

        if task.status == "todo":
            events = list(
                db.execute(
                    select(models.ActivityEvent)
                    .where(models.ActivityEvent.task_id == task.id)
                    .order_by(models.ActivityEvent.occurred_at.desc())
                    .limit(3)
                ).scalars()
            )
            if events:
                add(
                    "activity_suggests_started",
                    task,
                    "status",
                    "in_progress",
                    (
                        "There are commits against this task but it is still "
                        "marked as not started."
                    ),
                    [f"{e.title} ({e.url})" for e in events],
                )

    created.extend(_propose_time(db, existing))

    db.commit()
    return created


# How far back to offer to fill in. A fortnight is long enough to catch the
# week you meant to write up and never did, and short enough that turning
# this on does not produce sixty rows to read.
TIME_LOOKBACK_DAYS = 14


def _propose_time(db: Session, existing: set[str]) -> list[models.Suggestion]:
    """Offer an hours figure for each day that has commits and no time log.

    Proposed, never written, and described as an estimate in the rationale,
    because it is one: it is read off commit timestamps and cannot see the
    reading, the thinking, or the afternoon that produced nothing.

    Days that already have hours logged are skipped entirely. A figure you
    entered by hand is the better number, and offering to add to it would
    quietly double-count the week.
    """
    from app import sessions

    logged = sessions.already_logged(db)
    cutoff = date.today() - timedelta(days=TIME_LOOKBACK_DAYS)
    created: list[models.Suggestion] = []

    for sitting in sessions.days(db, since=cutoff):
        if (sitting.project_id, sitting.work_date) in logged:
            continue
        proposed = sessions.format_proposal(sitting)
        mark = _fingerprint(
            "time_from_commits", "project", sitting.project_id, proposed
        )
        if mark in existing:
            continue
        existing.add(mark)
        suggestion = models.Suggestion(
            rule="time_from_commits",
            fingerprint=mark,
            target_type="project",
            target_id=sitting.project_id,
            field="time_log",
            current_value=None,
            proposed_value=proposed,
            rationale=(
                f"You committed {sitting.commits} time(s) between "
                f"{sitting.span} and logged no hours that day. This is an "
                "estimate read off the commit times — it cannot see reading, "
                "thinking, or work that produced no commit, so treat it as a "
                "starting figure rather than a measurement."
            ),
            evidence=json.dumps(sitting.as_evidence()),
        )
        db.add(suggestion)
        created.append(suggestion)

    return created


def apply(db: Session, suggestion: models.Suggestion) -> None:
    """Carry out an accepted suggestion.

    Task status goes through the shared transition rather than being written
    directly, so an accepted suggestion has exactly the same side effects as
    the same change made by hand.
    """
    if suggestion.target_type == "project" and suggestion.field == "summary":
        project = db.get(models.Project, suggestion.target_id)
        if project is None:
            raise LookupError(f"Project {suggestion.target_id} no longer exists")
        # Routed through a suggestion rather than written by the digest that
        # produced it: a summary is prose you wrote, and overwriting it
        # because a model had a better phrasing is the one thing this system
        # is built not to do.
        project.summary = suggestion.proposed_value
        return

    if suggestion.target_type == "project" and suggestion.field == "milestone":
        project = db.get(models.Project, suggestion.target_id)
        if project is None:
            raise LookupError(f"Project {suggestion.target_id} no longer exists")
        # Appended rather than slotted in: the model proposed an order, but
        # where a checkpoint really belongs is a judgement, and reordering
        # someone's roadmap on their behalf is not what accepting a
        # suggestion should mean.
        last = db.execute(
            select(models.Milestone.position)
            .where(models.Milestone.project_id == project.id)
            .order_by(models.Milestone.position.desc())
            .limit(1)
        ).scalar()
        db.add(
            models.Milestone(
                project_id=project.id,
                title=suggestion.proposed_value,
                detail=suggestion.rationale,
                status="pending",
                position=(last or 0) + 1,
            )
        )
        return

    if suggestion.target_type == "project" and suggestion.field == "task":
        project = db.get(models.Project, suggestion.target_id)
        if project is None:
            raise LookupError(f"Project {suggestion.target_id} no longer exists")
        # A gap becomes work, which is the only sensible thing to do with
        # "this is missing". Marked `agent` like anything else the system
        # wrote, so the board never blurs what you decided with what was
        # proposed to you.
        db.add(
            models.Task(
                project_id=project.id,
                title=suggestion.proposed_value,
                notes=suggestion.rationale,
                status="todo",
                source="agent",
            )
        )
        return

    if suggestion.target_type == "project" and suggestion.field == "time_log":
        from app import sessions

        project = db.get(models.Project, suggestion.target_id)
        if project is None:
            raise LookupError(f"Project {suggestion.target_id} no longer exists")
        hours, work_date = sessions.parse_proposal(suggestion.proposed_value)
        # Marked `agent` so the ledger never blurs an hour you measured with
        # one that was inferred from a commit time. Everything downstream --
        # insights, estimate calibration -- can then say which it is reading.
        db.add(
            models.TimeLog(
                project_id=project.id,
                work_date=work_date,
                hours=hours,
                category="build",
                note=suggestion.rationale,
                source="agent",
            )
        )
        return

    if suggestion.target_type != "task" or suggestion.field != "status":
        raise ValueError(
            f"Don't know how to apply {suggestion.target_type}.{suggestion.field}"
        )

    task = db.get(models.Task, suggestion.target_id)
    if task is None:
        raise LookupError(f"Task {suggestion.target_id} no longer exists")

    transitions.set_task_status(task, suggestion.proposed_value)


def as_dict(finding: Finding) -> dict[str, Any]:
    return asdict(finding)
