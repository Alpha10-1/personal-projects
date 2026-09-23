"""Starting, reading and applying agent runs.

A run takes minutes and costs money, so it is not a request that waits. The
POST creates the row, starts the work on the event loop and returns
immediately; the client polls the row. That is duller than streaming and it
survives a page reload, which streaming does not -- and a page reload in the
middle of something that is editing your repository is exactly when you want
the record to still be there.

Applying is always a separate call. `auto_apply` only decides whether the
server makes that call itself the moment the run finishes, and it is refused
outright when the run touched a protected path.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent, ai, models, workspace
from app.db import SessionLocal, get_db

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])

# A run is long, but not unbounded. Past this the most likely explanation is
# a wedged event loop rather than a thorough agent, and a row stuck on
# "running" forever is worse than one that says it timed out.
RUN_TIMEOUT = 900.0


def run_or_404(db: Session, run_id: int) -> models.AgentRun:
    run = db.get(models.AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def serialize(run: models.AgentRun, *, full: bool = False) -> dict:
    out = {
        "id": run.id,
        "project_id": run.project_id,
        "task_id": run.task_id,
        "instruction": run.instruction,
        "status": run.status,
        "review_required": run.review_required,
        # Null means the tests were never run, which is a different
        # thing from a run that went red and must read as one.
        "tests_passed": run.tests_passed,
        "review_reason": run.review_reason,
        "auto_apply": run.auto_apply,
        "summary": run.summary,
        "base_sha": run.base_sha,
        "model": run.model,
        "turns": run.turns,
        "error": run.error,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "applied_at": run.applied_at,
        "file_count": len(json.loads(run.changes_json or "[]")),
    }
    if full:
        out["changes"] = json.loads(run.changes_json or "[]")
        out["diff"] = run.diff
        out["test_output"] = run.test_output
    return out


async def carry_out(run_id: int) -> None:
    """The background half of a run.

    Its own session, because the request that started it has long since
    closed. Every exit path writes a terminal status: a row left on
    "running" is a bug the user cannot distinguish from a slow model.
    """
    db = SessionLocal()
    try:
        run = db.get(models.AgentRun, run_id)
        project = db.get(models.Project, run.project_id)
        try:
            result = await asyncio.wait_for(
                agent.execute(project, run.instruction), timeout=RUN_TIMEOUT
            )
        except asyncio.TimeoutError:
            run.status, run.error = "failed", "The run did not finish in time."
            run.finished_at = models.utcnow()
            db.commit()
            return
        except (agent.AgentError, ai.AINotConfigured, workspace.WorkspaceError) as exc:
            run.status, run.error = "failed", str(exc)
            run.finished_at = models.utcnow()
            db.commit()
            return

        run.changes_json = json.dumps(result["changes"])
        run.diff = result["diff"]
        run.summary = result["summary"]
        run.turns = result["turns"]
        run.base_sha = result["base_sha"]
        run.tests_passed = result.get("tests_passed")
        run.test_output = result.get("test_output")
        run.review_required = result["review_required"]
        run.review_reason = result["review_reason"]
        run.error = result["error"]
        run.finished_at = models.utcnow()
        # A run that produced nothing is not a proposal. Saying "failed"
        # when the model simply decided no change was needed would be wrong,
        # so the summary carries that and the status reflects that there is
        # nothing to apply.
        run.status = "failed" if result["error"] and not result["changes"] else "proposed"
        db.commit()

        if (
            run.status == "proposed"
            and run.auto_apply
            and not run.review_required
            and result["changes"]
        ):
            try:
                root = workspace.resolve(project.local_path)
                agent.apply(root, run.changes_json)
                run.status, run.applied_at = "applied", models.utcnow()
                db.commit()
            except (agent.AgentError, workspace.WorkspaceError) as exc:
                # The proposal is still good; only the writing failed. Left
                # as "proposed" so it can be applied by hand once the cause
                # is fixed.
                run.error = f"Ready, but writing it out failed: {exc}"
                db.commit()
    except Exception:  # pragma: no cover - last resort, never leave "running"
        log.exception("Agent run %s crashed", run_id)
        try:
            run = db.get(models.AgentRun, run_id)
            if run and run.status == "running":
                run.status = "failed"
                run.error = "The run crashed. See the server log."
                run.finished_at = models.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


class RunRequest(BaseModel):
    instruction: str = Field(min_length=10, max_length=8_000)
    task_id: Optional[int] = None
    auto_apply: bool = Field(
        default=False,
        description=(
            "Write the result straight to the working tree when the run "
            "finishes. Ignored when the run touches a protected path."
        ),
    )


@router.post("/projects/{project_id}/agent/runs", status_code=201)
async def start_run(project_id: int, body: RunRequest, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail=ai.status()["reason"])
    try:
        workspace.resolve(project.local_path)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # One at a time per project. Two agents editing the same tree would
    # produce two proposals built on the same base, and applying both would
    # silently lose one of them.
    running = db.execute(
        select(models.AgentRun).where(
            models.AgentRun.project_id == project_id,
            models.AgentRun.status == "running",
        )
    ).scalars().first()
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Run {running.id} is still going on this project.",
        )

    run = models.AgentRun(
        project_id=project_id,
        task_id=body.task_id,
        instruction=body.instruction.strip(),
        auto_apply=body.auto_apply,
        model=ai.CHAT_MODEL,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    asyncio.create_task(carry_out(run.id))
    return serialize(run)


@router.get("/projects/{project_id}/agent/runs")
def list_runs(
    project_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(models.AgentRun)
        .where(models.AgentRun.project_id == project_id)
        .order_by(models.AgentRun.created_at.desc())
        .limit(limit)
    ).scalars()
    return [serialize(r) for r in rows]


@router.get("/agent/runs/{run_id}")
def read_run(run_id: int, db: Session = Depends(get_db)):
    return serialize(run_or_404(db, run_id), full=True)


@router.post("/agent/runs/{run_id}/apply")
def apply_run(run_id: int, db: Session = Depends(get_db)):
    """Write the proposal to the working tree.

    Uncommitted, so `git diff` is the review and `git checkout` is the undo.
    If HEAD has moved since the run reasoned, that is reported alongside --
    not refused, because the change is usually still right and the person
    applying it can see the diff.
    """
    run = run_or_404(db, run_id)
    if run.status != "proposed":
        raise HTTPException(
            status_code=409, detail=f"This run is {run.status}, not waiting to be applied."
        )
    changes = json.loads(run.changes_json or "[]")
    if not changes:
        raise HTTPException(status_code=409, detail="This run proposed no changes.")

    project = db.get(models.Project, run.project_id)
    try:
        root = workspace.resolve(project.local_path)
        written = agent.apply(root, run.changes_json)
    except (agent.AgentError, workspace.WorkspaceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run.status, run.applied_at = "applied", models.utcnow()
    db.commit()

    now = workspace.head(root)["last_commit"]
    moved = bool(now and run.base_sha and not now["sha"].startswith(run.base_sha))
    return {
        "applied": written,
        "stale_base": moved,
        "note": (
            "The repository has had new commits since this run was made; "
            "check the result." if moved else None
        ),
    }


@router.post("/agent/runs/{run_id}/discard")
def discard_run(run_id: int, db: Session = Depends(get_db)):
    run = run_or_404(db, run_id)
    if run.status == "applied":
        raise HTTPException(
            status_code=409,
            detail="This run was applied. Undo it with git rather than here.",
        )
    run.status = "discarded"
    db.commit()
    return serialize(run)
