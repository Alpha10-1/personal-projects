from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db

router = APIRouter(tags=["milestones"])


def _counts(db: Session, milestone_ids):
    if not milestone_ids:
        return {}
    rows = db.execute(
        select(
            models.Task.milestone_id,
            func.count(models.Task.id),
            func.sum(case((models.Task.status == "done", 1), else_=0)),
        )
        .where(models.Task.milestone_id.in_(milestone_ids))
        .group_by(models.Task.milestone_id)
    ).all()
    return {r[0]: (int(r[1]), int(r[2] or 0)) for r in rows}


def _serialize(db: Session, milestones):
    counts = _counts(db, [m.id for m in milestones])
    out = []
    for m in milestones:
        total, done = counts.get(m.id, (0, 0))
        out.append(
            schemas.MilestoneOut.model_validate(m).model_copy(
                update={"task_total": total, "task_done": done}
            )
        )
    return out


@router.get("/projects/{project_id}/milestones", response_model=list[schemas.MilestoneOut])
def list_milestones(project_id: int, db: Session = Depends(get_db)):
    milestones = list(
        db.execute(
            select(models.Milestone)
            .where(models.Milestone.project_id == project_id)
            .order_by(models.Milestone.position, models.Milestone.id)
        ).scalars()
    )
    return _serialize(db, milestones)


@router.post(
    "/projects/{project_id}/milestones",
    response_model=schemas.MilestoneOut,
    status_code=201,
)
def create_milestone(
    project_id: int, payload: schemas.MilestoneCreate, db: Session = Depends(get_db)
):
    if db.get(models.Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    milestone = models.Milestone(project_id=project_id, **payload.model_dump())
    if not payload.position:
        # Default to the end of the list rather than position 0, so adding a
        # milestone never silently reorders the ones already there.
        highest = db.execute(
            select(func.max(models.Milestone.position)).where(
                models.Milestone.project_id == project_id
            )
        ).scalar()
        milestone.position = (highest or 0) + 1
    if milestone.status == "done":
        milestone.completed_at = models.utcnow()

    db.add(milestone)
    db.commit()
    db.refresh(milestone)
    return _serialize(db, [milestone])[0]


@router.patch("/milestones/{milestone_id}", response_model=schemas.MilestoneOut)
def update_milestone(
    milestone_id: int, payload: schemas.MilestoneUpdate, db: Session = Depends(get_db)
):
    milestone = db.get(models.Milestone, milestone_id)
    if milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")

    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] != milestone.status:
        milestone.completed_at = (
            models.utcnow() if changes["status"] == "done" else None
        )
    for field, value in changes.items():
        setattr(milestone, field, value)

    db.commit()
    db.refresh(milestone)
    return _serialize(db, [milestone])[0]


@router.delete("/milestones/{milestone_id}", status_code=204)
def delete_milestone(milestone_id: int, db: Session = Depends(get_db)):
    milestone = db.get(models.Milestone, milestone_id)
    if milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    # The tasks under a milestone are real work; they stay on the project and
    # just stop being grouped under this checkpoint.
    for task in db.execute(
        select(models.Task).where(models.Task.milestone_id == milestone_id)
    ).scalars():
        task.milestone_id = None
    db.delete(milestone)
    db.commit()
