"""The repository's own inventory, and the survey built on top of it.

Two routes, and the split between them is the split between free and paid.

`GET` reads the repository and returns what is in it: modules, routes,
tables, pages, configuration. No model, no cost, and true by construction.

`POST` runs the survey -- the same inventory, handed to a model, which
writes the feature outline into the project's notes and proposes what is
missing. Every proposal is checked back against the repository first, and
the ones discarded for already existing come back in the response rather
than being dropped in silence.
"""

from dataclasses import asdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import (
    ai,
    churn,
    hygiene,
    inventory,
    models,
    roadmap,
    sessions,
    survey,
    workspace,
)
from app.db import get_db

router = APIRouter(tags=["inventory"])


def project_or_404(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/projects/{project_id}/inventory")
def read_inventory(project_id: int, db: Session = Depends(get_db)):
    """Everything the repository contains, read from the files."""
    project = project_or_404(db, project_id)
    try:
        root = workspace.resolve(project.local_path)
        return inventory.scan(root)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/survey")
async def run_survey(project_id: int, db: Session = Depends(get_db)):
    """Read the whole repository, write down what it does, propose what is missing."""
    project = project_or_404(db, project_id)
    try:
        return await survey.run(db, project)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ai.AINotConfigured, ai.AIFailed) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/projects/{project_id}/roadmap")
async def run_roadmap(project_id: int, db: Session = Depends(get_db)):
    """Read the board and the code, then propose what is on neither.

    No `GET` beside it, unlike the inventory: the board is already
    readable through `/projects/{id}/milestones` and `/tasks`, and a
    second way to read the same rows would be a second thing to keep
    correct.
    """
    project = project_or_404(db, project_id)
    try:
        return await roadmap.run(db, project)
    except (ai.AINotConfigured, ai.AIFailed) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/projects/{project_id}/churn")
def read_churn(
    project_id: int,
    window_days: int = Query(churn.WINDOW_DAYS, ge=7, le=730),
    db: Session = Depends(get_db),
):
    """Which files change most, and which of those nothing tests.

    Deterministic and free: commit file lists already stored, plus a search
    over the checkout for each busy file's names. It costs a handful of
    `git grep` calls and no model, which is why it lives beside the
    inventory rather than beside the survey.

    Answers without a checkout too -- the churn half needs only the
    database. The coverage half then reports itself as unknown rather than
    as "nothing is tested", which is the distinction the whole panel turns
    on.
    """
    project = project_or_404(db, project_id)
    return churn.map_project(db, project, window_days=window_days)


@router.get("/projects/{project_id}/hygiene")
def read_hygiene(project_id: int, db: Session = Depends(get_db)):
    """What has been committed to this repository that should not have been."""
    project = project_or_404(db, project_id)
    reports = [r for r in hygiene.scan(db, project_id=project.id)]
    if not reports:
        return {
            "project_id": project.id,
            "clean": True,
            "secrets": [],
            "noise": [],
            "templates": [],
            "commits_scanned": 0,
            "commits_without_detail": 0,
        }
    report = reports[0]
    if project.local_path:
        try:
            hygiene.check_present(workspace.resolve(project.local_path), report)
        except (workspace.WorkspaceError, OSError, ValueError):
            pass
    return {
        "project_id": project.id,
        "clean": report.clean,
        "secrets": [asdict(entry) for entry in report.secrets],
        "noise": [asdict(entry) for entry in report.noise],
        "templates": [asdict(entry) for entry in report.templates],
        "commits_scanned": report.commits_scanned,
        "commits_without_detail": report.commits_without_detail,
    }


@router.get("/projects/{project_id}/sessions")
def read_sessions(
    project_id: int,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Working days inferred from commit times, most recent first.

    Read-only and free. Accepting one of these is done through the ordinary
    suggestion routes, so this is the preview rather than the decision.
    """
    project = project_or_404(db, project_id)
    since = date.today() - timedelta(days=days)
    logged = sessions.already_logged(db)
    out = []
    for sitting in sessions.days(db, project_id=project.id, since=since):
        out.append(
            {
                **asdict(sitting),
                "span": sitting.span,
                "already_logged": (sitting.project_id, sitting.work_date) in logged,
            }
        )
    return {"project_id": project.id, "days": out}
