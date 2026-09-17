from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.enrich import enrich_projects, serialize

router = APIRouter(prefix="/projects", tags=["projects"])

# Active work first, wrapped-up work last. Used to order the board so the
# things needing attention are never below the things that don't.
STATUS_ORDER = {"active": 0, "planning": 1, "idea": 2, "on_hold": 3, "done": 4, "archived": 5}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def get_project_or_404(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("", response_model=list[schemas.ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    q: Optional[str] = None,
    include_archived: bool = False,
):
    stmt = select(models.Project)
    if not include_archived and status != "archived":
        stmt = stmt.where(models.Project.archived_at.is_(None))
    if status:
        stmt = stmt.where(models.Project.status == status)
    if category:
        stmt = stmt.where(models.Project.category == category)
    if priority:
        stmt = stmt.where(models.Project.priority == priority)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                models.Project.name.ilike(like),
                models.Project.summary.ilike(like),
                models.Project.objective.ilike(like),
                models.Project.tech_stack.ilike(like),
            )
        )

    projects = list(db.execute(stmt).scalars())
    projects.sort(
        key=lambda p: (
            STATUS_ORDER.get(p.status, 9),
            PRIORITY_ORDER.get(p.priority, 9),
            p.target_date or datetime.max.date(),
            -p.id,
        )
    )
    return serialize(schemas.ProjectOut, enrich_projects(db, projects))


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    project = models.Project(**payload.model_dump())
    if project.status == "done":
        project.completed_at = models.utcnow()
    db.add(project)
    db.commit()
    db.refresh(project)
    return serialize(schemas.ProjectOut, enrich_projects(db, [project]))[0]


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    return serialize(schemas.ProjectOut, enrich_projects(db, [project]))[0]


@router.patch("/{project_id}", response_model=schemas.ProjectOut)
def update_project(
    project_id: int, payload: schemas.ProjectUpdate, db: Session = Depends(get_db)
):
    project = get_project_or_404(db, project_id)
    changes = payload.model_dump(exclude_unset=True)

    if "status" in changes and changes["status"] != project.status:
        # completed_at tracks the most recent transition into "done", and is
        # cleared if the project is reopened, so it never reads as finished
        # work that is still running.
        if changes["status"] == "done":
            project.completed_at = models.utcnow()
        elif project.status == "done":
            project.completed_at = None

    for field, value in changes.items():
        setattr(project, field, value)

    db.commit()
    db.refresh(project)
    return serialize(schemas.ProjectOut, enrich_projects(db, [project]))[0]


@router.post("/{project_id}/archive", response_model=schemas.ProjectOut)
def archive_project(project_id: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    project.archived_at = models.utcnow()
    project.status = "archived"
    db.commit()
    db.refresh(project)
    return serialize(schemas.ProjectOut, enrich_projects(db, [project]))[0]


@router.post("/{project_id}/unarchive", response_model=schemas.ProjectOut)
def unarchive_project(project_id: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    project.archived_at = None
    # Archived is a terminal display state, so coming back needs a real one.
    # "on_hold" is the honest default: it is back on the board but not
    # claiming to be in flight until you say so.
    project.status = "done" if project.completed_at else "on_hold"
    db.commit()
    db.refresh(project)
    return serialize(schemas.ProjectOut, enrich_projects(db, [project]))[0]


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """Permanently remove a project and everything attached to it.

    Archiving is the reversible option and what the UI offers first; this is
    the escape hatch for a project created by mistake. Child rows are removed
    explicitly because SQLite does not enforce the foreign keys by default.
    """
    project = get_project_or_404(db, project_id)
    for model in (
        models.Task,
        models.Milestone,
        models.TimeLog,
        models.Note,
        models.Link,
        models.Attachment,
    ):
        for row in db.execute(
            select(model).where(model.project_id == project_id)
        ).scalars():
            db.delete(row)
    db.delete(project)
    db.commit()


@router.post("/{project_id}/duplicate", response_model=schemas.ProjectOut, status_code=201)
def duplicate_project(
    project_id: int,
    db: Session = Depends(get_db),
    copy_tasks: bool = Query(True),
    copy_milestones: bool = Query(True),
):
    """Clone a project's shape -- its plan, not its history.

    Tasks and milestones come across reset to open with their dates cleared;
    time logs, notes and files do not come across at all. The point is to
    reuse a structure that worked, not to look like the work was already done.
    """
    source = get_project_or_404(db, project_id)

    clone = models.Project(
        name=f"{source.name} (copy)",
        summary=source.summary,
        status="planning",
        category=source.category,
        priority=source.priority,
        objective=source.objective,
        definition_of_done=source.definition_of_done,
        stakeholder=source.stakeholder,
        tech_stack=source.tech_stack,
    )
    db.add(clone)
    db.flush()

    milestone_id_map = {}
    if copy_milestones:
        milestones = db.execute(
            select(models.Milestone).where(models.Milestone.project_id == project_id)
        ).scalars()
        for m in milestones:
            copy = models.Milestone(
                project_id=clone.id,
                title=m.title,
                detail=m.detail,
                position=m.position,
                status="pending",
            )
            db.add(copy)
            db.flush()
            milestone_id_map[m.id] = copy.id

    if copy_tasks:
        tasks = list(
            db.execute(
                select(models.Task).where(models.Task.project_id == project_id)
            ).scalars()
        )
        # Two passes: create every task first, then rewire the subtask links,
        # since a subtask can appear before its parent in the source order.
        task_id_map = {}
        for t in tasks:
            copy = models.Task(
                project_id=clone.id,
                milestone_id=milestone_id_map.get(t.milestone_id),
                title=t.title,
                notes=t.notes,
                priority=t.priority,
                estimate_hours=t.estimate_hours,
                status="todo",
            )
            db.add(copy)
            db.flush()
            task_id_map[t.id] = copy.id
        for t in tasks:
            if t.parent_task_id in task_id_map:
                db.get(models.Task, task_id_map[t.id]).parent_task_id = task_id_map[
                    t.parent_task_id
                ]

    db.commit()
    db.refresh(clone)
    return serialize(schemas.ProjectOut, enrich_projects(db, [clone]))[0]
