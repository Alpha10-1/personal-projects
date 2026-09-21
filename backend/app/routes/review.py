"""The analyst's review: what needs attention, and what it proposes doing.

Suggestions are applied only when accepted. Nothing on this router changes a
task as a side effect of being read.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, review, schemas
from app.db import get_db

router = APIRouter(tags=["review"])


def _titles(db: Session, suggestions) -> dict[tuple[str, int], str]:
    """What each suggestion is about, by name rather than by number.

    Keyed by type *and* id: task 8 and project 8 are different things, and a
    single id-keyed map quietly showed one's name against the other.

    Projects were missing entirely, so every suggestion raised against one --
    which is all of the summary proposals -- read as "project 8" on the
    Review page.
    """
    found: dict[tuple[str, int], str] = {}
    for target_type, model, column in (
        ("task", models.Task, models.Task.title),
        ("project", models.Project, models.Project.name),
    ):
        ids = {s.target_id for s in suggestions if s.target_type == target_type}
        if not ids:
            continue
        for row_id, title in db.execute(
            select(model.id, column).where(model.id.in_(ids))
        ).all():
            found[(target_type, row_id)] = title
    return found


def _serialize(db: Session, suggestions):
    titles = _titles(db, suggestions)
    out = []
    for s in suggestions:
        try:
            evidence = json.loads(s.evidence or "[]")
        except ValueError:
            evidence = []
        # Built field by field rather than validated off the ORM object:
        # `evidence` is stored as a JSON string and has to be decoded before
        # it can satisfy the list[str] on the way out.
        out.append(
            schemas.SuggestionOut(
                id=s.id,
                rule=s.rule,
                target_type=s.target_type,
                target_id=s.target_id,
                field=s.field,
                current_value=s.current_value,
                proposed_value=s.proposed_value,
                rationale=s.rationale,
                evidence=evidence,
                status=s.status,
                created_at=s.created_at,
                resolved_at=s.resolved_at,
                target_title=titles.get((s.target_type, s.target_id)),
            )
        )
    return out


def _pending(db: Session):
    return list(
        db.execute(
            select(models.Suggestion)
            .where(models.Suggestion.status == "pending")
            .order_by(models.Suggestion.created_at.desc(), models.Suggestion.id.desc())
        ).scalars()
    )


@router.get("/review", response_model=schemas.ReviewOut)
def get_review(
    db: Session = Depends(get_db),
    stale_days: int = Query(review.STALE_DAYS, ge=1, le=90),
    refresh: bool = Query(
        False,
        description="Also look for new suggestions before returning.",
    ),
):
    """Findings plus anything currently awaiting a decision.

    Read-only unless `refresh` is set, and even then it only ever *raises*
    suggestions -- it never applies one.
    """
    if refresh:
        review.propose(db)

    findings = review.find(db, stale_days=stale_days)
    suggestions = _serialize(db, _pending(db))

    counts: dict[str, int] = {"suggestions_pending": len(suggestions)}
    for finding in findings:
        counts[finding.rule] = counts.get(finding.rule, 0) + 1

    return schemas.ReviewOut(
        generated_at=datetime.now(),
        findings=[schemas.FindingOut(**review.as_dict(f)) for f in findings],
        suggestions=suggestions,
        counts=counts,
    )


@router.post("/suggestions/refresh", response_model=list[schemas.SuggestionOut])
def refresh_suggestions(db: Session = Depends(get_db)):
    """Look for new suggestions. Returns only the ones newly raised."""
    return _serialize(db, review.propose(db))


@router.get("/suggestions", response_model=list[schemas.SuggestionOut])
def list_suggestions(
    db: Session = Depends(get_db),
    status: str = "pending",
    limit: int = Query(100, ge=1, le=500),
):
    stmt = select(models.Suggestion)
    if status != "all":
        stmt = stmt.where(models.Suggestion.status == status)
    stmt = stmt.order_by(
        models.Suggestion.created_at.desc(), models.Suggestion.id.desc()
    ).limit(limit)
    return _serialize(db, list(db.execute(stmt).scalars()))


def _get_pending_or_404(db: Session, suggestion_id: int) -> models.Suggestion:
    suggestion = db.get(models.Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if suggestion.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Suggestion was already {suggestion.status}",
        )
    return suggestion


@router.post("/suggestions/{suggestion_id}/accept", response_model=schemas.SuggestionOut)
def accept(suggestion_id: int, db: Session = Depends(get_db)):
    """Apply the proposed change and mark the suggestion accepted."""
    suggestion = _get_pending_or_404(db, suggestion_id)
    try:
        review.apply(db, suggestion)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    suggestion.status = "accepted"
    suggestion.resolved_at = models.utcnow()
    db.commit()
    db.refresh(suggestion)
    return _serialize(db, [suggestion])[0]


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=schemas.SuggestionOut)
def dismiss(suggestion_id: int, db: Session = Depends(get_db)):
    """Turn it down. It will not be raised again: the dismissed row keeps its
    fingerprint, which is what stops the rule proposing the same thing."""
    suggestion = _get_pending_or_404(db, suggestion_id)
    suggestion.status = "dismissed"
    suggestion.resolved_at = models.utcnow()
    db.commit()
    db.refresh(suggestion)
    return _serialize(db, [suggestion])[0]


# --- One suggestion, in full -------------------------------------------------

# What a rule is actually looking for, in the words someone deciding would
# use. The rule name is kept visible beside it -- it is what you would switch
# off if the rule turned out to be noisy -- but on its own it explains
# nothing to anyone who has not read review.py.
RULE_EXPLANATIONS = {
    "activity_suggests_started": (
        "There are commits against a task that is still marked as not started, "
        "so the work looks like it is already underway."
    ),
    "merged_pr_suggests_done": (
        "A merged pull request names this task, which usually means the work "
        "it describes has landed."
    ),
    "history_summary": (
        "The whole commit history of the linked repository was read, and the "
        "project's summary does not describe what the commits show was built."
    ),
}

# Accepting writes to the tracker, so it is worth saying plainly what it will
# write and where, rather than leaving it to be inferred from a field name.
APPLIES = {
    ("project", "summary"): "Replaces this project's summary with the proposed text.",
    ("task", "status"): (
        "Moves this task to the proposed status, exactly as changing it by "
        "hand would -- including stamping its completion time if it becomes "
        "done."
    ),
}


def _live_value(db: Session, suggestion: models.Suggestion):
    """What the field holds right now, and whether the target still exists.

    The stored `current_value` is a snapshot from when the suggestion was
    raised and may be weeks old. Deciding from it means deciding against
    something that is no longer there -- and accepting overwrites whatever is
    there *now*, not what the snapshot says.
    """
    model = {"project": models.Project, "task": models.Task}.get(suggestion.target_type)
    if model is None:
        return None, False
    target = db.get(model, suggestion.target_id)
    if target is None:
        return None, False
    value = getattr(target, suggestion.field, None)
    return (None if value is None else str(value)), True


@router.get("/suggestions/{suggestion_id}", response_model=schemas.SuggestionDetailOut)
def get_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    """Everything behind one suggestion, for reading before deciding.

    The list view has to fit a row; a 240-character proposed summary does
    not. This returns the same suggestion with nothing abbreviated, plus the
    three things the list cannot show: what the field holds now, whether that
    has changed since the suggestion was raised, and what accepting would
    actually do.
    """
    suggestion = db.get(models.Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    base = _serialize(db, [suggestion])[0]
    live, exists = _live_value(db, suggestion)
    stored = suggestion.current_value or ""

    return schemas.SuggestionDetailOut(
        **base.model_dump(),
        live_value=live,
        target_exists=exists,
        # Compared against the snapshot so the UI can warn before an accept
        # quietly overwrites an edit made since.
        changed_since_raised=exists and (live or "") != stored,
        already_applied=exists and (live or "") == suggestion.proposed_value,
        rule_explanation=RULE_EXPLANATIONS.get(suggestion.rule),
        applies=APPLIES.get((suggestion.target_type, suggestion.field)),
        can_apply=(suggestion.target_type, suggestion.field) in APPLIES,
    )
