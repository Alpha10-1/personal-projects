from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas, transitions
from app.db import get_db
from app.deps import client_source
from app.enrich import enrich_tasks, serialize

router = APIRouter(prefix="/tasks", tags=["tasks"])

STATUS_ORDER = {"in_progress": 0, "blocked": 1, "todo": 2, "done": 3}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _sort_key(task: models.Task):
    return (
        STATUS_ORDER.get(task.status, 9),
        task.due_date or date.max,
        PRIORITY_ORDER.get(task.priority, 9),
        -task.id,
    )


def get_task_or_404(db: Session, task_id: int) -> models.Task:
    task = db.get(models.Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _validate_refs(db: Session, project_id, milestone_id, parent_task_id, task_id=None):
    if project_id is not None and db.get(models.Project, project_id) is None:
        raise HTTPException(status_code=400, detail="Unknown project")
    if milestone_id is not None:
        milestone = db.get(models.Milestone, milestone_id)
        if milestone is None:
            raise HTTPException(status_code=400, detail="Unknown milestone")
        if project_id is not None and milestone.project_id != project_id:
            raise HTTPException(
                status_code=400, detail="Milestone belongs to a different project"
            )
    if parent_task_id is not None:
        if parent_task_id == task_id:
            raise HTTPException(status_code=400, detail="A task cannot be its own parent")
        if db.get(models.Task, parent_task_id) is None:
            raise HTTPException(status_code=400, detail="Unknown parent task")


@router.get("", response_model=list[schemas.TaskOut])
def list_tasks(
    db: Session = Depends(get_db),
    project_id: Optional[int] = None,
    milestone_id: Optional[int] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    open_only: bool = False,
    due_within_days: Optional[int] = None,
    overdue: bool = False,
    unassigned_project: bool = False,
    top_level_only: bool = False,
    q: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    stmt = select(models.Task)
    if project_id is not None:
        stmt = stmt.where(models.Task.project_id == project_id)
    if unassigned_project:
        stmt = stmt.where(models.Task.project_id.is_(None))
    if milestone_id is not None:
        stmt = stmt.where(models.Task.milestone_id == milestone_id)
    if status:
        stmt = stmt.where(models.Task.status == status)
    if priority:
        stmt = stmt.where(models.Task.priority == priority)
    if open_only:
        stmt = stmt.where(models.Task.status != "done")
    if top_level_only:
        stmt = stmt.where(models.Task.parent_task_id.is_(None))
    if overdue:
        stmt = stmt.where(
            models.Task.status != "done",
            models.Task.due_date.is_not(None),
            models.Task.due_date < date.today(),
        )
    if due_within_days is not None:
        stmt = stmt.where(
            models.Task.due_date.is_not(None),
            models.Task.due_date <= date.today() + timedelta(days=due_within_days),
        )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(models.Task.title.ilike(like))

    tasks = sorted(db.execute(stmt).scalars(), key=_sort_key)[:limit]
    return serialize(schemas.TaskOut, enrich_tasks(db, tasks))


@router.post("", response_model=schemas.TaskOut, status_code=201)
def create_task(
    payload: schemas.TaskCreate,
    db: Session = Depends(get_db),
    source: str = Depends(client_source),
):
    _validate_refs(db, payload.project_id, payload.milestone_id, payload.parent_task_id)
    task = models.Task(**payload.model_dump(), source=source)
    if task.status == "done":
        task.completed_at = models.utcnow()
    db.add(task)
    db.commit()
    db.refresh(task)
    return serialize(schemas.TaskOut, enrich_tasks(db, [task]))[0]


@router.get("/{task_id}", response_model=schemas.TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = get_task_or_404(db, task_id)
    return serialize(schemas.TaskOut, enrich_tasks(db, [task]))[0]


@router.patch("/{task_id}", response_model=schemas.TaskOut)
def update_task(task_id: int, payload: schemas.TaskUpdate, db: Session = Depends(get_db)):
    task = get_task_or_404(db, task_id)
    changes = payload.model_dump(exclude_unset=True)

    _validate_refs(
        db,
        changes.get("project_id", task.project_id),
        changes.get("milestone_id", task.milestone_id),
        changes.get("parent_task_id", task.parent_task_id),
        task_id=task_id,
    )

    if "status" in changes:
        transitions.set_task_status(task, changes.pop("status"))

    for field, value in changes.items():
        setattr(task, field, value)

    db.commit()
    db.refresh(task)
    return serialize(schemas.TaskOut, enrich_tasks(db, [task]))[0]


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = get_task_or_404(db, task_id)
    # Subtasks are part of the task, not independent items -- they go with it.
    child_ids = [
        t.id
        for t in db.execute(
            select(models.Task).where(models.Task.parent_task_id == task_id)
        ).scalars()
    ]
    going = [task_id, *child_ids]

    # Time already logged is a record of hours actually spent, so it survives
    # the task and simply loses its task link. That applies to hours logged
    # against a subtask too, not just the task being deleted.
    for log in db.execute(
        select(models.TimeLog).where(models.TimeLog.task_id.in_(going))
    ).scalars():
        log.task_id = None

    # Activity is a record of something that happened elsewhere: it outlives
    # the task it was attributed to and keeps its project link.
    for event in db.execute(
        select(models.ActivityEvent).where(models.ActivityEvent.task_id.in_(going))
    ).scalars():
        event.task_id = None

    # Only direct subtasks go. Anything nested below them is promoted to
    # top-level rather than left pointing at a row that no longer exists.
    if child_ids:
        for grandchild in db.execute(
            select(models.Task).where(models.Task.parent_task_id.in_(child_ids))
        ).scalars():
            grandchild.parent_task_id = None

    # The subtasks' own link to this task is cleared before anything is
    # deleted. The models declare no relationship(), so SQLAlchemy batches the
    # deletes into one statement and cannot know to remove a child before its
    # parent; dropping the link first makes that order irrelevant.
    for child_id in child_ids:
        db.get(models.Task, child_id).parent_task_id = None
    db.flush()

    for child_id in child_ids:
        db.delete(db.get(models.Task, child_id))
    db.delete(task)
    db.commit()
