"""Activity pulled in from other systems.

Read-only over the recorded facts, plus an explicit sync. Nothing here is on
a timer: a sync happens because something asked for one, so there is always
an answer to "why did this row appear now".
"""

from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import github, models, schemas
from app.db import get_db
from app.enrich import project_name_map

router = APIRouter(prefix="/activity", tags=["activity"])


def _serialize(db: Session, events):
    names = project_name_map(db, [e.project_id for e in events])
    task_ids = {e.task_id for e in events if e.task_id}
    titles = {}
    if task_ids:
        titles = {
            row[0]: row[1]
            for row in db.execute(
                select(models.Task.id, models.Task.title).where(
                    models.Task.id.in_(task_ids)
                )
            ).all()
        }
    return [
        schemas.ActivityEventOut.model_validate(event).model_copy(
            update={
                "project_name": names.get(event.project_id),
                "task_title": titles.get(event.task_id),
            }
        )
        for event in events
    ]


@router.get("", response_model=list[schemas.ActivityEventOut])
def list_activity(
    db: Session = Depends(get_db),
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    repo: Optional[str] = None,
    kind: Optional[str] = None,
    unlinked_only: bool = False,
    last_days: Optional[int] = None,
    limit: int = 200,
):
    stmt = select(models.ActivityEvent)
    if project_id is not None:
        stmt = stmt.where(models.ActivityEvent.project_id == project_id)
    if task_id is not None:
        stmt = stmt.where(models.ActivityEvent.task_id == task_id)
    if repo:
        stmt = stmt.where(models.ActivityEvent.repo == repo)
    if kind:
        stmt = stmt.where(models.ActivityEvent.kind == kind)
    if unlinked_only:
        # The review queue: activity that matched no project at all.
        stmt = stmt.where(models.ActivityEvent.project_id.is_(None))
    if last_days is not None:
        cutoff = datetime.combine(
            date.today() - timedelta(days=last_days - 1), time.min
        )
        stmt = stmt.where(models.ActivityEvent.occurred_at >= cutoff)

    stmt = stmt.order_by(
        models.ActivityEvent.occurred_at.desc(), models.ActivityEvent.id.desc()
    ).limit(max(1, min(limit, 1000)))
    return _serialize(db, list(db.execute(stmt).scalars()))


@router.get("/repos", response_model=list[str])
def list_tracked_repos(db: Session = Depends(get_db)):
    """The repos that will be synced: the ones a live project points at."""
    return github.tracked_repos(db)


@router.post("/sync", response_model=list[schemas.SyncResult])
def sync(
    db: Session = Depends(get_db),
    repo: Optional[str] = None,
    limit: int = Query(100, ge=1, le=300),
):
    """Pull new activity. Without `repo`, syncs every tracked repo.

    Safe to run repeatedly: events are keyed by the provider's own id, so a
    second run adds nothing.
    """
    repos = [repo] if repo else github.tracked_repos(db)
    if not repos:
        raise HTTPException(
            status_code=400,
            detail=(
                "No repos to sync. Set a project's `repo` field to "
                '"owner/name", or pass ?repo=owner/name.'
            ),
        )

    results = []
    for name in repos:
        try:
            results.append(github.sync_repo(db, name, limit=limit))
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return results
