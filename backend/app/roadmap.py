"""Proposing the milestones and tasks a project does not have yet.

The same shape as `survey.py`, pointed at the board instead of the code.

1. `board.snapshot` reads what the project already has: every milestone,
   every task in every state, what has been written down, where the hours
   went. `inventory.scan` adds what the code already does, when there is a
   checkout to read.
2. The model is given both and asked what is missing.
3. Every proposal is checked back -- against the board by title, and
   against the repository by the terms the model names -- before it
   becomes a suggestion.

Step three is the point, as it is there. A backlog that keeps proposing
work you already planned is one you stop reading, and the second time that
happens the good suggestions go unread with the rest.

There are two checks rather than one because a piece of work can already
exist in two different places. "Add a settings page" may be on the board
as a task, or it may simply be built already and never written down. Both
count as done, and both have to be looked for.
"""

import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai, board, formatting, inventory, models, review, workspace

# A roadmap is a handful of milestones and a dozen tasks. Sonnet writes
# this in roughly 2,000 output tokens; the ceiling is headroom.
MAX_ROADMAP_TOKENS = 6_000


ROADMAP_SCHEMA = {
    "type": "object",
    "properties": {
        "where_it_stands": {
            "type": "string",
            "description": (
                "Two or three sentences on where this project actually is, "
                "read off the board and the code you were shown. Not "
                "encouragement -- an assessment."
            ),
        },
        "milestones": {
            "type": "array",
            "description": (
                "Checkpoints this project does not have and should. A "
                "milestone is a dated thing you would report upward -- "
                "'Model handed to the business for review', not 'Do the "
                "modelling'. Empty is a legitimate answer, and is the right "
                "one when the milestones already there cover the work."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The checkpoint reached."},
                    "detail": {
                        "type": "string",
                        "description": "What has to be true for it to be met.",
                    },
                    "why": {
                        "type": "string",
                        "description": (
                            "Why this project needs it, referring to what you "
                            "were shown."
                        ),
                    },
                    "after": {
                        "type": "string",
                        "description": (
                            "The title of the existing milestone this follows, "
                            "if any. Leave empty if it comes first."
                        ),
                    },
                    "look_for": {
                        "type": "array",
                        "description": (
                            "Terms that would already be in the repository if "
                            "this checkpoint had been reached. Specific ones."
                        ),
                        "items": {"type": "string"},
                        "maxItems": 6,
                    },
                },
                "required": ["title", "why"],
            },
            "maxItems": 8,
        },
        "tasks": {
            "type": "array",
            "description": (
                "Work this project does not have written down and needs. "
                "Every one is checked against the board and the repository "
                "before anyone sees it, so a task already there or already "
                "built will be discarded -- a padded list is a shorter list, "
                "not a longer one."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "Imperative and specific: 'Paginate the activity "
                            "feed', not 'Improve performance'. One piece of "
                            "work, not a theme."
                        ),
                    },
                    "why": {"type": "string"},
                    "milestone": {
                        "type": "string",
                        "description": (
                            "The milestone it belongs under -- an existing one "
                            "by title, or one you proposed above. Empty if "
                            "neither."
                        ),
                    },
                    "estimate_hours": {
                        "type": "number",
                        "description": "Your honest guess at the hours.",
                    },
                    "look_for": {
                        "type": "array",
                        "description": (
                            "THE IMPORTANT FIELD. Identifiers, file names, "
                            "route paths or config keys that would already be "
                            "in the repository if this work were done. If any "
                            "is found, the task is discarded as already built."
                        ),
                        "items": {"type": "string"},
                        "maxItems": 6,
                    },
                    "value": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["title", "why"],
            },
            "maxItems": 15,
        },
    },
    "required": ["where_it_stands", "milestones", "tasks"],
}


SYSTEM = (
    "You look at a project's board and propose only the milestones and "
    "tasks it does not already have.\n\n"
    "You are shown the whole board -- every milestone, every task in every "
    "state, what has been written down, the hours logged -- and, where "
    "there is a checkout to read, an inventory of what the code already "
    "does. Both are read from the real thing. Treat them as fact.\n\n"
    "Rules:\n"
    "- **Never propose what is already there.** Everything you propose is "
    "checked against the board by title and against the repository by the "
    "terms you name, and anything that matches is discarded before anyone "
    "sees it. Padding the list makes it shorter, not longer.\n"
    "- Finished work counts as there. A task marked done is done.\n"
    "- A milestone is a checkpoint someone would report upward, with a "
    "condition you can test. A task is one sitting's work. Do not propose "
    "a milestone that is really a task, or a task that is really a theme.\n"
    "- For each item, name what would already be in the repository if it "
    "had been done. Specific identifiers, not words like 'test' or 'api'.\n"
    "- Fit the project as it is. A project with no tasks at all needs a "
    "first few, not a full backlog. A project deep in delivery needs what "
    "is missing from the end, not a restatement of the beginning.\n"
    "- Propose nothing rather than something weak. An empty list is a "
    "legitimate answer and a common one.\n"
    + "\n"
    + formatting.INLINE
)


# --- taking the model's word with a pinch of salt ------------------------
#
# `required` inside an array's items is not enforced, so everything from
# the model is coerced here before anything downstream reads it. The same
# lesson as `survey.normalise`, learned the same way.


def as_item(raw, extra_keys=()) -> Optional[dict]:
    if isinstance(raw, str):
        text = raw.strip()
        return {"title": text, "why": text, "look_for": []} if text else None
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    item = {
        "title": title,
        "why": str(raw.get("why") or "").strip() or "No reason given.",
        "look_for": [
            str(t).strip() for t in (raw.get("look_for") or []) if str(t).strip()
        ],
    }
    for key in extra_keys:
        value = raw.get(key)
        if value not in (None, ""):
            item[key] = value
    return item


def normalise(result: dict) -> dict:
    return {
        "where_it_stands": str(result.get("where_it_stands") or "").strip(),
        "milestones": [
            item
            for item in (
                as_item(m, ("detail", "after"))
                for m in (result.get("milestones") or [])
            )
            if item
        ],
        "tasks": [
            item
            for item in (
                as_item(t, ("milestone", "estimate_hours", "value"))
                for t in (result.get("tasks") or [])
            )
            if item
        ],
    }


# --- the check -----------------------------------------------------------


def sift(
    items: list[dict],
    snap: dict,
    kinds: tuple,
    root=None,
    built: Optional[dict] = None,
) -> dict:
    """Split proposals into the ones worth raising and the ones already there.

    Two passes, cheapest first. The board is in memory, so title overlap
    costs nothing; the repository search costs a `git grep` per term and
    only runs on what survives.

    Both lists come back. A proposal dropped in silence is
    indistinguishable from one the model never made, and the difference
    matters when you are deciding whether to trust the filter.
    """
    keep: list[dict] = []
    dropped: list[dict] = []

    for item in items:
        on_board = board.covered(snap, item["title"], kinds)
        if on_board:
            dropped.append({**item, "already_there_because": on_board})
            continue

        terms = item.get("look_for") or []
        if root is None or not terms:
            keep.append(
                {
                    **item,
                    "verified": False,
                    "possibly_related": (
                        inventory.related(item["title"], built) if built else []
                    ),
                }
            )
            continue

        evidence = [
            why
            for why in (inventory.already_there(root, term, built) for term in terms)
            if why
        ]
        if evidence:
            dropped.append({**item, "already_there_because": evidence[0]})
        else:
            keep.append(
                {
                    **item,
                    "verified": True,
                    "checked": terms,
                    "possibly_related": inventory.related(item["title"], built),
                }
            )

    return {"keep": keep, "dropped": dropped}


def evidence_for(item: dict, kind: str) -> list[str]:
    lines = [item["why"]]
    if item.get("checked"):
        lines.append(
            "Checked the repository for "
            + ", ".join(f"`{t}`" for t in item["checked"])
            + " -- none found."
        )
    elif not item.get("verified"):
        lines.append(
            "Not verified against the code: nothing specific was named to "
            "check for, or this project has no local folder."
        )
    lines.append(f"Checked against every {kind} already on the board by title.")
    if item.get("possibly_related"):
        lines.append(
            "Worth checking: the repository already defines "
            + ", ".join(f"`{n}`" for n in item["possibly_related"][:5])
            + ", which may already cover it."
        )
    if item.get("milestone"):
        lines.append(f"Belongs under `{item['milestone']}`.")
    if item.get("after"):
        lines.append(f"Follows `{item['after']}`.")
    if item.get("estimate_hours"):
        lines.append(f"Guessed at {item['estimate_hours']}h.")
    return lines


def raise_items(
    db: Session, project, items: list[dict], field: str
) -> list[models.Suggestion]:
    """Turn what survived into suggestions against the project.

    Fingerprinted like every other suggestion, so running this twice does
    not double the list and a dismissed proposal stays dismissed.
    """
    existing = set(db.execute(select(models.Suggestion.fingerprint)).scalars())
    rule = f"roadmap_{field}"
    created = []
    for item in items:
        mark = review._fingerprint(rule, "project", project.id, item["title"])
        if mark in existing:
            continue
        existing.add(mark)
        suggestion = models.Suggestion(
            rule=rule,
            fingerprint=mark,
            target_type="project",
            target_id=project.id,
            field=field,
            current_value=None,
            proposed_value=item["title"],
            rationale=item["why"],
            evidence=json.dumps(evidence_for(item, field)),
        )
        db.add(suggestion)
        created.append(suggestion)
    return created


def context(snap: dict, inv: Optional[dict], turned_down: list[str]) -> str:
    parts = [board.as_prompt(snap)]
    if inv is not None:
        parts.append(
            "\n\n# What the code already does\n\n"
            "Read from the repository. Work visible here is done, whether or "
            "not the board says so.\n\n"
            + inventory.as_prompt(inv, max_chars=14_000)
        )
    else:
        parts.append(
            "\n\n# The code\n\nThis project has no local checkout, so the code "
            "could not be read. Anything you propose is checked against the "
            "board only -- say so in your reasoning where it matters."
        )
    if turned_down:
        parts.append(
            "\n\n# Proposed before and turned down -- do not propose again\n\n"
            + "\n".join(f"- {title}" for title in turned_down[:40])
        )
    return "\n".join(parts)


def dismissed_titles(db: Session, project_id: int) -> list[str]:
    return list(
        db.execute(
            select(models.Suggestion.proposed_value).where(
                models.Suggestion.rule.in_(
                    ("roadmap_milestone", "roadmap_task", "inventory_gap")
                ),
                models.Suggestion.target_id == project_id,
                models.Suggestion.status == "dismissed",
            )
        ).scalars()
    )


async def run(db: Session, project, *, raise_suggestions: bool = True) -> dict:
    """Read the board, read the code, and propose only what is on neither."""
    snap = board.snapshot(db, project)

    root = None
    built = None
    inv = None
    try:
        root = workspace.resolve(project.local_path)
        built = inventory.index(root)
        inv = built["inventory"]
    except workspace.WorkspaceError:
        # A project with no checkout is a normal project. The board check
        # still runs; only the code check is unavailable, and the caller is
        # told which.
        pass

    if not ai.is_configured():
        return {
            "board": snap,
            "where_it_stands": None,
            "milestones": [],
            "tasks": [],
            "already_there": [],
            "code_was_read": inv is not None,
            "reason": ai.status()["reason"],
        }

    result = normalise(
        await ai.structured(
            system=SYSTEM,
            prompt=context(snap, inv, dismissed_titles(db, project.id)),
            schema=ROADMAP_SCHEMA,
            tool_name="propose_roadmap",
            model=ai.CHAT_MODEL,
            max_tokens=MAX_ROADMAP_TOKENS,
            feature="roadmap",
            project_id=project.id,
        )
    )

    milestones = sift(result["milestones"], snap, ("milestone",), root, built)
    tasks = sift(result["tasks"], snap, ("task", "milestone"), root, built)

    suggestions = []
    if raise_suggestions:
        suggestions += raise_items(db, project, milestones["keep"], "milestone")
        suggestions += raise_items(db, project, tasks["keep"], "task")
        db.commit()

    return {
        "board": snap,
        "where_it_stands": result["where_it_stands"],
        "milestones": milestones["keep"],
        "tasks": tasks["keep"],
        "already_there": [
            {**item, "kind": "milestone"} for item in milestones["dropped"]
        ]
        + [{**item, "kind": "task"} for item in tasks["dropped"]],
        "code_was_read": inv is not None,
        "suggestions": [
            {"id": s.id, "field": s.field, "proposed_value": s.proposed_value}
            for s in suggestions
        ],
        "reason": None,
    }
