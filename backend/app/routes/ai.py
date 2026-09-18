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

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import (
    ai,
    assistant,
    github,
    history,
    models,
    planner,
    planner_prompts,
    spend,
)
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


def _readme_for(project: models.Project) -> str:
    """The README, or nothing.

    A repo without one is ordinary, and a network hiccup here should cost the
    plan a section rather than the whole call -- the commit history is the
    better evidence anyway.
    """
    if not project.repo:
        return ""
    try:
        return github.fetch_readme(project.repo)
    except (RuntimeError, OSError):
        return ""


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
            async for chunk in ai.stream(
                system=system, messages=turns, feature="chat"
            ):
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
            repo=project.repo, events=events, diffs=diffs, readme=_readme_for(project)
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

    body_parts = [assistant.clean_prose(result["note"])]
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

    proposed = assistant.fit(result.get("summary"), assistant.SUMMARY_LIMIT)
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


# --- Scaffolding -------------------------------------------------------------


class ScaffoldRequest(BaseModel):
    idea: str = Field(default="", description="A sentence or a paragraph.")
    repo: Optional[str] = Field(
        default=None, description="owner/name, to plan around existing code."
    )
    workspace: str = "personal"
    apply: bool = Field(
        default=False,
        description="False returns the plan to look at; True creates it.",
    )


def _build(db: Session, plan: dict, workspace: str, repo: Optional[str]) -> models.Project:
    """Turn a plan into real rows.

    Milestones are created first so a task can point at one by title. A task
    naming a milestone that isn't in the plan is filed under the project
    alone rather than dropped -- losing a task to a typo in the model's own
    output would be the worst possible failure here.
    """
    project = models.Project(
        name=plan.get("name") or "Untitled",
        summary=plan.get("summary"),
        objective=plan.get("objective"),
        definition_of_done=plan.get("definition_of_done"),
        category=plan.get("category") or "build",
        priority=plan.get("priority") or "medium",
        tech_stack=plan.get("tech_stack"),
        repo=repo,
        workspace=workspace,
        status="planning",
    )
    db.add(project)
    db.flush()

    by_title: dict[str, int] = {}
    for position, milestone in enumerate(plan.get("milestones") or []):
        title = (milestone.get("title") or "").strip()
        if not title:
            continue
        row = models.Milestone(
            project_id=project.id,
            title=title[:255],
            detail=milestone.get("detail"),
            position=position,
        )
        db.add(row)
        db.flush()
        by_title[title.lower()] = row.id

    for task in plan.get("tasks") or []:
        title = (task.get("title") or "").strip()
        if not title:
            continue
        named = (task.get("milestone") or "").strip().lower()
        db.add(
            models.Task(
                project_id=project.id,
                milestone_id=by_title.get(named),
                title=title[:255],
                notes=task.get("notes"),
                estimate_hours=task.get("estimate_hours"),
                priority=task.get("priority") or "medium",
                source="agent",
            )
        )

    db.commit()
    db.refresh(project)
    return project


@router.post("/scaffold")
async def scaffold(request: ScaffoldRequest, db: Session = Depends(get_db)):
    """Turn an idea, or a repo, into a project with milestones and tasks.

    Two modes on purpose. Without `apply` it returns the plan and writes
    nothing, so you can read it first; with `apply` it builds the whole thing
    in one go. Generated tasks are stamped `agent`, so a board filled in
    thirty seconds is still distinguishable from one you typed.
    """
    _require_ai()
    idea = request.idea.strip()
    if not idea and not request.repo:
        raise HTTPException(status_code=400, detail="Give an idea or a repo.")

    repo_context = None
    if request.repo:
        repo_context = {"full_name": request.repo, "readme": github.fetch_readme(request.repo)}
        if not idea:
            idea = f"Continue the work on {request.repo}."

    try:
        plan = await assistant.scaffold(idea, repo_context)
    except ai.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not request.apply:
        return {"applied": False, "plan": plan}

    project = _build(db, plan, request.workspace, request.repo)
    return {
        "applied": True,
        "plan": plan,
        "project_id": project.id,
        "milestones_created": len(plan.get("milestones") or []),
        "tasks_created": len(plan.get("tasks") or []),
    }


class ApplyPlanRequest(BaseModel):
    """Building a plan the caller already has, after editing it."""

    plan: dict
    workspace: str = "personal"
    repo: Optional[str] = None


@router.post("/scaffold/apply")
def apply_scaffold(request: ApplyPlanRequest, db: Session = Depends(get_db)):
    """Create the rows for a plan already generated.

    No model call: this is what the preview button posts back after you have
    dropped the tasks you did not want, so it costs nothing and cannot come
    back different from what you just read.
    """
    if not request.plan.get("name"):
        raise HTTPException(status_code=400, detail="The plan needs a name.")
    project = _build(db, request.plan, request.workspace, request.repo)
    return {
        "applied": True,
        "project_id": project.id,
        "milestones_created": len(request.plan.get("milestones") or []),
        "tasks_created": len(request.plan.get("tasks") or []),
    }


# --- Brainstorming -----------------------------------------------------------


class TurnRequest(BaseModel):
    content: str = Field(min_length=1)


@router.post("/brainstorms/{brainstorm_id}/turn")
async def brainstorm_turn(
    brainstorm_id: int, request: TurnRequest, db: Session = Depends(get_db)
):
    """One exchange in a saved brainstorm, streamed.

    Both sides are persisted -- yours before the call, the reply as it
    finishes. Saving yours first means a failed or abandoned call still
    leaves the question in the transcript, which is the half worth keeping.
    """
    _require_ai()
    session = db.get(models.Brainstorm, brainstorm_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Brainstorm not found")

    db.add(
        models.BrainstormMessage(
            brainstorm_id=session.id, role="user", content=request.content
        )
    )
    session.updated_at = models.utcnow()
    db.commit()

    history = list(
        db.execute(
            select(models.BrainstormMessage)
            .where(models.BrainstormMessage.brainstorm_id == session.id)
            .order_by(models.BrainstormMessage.created_at, models.BrainstormMessage.id)
        ).scalars()
    )
    turns = [{"role": m.role, "content": m.content} for m in history]
    project = db.get(models.Project, session.project_id) if session.project_id else None
    system = assistant.brainstorm_system(db, project)

    async def events():
        collected: list[str] = []
        try:
            async for chunk in ai.stream(
                system=system,
                messages=turns,
                feature="brainstorm",
                project_id=session.project_id,
            ):
                collected.append(chunk)
                yield ai.sse("delta", chunk)
        except (ai.AIFailed, ai.AINotConfigured) as exc:
            yield ai.sse("error", str(exc))

        if collected:
            # A fresh session: the request-scoped one is finished with by the
            # time a stream drains, and writing through it raises.
            from app.db import SessionLocal

            writer = SessionLocal()
            try:
                writer.add(
                    models.BrainstormMessage(
                        brainstorm_id=brainstorm_id,
                        role="assistant",
                        content="".join(collected),
                    )
                )
                row = writer.get(models.Brainstorm, brainstorm_id)
                if row is not None:
                    row.updated_at = models.utcnow()
                writer.commit()
            finally:
                writer.close()
        yield ai.sse("done", True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class HarvestRequest(BaseModel):
    apply: bool = False
    project_id: Optional[int] = None


@router.post("/brainstorms/{brainstorm_id}/harvest")
async def brainstorm_harvest(
    brainstorm_id: int, request: HarvestRequest, db: Session = Depends(get_db)
):
    """Pull the decisions and tasks out of a conversation.

    The point of keeping a brainstorm is that the good part is usually the
    third exchange; this is what stops it staying buried there.
    """
    _require_ai()
    session = db.get(models.Brainstorm, brainstorm_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Brainstorm not found")

    history = list(
        db.execute(
            select(models.BrainstormMessage)
            .where(models.BrainstormMessage.brainstorm_id == session.id)
            .order_by(models.BrainstormMessage.created_at, models.BrainstormMessage.id)
        ).scalars()
    )
    if not history:
        raise HTTPException(status_code=400, detail="Nothing said yet.")

    try:
        result = await assistant.harvest(
            session.topic, [{"role": m.role, "content": m.content} for m in history]
        )
    except ai.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    created = 0
    target = request.project_id or session.project_id
    if request.apply and result.get("tasks"):
        if target is None:
            raise HTTPException(
                status_code=400,
                detail="No project to add these to. Pass project_id.",
            )
        if db.get(models.Project, target) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        for task in result["tasks"]:
            title = (task.get("title") or "").strip()
            if not title:
                continue
            db.add(
                models.Task(
                    project_id=target,
                    title=title[:255],
                    notes=task.get("notes"),
                    estimate_hours=task.get("estimate_hours"),
                    source="agent",
                )
            )
            created += 1
        db.commit()

    return {**result, "applied": request.apply, "tasks_created": created, "project_id": target}


# --- What a repository's history says -----------------------------------------


def _project_with_history(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.repo:
        raise HTTPException(
            status_code=400, detail="This project isn't linked to a repo yet."
        )
    return project


@router.get("/projects/{project_id}/timeline")
def project_timeline(project_id: int, db: Session = Depends(get_db)):
    """The computed history: periods, areas, files, contributors.

    No model, no key needed, and no cost. This is the arithmetic the summary
    and the questions are both built on, exposed on its own so an answer can
    be checked against it.
    """
    project = _project_with_history(db, project_id)
    data = history.timeline(db, project)
    if not data["commits"]:
        raise HTTPException(
            status_code=400,
            detail=f"No commits recorded for {project.repo} yet. Sync it first.",
        )
    start, end = data["span"]
    return {**data, "span": {"first": start, "last": end}, "repo": project.repo}


@router.post("/projects/{project_id}/history")
async def project_history(project_id: int, db: Session = Depends(get_db)):
    """What this project is, and how it changed over its whole history.

    Different question from the repo review, which reads the most recent
    commits looking for bugs. This reads the shape of the entire history --
    what was built when, where the work concentrated, what was abandoned --
    and writes it into the tracker as a note.

    As with the digest, the note is written but a better project *summary* is
    only ever proposed.
    """
    _require_ai()
    project = _project_with_history(db, project_id)

    data = history.timeline(db, project)
    if not data["commits"]:
        raise HTTPException(
            status_code=400,
            detail=f"No commits recorded for {project.repo} yet. Sync it first.",
        )

    try:
        result = await assistant.summarise_history(
            history.as_text(data, project), readme=_readme_for(project)
        )
    except ai.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    body = [assistant.clean_prose(result["what_it_is"])]
    if result.get("phases"):
        body.append("\nHow it developed")
        for phase in result["phases"]:
            body.append(f"\n{phase['period']} — {phase['title']}")
            body.append(f"  {phase['what_changed']}")
    if result.get("where_the_work_went"):
        body.append("\nWhere the work went")
        for area in result["where_the_work_went"]:
            line = f"- {area['area']}: {assistant.clean_prose(area['what_it_does'])}"
            if area.get("activity"):
                line += f" ({assistant.clean_prose(area['activity'])})"
            body.append(line)
    if result.get("observations"):
        body.append("\nWorth noticing")
        body.extend(f"- {assistant.clean_prose(o)}" for o in result["observations"])

    start, end = data["span"]
    body.append(
        f"\nFrom {data['commits']} commits between {start:%d %b %Y} and "
        f"{end:%d %b %Y}"
        + (
            f"; file-level detail for {data['detailed']} of them."
            if data["detailed"] < data["commits"]
            else " with full file-level detail."
        )
    )

    note = models.Note(
        project_id=project.id,
        title=f"Project history {date.today().isoformat()}",
        body="\n".join(body),
        kind="note",
        source="agent",
    )
    db.add(note)

    proposed = assistant.fit(result.get("suggested_summary"), assistant.SUMMARY_LIMIT)
    summary_suggested = False
    if proposed and proposed != (project.summary or "").strip():
        fingerprint = f"history_summary:project:{project.id}"
        existing = db.execute(
            select(models.Suggestion).where(models.Suggestion.fingerprint == fingerprint)
        ).scalar_one_or_none()
        if existing is None or existing.status == "pending":
            if existing is not None:
                db.delete(existing)
                db.flush()
            db.add(
                models.Suggestion(
                    rule="history_summary",
                    fingerprint=fingerprint,
                    target_type="project",
                    target_id=project.id,
                    field="summary",
                    current_value=(project.summary or "")[:255],
                    proposed_value=proposed,
                    rationale="Read from the repository's whole commit history.",
                    evidence=json.dumps(
                        [f"{data['commits']} commits, {start:%b %Y} to {end:%b %Y}"]
                    ),
                )
            )
            summary_suggested = True

    db.commit()
    db.refresh(note)

    return {
        "project_id": project.id,
        "repo": project.repo,
        "commits": data["commits"],
        "detailed": data["detailed"],
        "note_id": note.id,
        "note": note.body,
        **result,
        "summary_suggested": summary_suggested,
    }


class AskRequest(BaseModel):
    question: str = Field(min_length=1)


@router.post("/projects/{project_id}/ask")
async def ask_about_project(
    project_id: int, request: AskRequest, db: Session = Depends(get_db)
):
    """Ask something about a project, answered from its commit history.

    Streamed, and grounded: the prompt is the computed timeline, so an answer
    can cite the month, area or file it came from. It is explicitly not the
    source code -- the history says what changed and when, not how a function
    works -- and the prompt says so, because a model asked about code it
    cannot see will otherwise describe what such code usually looks like.
    """
    _require_ai()
    project = _project_with_history(db, project_id)

    data = history.timeline(db, project)
    if not data["commits"]:
        raise HTTPException(
            status_code=400,
            detail=f"No commits recorded for {project.repo} yet. Sync it first.",
        )

    system = assistant.ask_system(history.as_text(data, project))
    question = request.question.strip()

    async def events():
        try:
            async for chunk in ai.stream(
                system=system,
                messages=[{"role": "user", "content": question}],
                feature="ask",
                project_id=project.id,
            ):
                yield ai.sse("delta", chunk)
        except (ai.AIFailed, ai.AINotConfigured) as exc:
            yield ai.sse("error", str(exc))
        yield ai.sse("done", True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Planning what to do next ------------------------------------------------


class PlanRequest(BaseModel):
    """What to plan around, and how much to spend finding out."""

    focus: Optional[str] = Field(
        default=None,
        description="Steer it: 'security', 'get it deployable', 'stop the churn'.",
    )
    research: bool = Field(
        default=False,
        description="Search the web first. Costs more, and sends queries about this project.",
    )
    max_searches: int = Field(default=ai.MAX_SEARCHES, ge=1, le=10)
    hours_per_week: float = Field(default=planner.DEFAULT_HOURS_PER_WEEK, gt=0, le=80)
    include_readme: bool = True


@router.post("/projects/{project_id}/plan")
async def plan_project(
    project_id: int, request: PlanRequest, db: Session = Depends(get_db)
):
    """What to do next on this project, as a choice between real options.

    Reads the README, the commit history, what is already on the board and
    the written record, then proposes two or three different directions --
    each with milestones, tasks and honest hour estimates.

    Writes nothing. The dates attached to each option are computed here from
    the model's hours and the weekly time you said you have, not asked for
    from the model: changing 10 hours a week to 4 is then arithmetic rather
    than another call.
    """
    _require_ai()
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    readme = _readme_for(project) if request.include_readme else ""
    data = planner.context(db, project, readme=readme)
    context_text = planner.as_text(data)

    researched = None
    if request.research:
        try:
            researched = await planner_prompts.do_research(
                context_text, request.focus, request.max_searches, project.id
            )
        except ai.AIFailed as exc:
            # A failed search must not cost the plan: it is an input, not the
            # point. The reply says it was asked for and did not arrive.
            researched = {"text": "", "sources": [], "searches": 0, "error": str(exc)}

    try:
        result = await planner_prompts.plan(
            context_text,
            research_text=(researched or {}).get("text") or None,
            focus=request.focus,
            project_id=project.id,
        )
    except ai.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    options = [
        planner.schedule(option, hours_per_week=request.hours_per_week)
        for option in result.get("options") or []
    ]

    return {
        "project_id": project.id,
        "repo": project.repo,
        "reading": assistant.clean_prose(result.get("reading")),
        "options": options,
        "recommended": assistant.clean_prose(result.get("recommended")),
        "not_worth_doing": result.get("not_worth_doing") or [],
        "unknowns": result.get("unknowns") or [],
        "research": researched,
        "grounded_in": {
            "readme": bool(readme),
            "commits": data["timeline"].get("commits", 0),
            "commits_detailed": data["timeline"].get("detailed", 0),
            "open_tasks": len(data["open_tasks"]),
            "notes": len(data["notes"]),
            "estimates_calibrated": bool(data["calibration"]),
        },
        "hours_per_week": request.hours_per_week,
    }


class ApplyOptionRequest(BaseModel):
    """One option, posted back after you have read it and dropped what you
    did not want."""

    option: dict
    hours_per_week: float = Field(default=planner.DEFAULT_HOURS_PER_WEEK, gt=0, le=80)
    set_target_dates: bool = True


@router.post("/projects/{project_id}/plan/apply")
def apply_plan(
    project_id: int, request: ApplyOptionRequest, db: Session = Depends(get_db)
):
    """Create the milestones and tasks for the option you chose.

    No model call: this builds exactly what you just read, including the rows
    you removed from it. Everything is stamped `agent`, so a board filled in
    ten seconds stays distinguishable from one you typed.

    It adds to the project and never rewrites it -- your summary, status and
    existing tasks are untouched.
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    option = request.option or {}
    if not (option.get("tasks") or option.get("milestones")):
        raise HTTPException(status_code=400, detail="That option has nothing in it.")

    dated = planner.schedule(option, hours_per_week=request.hours_per_week)

    # Existing milestones keep their order; new ones continue after them.
    position = len(planner.milestones(db, project.id))

    by_title: dict[str, int] = {}
    for milestone in dated.get("milestones") or []:
        title = (milestone.get("title") or "").strip()
        if not title:
            continue
        row = models.Milestone(
            project_id=project.id,
            title=title[:255],
            detail=milestone.get("detail"),
            position=position,
            due_date=(
                date.fromisoformat(milestone["due_date"])
                if request.set_target_dates and milestone.get("due_date")
                else None
            ),
        )
        db.add(row)
        db.flush()
        by_title[title.lower()] = row.id
        position += 1

    created = 0
    for task in option.get("tasks") or []:
        title = (task.get("title") or "").strip()
        if not title:
            continue
        named = (task.get("milestone") or "").strip().lower()
        db.add(
            models.Task(
                project_id=project.id,
                # A task naming a milestone that is not in this option is filed
                # under the project rather than dropped: losing work to a typo
                # in the model's own output is the worst failure available here.
                milestone_id=by_title.get(named),
                title=title[:255],
                notes=task.get("notes"),
                estimate_hours=task.get("estimate_hours"),
                priority=task.get("priority") or "medium",
                source="agent",
            )
        )
        created += 1

    db.commit()

    return {
        "project_id": project.id,
        "option": dated.get("title"),
        "milestones_created": len(by_title),
        "tasks_created": created,
        "total_hours": dated.get("total_hours"),
        "finishes": dated.get("finishes") if request.set_target_dates else None,
    }


# --- What it has cost --------------------------------------------------------


@router.get("/spend")
def ai_spend(
    days: int = Query(30, ge=1, le=365),
    recent: int = Query(20, ge=0, le=200),
    db: Session = Depends(get_db),
):
    """Every model call this system has made, and what it cost.

    Not behind the AI guard: the whole point is to be able to read the bill
    when the assistant is switched off, or after turning it off because of
    the bill.

    Costs are the ones computed when each call was made, at the rates in
    `spend.py`. They are an estimate of the API's own billing, not a
    statement from it -- close enough to answer "is this worth it", not a
    substitute for the invoice.
    """
    return {
        **spend.summary(db, days=days),
        "recent": spend.last_calls(db, limit=recent) if recent else [],
        "rates": {
            model: {
                "input_per_mtok": rate.input_per_mtok,
                "output_per_mtok": rate.output_per_mtok,
            }
            for model, rate in spend.RATES.items()
        },
        "web_search_usd": spend.WEB_SEARCH_USD,
    }
