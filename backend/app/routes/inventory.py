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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import ai, inventory, models, survey, workspace
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
