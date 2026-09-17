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


def _serialize(db: Session, suggestions):
    task_ids = {s.target_id for s in suggestions if s.target_type == "task"}
    titles = {}
    if task_ids:
        titles = {
            row[0]: row[1]
            for row in db.execute(
                select(models.Task.id, models.Task.title).where(
                    models.Task.id.in_(task_ids)
                )
            ).all()
        }
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
                target_title=titles.get(s.target_id),
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
