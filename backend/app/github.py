"""Pulling activity out of GitHub and recording it as facts.

This module does two separable things, kept separable on purpose:

  * fetching -- ask GitHub what happened, and translate the response into
    ActivityEvent rows without deciding what any of it means;
  * linking -- work out which project or task an event belongs to, and record
    *how* that was decided so a guess is never mistaken for a statement.

Nothing here infers progress, closes tasks or logs time. That interpretation
belongs further up where it can be reviewed, and keeping it out means the
facts can be re-interpreted later without re-fetching the history.
"""

import json
import os
import re
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

API_ROOT = "https://api.github.com"
TIMEOUT = httpx.Timeout(30.0)

# `Task: 42` in a commit message or PR body. Deliberately explicit rather than
# "#42": on GitHub that already means an issue, and borrowing it would attach
# work to the wrong thing the first time someone references an issue.
TASK_REF = re.compile(r"\btask[:\s#-]\s*(\d+)\b", re.IGNORECASE)


def parse_task_ref(text: Optional[str]) -> Optional[int]:
    """The task id a message explicitly claims, or None.

    Only an explicit reference counts. Guessing from prose is what turns a
    tidy history into confidently wrong attribution.
    """
    if not text:
        return None
    match = TASK_REF.search(text)
    return int(match.group(1)) if match else None


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_time(value: Optional[str]) -> datetime:
    """GitHub timestamps are ISO-8601 in UTC; stored naive to match the rest
    of the schema."""
    if not value:
        return models.utcnow()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


# --- Fetching ---------------------------------------------------------------


def fetch_from_github(repo: str, since: Optional[datetime], limit: int) -> list[dict]:
    """Commits and pull requests for a repo, newest first.

    Returns provider payloads. Anything that fails -- a bad repo name, a rate
    limit, a private repo with no token -- raises with GitHub's own wording,
    which is more useful than anything this layer could invent.
    """
    out: list[dict] = []
    with httpx.Client(base_url=API_ROOT, headers=_headers(), timeout=TIMEOUT) as client:
        params: dict[str, Any] = {"per_page": min(limit, 100)}
        if since:
            params["since"] = since.isoformat() + "Z"
        commits = client.get(f"/repos/{repo}/commits", params=params)
        _raise_for_github(commits, repo)
        for item in commits.json():
            out.append({"kind": "commit", "payload": item})

        pulls = client.get(
            f"/repos/{repo}/pulls",
            params={"state": "all", "sort": "updated", "direction": "desc",
                    "per_page": min(limit, 100)},
        )
        _raise_for_github(pulls, repo)
        for item in pulls.json():
            out.append({"kind": "pull_request", "payload": item})
    return out


def _raise_for_github(response: httpx.Response, repo: str) -> None:
    if not response.is_error:
        return
    try:
        message = response.json().get("message", response.reason_phrase)
    except ValueError:
        message = response.reason_phrase
    if response.status_code == 404:
        message = (
            f"{repo} not found. It may be private -- set GITHUB_TOKEN if so."
        )
    raise RuntimeError(f"GitHub {response.status_code}: {message}")


# --- Translating ------------------------------------------------------------


def to_event(repo: str, kind: str, payload: dict) -> dict:
    """One provider payload as the fields an ActivityEvent needs."""
    if kind == "commit":
        commit = payload.get("commit") or {}
        author = commit.get("author") or {}
        message = commit.get("message") or ""
        return {
            "external_id": f"github:commit:{payload.get('sha')}",
            "kind": "commit",
            "repo": repo,
            "actor": (payload.get("author") or {}).get("login") or author.get("name"),
            # A commit message's first line is the subject; the body is kept in
            # `raw` and is still searched for a task reference.
            "title": message.splitlines()[0] if message else "(no message)",
            "url": payload.get("html_url"),
            "occurred_at": _parse_time(author.get("date")),
            "ref_text": message,
        }
    if kind == "pull_request":
        return {
            "external_id": f"github:pr:{repo}#{payload.get('number')}",
            "kind": "pull_request",
            "repo": repo,
            "actor": (payload.get("user") or {}).get("login"),
            "title": payload.get("title") or "(untitled)",
            "url": payload.get("html_url"),
            "occurred_at": _parse_time(
                payload.get("merged_at") or payload.get("updated_at")
            ),
            "ref_text": " ".join(
                filter(None, [payload.get("title"), payload.get("body"),
                              (payload.get("head") or {}).get("ref")])
            ),
        }
    raise ValueError(f"Unsupported event kind: {kind}")


# --- Linking ----------------------------------------------------------------


def link(db: Session, repo: str, ref_text: Optional[str]) -> dict:
    """Decide which project and task an event belongs to.

    The repo settles the project: it is a mapping the user stated, so it is
    reliable. A task only gets linked when the text names one explicitly and
    that task really sits under the same project -- a reference to a task in
    some other project is far more likely to be a typo than a real link.
    """
    project = db.execute(
        select(models.Project).where(models.Project.repo == repo)
    ).scalars().first()
    if project is None:
        return {"project_id": None, "task_id": None, "linked_by": None}

    task_id = parse_task_ref(ref_text)
    if task_id is not None:
        task = db.get(models.Task, task_id)
        if task is not None and task.project_id == project.id:
            return {
                "project_id": project.id,
                "task_id": task.id,
                "linked_by": "convention",
            }

    return {"project_id": project.id, "task_id": None, "linked_by": "repo"}


# --- Syncing ----------------------------------------------------------------


def tracked_repos(db: Session) -> list[str]:
    """Repos worth syncing: the ones a project actually points at."""
    rows = db.execute(
        select(models.Project.repo).where(
            models.Project.repo.is_not(None),
            models.Project.archived_at.is_(None),
        )
    ).scalars()
    return sorted({r.strip() for r in rows if r and r.strip()})


def latest_event_time(db: Session, repo: str) -> Optional[datetime]:
    return db.execute(
        select(models.ActivityEvent.occurred_at)
        .where(models.ActivityEvent.repo == repo)
        .order_by(models.ActivityEvent.occurred_at.desc())
        .limit(1)
    ).scalar()


def sync_repo(
    db: Session,
    repo: str,
    *,
    limit: int = 100,
    fetcher: Optional[Callable[[str, Optional[datetime], int], Iterable[dict]]] = None,
) -> dict:
    """Record everything new for one repo.

    Idempotent: external_id is unique, and anything already stored is skipped
    rather than updated, so running this twice changes nothing the second
    time.

    `fetcher` is resolved here rather than defaulted in the signature, because
    a default is bound once at import and would quietly ignore any later
    substitution -- including a test's, which is how a test suite ends up
    calling GitHub for real without anyone noticing.
    """
    fetch = fetcher or fetch_from_github
    since = latest_event_time(db, repo)
    fetched = list(fetch(repo, since, limit))

    known = set(
        db.execute(
            select(models.ActivityEvent.external_id).where(
                models.ActivityEvent.repo == repo
            )
        ).scalars()
    )

    added = 0
    linked_to_task = 0
    for item in fetched:
        fields = to_event(repo, item["kind"], item["payload"])
        if fields["external_id"] in known:
            continue
        known.add(fields["external_id"])

        ref_text = fields.pop("ref_text", None)
        links = link(db, repo, ref_text)
        if links["linked_by"] == "convention":
            linked_to_task += 1

        db.add(
            models.ActivityEvent(
                provider="github",
                raw=json.dumps(item["payload"])[:20000],
                **fields,
                **links,
            )
        )
        added += 1

    db.commit()
    return {
        "repo": repo,
        "fetched": len(fetched),
        "added": added,
        "skipped": len(fetched) - added,
        "linked_to_task": linked_to_task,
        "since": since.isoformat() if since else None,
    }


# --- Diffs ------------------------------------------------------------------

# A patch per commit, bounded twice: once so a single reformatting commit
# can't swallow the whole budget, and once overall.
DIFF_PER_COMMIT_CHARS = 4_000
DIFF_TOTAL_CHARS = 24_000
DIFF_MAX_COMMITS = 6


def fetch_diffs(
    repo: str,
    shas: list[str],
    *,
    per_commit: int = DIFF_PER_COMMIT_CHARS,
    total: int = DIFF_TOTAL_CHARS,
) -> str:
    """The patches for specific commits, as one block of text.

    One request per commit, which is why the caller is expected to pass a
    handful rather than a history. A commit that can't be read is skipped with
    a marker instead of failing the batch: a review over five of six commits
    is still worth having, and silently dropping one is not.
    """
    chunks: list[str] = []
    used = 0
    with httpx.Client(base_url=API_ROOT, headers=_headers(), timeout=TIMEOUT) as client:
        for sha in shas[:DIFF_MAX_COMMITS]:
            if used >= total:
                chunks.append("... [diff budget reached; later commits omitted]")
                break
            try:
                response = client.get(f"/repos/{repo}/commits/{sha}")
                _raise_for_github(response, repo)
                data = response.json()
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                chunks.append(f"--- commit {sha[:8]}: diff unavailable ({exc})")
                continue

            subject = (data.get("commit", {}).get("message") or "").splitlines()
            body = [f"=== commit {sha[:8]}: {subject[0] if subject else ''}"]
            for changed in data.get("files") or []:
                patch = changed.get("patch")
                if not patch:
                    # Binary files and very large ones come back without a
                    # patch; naming them is still information.
                    body.append(
                        f"--- {changed.get('filename')} "
                        f"({changed.get('status')}, no patch available)"
                    )
                    continue
                body.append(f"--- {changed.get('filename')} ({changed.get('status')})")
                body.append(patch)

            text = "\n".join(body)
            if len(text) > per_commit:
                text = text[:per_commit] + "\n... [commit diff truncated]"
            chunks.append(text)
            used += len(text)

    return "\n\n".join(chunks)
