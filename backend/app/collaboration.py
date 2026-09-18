"""Turning git history into named people.

The join is `Person.github_login` against `ActivityEvent.actor`. That string
is the only identity git gives us, so it is what everything here hangs off.

Attribution is *stored*, not computed on read: an event carries `person_id`
once resolved. Two reasons. Someone renaming their GitHub account shouldn't
silently reassign three months of history, and a person added after their
work was ingested needs their existing events attached -- which is what
`relink` is for, rather than having every read re-guess.
"""

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models


def person_for_login(db: Session, login: Optional[str]) -> Optional[models.Person]:
    """The person owning a GitHub login, if one is recorded.

    Matched case-insensitively: GitHub logins are case-insensitive, and the
    casing in a commit payload does not always match what was typed into the
    person's record.
    """
    if not login:
        return None
    return db.execute(
        select(models.Person).where(
            func.lower(models.Person.github_login) == login.strip().lower()
        )
    ).scalar_one_or_none()


def relink(db: Session) -> dict:
    """Re-resolve every attribution from the logins currently on record.

    Runs after a person is added, edited or removed. It only ever sets
    `person_id` from `actor`/`author_login`, so it is idempotent and cannot
    invent an attribution that the logins do not support -- an event whose
    actor matches nobody is set back to null rather than left pointing at
    whoever used to own that login.
    """
    people = list(db.execute(select(models.Person)).scalars())
    by_login = {
        p.github_login.strip().lower(): p.id
        for p in people
        if p.github_login and p.github_login.strip()
    }

    events = 0
    for event in db.execute(select(models.ActivityEvent)).scalars():
        wanted = by_login.get((event.actor or "").strip().lower()) if event.actor else None
        if event.person_id != wanted:
            event.person_id = wanted
            events += 1

    notes = 0
    for item in db.execute(select(models.Feedback)).scalars():
        if item.source == "manual":
            # Entered by hand against a chosen person; there is no login to
            # re-derive it from, so leave it alone.
            continue
        wanted = (
            by_login.get((item.author_login or "").strip().lower())
            if item.author_login
            else None
        )
        if item.person_id != wanted:
            item.person_id = wanted
            notes += 1

    db.commit()
    return {"activity_relinked": events, "feedback_relinked": notes, "people": len(people)}


def stats_for_people(db: Session, person_ids: list[int]) -> dict[int, dict]:
    """Contribution and feedback counts, in three queries rather than three
    per person."""
    blank = {
        "project_count": 0,
        "contribution_count": 0,
        "feedback_open": 0,
        "last_contribution_at": None,
    }
    if not person_ids:
        return {}
    out = {pid: dict(blank) for pid in person_ids}

    for pid, count in db.execute(
        select(models.ProjectMember.person_id, func.count())
        .where(models.ProjectMember.person_id.in_(person_ids))
        .group_by(models.ProjectMember.person_id)
    ).all():
        out[pid]["project_count"] = count

    for pid, count, newest in db.execute(
        select(
            models.ActivityEvent.person_id,
            func.count(),
            func.max(models.ActivityEvent.occurred_at),
        )
        .where(models.ActivityEvent.person_id.in_(person_ids))
        .group_by(models.ActivityEvent.person_id)
    ).all():
        out[pid]["contribution_count"] = count
        out[pid]["last_contribution_at"] = newest

    for pid, count in db.execute(
        select(models.Feedback.person_id, func.count())
        .where(
            models.Feedback.person_id.in_(person_ids),
            models.Feedback.status == "open",
        )
        .group_by(models.Feedback.person_id)
    ).all():
        out[pid]["feedback_open"] = count

    return out


def project_stats_for_people(db: Session, project_id: int, person_ids: list[int]) -> dict:
    """The same counts, but only for work on one project."""
    blank = {"contribution_count": 0, "feedback_open": 0, "last_contribution_at": None}
    if not person_ids:
        return {}
    out = {pid: dict(blank) for pid in person_ids}

    for pid, count, newest in db.execute(
        select(
            models.ActivityEvent.person_id,
            func.count(),
            func.max(models.ActivityEvent.occurred_at),
        )
        .where(
            models.ActivityEvent.person_id.in_(person_ids),
            models.ActivityEvent.project_id == project_id,
        )
        .group_by(models.ActivityEvent.person_id)
    ).all():
        out[pid]["contribution_count"] = count
        out[pid]["last_contribution_at"] = newest

    for pid, count in db.execute(
        select(models.Feedback.person_id, func.count())
        .where(
            models.Feedback.person_id.in_(person_ids),
            models.Feedback.project_id == project_id,
            models.Feedback.status == "open",
        )
        .group_by(models.Feedback.person_id)
    ).all():
        out[pid]["feedback_open"] = count

    return out


def contributions(
    db: Session,
    *,
    person_id: Optional[int] = None,
    project_id: Optional[int] = None,
    limit: int = 100,
) -> list[models.ActivityEvent]:
    stmt = select(models.ActivityEvent)
    if person_id is not None:
        stmt = stmt.where(models.ActivityEvent.person_id == person_id)
    if project_id is not None:
        stmt = stmt.where(models.ActivityEvent.project_id == project_id)
    return list(
        db.execute(
            stmt.order_by(models.ActivityEvent.occurred_at.desc()).limit(limit)
        ).scalars()
    )


def unattributed_logins(db: Session) -> list[dict]:
    """Logins that appear in the git history but belong to nobody on record.

    The prompt for adding a person: these are the people already working on
    your projects who the tracker cannot name.
    """
    rows = db.execute(
        select(
            models.ActivityEvent.actor,
            func.count(),
            func.max(models.ActivityEvent.occurred_at),
        )
        .where(
            models.ActivityEvent.person_id.is_(None),
            models.ActivityEvent.actor.is_not(None),
        )
        .group_by(models.ActivityEvent.actor)
        .order_by(func.count().desc())
    ).all()
    return [
        {"login": login, "events": count, "last_seen": newest}
        for login, count, newest in rows
        if login
    ]
