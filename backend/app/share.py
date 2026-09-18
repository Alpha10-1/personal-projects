"""A read-only progress snapshot you can send someone.

One self-contained HTML file: inline styles, no scripts, no external
requests. That is deliberate -- it has to survive being attached to an email
or dropped in Teams, opened on a machine that has never heard of this app,
and it must not phone home from someone else's laptop.

**What it leaves out is the point.** This file goes to other people, so it
carries progress and nothing candid:

    included   name, summary, objective, definition of done, status, dates,
               progress, milestones, task titles and states, who contributed,
               open feedback, and the latest digest
    excluded   retro / lessons learned, hours and time logs, the stakeholder
               field, and every note except a digest

The exclusions are the fields where you say what you actually think: a retro
is written for you, hours logged are nobody else's business, and a note
marked `blocker` is often blunt about a person. A sharing feature that
required proofreading before every send would not get used.
"""

import html
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import enrich, models

STATUS_WORDS = {
    "idea": "Idea",
    "planning": "Planning",
    "active": "Active",
    "on_hold": "On hold",
    "done": "Done",
    "archived": "Archived",
}

ACTIVITY_LIMIT = 15


def _e(value) -> str:
    """Escape, and render None as an empty string rather than "None"."""
    return html.escape(str(value)) if value not in (None, "") else ""


def _paragraphs(text: Optional[str]) -> str:
    if not text:
        return ""
    blocks = [b.strip() for b in str(text).split("\n") if b.strip()]
    return "".join(f"<p>{_e(b)}</p>" for b in blocks)


def gather(db: Session, project: models.Project) -> dict:
    """Everything the snapshot shows, read in one place so the HTML below is
    only formatting."""
    tasks = list(
        db.execute(
            select(models.Task).where(models.Task.project_id == project.id)
        ).scalars()
    )
    milestones = list(
        db.execute(
            select(models.Milestone)
            .where(models.Milestone.project_id == project.id)
            .order_by(models.Milestone.position, models.Milestone.id)
        ).scalars()
    )

    progress = enrich.compute_progress(
        project,
        len(tasks),
        sum(1 for t in tasks if t.status == "done"),
        len(milestones),
        sum(1 for m in milestones if m.status == "done"),
    )

    people = {p.id: p.name for p in db.execute(select(models.Person)).scalars()}

    contributors = [
        {
            "who": people.get(person_id) or actor or "unknown",
            "named": person_id is not None,
            "events": count,
            "last": newest,
        }
        for actor, person_id, count, newest in db.execute(
            select(
                models.ActivityEvent.actor,
                models.ActivityEvent.person_id,
                func.count(),
                func.max(models.ActivityEvent.occurred_at),
            )
            .where(models.ActivityEvent.project_id == project.id)
            .group_by(models.ActivityEvent.actor, models.ActivityEvent.person_id)
            .order_by(func.count().desc())
        ).all()
        if actor or person_id
    ]

    activity = list(
        db.execute(
            select(models.ActivityEvent)
            .where(models.ActivityEvent.project_id == project.id)
            .order_by(models.ActivityEvent.occurred_at.desc())
            .limit(ACTIVITY_LIMIT)
        ).scalars()
    )

    feedback = list(
        db.execute(
            select(models.Feedback)
            .where(
                models.Feedback.project_id == project.id,
                models.Feedback.status == "open",
            )
            .order_by(models.Feedback.occurred_at.desc())
            .limit(20)
        ).scalars()
    )

    # Only a digest, and only the newest. Every other note is internal.
    digest = db.execute(
        select(models.Note)
        .where(
            models.Note.project_id == project.id,
            models.Note.title.like("Progress digest%"),
        )
        .order_by(models.Note.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    return {
        "project": project,
        "tasks": tasks,
        "milestones": milestones,
        "progress": progress,
        "people": people,
        "contributors": contributors,
        "activity": activity,
        "feedback": feedback,
        "digest": digest,
    }


CSS = """
:root{--bg:#fbfbfa;--card:#fff;--ink:#1c1c1a;--muted:#6b6b66;--line:#e5e4e1;
--accent:#3d6b53;--warn:#a8632a}
@media (prefers-color-scheme:dark){:root{--bg:#191917;--card:#211f1e;
--ink:#eceae6;--muted:#9c9a94;--line:#35322f;--accent:#8fb89e;--warn:#d9a06a}}
*{box-sizing:border-box}
body{margin:0;padding:28px 16px 56px;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:23px;margin:0 0 4px;line-height:1.25}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.07em;
color:var(--muted);margin:30px 0 10px;font-weight:600}
p{margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;margin:0 0 14px}
.meta{color:var(--muted);font-size:13px;margin:0 0 18px}
.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;
padding:1px 9px;font-size:12px;color:var(--muted);margin:0 5px 5px 0}
.bar{height:7px;background:var(--line);border-radius:999px;overflow:hidden;
margin:10px 0 6px}
.bar>i{display:block;height:100%;background:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);
vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;
color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:none}
.done{color:var(--muted);text-decoration:line-through}
.late{color:var(--warn);font-weight:600}
.quote{border-left:3px solid var(--line);padding-left:12px;margin:0 0 12px;
color:var(--ink)}
.who{font-weight:600}
footer{color:var(--muted);font-size:12px;margin-top:34px;
border-top:1px solid var(--line);padding-top:14px}
@media print{body{background:#fff;padding:0}.card{break-inside:avoid}}
"""


def render(db: Session, project: models.Project) -> str:
    """The snapshot, as one HTML document."""
    d = gather(db, project)
    p = d["project"]
    today = date.today()

    tasks_done = sum(1 for t in d["tasks"] if t.status == "done")
    overdue = [
        t
        for t in d["tasks"]
        if t.due_date and t.due_date < today and t.status != "done"
    ]

    out = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_e(p.name)} — progress</title>",
        f"<style>{CSS}</style></head><body><div class=\"wrap\">",
        f"<h1>{_e(p.name)}</h1>",
        '<p class="meta">',
        f'<span class="pill">{_e(STATUS_WORDS.get(p.status, p.status))}</span>',
    ]
    if p.target_date:
        late = p.target_date < today and p.status != "done"
        out.append(
            f'<span class="pill">Target {_e(p.target_date.isoformat())}'
            + (" — passed" if late else "")
            + "</span>"
        )
    out.append(f'<span class="pill">{d["progress"]}% complete</span>')
    out.append("</p>")

    out.append(f'<div class="bar"><i style="width:{max(0, min(100, d["progress"]))}%"></i></div>')

    if p.summary or p.objective or p.definition_of_done:
        out.append('<div class="card">')
        out.append(_paragraphs(p.summary))
        if p.objective:
            out.append("<h2>Objective</h2>" + _paragraphs(p.objective))
        if p.definition_of_done:
            out.append("<h2>What done looks like</h2>" + _paragraphs(p.definition_of_done))
        out.append("</div>")

    if d["digest"]:
        out.append("<h2>Latest update</h2><div class=\"card\">")
        out.append(_paragraphs(d["digest"].body))
        out.append(
            f'<p class="meta">Written {_e(d["digest"].created_at.strftime("%d %b %Y"))} '
            "— generated from the project's activity.</p></div>"
        )

    if d["milestones"]:
        out.append("<h2>Milestones</h2><div class=\"card\"><table>")
        out.append("<tr><th>Milestone</th><th>Due</th><th>State</th></tr>")
        for m in d["milestones"]:
            cls = ' class="done"' if m.status == "done" else ""
            due = m.due_date.isoformat() if m.due_date else "—"
            late = (
                m.due_date and m.due_date < today and m.status != "done"
            )
            due_cls = ' class="late"' if late else ""
            state = "Done" if m.status == "done" else "Pending"
            out.append(
                f"<tr><td{cls}>{_e(m.title)}</td>"
                f"<td{due_cls}>{_e(due)}</td>"
                f"<td>{state}</td></tr>"
            )
        out.append("</table></div>")

    if d["tasks"]:
        out.append(
            f"<h2>Tasks — {tasks_done} of {len(d['tasks'])} done"
            + (f", {len(overdue)} overdue" if overdue else "")
            + "</h2><div class=\"card\"><table>"
        )
        out.append("<tr><th>Task</th><th>State</th></tr>")
        for t in sorted(d["tasks"], key=lambda t: (t.status == "done", t.title)):
            cls = ' class="done"' if t.status == "done" else ""
            state = t.status.replace("_", " ")
            if t.due_date and t.due_date < today and t.status != "done":
                state = '<span class="late">overdue</span>'
            out.append(f"<tr><td{cls}>{_e(t.title)}</td><td>{state}</td></tr>")
        out.append("</table></div>")

    if d["contributors"]:
        out.append("<h2>Who has contributed</h2><div class=\"card\"><table>")
        out.append("<tr><th>Person</th><th>Commits &amp; PRs</th><th>Last seen</th></tr>")
        for c in d["contributors"]:
            out.append(
                f'<tr><td class="who">{_e(c["who"])}</td>'
                f'<td>{c["events"]}</td>'
                f'<td>{_e(c["last"].strftime("%d %b %Y")) if c["last"] else "—"}</td></tr>'
            )
        out.append("</table></div>")

    if d["feedback"]:
        out.append("<h2>Open questions and suggestions</h2><div class=\"card\">")
        for f in d["feedback"]:
            who = d["people"].get(f.person_id) or f.author_login or "unknown"
            out.append(
                f'<div class="quote"><span class="who">{_e(who)}</span> '
                f'<span class="meta">{_e(f.occurred_at.strftime("%d %b %Y"))}</span>'
                f"{_paragraphs(f.body)}</div>"
            )
        out.append("</div>")

    if d["activity"]:
        out.append("<h2>Recent activity</h2><div class=\"card\"><table>")
        for event in d["activity"]:
            who = d["people"].get(event.person_id) or event.actor or "—"
            out.append(
                f'<tr><td>{_e(event.occurred_at.strftime("%d %b"))}</td>'
                f'<td class="who">{_e(who)}</td>'
                f"<td>{_e(event.title[:140])}</td></tr>"
            )
        out.append("</table></div>")

    out.append(
        "<footer>Read-only snapshot generated "
        f"{_e(datetime.now().strftime('%d %b %Y at %H:%M'))}. "
        "It shows progress only — hours, retrospectives and internal notes "
        "are not included. Nothing here updates on its own; ask for a fresh "
        "copy for the current picture.</footer>"
    )
    out.append("</div></body></html>")
    return "".join(out)
