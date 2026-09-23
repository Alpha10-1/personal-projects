"""Working sessions, inferred from when commits happened.

The tracker's whole downstream -- hours by week, cycle time, whether your
estimates run long -- needs logged time, and the honest position is that
nobody logs time by hand every day. What does exist is 200 commits with
timestamps on them, already ingested and sitting in `activity_events`.

A commit is a sign that work was happening, and a run of commits close
together is a sign it was continuous. That is enough to *propose* a figure,
which is all this does. Nothing is written: each session becomes a
suggestion with its evidence attached, and you accept it, edit it, or throw
it away.

**This is an estimate and is labelled as one everywhere it surfaces.** It
cannot see the two hours you spent reading before the first commit, or the
afternoon that produced nothing worth committing, and it counts a one-line
typo fix the same as an hour of work that happened to land in one commit.
What it is good at is the shape of a day you have already forgotten:
"twelve commits between 19:10 and 22:40" is a fact, and turning it into
"about 3.5 hours" is a better guess than the zero that is there now.

The arithmetic, in full:

  * commits are grouped per project per calendar day, in local time;
  * within a day, a gap longer than `GAP` starts a new session;
  * a session runs from its first commit to its last, plus `LEAD_IN` for
    the work that produced the first one;
  * the result is rounded to the nearest quarter hour, floored at
    `MIN_HOURS` and capped at `MAX_DAY_HOURS` across the day.

Every one of those is a guess with a number attached, which is why they are
constants at the top of the file rather than buried in the loop.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

# Longer than this between commits and it was two sittings, not one. Ninety
# minutes is deliberately generous: splitting a session in two costs you the
# lead-in twice, which over-counts, and the failure this is guarding against
# is counting a morning commit and an evening commit as six hours.
GAP = timedelta(minutes=90)

# Work happened before the first commit of a session. Half an hour is a
# guess; it is the single assumption most likely to be wrong, and the one
# most worth arguing with.
LEAD_IN = timedelta(minutes=30)

# A session is never proposed as less than this. A commit represents *some*
# work, and proposing six minutes is worse than proposing nothing.
MIN_HOURS = 0.5

# Nobody's inferred day is longer than this. A day that computes higher is
# a sign the clustering is wrong -- a rebase, an import, a batch of commits
# replayed from elsewhere -- and capping it is more honest than believing it.
MAX_DAY_HOURS = 12.0

ROUND_TO = 0.25


@dataclass
class Sitting:
    """One continuous run of commits, and the hours it suggests."""

    project_id: int
    work_date: date
    hours: float
    commits: int
    first_at: datetime
    last_at: datetime
    titles: list[str] = field(default_factory=list)
    lines_changed: int = 0

    @property
    def span(self) -> str:
        return f"{self.first_at:%H:%M}–{self.last_at:%H:%M}"

    def as_evidence(self) -> list[str]:
        out = [
            f"{self.commits} commit(s) between {self.span}",
            f"about {self.hours}h, including {int(LEAD_IN.total_seconds() // 60)} "
            "minutes before the first commit",
        ]
        if self.lines_changed:
            out.append(f"{self.lines_changed} line(s) changed")
        out.extend(self.titles[:5])
        return out


def quantise(hours: float) -> float:
    return round(round(hours / ROUND_TO) * ROUND_TO, 2)


def lines_in(event: models.ActivityEvent) -> int:
    if not event.file_stats:
        return 0
    try:
        payload = json.loads(event.file_stats)
    except (ValueError, TypeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    return int(payload.get("additions") or 0) + int(payload.get("deletions") or 0)


def cluster(events: list[models.ActivityEvent]) -> list[list[models.ActivityEvent]]:
    """Split one day's commits into sittings on the gaps between them."""
    runs: list[list[models.ActivityEvent]] = []
    for event in sorted(events, key=lambda e: e.occurred_at):
        if runs and event.occurred_at - runs[-1][-1].occurred_at <= GAP:
            runs[-1].append(event)
        else:
            runs.append([event])
    return runs


def sittings(
    db: Session,
    project_id: Optional[int] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> list[Sitting]:
    """Every inferred working session, most recent first.

    Only commits attributed to a project are read. An unattributed commit
    could belong anywhere, and guessing which project to bill an evening to
    is a worse error than leaving it out.
    """
    stmt = select(models.ActivityEvent).where(
        models.ActivityEvent.kind == "commit",
        models.ActivityEvent.project_id.is_not(None),
    )
    if project_id is not None:
        stmt = stmt.where(models.ActivityEvent.project_id == project_id)
    if since is not None:
        stmt = stmt.where(models.ActivityEvent.occurred_at >= datetime.combine(since, datetime.min.time()))
    if until is not None:
        stmt = stmt.where(models.ActivityEvent.occurred_at < datetime.combine(until + timedelta(days=1), datetime.min.time()))

    events = list(db.execute(stmt).scalars())

    by_day: dict[tuple[int, date], list[models.ActivityEvent]] = {}
    for event in events:
        if event.occurred_at is None:
            continue
        by_day.setdefault((event.project_id, event.occurred_at.date()), []).append(event)

    out: list[Sitting] = []
    for (pid, day), day_events in by_day.items():
        day_sittings: list[Sitting] = []
        for run in cluster(day_events):
            span = run[-1].occurred_at - run[0].occurred_at + LEAD_IN
            day_sittings.append(
                Sitting(
                    project_id=pid,
                    work_date=day,
                    hours=max(MIN_HOURS, quantise(span.total_seconds() / 3600)),
                    commits=len(run),
                    first_at=run[0].occurred_at,
                    last_at=run[-1].occurred_at,
                    titles=[(e.title or "").splitlines()[0][:100] for e in run],
                    lines_changed=sum(lines_in(e) for e in run),
                )
            )

        # The cap is applied across the day, not per sitting, and scaled
        # rather than truncated -- so a day that computes as fourteen hours
        # keeps the shape of its sittings and loses only the total.
        total = sum(s.hours for s in day_sittings)
        if total > MAX_DAY_HOURS:
            scale = MAX_DAY_HOURS / total
            for sitting in day_sittings:
                sitting.hours = max(MIN_HOURS, quantise(sitting.hours * scale))
        out.extend(day_sittings)

    out.sort(key=lambda s: (s.work_date, s.first_at), reverse=True)
    return out


def days(db: Session, project_id: Optional[int] = None, **kw) -> list[Sitting]:
    """One proposal per project per day, rather than one per sitting.

    A day is the unit a time log is kept in, and being asked to accept three
    separate rows for one Tuesday is friction with no information in it.
    """
    merged: dict[tuple[int, date], Sitting] = {}
    for sitting in sittings(db, project_id, **kw):
        key = (sitting.project_id, sitting.work_date)
        held = merged.get(key)
        if held is None:
            merged[key] = sitting
            continue
        held.hours = quantise(held.hours + sitting.hours)
        held.commits += sitting.commits
        held.first_at = min(held.first_at, sitting.first_at)
        held.last_at = max(held.last_at, sitting.last_at)
        held.lines_changed += sitting.lines_changed
        held.titles.extend(sitting.titles)
    return sorted(merged.values(), key=lambda s: s.work_date, reverse=True)


def already_logged(db: Session) -> set[tuple[int, date]]:
    """Project-days that already have hours against them.

    Checked separately from the suggestion fingerprint because the two mean
    different things: a fingerprint stops the same proposal twice, and this
    stops a proposal for an afternoon you have already written down by hand.
    """
    rows = db.execute(
        select(models.TimeLog.project_id, models.TimeLog.work_date).where(
            models.TimeLog.project_id.is_not(None)
        )
    ).all()
    return {(pid, day) for pid, day in rows}


# The proposal text, which is both what you read and what is parsed back
# when you accept it. One format, defined once, with the parser beside it.
def format_proposal(sitting: Sitting) -> str:
    return f"{sitting.hours}h on {sitting.work_date.isoformat()}"


def parse_proposal(value: str) -> tuple[float, date]:
    hours, _, day = (value or "").partition("h on ")
    return float(hours), date.fromisoformat(day.strip())
