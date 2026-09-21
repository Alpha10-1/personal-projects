"""The Code tab: a live view of a project's checkout.

Everything here is a read of the disk as it is at the moment of the request.
Nothing is cached, because the whole point is to see the edit you made three
seconds ago in your editor. Nothing is written either -- this router only
looks; applying changes lives with the agent that proposes them.

`WorkspaceError` becomes a 400 rather than a 500 throughout: every one of
them is a path the user can correct, a folder they can create, or a setting
they can change. A 500 would say the server is broken when it is doing
exactly what it should.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import editor, models, workspace
from app.db import get_db

router = APIRouter(prefix="/projects/{project_id}/workspace", tags=["workspace"])


def project_or_404(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def root_for(db: Session, project_id: int):
    """The checkout, or a 400 that says why there isn't one."""
    project = project_or_404(db, project_id)
    try:
        return workspace.resolve(project.local_path)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def read_state(project_id: int, db: Session = Depends(get_db)):
    """Branch, last commit and uncommitted changes -- or why none of that is
    available. Deliberately a 200 either way: "no folder set" is the normal
    state of most projects and the tab has to render something."""
    project = project_or_404(db, project_id)
    state = workspace.state(project.local_path)
    state["local_path"] = project.local_path
    state["editor_available"] = editor.available()
    return state


@router.get("/tree")
def read_tree(
    project_id: int,
    path: str = Query("", description="Folder relative to the repository root"),
    db: Session = Depends(get_db),
):
    root = root_for(db, project_id)
    try:
        return {"path": path, "entries": workspace.listing(root, path)}
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/file")
def read_file(
    project_id: int,
    path: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    root = root_for(db, project_id)
    try:
        payload = workspace.read_file(root, path)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload["editor_url"] = editor.url_for(workspace.safe_join(root, path))
    return payload


@router.get("/diff")
def read_diff(
    project_id: int,
    path: Optional[str] = None,
    staged: bool = False,
    db: Session = Depends(get_db),
):
    root = root_for(db, project_id)
    try:
        return {"path": path, "staged": staged, "diff": workspace.diff(root, path, staged)}
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/search")
def search(
    project_id: int,
    q: str = Query(..., min_length=2),
    limit: int = Query(60, ge=1, le=300),
    db: Session = Depends(get_db),
):
    root = root_for(db, project_id)
    return {"query": q, "hits": workspace.search(root, q, limit)}


class OpenRequest(BaseModel):
    path: Optional[str] = Field(default=None, description="Relative; omit for the repo root")
    line: Optional[int] = Field(default=None, ge=1)


@router.post("/open")
def open_in_editor(project_id: int, body: OpenRequest, db: Session = Depends(get_db)):
    """Open the file in the desktop editor.

    Runs on the server because the server and the editor are the same
    machine -- which is true here by construction and would be the first
    thing to revisit if this were ever hosted.
    """
    root = root_for(db, project_id)
    try:
        target = workspace.safe_join(root, body.path) if body.path else root
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        ran = editor.open_path(target, body.line)
    except editor.EditorUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"opened": str(target), "command": ran, "url": editor.url_for(target, body.line)}
