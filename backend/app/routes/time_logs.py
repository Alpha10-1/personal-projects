from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.deps import client_source
from app.enrich import project_name_map

router = APIRouter(prefix="/time-logs", tags=["time"])


def _serialize(db: Session, logs):
    names = project_name_map(db, [log.project_id for log in logs])
    task_ids = {log.task_id for log in logs if log.task_id}
    titles = {}
    if task_ids:
        rows = db.execute(
            select(models.Task.id, models.Task.title).where(models.Task.id.in_(task_ids))
        ).all()
        titles = {r[0]: r[1] for r in rows}
    return [
        schemas.TimeLogOut.model_validate(log).model_copy(
            update={
                "project_name": names.get(log.project_id),
                "task_title": titles.get(log.task_id),
            }
        )
        for log in logs
    ]


def _resolve_project(db: Session, data: dict):
    """A log against a task belongs to that task's project.

    Filling it in here means time rolls up to the project even when it was
    entered from a task row, and the two can't drift apart.
    """
    task_id = data.get("task_id")
    if task_id:
        task = db.get(models.Task, task_id)
        if task is None:
            raise HTTPException(status_code=400, detail="Unknown task")
        if task.project_id:
            data["project_id"] = task.project_id
    elif data.get("project_id") and db.get(models.Project, data["project_id"]) is None:
        raise HTTPException(status_code=400, detail="Unknown project")
    return data


@router.get("", response_model=list[schemas.TimeLogOut])
def list_time_logs(
    db: Session = Depends(get_db),
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    category: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    last_days: Optional[int] = None,
    limit: int = 500,
):
    stmt = select(models.TimeLog)
    if project_id is not None:
        stmt = stmt.where(models.TimeLog.project_id == project_id)
    if task_id is not None:
        stmt = stmt.where(models.TimeLog.task_id == task_id)
    if category:
        stmt = stmt.where(models.TimeLog.category == category)
    if last_days is not None:
        stmt = stmt.where(
            models.TimeLog.work_date >= date.today() - timedelta(days=last_days - 1)
        )
    if date_from:
        stmt = stmt.where(models.TimeLog.work_date >= date_from)
    if date_to:
        stmt = stmt.where(models.TimeLog.work_date <= date_to)

    stmt = stmt.order_by(
        models.TimeLog.work_date.desc(), models.TimeLog.id.desc()
    ).limit(max(1, min(limit, 2000)))
    return _serialize(db, list(db.execute(stmt).scalars()))


@router.post("", response_model=schemas.TimeLogOut, status_code=201)
def create_time_log(
    payload: schemas.TimeLogCreate,
    db: Session = Depends(get_db),
    source: str = Depends(client_source),
):
    data = _resolve_project(db, payload.model_dump())
    log = models.TimeLog(**data, source=source)
    db.add(log)
    db.commit()
    db.refresh(log)
    return _serialize(db, [log])[0]


@router.patch("/{log_id}", response_model=schemas.TimeLogOut)
def update_time_log(
    log_id: int, payload: schemas.TimeLogUpdate, db: Session = Depends(get_db)
):
    log = db.get(models.TimeLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Time log not found")

    changes = payload.model_dump(exclude_unset=True)
    merged = {
        "task_id": changes.get("task_id", log.task_id),
        "project_id": changes.get("project_id", log.project_id),
    }
    resolved = _resolve_project(db, merged)
    changes["project_id"] = resolved["project_id"]

    for field, value in changes.items():
        setattr(log, field, value)

    db.commit()
    db.refresh(log)
    return _serialize(db, [log])[0]


@router.delete("/{log_id}", status_code=204)
def delete_time_log(log_id: int, db: Session = Depends(get_db)):
    log = db.get(models.TimeLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Time log not found")
    db.delete(log)
    db.commit()
