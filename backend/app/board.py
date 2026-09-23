"""What the project already has on it, read before anything is proposed.

`inventory.py` is the equivalent for the code: it answers "what is built"
so nothing already built gets suggested. This answers the same question for
the board -- what milestones exist, what tasks exist and in what state,
what has been written down, where the hours went -- so that nothing already
planned gets suggested either.

The two failures are the same failure. A suggestion to build what exists
and a suggestion to plan what is already planned both teach you that the
suggestions are not worth reading.

There is one difference worth being explicit about. Code can be searched:
`list_widgets` is in the repository or it is not. A piece of work cannot,
because the same task gets written down five different ways. So the check
here is vocabulary overlap between titles, tuned to over-match rather than
under-match: missing a real gap is a gap you notice yourself, whereas
proposing what is already on the board is what makes the feature
worthless.

Its limit is worth knowing. It matches paraphrases that share words --
"Add rate limiting" against "Rate-limit the API" -- and light inflections,
so "Beta released" catches "Release the beta". It does **not** match
synonyms: "Back up the database" and "Database dump script" are the same
work and this will not say so. That is what the repository check in
`roadmap.sift` is for, and why there are two checks rather than one.

Nothing here calls a model, and nothing here writes.
"""

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

# Two titles sharing this much of their significant vocabulary are the same
# piece of work. Loose enough to catch "Add rate limiting" against
# "Rate-limit the API", tight enough not to collapse everything about the
# API into one item.
TITLE_OVERLAP = 0.6

# Words that carry no signal when comparing two pieces of work. Almost
# every task title starts with one of the verbs.
STOPWORDS = {
    "a", "an", "the", "to", "for", "of", "in", "on", "and", "or", "with",
    "it", "its", "this", "that", "is", "be", "as", "at", "by", "from",
    "add", "make", "use", "support", "create", "build", "set", "up",
    "implement", "improve", "update", "write", "new", "some", "more",
}

# How much of the board to show a model. Long enough to be complete for a
# real project, short enough that the prompt is not mostly backlog.
MAX_TASKS = 120
MAX_NOTES = 25


# Two characters, because `CI`, `UI` and `DB` are the whole content of the
# titles they appear in. The stopword list does the filtering that a length
# floor was doing badly.
MIN_WORD = 2

# A stem has to be left standing afterwards, or `thing` becomes `th` and
# starts matching everything.
MIN_STEM = 4

# Order matters, longest first. The trailing `e` is last and is what
# makes `release` and `released` agree: one loses `ed`, the other loses
# its `e`, and both land on `releas`.
SUFFIXES = ("ing", "ed", "es", "s", "e")


def stem(word: str) -> str:
    """Crude suffix trimming, so `released` and `release` are one word.

    Not a real stemmer and not trying to be. It exists for the single
    commonest way the same work gets written twice -- one person writes
    "Release the beta", the next writes "Beta released" -- and anything
    cleverer would need a dictionary to be worth the trouble.
    """
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= MIN_STEM:
            return word[: -len(suffix)]
    return word


def significant(title: str) -> set[str]:
    """The words in a title that actually say what the work is."""
    words = {
        word.strip(".,:;()[]`\"'!?")
        for word in (title or "").replace("-", " ").replace("/", " ").lower().split()
    }
    return {stem(w) for w in words if len(w) >= MIN_WORD and w not in STOPWORDS}


def overlaps(title: str, existing: str) -> bool:
    """Whether two pieces of work are the same thing said differently.

    Measured against the *shorter* of the two, so "Rate limit the API"
    matches "Add rate limiting to the API endpoints" -- a longer, more
    specific restatement of a thing is still that thing.
    """
    a, b = significant(title), significant(existing)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= TITLE_OVERLAP


def covered(snapshot: dict, title: str, kinds=("task", "milestone")) -> Optional[str]:
    """Whether this is already on the board, in any state.

    Finished work counts. "You should do X" about something delivered last
    month is the same failure as proposing what is already in the code, and
    the board is where that shows.
    """
    for kind in kinds:
        for item in snapshot.get(f"{kind}s", []):
            if overlaps(title, item["title"]):
                state = item.get("status", "")
                how = "already done" if state == "done" else f"already {state}"
                return f'{kind} "{item["title"]}" covers this and is {how}'
    return None


def snapshot(db: Session, project) -> dict:
    """Everything the project already has, as rows rather than prose."""
    milestones = list(
        db.execute(
            select(models.Milestone)
            .where(models.Milestone.project_id == project.id)
            .order_by(models.Milestone.position, models.Milestone.id)
        ).scalars()
    )
    tasks = list(
        db.execute(
            select(models.Task).where(models.Task.project_id == project.id)
        ).scalars()
    )
    notes = list(
        db.execute(
            select(models.Note)
            .where(models.Note.project_id == project.id)
            .order_by(models.Note.created_at.desc())
            .limit(MAX_NOTES)
        ).scalars()
    )
    logs = list(
        db.execute(
            select(models.TimeLog).where(models.TimeLog.project_id == project.id)
        ).scalars()
    )
    commits = list(
        db.execute(
            select(models.ActivityEvent)
            .where(
                models.ActivityEvent.project_id == project.id,
                models.ActivityEvent.kind == "commit",
            )
            .order_by(models.ActivityEvent.occurred_at.desc())
            .limit(200)
        ).scalars()
    )

    by_milestone: dict[Optional[int], list[models.Task]] = {}
    for task in tasks:
        by_milestone.setdefault(task.milestone_id, []).append(task)

    status_counts: dict[str, int] = {}
    for task in tasks:
        status_counts[task.status] = status_counts.get(task.status, 0) + 1

    return {
        "project": {
            "name": project.name,
            "summary": project.summary,
            "objective": project.objective,
            "definition_of_done": project.definition_of_done,
            "status": project.status,
            "target_date": project.target_date,
        },
        "milestones": [
            {
                "id": m.id,
                "title": m.title,
                "detail": m.detail,
                "status": m.status,
                "due_date": m.due_date,
                "position": m.position,
                "tasks": len(by_milestone.get(m.id, [])),
                "tasks_done": sum(
                    1 for t in by_milestone.get(m.id, []) if t.status == "done"
                ),
            }
            for m in milestones
        ],
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "milestone_id": t.milestone_id,
                "estimate_hours": t.estimate_hours,
                "due_date": t.due_date,
            }
            for t in tasks[:MAX_TASKS]
        ],
        "task_counts": status_counts,
        "unassigned_tasks": len(by_milestone.get(None, [])),
        "notes": [
            {"title": n.title, "kind": n.kind, "body": n.body} for n in notes
        ],
        "hours_logged": round(sum(log.hours for log in logs), 1),
        "commits": len(commits),
        "last_commit": commits[0].occurred_at if commits else None,
        "totals": {
            "milestones": len(milestones),
            "milestones_done": sum(1 for m in milestones if m.status == "done"),
            "tasks": len(tasks),
            "tasks_done": sum(1 for t in tasks if t.status == "done"),
            "tasks_truncated": max(0, len(tasks) - MAX_TASKS),
        },
    }


def as_prompt(snap: dict, note_chars: int = 2_000) -> str:
    """The board written out for a model to read before it proposes anything.

    Tasks are listed in full rather than counted. A count tells a model
    that work exists; the titles are what stop it proposing the same work
    again, which is the entire point of showing it.
    """
    project = snap["project"]
    parts = [f"# The project\n\nName: {project['name']}"]
    for label, key in (
        ("Summary", "summary"),
        ("Objective", "objective"),
        ("Done looks like", "definition_of_done"),
    ):
        if project.get(key):
            parts.append(f"{label}: {project[key]}")
    parts.append(f"Status: {project['status']}")
    if project.get("target_date"):
        parts.append(f"Target date: {project['target_date']}")

    totals = snap["totals"]
    parts.append(
        f"\n# What is on the board already\n\n"
        f"{totals['milestones']} milestones ({totals['milestones_done']} done), "
        f"{totals['tasks']} tasks ({totals['tasks_done']} done), "
        f"{snap['hours_logged']}h logged, {snap['commits']} commits ingested."
    )

    parts.append("\n## Milestones -- do not propose any of these again\n")
    if snap["milestones"]:
        for m in snap["milestones"]:
            line = f"- [{m['status']}] {m['title']}"
            if m["due_date"]:
                line += f" (due {m['due_date']})"
            if m["tasks"]:
                line += f" — {m['tasks_done']}/{m['tasks']} tasks done"
            parts.append(line)
            if m["detail"]:
                parts.append(f"    {m['detail'][:200]}")
    else:
        parts.append("(none — this project has no milestones at all)")

    parts.append("\n## Tasks -- do not propose any of these again\n")
    if snap["tasks"]:
        for t in snap["tasks"]:
            parts.append(f"- [{t['status']}] {t['title']}")
        if totals["tasks_truncated"]:
            parts.append(
                f"\n**{totals['tasks_truncated']} further tasks were not listed.** "
                "Treat anything you did not see as possibly present, not absent."
            )
    else:
        parts.append("(none — this project has no tasks at all)")

    if snap["notes"]:
        parts.append("\n## What has been written down\n")
        for note in snap["notes"]:
            head = f"- [{note['kind']}] {note['title'] or '(untitled)'}"
            body = (note["body"] or "").strip()
            if body:
                head += f"\n{body[:note_chars]}"
                if len(body) > note_chars:
                    head += "\n  … (truncated)"
            parts.append(head)

    return "\n".join(parts)


# Filenames say what the work is about at least as often as a note does:
# a change to `powerbi.py` in a project with a task called "Power BI
# delegated sign-in" is the same work, and nothing but the path says so.
def words_in_path(path: str) -> set[str]:
    """The significant words in a file path, treated like a title.

    Extensions and directory names people repeat everywhere are dropped --
    `src`, `app`, `components` describe where a file lives rather than what
    it does, and matching on them would tie every change to every task.
    """
    parts = (path or "").replace("\\", "/").split("/")
    if parts and "." in parts[-1]:
        parts[-1] = parts[-1].rsplit(".", 1)[0]
    words = significant(" ".join(parts).replace("_", " "))
    return words - PLACE_WORDS


PLACE_WORDS = {
    "src", "app", "apps", "lib", "libs", "component", "components", "page",
    "pages", "rout", "routes", "backend", "frontend", "server", "client",
    "util", "utils", "test", "tests", "index", "main", "public", "static",
    "style", "styles", "config", "core", "common", "shared", "modul",
}

# How much of a task's vocabulary has to appear in the change before it is
# worth offering. Lower than TITLE_OVERLAP because the evidence is thinner:
# a path and a short note against a whole title.
LINK_OVERLAP = 0.34


def likely_tasks(db: Session, project, path: str, note: str = "", limit: int = 3):
    """Open tasks this change might belong to, best first.

    A guess, offered and never applied. The cost of a wrong guess here is
    that someone picks the right one from a list instead of a shorter list,
    which is why it is tuned to suggest rather than to be certain -- and
    why nothing is attached until a person chooses it.

    Only open tasks. Attaching a change to something already finished is
    almost always a sign the finished task was the wrong match.
    """
    haystack = words_in_path(path) | significant(note or "")
    if not haystack:
        return []

    scored = []
    for task in db.execute(
        select(models.Task).where(
            models.Task.project_id == project.id,
            models.Task.status != "done",
        )
    ).scalars():
        wanted = significant(task.title)
        if not wanted:
            continue
        hits = wanted & haystack
        score = len(hits) / len(wanted)
        if score >= LINK_OVERLAP:
            scored.append(
                {
                    "task_id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "score": round(score, 2),
                    "matched": sorted(hits),
                }
            )
    scored.sort(key=lambda row: (-row["score"], row["title"]))
    return scored[:limit]
