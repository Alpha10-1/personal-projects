"""What the assistant costs, recorded per call.

Everything else in this system is free to run: SQLite on your own machine,
GitHub's public API, arithmetic. `ai.py` is the one part that bills, and until
this module existed there was no way to answer "what did last month cost" or
even "how many calls did I make".

Two decisions worth keeping:

**Recorded at the client, not at the call sites.** Every path through `ai.py`
records, so a feature added later is counted whether or not whoever adds it
remembers to. Accounting that depends on being remembered is accounting that
drifts.

**Cost is computed once and stored.** Prices change; what you spent in
September does not. Storing the dollar figure beside the token counts means a
later edit to the table below cannot silently rewrite history, and the tokens
are still there to recompute from if a rate here turns out to have been wrong.

Rates are US dollars per million tokens, from the published pricing at the
time of writing. Cache writes bill at 1.25x the input rate and cache reads at
0.1x. Web search is $10 per 1,000 searches on top of the tokens the results
themselves cost; a search that errors is not billed.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.db import SessionLocal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rate:
    input_per_mtok: float
    output_per_mtok: float


# Keyed by the start of the model id, so a dated release (`claude-haiku-4-5-
# 20251001`) matches its family without needing a row per snapshot.
RATES: dict[str, Rate] = {
    "claude-opus-5": Rate(5.00, 25.00),
    "claude-sonnet-5": Rate(2.00, 10.00),
    "claude-sonnet-4-6": Rate(3.00, 15.00),
    "claude-haiku-4-5": Rate(1.00, 5.00),
}

# The published multipliers on the input rate.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

WEB_SEARCH_USD = 10.0 / 1000

MILLION = 1_000_000


def rate_for(model: str) -> Optional[Rate]:
    """The rate for a model id, matched on the longest known prefix.

    Returns None for a model this table has never heard of -- a new one, or a
    typo in PP_AI_MODEL. The call is still recorded with its token counts and
    a cost of zero, because silently pricing an unknown model at a guessed
    rate would be worse than admitting the gap.
    """
    name = (model or "").strip()
    matches = [key for key in RATES if name.startswith(key)]
    return RATES[max(matches, key=len)] if matches else None


def cost_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    web_searches: int = 0,
) -> float:
    """What one call cost, in dollars."""
    searches = web_searches * WEB_SEARCH_USD
    rate = rate_for(model)
    if rate is None:
        return round(searches, 6)

    tokens = (
        input_tokens * rate.input_per_mtok
        + cache_write_tokens * rate.input_per_mtok * CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * rate.input_per_mtok * CACHE_READ_MULTIPLIER
        + output_tokens * rate.output_per_mtok
    ) / MILLION
    return round(tokens + searches, 6)


def from_usage(usage) -> dict:
    """Pull the token counts off an SDK usage object.

    Defensive by design: this reads fields off an object owned by someone
    else's library, and a missing attribute must cost an accounting row, not
    the feature the user was using.
    """
    if usage is None:
        return {}
    server = getattr(usage, "server_tool_use", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        "web_searches": int(getattr(server, "web_search_requests", 0) or 0),
    }


def record(
    *,
    feature: str,
    model: str,
    usage=None,
    project_id: Optional[int] = None,
    ok: bool = True,
    error: Optional[str] = None,
    seconds: Optional[float] = None,
) -> None:
    """Write one call to the ledger.

    Opens its own session: this is called from `ai.py`, which has no request
    and no session of its own, and holding the caller's would tie the ledger
    to whether their transaction commits.

    Never raises. A broken ledger must not break a working feature -- the
    failure is logged and the call carries on.
    """
    counts = from_usage(usage)
    try:
        row = models.AiUsage(
            feature=feature[:60],
            model=(model or "unknown")[:80],
            project_id=project_id,
            ok=ok,
            error=(error or None) if error is None else str(error)[:255],
            seconds=round(seconds, 3) if seconds is not None else None,
            cost_usd=cost_usd(model, **counts),
            **counts,
        )
        with SessionLocal() as session:
            session.add(row)
            session.commit()
    except Exception:  # pragma: no cover - the ledger is never worth a 500
        log.warning("Could not record AI usage for %s", feature, exc_info=True)


# --- Reading it back ---------------------------------------------------------


def summary(db: Session, days: int = 30) -> dict:
    """What has been spent, and on what.

    Grouped three ways because they answer different questions: by feature
    ("is the repo review worth it?"), by model ("is anything going to the
    expensive one that shouldn't?") and by day ("was that spike me testing,
    or something looping?").
    """
    since = datetime.now() - timedelta(days=days)
    rows = list(
        db.execute(
            select(models.AiUsage).where(models.AiUsage.at >= since)
        ).scalars()
    )

    def totals(items) -> dict:
        return {
            "calls": len(items),
            "failed": sum(1 for r in items if not r.ok),
            "input_tokens": sum(r.input_tokens for r in items),
            "output_tokens": sum(r.output_tokens for r in items),
            "cache_read_tokens": sum(r.cache_read_tokens for r in items),
            "cache_write_tokens": sum(r.cache_write_tokens for r in items),
            "web_searches": sum(r.web_searches for r in items),
            "cost_usd": round(sum(r.cost_usd for r in items), 4),
        }

    by_feature: dict[str, list] = {}
    by_model: dict[str, list] = {}
    by_day: dict[str, list] = {}
    for row in rows:
        by_feature.setdefault(row.feature, []).append(row)
        by_model.setdefault(row.model, []).append(row)
        by_day.setdefault(row.at.date().isoformat(), []).append(row)

    features = sorted(
        ({"feature": name, **totals(items)} for name, items in by_feature.items()),
        key=lambda item: -item["cost_usd"],
    )
    models_out = sorted(
        ({"model": name, **totals(items)} for name, items in by_model.items()),
        key=lambda item: -item["cost_usd"],
    )
    daily = [
        {"day": day, **totals(items)} for day, items in sorted(by_day.items())
    ]

    month_start = date.today().replace(day=1)
    month = [r for r in rows if r.at.date() >= month_start]

    return {
        "days": days,
        "total": totals(rows),
        "month_to_date": totals(month),
        "by_feature": features,
        "by_model": models_out,
        "by_day": daily,
        "most_expensive_call": _biggest(rows),
        "priced_models": sorted(RATES),
        "unpriced_models": sorted(
            {r.model for r in rows if rate_for(r.model) is None}
        ),
    }


def _biggest(rows: list) -> Optional[dict]:
    if not rows:
        return None
    row = max(rows, key=lambda r: r.cost_usd)
    return {
        "feature": row.feature,
        "model": row.model,
        "at": row.at,
        "cost_usd": round(row.cost_usd, 4),
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "web_searches": row.web_searches,
    }


def last_calls(db: Session, limit: int = 20) -> list[dict]:
    rows = list(
        db.execute(
            select(models.AiUsage)
            .order_by(models.AiUsage.at.desc())
            .limit(limit)
        ).scalars()
    )
    return [
        {
            "at": r.at,
            "feature": r.feature,
            "model": r.model,
            "project_id": r.project_id,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "web_searches": r.web_searches,
            "cost_usd": round(r.cost_usd, 6),
            "seconds": r.seconds,
            "ok": r.ok,
            "error": r.error,
        }
        for r in rows
    ]


def total_since(db: Session, since: datetime) -> float:
    value = db.execute(
        select(func.sum(models.AiUsage.cost_usd)).where(models.AiUsage.at >= since)
    ).scalar()
    return round(value or 0.0, 4)
