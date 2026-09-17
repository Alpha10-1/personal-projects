from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
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

    tasks = sorted(db.execute(stmt).scalars(), key=_sort_key)
    return serialize(schemas.TaskOut, enrich_tasks(db, tasks))


@router.post("", response_model=schemas.TaskOut, status_code=201)
def create_task(payload: schemas.TaskCreate, db: Session = Depends(get_db)):
    _validate_refs(db, payload.project_id, payload.milestone_id, payload.parent_task_id)
    task = models.Task(**payload.model_dump())
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

    if "status" in changes and changes["status"] != task.status:
        if changes["status"] == "done":
            task.completed_at = models.utcnow()
        elif task.status == "done":
            task.completed_at = None
        # A task that is no longer blocked has no blocking reason. Leaving the
        # old one behind makes stale text look current on the board.
        if changes["status"] != "blocked":
            task.blocked_reason = None

    for field, value in changes.items():
        setattr(task, field, value)

    db.commit()
    db.refresh(task)
    return serialize(schemas.TaskOut, enrich_tasks(db, [task]))[0]


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = get_task_or_404(db, task_id)
    # Subtasks are part of the task, not independent items -- they go with it.
    for child in db.execute(
        select(models.Task).where(models.Task.parent_task_id == task_id)
    ).scalars():
        db.delete(child)
    # Time already logged is a record of hours actually spent, so it survives
    # the task and simply loses its task link.
    for log in db.execute(
        select(models.TimeLog).where(models.TimeLog.task_id == task_id)
    ).scalars():
        log.task_id = None
    db.delete(task)
    db.commit()
