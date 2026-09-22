"""Reading the whole repository, and proposing only what is not there yet.

Three steps, and the middle one is the only one that costs money.

1. `inventory.scan` reads the repository: every module, route, table,
   component and configuration variable. Free, and true.
2. The model is given that inventory and asked for two things -- an account
   of what the project *does*, feature by feature, and a list of what is
   missing.
3. Every gap is then checked back against the repository before it becomes
   a suggestion. A gap whose evidence turns up is dropped.

Step three is the whole point. A model shown a large codebase will
confidently suggest adding something it did not notice, and one suggestion
like that costs more trust than ten good ones earn. So the model is made to
commit, for each gap, to what it would expect to find if the thing already
existed -- and those terms are searched for. It is not asked to be right;
it is asked to be checkable.

The surviving gaps become suggestions against the project, which accepting
turns into tasks. The feature outline becomes a note. Neither overwrites
anything you wrote.
"""

import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai, board, formatting, inventory, models, review, workspace

# The outline is prose with a dozen features and a dozen gaps in it.
MAX_SURVEY_TOKENS = 8_000

# The note the outline is written into. Found by title so a second run
# updates it rather than leaving the project with five of them.
NOTE_TITLE = "What this project does"



SURVEY_SCHEMA = {
    "type": "object",
    "properties": {
        "what_it_is": {
            "type": "string",
            "description": (
                "What this project is and does, in 3-5 sentences, for "
                "someone who has never seen it. Written from the inventory, "
                "not from the name."
            ),
        },
        "features": {
            "type": "array",
            "description": (
                "Everything the project can already do, one entry per "
                "capability. Be thorough -- this is the record of what is "
                "built, and anything missing from it risks being suggested "
                "as new work later."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short, e.g. 'Time tracking'."},
                    "what_it_does": {"type": "string"},
                    "where": {
                        "type": "array",
                        "description": "The files it lives in, from the inventory.",
                        "items": {"type": "string"},
                        "maxItems": 6,
                    },
                    "state": {
                        "type": "string",
                        "enum": ["complete", "partial", "scaffolded"],
                        "description": (
                            "complete: it works end to end. partial: some of "
                            "it is there. scaffolded: the shape exists but "
                            "little behind it."
                        ),
                    },
                },
                "required": ["name", "what_it_does", "state"],
            },
            "maxItems": 30,
        },
        "gaps": {
            "type": "array",
            "description": (
                "What would make this project better and is NOT already "
                "there. Every one of these will be checked against the "
                "repository before it is shown to anyone, so a gap that "
                "already exists will be caught and discarded -- there is "
                "nothing to gain by padding the list."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "What to do, as a task title. Imperative and "
                            "specific: 'Paginate the activity feed', not "
                            "'Improve performance'."
                        ),
                    },
                    "why": {
                        "type": "string",
                        "description": (
                            "Why it matters here, referring to what you saw "
                            "in the inventory."
                        ),
                    },
                    "look_for": {
                        "type": "array",
                        "description": (
                            "THE IMPORTANT FIELD. Identifiers, file names, "
                            "route paths or configuration keys that would "
                            "already exist in this repository if this work "
                            "had been done. Be specific and be generous -- "
                            "four or five distinct terms. If any of them is "
                            "found, this gap is discarded as already done. "
                            "Do not give vague words like 'test' or 'api'."
                        ),
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 8,
                    },
                    "area": {
                        "type": "string",
                        "description": "Where it would go, e.g. `backend/app`.",
                    },
                    "size": {"type": "string", "enum": ["small", "medium", "large"]},
                    "value": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["title", "why", "look_for"],
            },
            "maxItems": 15,
        },
    },
    "required": ["what_it_is", "features", "gaps"],
}


SYSTEM = (
    "You survey a codebase and report what it does and what it is missing.\n\n"
    "You are given an inventory read out of the repository itself: every "
    "module with the definitions in it, every HTTP route, every database "
    "table, every page and every configuration variable. It is complete "
    "unless it says otherwise. Treat it as fact.\n\n"
    "Two jobs.\n\n"
    "**The features.** Go through the inventory and write down what this "
    "project can already do. Be thorough and be concrete -- name the files. "
    "This is the record of what exists, and a capability you leave out is "
    "one that may be proposed as new work later.\n\n"
    "**The gaps.** What would genuinely make it better, and is not there.\n"
    "- Never propose something the inventory shows already exists. Every "
    "gap is checked against the repository afterwards, and one that is "
    "already built gets discarded -- so a padded list is not a longer list, "
    "it is a shorter one.\n"
    "- For each gap, name what you would find in this repository if it had "
    "already been done: function names, file names, route paths, config "
    "keys. Specific ones. That is what the check uses.\n"
    "- Propose work that fits this project. A personal single-user tracker "
    "does not need multi-tenancy, and a tool with no users does not need "
    "an onboarding flow.\n"
    "- Prefer a few things that matter to a dozen that do not. An empty "
    "list is a legitimate answer.\n"
    "- Do not propose 'add more tests' as a gap on its own. Say which "
    "untested thing, and why that one.\n"
    + "\n"
    + formatting.INLINE
)


# --- taking the model's word with a pinch of salt ------------------------
#
# A tool schema's *top-level* `required` is enforced; `required` inside an
# array's items is not. Asked for a list of objects, a model will sometimes
# return a list of strings -- which it did on the first real run of this,
# and which took the whole request down with an AttributeError deep inside
# the renderer. Everything from the model is coerced here, once, so that
# nothing downstream has to wonder.


def as_feature(item) -> Optional[dict]:
    """One feature, whatever shape it arrived in."""
    if isinstance(item, str):
        text = item.strip()
        return {"name": text[:60], "what_it_does": text, "where": [], "state": "complete"} if text else None
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    what = str(item.get("what_it_does") or "").strip()
    if not name and not what:
        return None
    where = [str(w) for w in (item.get("where") or []) if w]
    state = item.get("state")
    return {
        "name": name or what[:60],
        "what_it_does": what or name,
        "where": where,
        "state": state if state in ("complete", "partial", "scaffolded") else "complete",
    }


def as_gap(item) -> Optional[dict]:
    """One gap, or None if there is not enough of it to act on.

    A gap with no title is not a gap. A gap with no `look_for` is kept but
    will be marked unverified, because "we could not check this" is a
    different thing from "we checked and it is missing".
    """
    if isinstance(item, str):
        text = item.strip()
        return {"title": text, "why": text, "look_for": []} if text else None
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    return {
        **{k: v for k, v in item.items() if k not in ("title", "why", "look_for")},
        "title": title,
        "why": str(item.get("why") or "").strip() or "No reason given.",
        "look_for": [str(t).strip() for t in (item.get("look_for") or []) if str(t).strip()],
    }


def normalise(result: dict) -> dict:
    """The model's answer, in the shape the rest of this module expects."""
    return {
        "what_it_is": str(result.get("what_it_is") or "").strip(),
        "features": [f for f in map(as_feature, result.get("features") or []) if f],
        "gaps": [g for g in map(as_gap, result.get("gaps") or []) if g],
    }


def already_tracked(db: Session, project_id: int, title: str) -> Optional[str]:
    """Whether this is already on the board, in any state.

    Delegates to `board.covered`, which is the same comparison the roadmap
    uses -- one notion of "we already have that" rather than two that
    drift apart.
    """
    project = db.get(models.Project, project_id)
    return board.covered(board.snapshot(db, project), title)


def context(db: Session, project, inv: dict) -> str:
    """The inventory, plus what is already on the board about it."""
    parts = [inventory.as_prompt(inv)]

    facts = [f"Name: {project.name}"]
    if project.summary:
        facts.append(f"Current summary: {project.summary}")
    if project.objective:
        facts.append(f"Objective: {project.objective}")
    if project.definition_of_done:
        facts.append(f"Done looks like: {project.definition_of_done}")
    parts.append("\n# The project as the user describes it\n\n" + "\n".join(facts))

    tasks = list(
        db.execute(
            select(models.Task).where(models.Task.project_id == project.id)
        ).scalars()
    )
    if tasks:
        parts.append(
            "\n# Already on the board -- do not propose any of these\n\n"
            + "\n".join(f"- [{t.status}] {t.title}" for t in tasks[:60])
        )

    turned_down = list(
        db.execute(
            select(models.Suggestion).where(
                models.Suggestion.rule == "inventory_gap",
                models.Suggestion.target_id == project.id,
                models.Suggestion.status == "dismissed",
            )
        ).scalars()
    )
    if turned_down:
        parts.append(
            "\n# Proposed before and turned down -- do not propose again\n\n"
            + "\n".join(f"- {s.proposed_value}" for s in turned_down[:30])
        )

    return "\n".join(parts)


def write_note(db: Session, project, result: dict) -> models.Note:
    """Put the outline in the project's notes, updating rather than piling up."""
    body = render_outline(result)
    note = db.execute(
        select(models.Note).where(
            models.Note.project_id == project.id,
            models.Note.title == NOTE_TITLE,
        )
    ).scalars().first()
    if note is None:
        note = models.Note(
            project_id=project.id, title=NOTE_TITLE, kind="note", source="agent"
        )
        db.add(note)
    note.body = body
    note.source = "agent"
    return note


def render_outline(result: dict) -> str:
    """The outline as Markdown, which is what a note is rendered as."""
    lines = [result["what_it_is"], ""]
    features = result["features"]
    if features:
        lines.append("## What it does\n")
        for feature in features:
            state = feature["state"]
            mark = {"complete": "", "partial": " *(partial)*", "scaffolded": " *(scaffolded)*"}
            where = feature["where"]
            lines.append(
                f"- **{feature['name']}**{mark.get(state, '')} — "
                f"{feature['what_it_does']}"
                + (f" `{'`, `'.join(where[:4])}`" if where else "")
            )
    return "\n".join(lines).strip()


def raise_gaps(db: Session, project, gaps: list[dict]) -> list[models.Suggestion]:
    """Turn the surviving gaps into suggestions against the project.

    Fingerprinted like every other suggestion, so running the survey twice
    does not double the list and a dismissed gap stays dismissed.
    """
    existing = set(db.execute(select(models.Suggestion.fingerprint)).scalars())
    created = []
    for gap in gaps:
        title = gap["title"].strip()
        mark = review._fingerprint("inventory_gap", "project", project.id, title)
        if mark in existing:
            continue
        existing.add(mark)
        evidence = [gap["why"]]
        if gap.get("checked"):
            evidence.append(
                "Checked against the repository for "
                + ", ".join(f"`{t}`" for t in gap["checked"])
                + " -- none found."
            )
        elif not gap.get("verified", True):
            evidence.append(
                "Not verified: the model named nothing specific to check for."
            )
        if gap.get("possibly_related"):
            # Weaker than a `look_for` hit, so it warns rather than drops --
            # but it is the check that would have caught a proposal to add
            # per-project protected paths to a codebase that already had
            # them under a name the model did not think to search for.
            evidence.append(
                "Worth checking: this repository already defines "
                + ", ".join(f"`{n}`" for n in gap["possibly_related"][:5])
                + ", which may already cover it."
            )
        if gap.get("area"):
            evidence.append(f"Would go in `{gap['area']}`.")
        suggestion = models.Suggestion(
            rule="inventory_gap",
            fingerprint=mark,
            target_type="project",
            target_id=project.id,
            field="task",
            current_value=None,
            proposed_value=title,
            rationale=gap["why"],
            evidence=json.dumps(evidence),
        )
        db.add(suggestion)
        created.append(suggestion)
    return created


async def run(db: Session, project, *, raise_suggestions: bool = True) -> dict:
    """Survey the repository and propose what is genuinely missing."""
    root = workspace.resolve(project.local_path)
    built = inventory.index(root)
    inv = built["inventory"]

    if not ai.is_configured():
        return {
            "inventory": inv,
            "outline": None,
            "gaps": [],
            "already_done": [],
            "suggestions": [],
            "reason": ai.status()["reason"],
        }

    result = await ai.structured(
        system=SYSTEM,
        prompt=context(db, project, inv),
        schema=SURVEY_SCHEMA,
        tool_name="survey_repository",
        model=ai.CHAT_MODEL,
        max_tokens=MAX_SURVEY_TOKENS,
        feature="survey",
        project_id=project.id,
    )

    result = normalise(result)
    checked = inventory.verify(root, result["gaps"], built)
    kept, dropped = checked["gaps"], checked["already_done"]

    # And the board, which the repository cannot tell us about.
    surviving = []
    for gap in kept:
        tracked = already_tracked(db, project.id, gap["title"])
        if tracked:
            dropped.append({**gap, "already_done_because": tracked})
        else:
            surviving.append(gap)

    note = write_note(db, project, result)
    suggestions = raise_gaps(db, project, surviving) if raise_suggestions else []
    db.commit()

    return {
        "inventory": inv,
        "outline": {
            "what_it_is": result["what_it_is"],
            "features": result["features"],
        },
        "note_id": note.id,
        "gaps": surviving,
        "already_done": dropped,
        "suggestions": [
            {"id": s.id, "proposed_value": s.proposed_value, "rationale": s.rationale}
            for s in suggestions
        ],
        "reason": None,
    }
