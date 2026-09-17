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
        "When writing, say what you changed and why. Task status drives "
        "completed_at and therefore the cycle-time figures, so do not mark "
        "work done unless the user said it is done."
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
        async with httpx.AsyncClient(base_url=API_URL, timeout=TIMEOUT) as client:
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


if __name__ == "__main__":
    server.run(transport="stdio")
