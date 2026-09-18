"""The personal side: your own repos, your own ideas.

Same database, same models, one discriminator -- `Project.workspace`. The
separation that matters is not storage, it is that the analyst does not come
here: no findings, no suggestion rules, no scheduled run. A side project you
pick up every few months is not "stalled", and being told it is would train
you to ignore the reviews that do matter.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import github, models, schemas
from app.db import get_db

router = APIRouter(prefix="/personal", tags=["personal"])


class ImportRequest(BaseModel):
    repos: list[str] = Field(default_factory=list, description="full_name values")
    status: schemas.ProjectStatus = "active"


class BrainstormCreate(BaseModel):
    topic: str = Field(min_length=1, max_length=255)
    project_id: Optional[int] = None


class BrainstormMessageIn(BaseModel):
    content: str = Field(min_length=1)


def _get_brainstorm_or_404(db: Session, brainstorm_id: int) -> models.Brainstorm:
    session = db.get(models.Brainstorm, brainstorm_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Brainstorm not found")
    return session


# --- Your repos --------------------------------------------------------------


@router.get("/repos")
def list_repos(
    db: Session = Depends(get_db),
    user: Optional[str] = None,
    limit: int = Query(100, ge=1, le=100),
):
    """Every repo on your GitHub account, with whether it's already imported.

    The account is worked out rather than asked for: an explicit `?user=`,
    then `PP_GITHUB_USER`, then whoever `GITHUB_TOKEN` belongs to, then the
    owner of this checkout's own git remote -- which needs no setting up at
    all, because the tracker is itself a repository on the account in
    question. Only if all of that fails is there a question to ask.

    `resolved_from` comes back with the answer so the UI can say *why* it is
    showing this account, rather than silently picking one.
    """
    who, how = github.resolve_account(db, user)
    if not who:
        raise HTTPException(
            status_code=400,
            detail=(
                "Couldn't work out whose repos to list. Pass ?user=your-login, "
                "or set PP_GITHUB_USER in backend/.env."
            ),
        )

    try:
        payloads = github.fetch_user_repos(who, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    taken = set(
        db.execute(
            select(models.Project.repo).where(models.Project.repo.is_not(None))
        ).scalars()
    )

    repos = [github.to_repo_summary(p) for p in payloads]
    for repo in repos:
        repo["imported"] = repo["full_name"] in taken

    return {
        "user": who,
        "resolved_from": how,
        "authenticated": bool(os.getenv("GITHUB_TOKEN")),
        "count": len(repos),
        "repos": repos,
    }


@router.post("/repos/import", response_model=list[schemas.ProjectOut])
def import_repos(payload: ImportRequest, db: Session = Depends(get_db)):
    """Create a personal project for each repo named.

    Skips anything already mapped rather than erroring: importing the same
    list twice should be a no-op, not a failure, because the obvious thing to
    do after importing five repos is to come back and import the sixth.
    """
    if not payload.repos:
        raise HTTPException(status_code=400, detail="No repos given")

    taken = set(
        db.execute(
            select(models.Project.repo).where(models.Project.repo.is_not(None))
        ).scalars()
    )

    # The owner is also in the names being imported, so that is the last
    # fallback after the shared resolution.
    def _owner_of(full_name: str) -> Optional[str]:
        return full_name.split("/", 1)[0] if "/" in full_name else None

    who = (
        github.resolve_account(db)[0]
        or next((o for o in map(_owner_of, payload.repos) if o), None)
    )
    try:
        by_name = {
            p.get("full_name"): p
            for p in github.fetch_user_repos(who, limit=100)
        } if who else {}
    except RuntimeError:
        # Importing by name should still work if the listing call fails; the
        # name is all that is strictly needed.
        by_name = {}

    created = []
    for full_name in payload.repos:
        if full_name in taken:
            continue
        meta = by_name.get(full_name, {})
        created.append(
            models.Project(
                name=meta.get("name") or full_name.split("/")[-1],
                summary=meta.get("description"),
                repo=full_name,
                workspace="personal",
                status=payload.status,
                category="build",
                priority="medium",
                tech_stack=meta.get("language"),
            )
        )

    for project in created:
        db.add(project)
    db.commit()
    for project in created:
        db.refresh(project)

    from app.enrich import enrich_projects

    return enrich_projects(db, created)


# --- Brainstorms -------------------------------------------------------------


def _serialize(session: models.Brainstorm, messages: list[models.BrainstormMessage]) -> dict:
    return {
        "id": session.id,
        "topic": session.topic,
        "project_id": session.project_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "message_count": len(messages),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


def _messages(db: Session, brainstorm_id: int) -> list[models.BrainstormMessage]:
    return list(
        db.execute(
            select(models.BrainstormMessage)
            .where(models.BrainstormMessage.brainstorm_id == brainstorm_id)
            .order_by(models.BrainstormMessage.created_at, models.BrainstormMessage.id)
        ).scalars()
    )


@router.get("/brainstorms")
def list_brainstorms(
    db: Session = Depends(get_db),
    project_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=200),
):
    stmt = select(models.Brainstorm)
    if project_id is not None:
        stmt = stmt.where(models.Brainstorm.project_id == project_id)
    sessions = list(
        db.execute(
            stmt.order_by(models.Brainstorm.updated_at.desc()).limit(limit)
        ).scalars()
    )
    # The list view needs counts, not transcripts.
    return [
        {**_serialize(s, _messages(db, s.id)), "messages": []} for s in sessions
    ]


@router.post("/brainstorms", status_code=201)
def create_brainstorm(payload: BrainstormCreate, db: Session = Depends(get_db)):
    if payload.project_id is not None and db.get(models.Project, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    session = models.Brainstorm(topic=payload.topic, project_id=payload.project_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return _serialize(session, [])


@router.get("/brainstorms/{brainstorm_id}")
def get_brainstorm(brainstorm_id: int, db: Session = Depends(get_db)):
    session = _get_brainstorm_or_404(db, brainstorm_id)
    return _serialize(session, _messages(db, brainstorm_id))


@router.delete("/brainstorms/{brainstorm_id}", status_code=204)
def delete_brainstorm(brainstorm_id: int, db: Session = Depends(get_db)):
    session = _get_brainstorm_or_404(db, brainstorm_id)
    for message in _messages(db, brainstorm_id):
        db.delete(message)
    db.flush()
    db.delete(session)
    db.commit()
