"""What the model is told, and what it is asked for.

Kept apart from `ai.py`, which only knows how to make a call. This module
decides what leaves the machine, and it builds that from explicit queries so
the answer to "what did it send?" is readable here rather than inferred from
a prompt string.

The assistant reads, and writes in exactly one direction: it may **add** a
note, stamped as agent-written, and it may **propose** a change. It may never
overwrite something you wrote.

That line matters more than it looks. The digest often has a better sentence
than the project summary you typed six weeks ago -- and replacing it would
mean that, over time, nobody could tell which words in the tracker were
anyone's. So a note (additive, attributed, harmless) lands on its own, and a
summary rewrite becomes a suggestion on the Review page with the old text
kept beside the new one.
"""

import json
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai, models

PROJECT_STATUSES = ["idea", "planning", "active", "on_hold", "done", "archived"]
PROJECT_CATEGORIES = ["research", "build", "analysis", "learning", "ops", "other"]
PRIORITIES = ["low", "medium", "high"]

# How much of the tracker the chat is given. Enough to answer "what should I
# be doing" without posting the entire database on every turn.
CHAT_PROJECT_LIMIT = 25
CHAT_TASK_LIMIT = 40
CHAT_NOTE_LIMIT = 10
CHAT_ACTIVITY_LIMIT = 25


# --- What the model is told about the work ----------------------------------


def tracker_context(db: Session) -> str:
    """A compact snapshot of the tracker, as text.

    Text rather than JSON because it is read, not parsed, and prose costs
    fewer tokens than braces for the same content.
    """
    lines: list[str] = [f"Today is {date.today().isoformat()}."]

    projects = list(
        db.execute(
            select(models.Project)
            .where(models.Project.archived_at.is_(None))
            .order_by(models.Project.priority, models.Project.name)
            .limit(CHAT_PROJECT_LIMIT)
        ).scalars()
    )
    if projects:
        lines.append("\nPROJECTS")
        for p in projects:
            bits = [f"#{p.id} {p.name} [{p.status}/{p.category}/{p.priority}]"]
            if p.target_date:
                bits.append(f"target {p.target_date.isoformat()}")
            if p.repo:
                bits.append(f"repo {p.repo}")
            lines.append("  " + ", ".join(bits))
            if p.summary:
                lines.append(f"      {ai.clip(p.summary, 300)}")

    tasks = list(
        db.execute(
            select(models.Task)
            .where(models.Task.status != "done")
            .order_by(models.Task.due_date.is_(None), models.Task.due_date)
            .limit(CHAT_TASK_LIMIT)
        ).scalars()
    )
    if tasks:
        lines.append("\nOPEN TASKS")
        for t in tasks:
            bits = [f"#{t.id} {t.title} [{t.status}/{t.priority}]"]
            if t.project_id:
                bits.append(f"project {t.project_id}")
            if t.due_date:
                bits.append(f"due {t.due_date.isoformat()}")
            if t.blocked_reason:
                bits.append(f"blocked: {ai.clip(t.blocked_reason, 120)}")
            lines.append("  " + ", ".join(bits))

    notes = list(
        db.execute(
            select(models.Note)
            .order_by(models.Note.created_at.desc())
            .limit(CHAT_NOTE_LIMIT)
        ).scalars()
    )
    if notes:
        lines.append("\nRECENT NOTES")
        for n in notes:
            lines.append(f"  [{n.kind}] {n.title or '(untitled)'}: {ai.clip(n.body, 300)}")

    events = list(
        db.execute(
            select(models.ActivityEvent)
            .order_by(models.ActivityEvent.occurred_at.desc())
            .limit(CHAT_ACTIVITY_LIMIT)
        ).scalars()
    )
    if events:
        lines.append("\nRECENT GITHUB ACTIVITY")
        for e in events:
            lines.append(
                f"  {e.occurred_at:%Y-%m-%d} [{e.kind}] {ai.clip(e.title, 160)}"
                + (f" (task {e.task_id})" if e.task_id else "")
            )

    return ai.clip("\n".join(lines), ai.MAX_CONTEXT_CHARS)


def _vocabulary_note() -> str:
    return (
        f"Valid project statuses: {', '.join(PROJECT_STATUSES)}. "
        f"Valid categories: {', '.join(PROJECT_CATEGORIES)}. "
        f"Valid priorities: {', '.join(PRIORITIES)}."
    )


# --- Live suggestions while a form is open ----------------------------------

SUGGEST_SYSTEM = (
    "You help someone fill in a project tracker as they type. You are "
    "completing a form, not writing an essay.\n\n"
    "Rules:\n"
    "- Build only on what they have actually written. Do not invent "
    "stakeholders, dates, numbers or technologies they have not mentioned.\n"
    "- Match their voice: plain, specific, no marketing language, no "
    "'leverage', 'robust' or 'cutting-edge'.\n"
    "- Every field is optional. Omit a field rather than padding it, and omit "
    "anything that would just restate the title.\n"
    "- If the draft is too thin to say anything useful, return nothing.\n"
    "- Summaries are one or two sentences. Task titles are imperative and "
    "concrete: 'Pull the shift data' not 'Data acquisition phase'."
)

PROJECT_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One or two sentences on what this project is.",
        },
        "objective": {
            "type": "string",
            "description": "Why it is worth doing, in one or two sentences.",
        },
        "definition_of_done": {
            "type": "string",
            "description": "How you would know it is finished. Concrete and checkable.",
        },
        "category": {"type": "string", "enum": PROJECT_CATEGORIES},
        "priority": {"type": "string", "enum": PRIORITIES},
        "tech_stack": {
            "type": "string",
            "description": "Comma-separated tools, only if the draft implies them.",
        },
        "tasks": {
            "type": "array",
            "description": "Up to five concrete first steps.",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "questions": {
            "type": "array",
            "description": "Up to three things left genuinely unclear in the draft.",
            "items": {"type": "string"},
            "maxItems": 3,
        },
    },
}

TASK_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "A sharper version of their title. Omit if theirs is fine.",
        },
        "notes": {
            "type": "string",
            "description": "What doing this actually involves. Two or three lines.",
        },
        "priority": {"type": "string", "enum": PRIORITIES},
        "estimate_hours": {
            "type": "number",
            "description": "A realistic estimate in hours, if the task is clear enough.",
        },
        "subtasks": {
            "type": "array",
            "description": "Up to five steps, only if this is genuinely several jobs.",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "questions": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
    },
}


def draft_text(draft: dict) -> str:
    """The part of a draft worth spending a call on.

    Returns "" when there is nothing but whitespace and defaults, which is how
    the caller decides not to ask at all.
    """
    interesting = ("name", "title", "summary", "objective", "definition_of_done", "notes")
    parts = [str(draft.get(k) or "").strip() for k in interesting]
    return " ".join(p for p in parts if p).strip()


def _draft_prompt(draft: dict, project: Optional[models.Project], kind: str) -> str:
    filled = {k: v for k, v in draft.items() if str(v or "").strip()}
    lines = [
        f"They are creating a {kind}. This is the form so far:",
        json.dumps(filled, indent=2, default=str),
    ]
    if project is not None:
        lines.append(
            f"\nIt belongs to project #{project.id} {project.name} "
            f"[{project.status}/{project.category}]."
        )
        if project.objective:
            lines.append(f"That project's objective: {ai.clip(project.objective, 400)}")
        if project.tech_stack:
            lines.append(f"Its stack: {project.tech_stack}")
    lines.append("\n" + _vocabulary_note())
    lines.append(
        "\nSuggest only fields that would genuinely improve this draft. "
        "Leave out anything they already wrote well."
    )
    return ai.clip("\n".join(lines))


async def suggest_project(draft: dict) -> dict:
    return await ai.structured(
        system=SUGGEST_SYSTEM,
        prompt=_draft_prompt(draft, None, "project"),
        schema=PROJECT_DRAFT_SCHEMA,
        tool_name="suggest_project",
    )


async def suggest_task(draft: dict, project: Optional[models.Project]) -> dict:
    return await ai.structured(
        system=SUGGEST_SYSTEM,
        prompt=_draft_prompt(draft, project, "task"),
        schema=TASK_DRAFT_SCHEMA,
        tool_name="suggest_task",
    )


# --- Chat -------------------------------------------------------------------

CHAT_SYSTEM = (
    "You are the analyst inside a single-user project tracker. You know the "
    "user's projects, tasks, notes and GitHub activity, shown below.\n\n"
    "You can read but not write: you cannot create or change anything. When "
    "something should change, say exactly what and let them do it -- the "
    "Review page is where proposals get accepted.\n\n"
    "Be direct and short. Answer from the data given and cite it by id "
    "(#12) so they can check you. If the data does not cover the question, "
    "say so plainly instead of guessing. No preamble, no summarising the "
    "question back."
)


def chat_system(db: Session) -> str:
    return f"{CHAT_SYSTEM}\n\n--- THE TRACKER RIGHT NOW ---\n{tracker_context(db)}"


# --- Repo review ------------------------------------------------------------

REPO_SYSTEM = (
    "You are reviewing recent work in a git repository for the person who "
    "wrote it.\n\n"
    "You are given commit messages and, where available, the diffs. Judge "
    "only what is in front of you. If a concern depends on code you were not "
    "shown, say that rather than assuming.\n\n"
    "Be specific and technical. A finding that names a file and says what "
    "breaks is worth more than five general observations. Do not report "
    "style preferences as bugs, and do not pad the lists -- returning two "
    "real issues is a better answer than six weak ones."
)

REPO_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "What this work amounted to, in 2-4 sentences, for someone catching up.",
        },
        "themes": {
            "type": "array",
            "description": "The strands of work visible in these changes.",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "risks": {
            "type": "array",
            "description": "Possible bugs or correctness problems.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "where": {
                        "type": "string",
                        "description": "File or commit it comes from, if identifiable.",
                    },
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["title", "detail", "confidence"],
            },
            "maxItems": 8,
        },
        "improvements": {
            "type": "array",
            "description": "Worthwhile changes that are not bugs.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["title", "detail"],
            },
            "maxItems": 8,
        },
    },
    "required": ["summary"],
}


async def review_repo(*, repo: str, events: list, diffs: str) -> dict:
    """Ask for a summary, risks and improvements over recent repo activity."""
    lines = [f"Repository: {repo}", "", "RECENT ACTIVITY"]
    for e in events:
        lines.append(f"- {e.occurred_at:%Y-%m-%d} [{e.kind}] {ai.clip(e.title, 200)}")
    if diffs:
        lines.append("\nDIFFS")
        lines.append(diffs)
    else:
        lines.append(
            "\nNo diffs were available, so judge from the commit messages alone "
            "and say that the review is limited to them."
        )

    return await ai.structured(
        system=REPO_SYSTEM,
        prompt=ai.clip("\n".join(lines), ai.MAX_CONTEXT_CHARS * 3),
        schema=REPO_SCHEMA,
        tool_name="review_repo",
        model=ai.CHAT_MODEL,
        max_tokens=ai.MAX_REVIEW_TOKENS,
    )


# --- Project digest ---------------------------------------------------------

# A project summary is "one or two lines" in the form, and the column it lands
# in is 255 characters. Asking for something that fits is better than
# truncating a good sentence afterwards.
SUMMARY_LIMIT = 240

DIGEST_SYSTEM = (
    "You write a short progress update on one project, for the people "
    "working on it and the people who asked for it.\n\n"
    "Rules:\n"
    "- Say what moved and what did not. A digest that reads as though "
    "everything is fine when two tasks are overdue is worse than none.\n"
    "- Credit contributions to the person who made them, by name, from the "
    "activity you are given. Do not credit work to someone whose name is not "
    "in that list.\n"
    "- The audience includes people outside the work, so no unexplained "
    "internal shorthand.\n"
    "- Plain and specific. No 'we are excited to', no 'significant "
    "progress', no filler.\n"
    "- Judge only from what you are given. Silence in the data means you "
    "don't know, not that nothing happened."
)

DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "note": {
            "type": "string",
            "description": (
                "The progress update itself, 2-5 short paragraphs. This is "
                "what gets stored and shared."
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "A replacement one-or-two-sentence project summary, at most "
                f"{SUMMARY_LIMIT} characters. Omit unless the current one is "
                "now actually wrong or out of date."
            ),
        },
        "contributions": {
            "type": "array",
            "description": "One entry per person who did something, from the activity given.",
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string"},
                    "what": {"type": "string"},
                },
                "required": ["who", "what"],
            },
            "maxItems": 12,
        },
        "risks": {
            "type": "array",
            "description": "What is slipping or at risk, from the data.",
            "items": {"type": "string"},
            "maxItems": 6,
        },
        "open_questions": {
            "type": "array",
            "description": "Decisions waiting on someone, including unanswered feedback.",
            "items": {"type": "string"},
            "maxItems": 6,
        },
    },
    "required": ["note"],
}


def digest_context(db: Session, project: models.Project) -> str:
    """Everything the digest is written from, as text.

    Contributions are named here rather than left as GitHub logins: the
    digest is for people, and "arendse committed" reads as a machine's
    summary of a person.
    """
    lines = [
        f"PROJECT #{project.id}: {project.name}",
        f"status {project.status} / {project.category} / priority {project.priority}",
    ]
    if project.summary:
        lines.append(f"current summary: {ai.clip(project.summary, 500)}")
    if project.objective:
        lines.append(f"objective: {ai.clip(project.objective, 500)}")
    if project.definition_of_done:
        lines.append(f"definition of done: {ai.clip(project.definition_of_done, 500)}")
    if project.target_date:
        lines.append(
            f"target date: {project.target_date.isoformat()} "
            f"(today is {date.today().isoformat()})"
        )
    if project.repo:
        lines.append(f"repo: {project.repo}")

    members = db.execute(
        select(models.ProjectMember, models.Person)
        .join(models.Person, models.Person.id == models.ProjectMember.person_id)
        .where(models.ProjectMember.project_id == project.id)
    ).all()
    if members:
        lines.append("\nPEOPLE ON THIS PROJECT")
        for member, person in members:
            bits = [f"  {person.name} ({member.role}"]
            if person.role_title:
                bits.append(f", {person.role_title}")
            bits.append(")")
            lines.append("".join(bits))

    tasks = list(
        db.execute(
            select(models.Task).where(models.Task.project_id == project.id)
        ).scalars()
    )
    if tasks:
        done = [t for t in tasks if t.status == "done"]
        lines.append(f"\nTASKS ({len(done)} of {len(tasks)} done)")
        for task in tasks:
            bits = [f"  #{task.id} {task.title} [{task.status}]"]
            if task.due_date:
                bits.append(f"due {task.due_date.isoformat()}")
            if task.blocked_reason:
                bits.append(f"blocked: {ai.clip(task.blocked_reason, 150)}")
            lines.append(", ".join(bits))

    milestones = list(
        db.execute(
            select(models.Milestone)
            .where(models.Milestone.project_id == project.id)
            .order_by(models.Milestone.position)
        ).scalars()
    )
    if milestones:
        lines.append("\nMILESTONES")
        for milestone in milestones:
            due = f" due {milestone.due_date.isoformat()}" if milestone.due_date else ""
            lines.append(f"  {milestone.title} [{milestone.status}]{due}")

    events = list(
        db.execute(
            select(models.ActivityEvent)
            .where(models.ActivityEvent.project_id == project.id)
            .order_by(models.ActivityEvent.occurred_at.desc())
            .limit(40)
        ).scalars()
    )
    if events:
        names = {
            p.id: p.name for p in db.execute(select(models.Person)).scalars()
        }
        lines.append("\nRECENT ACTIVITY (who did what)")
        for event in events:
            who = names.get(event.person_id) or event.actor or "unknown"
            lines.append(
                f"  {event.occurred_at:%Y-%m-%d} {who}: [{event.kind}] "
                f"{ai.clip(event.title, 160)}"
            )

    feedback = list(
        db.execute(
            select(models.Feedback)
            .where(models.Feedback.project_id == project.id)
            .order_by(models.Feedback.occurred_at.desc())
            .limit(20)
        ).scalars()
    )
    if feedback:
        names = {p.id: p.name for p in db.execute(select(models.Person)).scalars()}
        lines.append("\nFEEDBACK FROM PEOPLE")
        for item in feedback:
            who = names.get(item.person_id) or item.author_login or "unknown"
            lines.append(
                f"  [{item.status}] {who} ({item.source}): {ai.clip(item.body, 400)}"
            )

    return ai.clip("\n".join(lines), ai.MAX_CONTEXT_CHARS * 2)


async def write_digest(db: Session, project: models.Project) -> dict:
    return await ai.structured(
        system=DIGEST_SYSTEM,
        prompt=digest_context(db, project),
        schema=DIGEST_SCHEMA,
        tool_name="write_digest",
        model=ai.CHAT_MODEL,
        max_tokens=ai.MAX_REVIEW_TOKENS,
    )
