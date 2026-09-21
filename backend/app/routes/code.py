"""Editing a file, and getting it approved before it lands.

Every change to a project's files goes through here, whether it was typed
into the browser or proposed by the agent. The shape is deliberate:

- **Saving does not write.** It raises a change, in `pending`.
- **Approving writes.** One step, because a change that is approved but not
  on disk would be a state nobody can reason about.
- **The leader is a name, not a gate.** This app has no login and one user,
  so nothing here can stop anyone approving anything. What it does is make
  the moment explicit and record whose call it was -- which is the part
  that is useful when the question later is "why is this like this".

The impact outline hangs off the same rows, and is the answer to the case
this was really built for: a change waved through, and the need to see
afterwards what it actually reached.
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent, ai, explainer, impact, models, workspace
from app.db import get_db

router = APIRouter(tags=["code"])

# What counts as "recent" when showing what has just landed. A working day
# either side of a mistake is the window in which someone is still likely
# to be looking for it.
RECENT_LIMIT = 30


def project_or_404(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def change_or_404(db: Session, change_id: int) -> models.CodeChange:
    change = db.get(models.CodeChange, change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="Change not found")
    return change


def root_for(db: Session, project: models.Project):
    try:
        return workspace.resolve(project.local_path)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def person_name(db: Session, person_id: Optional[int]) -> Optional[str]:
    if not person_id:
        return None
    person = db.get(models.Person, person_id)
    return person.name if person else None


def serialize(db: Session, change: models.CodeChange, *, full: bool = False) -> dict:
    out = {
        "id": change.id,
        "project_id": change.project_id,
        "path": change.path,
        "action": change.action,
        "origin": change.origin,
        "agent_run_id": change.agent_run_id,
        "note": change.note,
        "status": change.status,
        "approved_by_id": change.approved_by_id,
        "approved_by": person_name(db, change.approved_by_id),
        "approved_at": change.approved_at,
        "decision_note": change.decision_note,
        "reverted_at": change.reverted_at,
        "base_sha": change.base_sha,
        "created_at": change.created_at,
        "lines": impact.line_counts(change.before_text, change.after_text),
    }
    if full:
        out["before_text"] = change.before_text
        out["after_text"] = change.after_text
        out["diff"] = unified(change)
    return out


def unified(change: models.CodeChange) -> str:
    from difflib import unified_diff

    before = (change.before_text or "").splitlines(keepends=True)
    after = (change.after_text or "").splitlines(keepends=True)
    return "".join(
        unified_diff(
            before,
            after,
            fromfile=f"a/{change.path}" if change.before_text is not None else "/dev/null",
            tofile=f"b/{change.path}" if change.after_text is not None else "/dev/null",
        )
    )


# --- raising a change ----------------------------------------------------


class EditRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: Optional[str] = Field(
        default=None, description="The whole new file. Omit to propose a deletion."
    )
    note: Optional[str] = Field(default=None, max_length=2000)
    delete: bool = False


@router.post("/projects/{project_id}/code/changes", status_code=201)
def raise_change(project_id: int, body: EditRequest, db: Session = Depends(get_db)):
    """Save an edit as a change waiting for approval.

    Refuses a no-op rather than creating a row that says nothing happened,
    and refuses the credential paths the agent is also barred from -- the
    editor is a different door into the same house.
    """
    project = project_or_404(db, project_id)
    root = root_for(db, project)

    if agent.matches(body.path, agent.NEVER_WRITE):
        raise HTTPException(
            status_code=400,
            detail=f"{body.path} holds credentials or installed code and is not editable here.",
        )
    try:
        workspace.safe_join(root, body.path)
        try:
            existing = workspace.read_file(root, body.path)
            before = None if existing["binary"] else existing["content"]
            if existing["binary"]:
                raise HTTPException(
                    status_code=400, detail=f"{body.path} is a binary file."
                )
            if existing["truncated"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"{body.path} is too large to edit here safely.",
                )
        except workspace.WorkspaceError:
            before = None  # a new file
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    after = None if body.delete else body.content
    if after is None and not body.delete:
        raise HTTPException(status_code=422, detail="Provide content, or set delete.")
    if before == after:
        raise HTTPException(status_code=409, detail="That is already what the file says.")
    if before is None and after is None:
        raise HTTPException(status_code=409, detail="There is nothing there to delete.")

    head = workspace.head(root)["last_commit"]
    change = models.CodeChange(
        project_id=project_id,
        path=body.path,
        action="delete" if after is None else "create" if before is None else "modify",
        before_text=before,
        after_text=after,
        origin="human",
        note=body.note,
        base_sha=head["sha"] if head else None,
    )
    db.add(change)
    db.commit()
    db.refresh(change)
    return serialize(db, change, full=True)


@router.post("/agent/runs/{run_id}/submit", status_code=201)
def submit_run_for_review(run_id: int, db: Session = Depends(get_db)):
    """Turn an agent run's proposal into change rows the leader can approve.

    The agent's own Apply writes everything at once; this routes the same
    proposal through the same review as a typed edit, one row per file, so
    a run can be accepted in part.
    """
    run = db.get(models.AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "proposed":
        raise HTTPException(
            status_code=409, detail=f"This run is {run.status}, not waiting to be reviewed."
        )
    project = project_or_404(db, run.project_id)
    root = root_for(db, project)

    raised = []
    for proposed in json.loads(run.changes_json or "[]"):
        try:
            existing = workspace.read_file(root, proposed["path"])
            before = None if existing["binary"] else existing["content"]
        except workspace.WorkspaceError:
            before = None
        change = models.CodeChange(
            project_id=run.project_id,
            path=proposed["path"],
            action=proposed["action"],
            before_text=before,
            after_text=proposed.get("content"),
            origin="agent",
            agent_run_id=run.id,
            note=run.summary,
            base_sha=run.base_sha,
        )
        db.add(change)
        raised.append(change)
    db.commit()
    return [serialize(db, c) for c in raised]


# --- reading ------------------------------------------------------------


@router.get("/projects/{project_id}/code/changes")
def list_changes(
    project_id: int,
    status: Optional[str] = None,
    limit: int = Query(RECENT_LIMIT, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(models.CodeChange).where(models.CodeChange.project_id == project_id)
    if status:
        stmt = stmt.where(models.CodeChange.status == status)
    rows = db.execute(
        stmt.order_by(models.CodeChange.created_at.desc()).limit(limit)
    ).scalars()
    return [serialize(db, row) for row in rows]


@router.get("/code/changes/{change_id}")
def read_change(change_id: int, db: Session = Depends(get_db)):
    return serialize(db, change_or_404(db, change_id), full=True)


@router.get("/code/changes/{change_id}/impact")
def read_impact(change_id: int, db: Session = Depends(get_db)):
    """What this change reaches, computed now rather than when it was made.

    Now, because the question is usually asked after something else has
    moved: a reference that did not exist when the change was raised is
    exactly the kind of thing worth knowing about.
    """
    change = change_or_404(db, change_id)
    project = project_or_404(db, change.project_id)
    root = root_for(db, project)
    outline = impact.assess(
        root,
        change.path,
        change.before_text,
        change.after_text,
        protected=agent.protected_patterns(project.protected_paths),
    )
    outline["change_id"] = change.id
    outline["status"] = change.status
    # Whether what is on disk is still what was approved. A change someone
    # has since edited over should not read as though it is intact.
    if change.status == "approved":
        try:
            current = workspace.read_file(root, change.path)
            outline["still_as_approved"] = (
                not current["binary"] and current["content"] == change.after_text
            )
        except workspace.WorkspaceError:
            outline["still_as_approved"] = change.action == "delete"
    else:
        outline["still_as_approved"] = None
    return outline


# --- deciding ------------------------------------------------------------


class Decision(BaseModel):
    # Who is making the call. Defaults to the project's leader; required to
    # be *someone*, because an unattributed approval is the thing this
    # feature exists to prevent.
    person_id: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/code/changes/{change_id}/approve")
def approve(change_id: int, body: Decision, db: Session = Depends(get_db)):
    change = change_or_404(db, change_id)
    if change.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"This change is {change.status}, not pending."
        )
    project = project_or_404(db, change.project_id)
    root = root_for(db, project)

    person_id = body.person_id or project.leader_id
    if person_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "This project has no leader set, so there is nobody to approve "
                "in the name of. Set one in the project's brief, or say who is "
                "approving."
            ),
        )
    if db.get(models.Person, person_id) is None:
        raise HTTPException(status_code=404, detail="That person does not exist.")

    try:
        agent.apply(
            root,
            json.dumps(
                [
                    {
                        "path": change.path,
                        "action": change.action,
                        "content": change.after_text,
                    }
                ]
            ),
        )
    except (agent.AgentError, workspace.WorkspaceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    change.status = "approved"
    change.approved_by_id = person_id
    change.approved_at = models.utcnow()
    change.decision_note = body.note
    db.commit()
    db.refresh(change)

    head = workspace.head(root)["last_commit"]
    return {
        **serialize(db, change, full=True),
        "stale_base": bool(
            head and change.base_sha and not head["sha"].startswith(change.base_sha)
        ),
    }


@router.post("/code/changes/{change_id}/reject")
def reject(change_id: int, body: Decision, db: Session = Depends(get_db)):
    change = change_or_404(db, change_id)
    if change.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"This change is {change.status}, not pending."
        )
    change.status = "rejected"
    change.approved_by_id = body.person_id or change.approved_by_id
    change.decision_note = body.note
    db.commit()
    db.refresh(change)
    return serialize(db, change)


@router.post("/code/changes/{change_id}/revert")
def revert(change_id: int, db: Session = Depends(get_db)):
    """Put the file back the way it was before this change.

    Exact, because both sides were stored. It writes `before_text` and does
    not commit, so the revert is itself reviewable in `git diff` -- and it
    refuses if the file has moved on since, because silently discarding
    someone else's later edit is a worse outcome than a refusal.
    """
    change = change_or_404(db, change_id)
    if change.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"This change is {change.status}; only an approved one can be reverted.",
        )
    project = project_or_404(db, change.project_id)
    root = root_for(db, project)

    try:
        current = workspace.read_file(root, change.path)
        on_disk = None if current["binary"] else current["content"]
    except workspace.WorkspaceError:
        on_disk = None
    if on_disk != change.after_text:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{change.path} has changed since this was approved, so reverting "
                "would throw away whatever came after it. Undo it with git instead."
            ),
        )

    try:
        agent.apply(
            root,
            json.dumps(
                [
                    {
                        "path": change.path,
                        "action": "delete" if change.before_text is None else "modify",
                        "content": change.before_text,
                    }
                ]
            ),
        )
    except (agent.AgentError, workspace.WorkspaceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    change.status = "reverted"
    change.reverted_at = models.utcnow()
    db.commit()
    db.refresh(change)
    return serialize(db, change, full=True)


# --- explaining ----------------------------------------------------------


@router.get("/projects/{project_id}/code/explain")
def explain_selection(
    project_id: int,
    path: str = Query(..., min_length=1),
    start_line: int = Query(..., ge=1),
    end_line: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    """The measured facts about a selection: definitions, references, tests.

    Free and instant, because it is a search over the repository. This is
    what the model is grounded in, and it is also the answer when the model
    is unavailable -- so it stays a route of its own rather than being
    folded into the one below.
    """
    project = project_or_404(db, project_id)
    root = root_for(db, project)
    try:
        return impact.explain(root, path, start_line, end_line)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ExplainRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    # Ask again for the same unchanged code. Off by default, because the
    # whole point of the cache is that the second look is free.
    refresh: bool = False


@router.post("/projects/{project_id}/code/explain")
async def explain_with_model(
    project_id: int, body: ExplainRequest, db: Session = Depends(get_db)
):
    """The same facts, plus the model's reading of them.

    A POST because it can spend money and write a row. Always returns the
    facts, even when the model is not configured or the call fails -- the
    deterministic half was useful before this existed and should not
    disappear because the paid half is unavailable.
    """
    project = project_or_404(db, project_id)
    try:
        return await explainer.explain(
            db,
            project,
            body.path,
            body.start_line,
            body.end_line,
            refresh=body.refresh,
        )
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ai.AINotConfigured, ai.AIFailed) as exc:
        # The facts still exist, so answer with them and say why the rest
        # is missing rather than failing the whole request.
        root = root_for(db, project)
        try:
            facts = impact.explain(root, body.path, body.start_line, body.end_line)
        except workspace.WorkspaceError as inner:
            raise HTTPException(status_code=400, detail=str(inner)) from inner
        return {
            "facts": facts,
            "explanation": None,
            "model": None,
            "cached": False,
            "reason": str(exc),
        }
