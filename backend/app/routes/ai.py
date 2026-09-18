"""The assistant's endpoints.

These spend money and send data off the machine, which is the opposite trade
from the rest of the API, so each is reachable only when a key is configured
and each says plainly when it isn't.

All but one are read-only with respect to the tracker. `/digest` adds a note
and may raise a suggestion -- it adds rows, and never rewrites one you wrote.
"""

import json
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai, assistant, github, models
from app.db import get_db

router = APIRouter(prefix="/ai", tags=["ai"])

# Recent commits are what a repo review is about; going further back turns it
# into a code audit, which is a different job and a much bigger prompt.
REVIEW_EVENT_LIMIT = 20


class DraftRequest(BaseModel):
    """A form in progress. Everything optional -- that is the point."""

    draft: dict[str, Any] = Field(default_factory=dict)
    project_id: Optional[int] = None


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatTurn]


def _require_ai() -> None:
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail=ai.status()["reason"])


@router.get("/status")
def ai_status():
    """Whether the assistant is available. Deliberately not behind the guard:
    the UI calls this to decide whether to show any of it."""
    return ai.status()


# --- Suggestions while typing ------------------------------------------------


async def _suggest(coro) -> dict:
    try:
        fields = await coro
    except ai.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Empty strings and empty lists are the model declining to suggest that
    # field; dropping them here means the UI can treat anything it receives as
    # something worth offering.
    return {"fields": {k: v for k, v in fields.items() if v not in (None, "", [], {})}}


@router.post("/suggest/project")
async def suggest_project(request: DraftRequest):
    _require_ai()
    if len(assistant.draft_text(request.draft)) < ai.MIN_DRAFT_CHARS:
        return {"fields": {}, "skipped": "draft too short"}
    return await _suggest(assistant.suggest_project(request.draft))


@router.post("/suggest/task")
async def suggest_task(request: DraftRequest, db: Session = Depends(get_db)):
    _require_ai()
    if len(assistant.draft_text(request.draft)) < ai.MIN_DRAFT_CHARS:
        return {"fields": {}, "skipped": "draft too short"}
    project = (
        db.get(models.Project, request.project_id) if request.project_id else None
    )
    return await _suggest(assistant.suggest_task(request.draft, project))


# --- Chat --------------------------------------------------------------------


@router.post("/chat")
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    """Streamed, as Server-Sent Events.

    The tracker context is built here, once, from the database -- not carried
    in the request -- so the browser can't widen what the model is shown.
    """
    _require_ai()
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages")

    system = assistant.chat_system(db)
    turns = [
        {"role": m.role if m.role in ("user", "assistant") else "user", "content": m.content}
        for m in request.messages
        if m.content.strip()
    ]
    if not turns:
        raise HTTPException(status_code=400, detail="No messages")

    async def events():
        try:
            async for chunk in ai.stream(system=system, messages=turns):
                yield ai.sse("delta", chunk)
        except (ai.AIFailed, ai.AINotConfigured) as exc:
            # The 200 and headers went out with the first byte, so a failure
            # can only be reported inside the stream.
            yield ai.sse("error", str(exc))
        yield ai.sse("done", True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Repo review -------------------------------------------------------------


@router.post("/projects/{project_id}/repo-review")
async def repo_review(project_id: int, db: Session = Depends(get_db)):
    """Summary, possible bugs and improvements over a project's recent commits.

    Reads the activity already ingested rather than re-fetching it, then asks
    GitHub only for the diffs, which are the part not worth storing.
    """
    _require_ai()
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.repo:
        raise HTTPException(
            status_code=400,
            detail="This project isn't linked to a repo yet.",
        )

    events = list(
        db.execute(
            select(models.ActivityEvent)
            .where(models.ActivityEvent.repo == project.repo)
            .order_by(models.ActivityEvent.occurred_at.desc())
            .limit(REVIEW_EVENT_LIMIT)
        ).scalars()
    )
    if not events:
        raise HTTPException(
            status_code=400,
            detail=f"No activity recorded for {project.repo} yet. Sync it first.",
        )

    shas = []
    for event in events:
        if event.kind != "commit":
            continue
        try:
            payload = json.loads(event.raw or "{}")
        except ValueError:
            continue
        sha = payload.get("sha")
        if sha:
            shas.append(sha)

    # A missing diff downgrades the review, it doesn't fail it -- the commit
    # messages alone still support a summary, and the prompt says so.
    try:
        diffs = github.fetch_diffs(project.repo, shas) if shas else ""
    except Exception:
        diffs = ""

    try:
        result = await assistant.review_repo(
            repo=project.repo, events=events, diffs=diffs
        )
    except ai.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "project_id": project.id,
        "repo": project.repo,
        "commits_reviewed": len(shas),
        "events_considered": len(events),
        "diffs_included": bool(diffs),
        **result,
    }


# --- Project digest ----------------------------------------------------------


@router.post("/projects/{project_id}/digest")
async def project_digest(project_id: int, db: Session = Depends(get_db)):
    """Write a progress update for a project, crediting who did what.

    The note is written straight away: it is additive, stamped `agent`, and
    adds a row rather than changing one. A better project *summary* is only
    ever proposed -- it goes onto the Review page as a suggestion with your
    current wording beside it, because replacing prose you wrote is the one
    write this system does not do on its own.
    """
    _require_ai()
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        result = await assistant.write_digest(db, project)
    except ai.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    body_parts = [result["note"]]
    if result.get("contributions"):
        body_parts.append(
            "\nContributions\n"
            + "\n".join(
                f"- {c['who']}: {c['what']}" for c in result["contributions"]
            )
        )
    if result.get("risks"):
        body_parts.append("\nRisks\n" + "\n".join(f"- {r}" for r in result["risks"]))
    if result.get("open_questions"):
        body_parts.append(
            "\nOpen questions\n"
            + "\n".join(f"- {q}" for q in result["open_questions"])
        )

    note = models.Note(
        project_id=project.id,
        title=f"Progress digest {date.today().isoformat()}",
        body="\n".join(body_parts),
        kind="note",
        source="agent",
    )
    db.add(note)

    proposed = (result.get("summary") or "").strip()[: assistant.SUMMARY_LIMIT]
    summary_suggested = False
    if proposed and proposed != (project.summary or "").strip():
        # One pending summary suggestion per project at a time: the
        # fingerprint is the project, not the text, so running the digest
        # again replaces the proposal instead of stacking up a pile of them.
        fingerprint = f"ai_summary_refresh:project:{project.id}"
        existing = db.execute(
            select(models.Suggestion).where(
                models.Suggestion.fingerprint == fingerprint
            )
        ).scalar_one_or_none()

        if existing is None or existing.status == "pending":
            if existing is not None:
                db.delete(existing)
                db.flush()
            db.add(
                models.Suggestion(
                    rule="ai_summary_refresh",
                    fingerprint=fingerprint,
                    target_type="project",
                    target_id=project.id,
                    field="summary",
                    current_value=(project.summary or "")[:255],
                    proposed_value=proposed,
                    rationale="The digest read the current summary as out of date.",
                    evidence=json.dumps(
                        [f"Digest written {date.today().isoformat()}"]
                    ),
                )
            )
            summary_suggested = True

    db.commit()
    db.refresh(note)

    return {
        "project_id": project.id,
        "note_id": note.id,
        "note": note.body,
        "title": note.title,
        "contributions": result.get("contributions", []),
        "risks": result.get("risks", []),
        "open_questions": result.get("open_questions", []),
        "summary_suggested": summary_suggested,
        "proposed_summary": proposed if summary_suggested else None,
    }
