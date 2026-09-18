"""MCP server exposing the project tracker to Claude and other MCP clients.

It talks to the FastAPI app over HTTP rather than reaching into the database,
for two reasons: every rule the API enforces (reference checks, status side
effects, the not-null guards) applies to the agent exactly as it does to the
UI, and pointing this at a hosted backend later is a change of PP_API_URL
rather than a rewrite.

Run it with the backend already running:

    PP_API_URL=http://localhost:8000 python mcp_server.py

Every call that changes data is appended to data/agent-audit.jsonl, so there
is always a record of what the agent did that is independent of the agent.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

API_URL = os.getenv("PP_API_URL", "http://localhost:8000").rstrip("/")

# Resolved the same way app/db.py resolves it, but without importing it: this
# process should not open the database or care that it is SQLite.
DATA_DIR = Path(os.getenv("PP_DATA_DIR", Path(__file__).resolve().parent / "data"))
AUDIT_LOG = DATA_DIR / "agent-audit.jsonl"

TIMEOUT = httpx.Timeout(20.0)

# Every write through this server is stamped as agent-written, so what an
# agent produced is never indistinguishable from what the user typed.
HEADERS = {"X-PP-Source": "agent"}

# read_only_hint lets a client show which tools only look and which change
# things, so "summarise my week" and "close these tasks" are not equivalent.
READS = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False,
    open_world_hint=False,
)

server = MCPServer(
    name="personal-projects",
    version="1.0.0",
    instructions=(
        "A single-user project, task and time tracker.\n\n"
        "Projects hold milestones and tasks; time logs record hours actually "
        "spent and may hang off a task, a project, or neither. Notes are the "
        "written record: decisions, results and blockers.\n\n"
        "Prefer `today` for 'what should I be doing' and `insights` for "
        "'where did the time go'. Both are precomputed -- do not recompute "
        "them from the list endpoints.\n\n"
        "Activity (commits, pull requests) is recorded fact; suggestions "
        "are proposals waiting on the user. Accept or dismiss one only "
        "when the user has decided -- present the rationale and ask.\n\n"
        "When writing, say what you changed and why. Task status drives "
        "completed_at and therefore the cycle-time figures, so do not mark "
        "work done unless the user said it is done.\n\n"
        "People, feedback and dashboards hang off projects: `project_team` is "
        "the whole picture of who worked on one. `project_timeline` is the "
        "computed history of a linked repository -- what changed, where and "
        "when -- and is the right place to answer 'what is this project' or "
        "'when did X start' from, because it is arithmetic over recorded "
        "commits rather than a guess.\n\n"
        "The tracker has its own assistant for drafting and chat. Those "
        "endpoints are deliberately not exposed here: you are the model, so "
        "you are given the facts to reason over instead of a second model to "
        "ask."
    ),
)


def _prune(values: dict[str, Any]) -> dict[str, Any]:
    """Drop unset arguments.

    The API distinguishes "field omitted" from "field set to null", and
    rejects null for anything backed by a NOT NULL column, so an unset
    optional argument must not travel as one.
    """
    return {k: v for k, v in values.items() if v is not None}


def _audit(method: str, path: str, payload: Any, outcome: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "method": method,
                        "path": path,
                        "payload": payload,
                        "outcome": outcome,
                    }
                )
                + "\n"
            )
    except OSError:
        # An unwritable audit file must not take the tool down with it.
        pass


def _explain(response: httpx.Response) -> str:
    """Turn an API error into something an agent can act on."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        return f"{response.status_code}: {response.text[:200]}"
    if isinstance(detail, list):  # FastAPI validation errors
        parts = []
        for item in detail:
            loc = item.get("loc") or []
            field = loc[-1] if loc else "request"
            parts.append(f"{field}: {item.get('msg')}")
        return f"{response.status_code}: " + "; ".join(parts)
    return f"{response.status_code}: {detail or response.reason_phrase}"


async def _call(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
) -> Any:
    mutating = method != "GET"
    try:
        async with httpx.AsyncClient(
            base_url=API_URL, timeout=TIMEOUT, headers=HEADERS
        ) as client:
            response = await client.request(
                method, path, params=_prune(params or {}), json=body
            )
    except httpx.RequestError as exc:
        if mutating:
            _audit(method, path, body, f"unreachable: {exc}")
        raise ToolError(
            f"Can't reach the tracker API at {API_URL}. Is the backend running? "
            f"({exc})"
        ) from exc

    if response.is_error:
        message = _explain(response)
        if mutating:
            _audit(method, path, body, f"error {message}")
        raise ToolError(message)

    if mutating:
        _audit(method, path, body, f"ok {response.status_code}")
    return None if response.status_code == 204 else response.json()


# --- Reading ----------------------------------------------------------------


@server.tool(
    description=(
        "What needs attention today: overdue, due today and upcoming tasks, "
        "work in progress, blocked items, active projects, milestones falling "
        "due, and hours logged today and this week."
    ),
    annotations=READS,
)
async def today(horizon_days: int = 7) -> dict:
    return await _call("GET", "/dashboard/today", params={"horizon_days": horizon_days})


@server.tool(
    description=(
        "Where time went and whether work is closing: hours by week, category "
        "and project, tasks opened vs closed, median and p90 cycle time, and "
        "per-project progress. Window defaults to the last 56 days."
    ),
    annotations=READS,
)
async def insights(days: int = 56) -> dict:
    return await _call("GET", "/dashboard/insights", params={"days": days})


@server.tool(
    description=(
        "List projects with their derived progress, logged hours and overdue "
        "counts. Archived projects are excluded unless asked for."
    ),
    annotations=READS,
)
async def list_projects(
    status: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    include_archived: bool = False,
) -> list:
    return await _call(
        "GET",
        "/projects",
        params={
            "status": status,
            "category": category,
            "q": q,
            "include_archived": include_archived,
        },
    )


@server.tool(
    description=(
        "List tasks, filtered. `open_only` excludes done work, `overdue` "
        "returns open tasks past their due date, `due_within_days` looks "
        "ahead from today."
    ),
    annotations=READS,
)
async def list_tasks(
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    open_only: bool = False,
    overdue: bool = False,
    due_within_days: Optional[int] = None,
    q: Optional[str] = None,
) -> list:
    return await _call(
        "GET",
        "/tasks",
        params={
            "project_id": project_id,
            "status": status,
            "open_only": open_only,
            "overdue": overdue,
            "due_within_days": due_within_days,
            "q": q,
        },
    )


@server.tool(
    description="Milestones for a project, in board order.",
    annotations=READS,
)
async def list_milestones(project_id: int) -> list:
    return await _call("GET", f"/projects/{project_id}/milestones")


@server.tool(
    description=(
        "Time logs, newest first. Use `last_days` for a rolling window or "
        "`date_from`/`date_to` (YYYY-MM-DD) for a fixed one."
    ),
    annotations=READS,
)
async def list_time_logs(
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    last_days: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list:
    return await _call(
        "GET",
        "/time-logs",
        params={
            "project_id": project_id,
            "task_id": task_id,
            "last_days": last_days,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@server.tool(
    description=(
        "Notes: the written record of decisions, results, blockers and ideas. "
        "Pinned notes come first."
    ),
    annotations=READS,
)
async def list_notes(
    project_id: Optional[int] = None,
    kind: Optional[str] = None,
    q: Optional[str] = None,
    pinned_only: bool = False,
) -> list:
    return await _call(
        "GET",
        "/notes",
        params={
            "project_id": project_id,
            "kind": kind,
            "q": q,
            "pinned_only": pinned_only,
        },
    )


# --- Writing ----------------------------------------------------------------


@server.tool(
    description=(
        "Create a task. `status` is todo, in_progress, blocked or done; "
        "`priority` is low, medium or high; `due_date` is YYYY-MM-DD. A "
        "milestone must belong to the same project as the task."
    ),
    annotations=WRITES,
)
async def create_task(
    title: str,
    project_id: Optional[int] = None,
    milestone_id: Optional[int] = None,
    parent_task_id: Optional[int] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    due_date: Optional[str] = None,
    estimate_hours: Optional[float] = None,
) -> dict:
    return await _call(
        "POST",
        "/tasks",
        body=_prune(
            {
                "title": title,
                "project_id": project_id,
                "milestone_id": milestone_id,
                "parent_task_id": parent_task_id,
                "notes": notes,
                "status": status,
                "priority": priority,
                "due_date": due_date,
                "estimate_hours": estimate_hours,
            }
        ),
    )


@server.tool(
    description=(
        "Update a task. Only the fields you pass change. Setting status to "
        "done stamps its completion time and feeds the cycle-time figures, so "
        "only do that for work the user has said is finished. Moving off "
        "blocked clears the blocked reason."
    ),
    annotations=WRITES,
)
async def update_task(
    task_id: int,
    title: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    due_date: Optional[str] = None,
    notes: Optional[str] = None,
    blocked_reason: Optional[str] = None,
    project_id: Optional[int] = None,
    milestone_id: Optional[int] = None,
    estimate_hours: Optional[float] = None,
) -> dict:
    body = _prune(
        {
            "title": title,
            "status": status,
            "priority": priority,
            "due_date": due_date,
            "notes": notes,
            "blocked_reason": blocked_reason,
            "project_id": project_id,
            "milestone_id": milestone_id,
            "estimate_hours": estimate_hours,
        }
    )
    if not body:
        raise ToolError("Nothing to update: pass at least one field to change.")
    return await _call("PATCH", f"/tasks/{task_id}", body=body)


@server.tool(
    description=(
        "Record hours actually spent. `work_date` is YYYY-MM-DD and `hours` "
        "must be above 0 and at most 24. `category` is one of research, "
        "build, analysis, meeting, learning, admin, other. A log attached to "
        "a task inherits that task's project automatically."
    ),
    annotations=WRITES,
)
async def log_time(
    work_date: str,
    hours: float,
    category: Optional[str] = None,
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    note: Optional[str] = None,
) -> dict:
    return await _call(
        "POST",
        "/time-logs",
        body=_prune(
            {
                "work_date": work_date,
                "hours": hours,
                "category": category,
                "project_id": project_id,
                "task_id": task_id,
                "note": note,
            }
        ),
    )


@server.tool(
    description=(
        "Write a note. `kind` is note, decision, result, blocker or idea. "
        "This is the right place for a standup summary, a decision worth "
        "remembering, or a dead end worth not repeating."
    ),
    annotations=WRITES,
)
async def add_note(
    body: str,
    title: Optional[str] = None,
    project_id: Optional[int] = None,
    kind: Optional[str] = None,
    pinned: Optional[bool] = None,
) -> dict:
    return await _call(
        "POST",
        "/notes",
        body=_prune(
            {
                "body": body,
                "title": title,
                "project_id": project_id,
                "kind": kind,
                "pinned": pinned,
            }
        ),
    )


@server.tool(
    description=(
        "Update a project's status, target date or manual progress override "
        "(0-100). The override wins over task-derived progress, so use it "
        "only for work that genuinely isn't broken into tasks yet."
    ),
    annotations=WRITES,
)
async def update_project(
    project_id: int,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    target_date: Optional[str] = None,
    progress_override: Optional[int] = None,
    summary: Optional[str] = None,
) -> dict:
    body = _prune(
        {
            "status": status,
            "priority": priority,
            "target_date": target_date,
            "progress_override": progress_override,
            "summary": summary,
        }
    )
    if not body:
        raise ToolError("Nothing to update: pass at least one field to change.")
    return await _call("PATCH", f"/projects/{project_id}", body=body)


@server.tool(
    description=(
        "Recorded activity from other systems -- GitHub commits and pull "
        "requests. These are facts about what happened, already linked to a "
        "project where the repo is known. `unlinked_only` returns the ones "
        "that matched no project, which is the review queue."
    ),
    annotations=READS,
)
async def list_activity(
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    repo: Optional[str] = None,
    kind: Optional[str] = None,
    unlinked_only: bool = False,
    last_days: Optional[int] = None,
) -> list:
    return await _call(
        "GET",
        "/activity",
        params={
            "project_id": project_id,
            "task_id": task_id,
            "repo": repo,
            "kind": kind,
            "unlinked_only": unlinked_only,
            "last_days": last_days,
        },
    )


@server.tool(
    description=(
        "Pull new activity from GitHub for the tracked repos, or one named "
        "repo. Safe to run repeatedly -- events are keyed by GitHub's own id, "
        "so a second run adds nothing. A project is tracked once its `repo` "
        "field is set to \"owner/name\"."
    ),
    annotations=WRITES,
)
async def sync_activity(repo: Optional[str] = None, limit: int = 100) -> list:
    return await _call(
        "POST", "/activity/sync", params={"repo": repo, "limit": limit}
    )


@server.tool(
    description=(
        "The analyst's review of the board: findings (stale work in progress, "
        "long-standing blockers, overdue tasks, estimate overruns, projects "
        "past their target) plus any suggestions currently awaiting a "
        "decision. Read-only. Use this as the basis for a standup or weekly "
        "write-up rather than recomputing it from the list endpoints -- and "
        "quote the findings rather than inventing your own."
    ),
    annotations=READS,
)
async def review(stale_days: int = 7) -> dict:
    return await _call("GET", "/review", params={"stale_days": stale_days})


@server.tool(
    description=(
        "Look for new suggestions from the current evidence. Returns only the "
        "ones newly raised. This proposes; it never changes a task."
    ),
    annotations=WRITES,
)
async def refresh_suggestions() -> list:
    return await _call("POST", "/suggestions/refresh")


@server.tool(
    description=(
        "Suggestions awaiting a decision. `status` accepts pending (the "
        "default), accepted, dismissed or all. Each carries the rule that "
        "raised it, the change proposed, and the evidence behind it."
    ),
    annotations=READS,
)
async def list_suggestions(status: str = "pending") -> list:
    return await _call("GET", "/suggestions", params={"status": status})


@server.tool(
    description=(
        "Accept a suggestion and apply its change. Only do this when the user "
        "has said to -- the whole point of a suggestion is that a person "
        "decides. Present the rationale and evidence and ask first."
    ),
    annotations=WRITES,
)
async def accept_suggestion(suggestion_id: int) -> dict:
    return await _call("POST", f"/suggestions/{suggestion_id}/accept")


@server.tool(
    description=(
        "Turn a suggestion down. It will not be raised again. As with "
        "accepting, this is the user's call, not yours."
    ),
    annotations=WRITES,
)
async def dismiss_suggestion(suggestion_id: int) -> dict:
    return await _call("POST", f"/suggestions/{suggestion_id}/dismiss")


# --- How a project was built ------------------------------------------------


@server.tool(
    description=(
        "The computed history of a project's repository: commits per month "
        "with the areas they touched, when each part of the tree first and "
        "last changed, the most-changed files, and who committed. Arithmetic "
        "over recorded commits, not an opinion -- answer questions about what "
        "a project is, when something started, or what has been abandoned "
        "from this, and cite the month or file you got it from. `detailed` "
        "says how many of the commits have file-level detail; the rest are "
        "known only by their message until deep_sync_commits has run."
    ),
    annotations=READS,
)
async def project_timeline(project_id: int) -> dict:
    return await _call("GET", f"/ai/projects/{project_id}/timeline")


@server.tool(
    description=(
        "Fetch which files each commit changed, for commits that don't have "
        "that yet. Costs one GitHub request per commit, so it runs newest "
        "first in chunks; `still_missing` in the reply says whether there is "
        "more to fetch. Run it when project_timeline reports fewer detailed "
        "commits than total."
    ),
    annotations=WRITES,
)
async def deep_sync_commits(repo: Optional[str] = None, limit: int = 60) -> list:
    return await _call(
        "POST", "/activity/deep-sync", params={"repo": repo, "limit": limit}
    )


# --- People and collaboration -----------------------------------------------


@server.tool(
    description=(
        "People on record: collaborators, reviewers and contributors, with "
        "their GitHub login and how much of the activity is theirs."
    ),
    annotations=READS,
)
async def list_people(include_archived: bool = False) -> list:
    return await _call(
        "GET", "/people", params={"include_archived": include_archived}
    )


@server.tool(
    description=(
        "Add someone to the address book. A `github_login` is what links "
        "their commits and pull requests to them -- without it they are a "
        "name with no contributions attached. Adding one re-attributes their "
        "existing history immediately."
    ),
    annotations=WRITES,
)
async def add_person(
    name: str,
    github_login: Optional[str] = None,
    email: Optional[str] = None,
    role_title: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    return await _call(
        "POST",
        "/people",
        body=_prune(
            {
                "name": name,
                "github_login": github_login,
                "email": email,
                "role_title": role_title,
                "notes": notes,
            }
        ),
    )


@server.tool(
    description=(
        "Who worked on a project, in one call: the people linked to it and "
        "their roles, every login that has committed to it with how much and "
        "how recently, and the feedback recorded against it. Use this rather "
        "than assembling it from list_people and list_activity."
    ),
    annotations=READS,
)
async def project_team(project_id: int) -> dict:
    return await _call("GET", f"/projects/{project_id}/collaboration")


@server.tool(
    description=(
        "Link a person to a project. `role` is viewer or contributor -- a "
        "viewer is shown the progress, a contributor is someone whose work is "
        "expected to appear in it."
    ),
    annotations=WRITES,
)
async def add_member(project_id: int, person_id: int, role: Optional[str] = None) -> dict:
    return await _call(
        "POST",
        f"/projects/{project_id}/members",
        body=_prune({"person_id": person_id, "role": role}),
    )


@server.tool(
    description=(
        "What people have said: review comments, issue comments and notes "
        "typed by hand. `status` is open (the default), actioned, declined "
        "or all. These are the suggestions git never records."
    ),
    annotations=READS,
)
async def list_feedback(
    project_id: Optional[int] = None,
    person_id: Optional[int] = None,
    status: str = "open",
    limit: int = 100,
) -> list:
    return await _call(
        "GET",
        "/feedback",
        params={
            "project_id": project_id,
            "person_id": person_id,
            "status": status,
            "limit": limit,
        },
    )


@server.tool(
    description=(
        "Record something a person said that GitHub will never see -- a "
        "remark in a meeting, a suggestion over a call. It is stored as "
        "manually entered, so it is never mistaken for a real review comment."
    ),
    annotations=WRITES,
)
async def add_feedback(
    body: str,
    project_id: Optional[int] = None,
    person_id: Optional[int] = None,
    url: Optional[str] = None,
) -> dict:
    return await _call(
        "POST",
        "/feedback",
        body=_prune(
            {
                "body": body,
                "project_id": project_id,
                "person_id": person_id,
                "url": url,
            }
        ),
    )


@server.tool(
    description=(
        "Change a piece of feedback: mark it actioned or declined, or attach "
        "it to the right person or project. Only mark something actioned when "
        "the work it asked for has actually been done."
    ),
    annotations=WRITES,
)
async def update_feedback(
    feedback_id: int,
    status: Optional[str] = None,
    person_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> dict:
    body = _prune(
        {"status": status, "person_id": person_id, "project_id": project_id}
    )
    if not body:
        raise ToolError("Nothing to update: pass at least one field to change.")
    return await _call("PATCH", f"/feedback/{feedback_id}", body=body)


# --- Your GitHub account ----------------------------------------------------


@server.tool(
    description=(
        "Every repository on the user's GitHub account, whether or not it is "
        "a project here -- `imported` says which are already tracked. The "
        "account is worked out automatically. Results are cached for a few "
        "minutes; this call spends GitHub quota, and `rate_limit` in the "
        "reply says how much is left."
    ),
    annotations=READS,
)
async def list_repos(user: Optional[str] = None, limit: int = 100) -> dict:
    return await _call("GET", "/personal/repos", params={"user": user, "limit": limit})


@server.tool(
    description=(
        "Create a personal project for each repository named, as "
        "\"owner/name\". Anything already tracked is skipped, so importing "
        "the same list twice adds nothing."
    ),
    annotations=WRITES,
)
async def import_repos(repos: list[str], status: Optional[str] = None) -> list:
    if not repos:
        raise ToolError("Pass at least one repo, as owner/name.")
    return await _call(
        "POST", "/personal/repos/import", body=_prune({"repos": repos, "status": status})
    )


@server.tool(
    description=(
        "Saved brainstorms: their topic, the project they belong to and how "
        "many turns each has. Transcripts are not included -- read one with "
        "read_brainstorm."
    ),
    annotations=READS,
)
async def list_brainstorms(project_id: Optional[int] = None, limit: int = 50) -> list:
    return await _call(
        "GET", "/personal/brainstorms", params={"project_id": project_id, "limit": limit}
    )


@server.tool(
    description="One brainstorm in full, with its transcript in order.",
    annotations=READS,
)
async def read_brainstorm(brainstorm_id: int) -> dict:
    return await _call("GET", f"/personal/brainstorms/{brainstorm_id}")


# --- Dashboards -------------------------------------------------------------


@server.tool(
    description=(
        "Reports and dashboards, whether pasted in by hand or mirrored from "
        "the Power BI Service, with their refresh state where it is known. "
        "`unlinked_only` returns the ones not attached to a project."
    ),
    annotations=READS,
)
async def list_dashboards(
    project_id: Optional[int] = None, unlinked_only: bool = False, limit: int = 200
) -> list:
    return await _call(
        "GET",
        "/dashboards",
        params={
            "project_id": project_id,
            "unlinked_only": unlinked_only,
            "limit": limit,
        },
    )


@server.tool(
    description=(
        "Record a report and optionally attach it to a project. A Power BI "
        "URL is recognised, so a later sync updates this row rather than "
        "adding a second one for the same report."
    ),
    annotations=WRITES,
)
async def add_dashboard(
    name: str,
    url: Optional[str] = None,
    project_id: Optional[int] = None,
    note: Optional[str] = None,
) -> dict:
    return await _call(
        "POST",
        "/dashboards",
        body=_prune(
            {"name": name, "url": url, "project_id": project_id, "note": note}
        ),
    )


if __name__ == "__main__":
    server.run(transport="stdio")
