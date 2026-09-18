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
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import collaboration, models

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


# GitHub reports the quota on every response, so it is tracked for free
# rather than costing a call of its own to ask about.
_LAST_RATE: dict[str, Any] = {}

# Unauthenticated callers get 60 requests an hour, which one afternoon of
# opening the repo list will spend. A token raises it to 5000.
ANON_HOURLY_LIMIT = 60


def _note_rate_limit(response: httpx.Response) -> None:
    headers = response.headers
    if "x-ratelimit-limit" not in headers:
        return
    try:
        reset = int(headers.get("x-ratelimit-reset", 0))
    except ValueError:
        reset = 0
    _LAST_RATE.update(
        {
            "limit": int(headers.get("x-ratelimit-limit", 0) or 0),
            "remaining": int(headers.get("x-ratelimit-remaining", 0) or 0),
            "reset_at": datetime.fromtimestamp(reset) if reset else None,
            "authenticated": bool(os.getenv("GITHUB_TOKEN")),
            "seen_at": datetime.now(),
        }
    )


def rate_limit() -> dict:
    """What GitHub last said about the quota.

    Empty until something has actually been fetched -- reporting a guess
    would be worse than reporting nothing.
    """
    return dict(_LAST_RATE)


def _raise_for_github(response: httpx.Response, repo: str) -> None:
    _note_rate_limit(response)
    if not response.is_error:
        return
    try:
        message = response.json().get("message", response.reason_phrase)
    except ValueError:
        message = response.reason_phrase

    # A spent quota arrives as a 403 whose message is about rate limits, which
    # reads as a permissions problem unless it is named. It is also the one
    # GitHub error with an obvious fix, so the fix goes in the message.
    exhausted = _LAST_RATE.get("remaining") == 0 or "rate limit" in str(message).lower()
    if response.status_code in (403, 429) and exhausted:
        reset = _LAST_RATE.get("reset_at")
        when = f" It resets at {reset:%H:%M}." if reset else ""
        extra = (
            ""
            if os.getenv("GITHUB_TOKEN")
            else " Set GITHUB_TOKEN in backend/.env to raise the limit from "
            f"{ANON_HOURLY_LIMIT} an hour to 5000."
        )
        raise RuntimeError(f"GitHub rate limit reached.{when}{extra}")

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

        # Named at ingest, the same way the project link is. An actor nobody
        # owns stays null and is picked up later by /people/relink.
        person = collaboration.person_for_login(db, fields.get("actor"))

        db.add(
            models.ActivityEvent(
                provider="github",
                raw=json.dumps(item["payload"])[:20000],
                person_id=person.id if person else None,
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


# --- Feedback from people ---------------------------------------------------

# A review comment is often a sentence fragment ("nit: spacing"). Below this
# there is nothing worth recording as a suggestion.
MIN_FEEDBACK_CHARS = 12


def fetch_comments(repo: str, since: Optional[datetime], limit: int) -> list[dict]:
    """Review comments and issue comments for a repo, newest first.

    Two endpoints, both repo-wide rather than per pull request, so this is two
    calls regardless of how much has happened. GitHub treats a pull request as
    an issue, so `issues/comments` also covers discussion on PRs that isn't
    attached to a line of code.
    """
    out: list[dict] = []
    params: dict[str, Any] = {"per_page": min(limit, 100), "sort": "created",
                              "direction": "desc"}
    if since:
        params["since"] = since.isoformat() + "Z"

    with httpx.Client(base_url=API_ROOT, headers=_headers(), timeout=TIMEOUT) as client:
        for path, kind in (
            (f"/repos/{repo}/pulls/comments", "pr_review"),
            (f"/repos/{repo}/issues/comments", "issue_comment"),
        ):
            response = client.get(path, params=params)
            _raise_for_github(response, repo)
            for item in response.json():
                out.append({"kind": kind, "payload": item})
    return out


def _feedback_from_comment(kind: str, payload: dict) -> Optional[dict]:
    body = (payload.get("body") or "").strip()
    if len(body) < MIN_FEEDBACK_CHARS:
        return None
    return {
        "source": kind,
        "external_id": f"{kind}:{payload.get('id')}",
        "author_login": (payload.get("user") or {}).get("login"),
        "body": body[:8000],
        "url": payload.get("html_url"),
        "occurred_at": _parse_time(payload.get("created_at")),
    }


def _feedback_from_pull_body(event: models.ActivityEvent) -> Optional[dict]:
    """A pull request's own description, read out of the payload already
    stored -- so this source costs no extra request."""
    try:
        payload = json.loads(event.raw or "{}")
    except ValueError:
        return None
    body = (payload.get("body") or "").strip()
    if len(body) < MIN_FEEDBACK_CHARS:
        return None
    return {
        "source": "pr_body",
        "external_id": f"pr_body:{payload.get('id')}",
        "author_login": (payload.get("user") or {}).get("login"),
        "body": body[:8000],
        "url": payload.get("html_url"),
        "occurred_at": event.occurred_at,
    }


def sync_feedback(
    db: Session,
    repo: str,
    *,
    limit: int = 100,
    fetcher: Optional[Callable[[str, Optional[datetime], int], Iterable[dict]]] = None,
) -> dict:
    """Mirror what people said in the repo into `feedback`.

    Three sources, one of which is free: pull request descriptions come from
    payloads `sync_repo` already stored, so only the two comment endpoints are
    fetched.

    Idempotent on `external_id`, which is prefixed by source -- GitHub's
    comment ids are only unique within their own endpoint, and a review
    comment and an issue comment can collide on the bare number.
    """
    fetch = fetcher or fetch_comments

    known = set(
        db.execute(
            select(models.Feedback.external_id).where(
                models.Feedback.external_id.is_not(None)
            )
        ).scalars()
    )

    candidates: list[dict] = []

    for event in db.execute(
        select(models.ActivityEvent).where(
            models.ActivityEvent.repo == repo,
            models.ActivityEvent.kind == "pull_request",
        )
    ).scalars():
        fields = _feedback_from_pull_body(event)
        if fields:
            candidates.append(fields)

    latest = db.execute(
        select(func.max(models.Feedback.occurred_at)).where(
            models.Feedback.source.in_(("pr_review", "issue_comment"))
        )
    ).scalar()
    fetched = list(fetch(repo, latest, limit))
    for item in fetched:
        fields = _feedback_from_comment(item["kind"], item["payload"])
        if fields:
            candidates.append(fields)

    added = 0
    attributed = 0
    for fields in candidates:
        if not fields["external_id"] or fields["external_id"] in known:
            continue
        known.add(fields["external_id"])

        person = collaboration.person_for_login(db, fields["author_login"])
        if person:
            attributed += 1

        db.add(
            models.Feedback(
                person_id=person.id if person else None,
                # Only the project, not the task: feedback is prose about the
                # work, and `link` would be guessing at a task from it.
                project_id=link(db, repo, fields["body"])["project_id"],
                **fields,
            )
        )
        added += 1

    db.commit()
    return {
        "repo": repo,
        "fetched": len(fetched),
        "added": added,
        "attributed": attributed,
        "skipped": len(candidates) - added,
    }


# --- Discovering your own repos ---------------------------------------------


def github_user() -> Optional[str]:
    """Whose repos to list.

    `PP_GITHUB_USER` if set. Otherwise the owner of a repo already mapped to a
    project, which is almost always right and saves asking for something the
    database already knows.
    """
    configured = (os.getenv("PP_GITHUB_USER") or "").strip()
    return configured or None


def owner_from_projects(db: Session) -> Optional[str]:
    for repo in tracked_repos(db):
        if "/" in repo:
            return repo.split("/", 1)[0]
    return None


# Your repositories do not change minute to minute, and the personal page
# fetches them on every visit. Without this, opening that page a dozen times
# spends a fifth of an unauthenticated hourly quota on an unchanged answer.
REPO_CACHE_TTL = 300.0
_REPO_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


def clear_repo_cache() -> None:
    _REPO_CACHE.clear()


def fetch_user_repos(user: str, limit: int = 100, *, fresh: bool = False) -> list[dict]:
    """Every repo on an account, newest activity first, cached briefly.

    `fresh` bypasses the cache, which is what the refresh button asks for.
    The cache key includes whether a token is set, because the same account
    answers differently once it can see private repos.
    """
    key = (user.lower(), limit, bool(os.getenv("GITHUB_TOKEN")))
    if not fresh:
        hit = _REPO_CACHE.get(key)
        if hit and (time.monotonic() - hit[0]) < REPO_CACHE_TTL:
            return hit[1]

    payloads = _fetch_user_repos_uncached(user, limit)
    _REPO_CACHE[key] = (time.monotonic(), payloads)
    return payloads


def _fetch_user_repos_uncached(user: str, limit: int = 100) -> list[dict]:
    """The actual call.

    With `GITHUB_TOKEN` set this uses `/user/repos`, which includes private
    repos and anything the token can see. Without one it falls back to the
    public listing for that username -- fewer repos, but no setup.
    """
    token = os.getenv("GITHUB_TOKEN")
    with httpx.Client(base_url=API_ROOT, headers=_headers(), timeout=TIMEOUT) as client:
        if token:
            path = "/user/repos"
            params: dict[str, Any] = {
                "per_page": min(limit, 100),
                "sort": "updated",
                "affiliation": "owner,collaborator",
            }
        else:
            path = f"/users/{user}/repos"
            params = {"per_page": min(limit, 100), "sort": "updated"}

        response = client.get(path, params=params)
        _raise_for_github(response, user)
        return response.json()


def to_repo_summary(payload: dict) -> dict:
    """The fields worth showing when choosing what to import."""
    return {
        "full_name": payload.get("full_name"),
        "name": payload.get("name"),
        "description": payload.get("description"),
        "language": payload.get("language"),
        "private": bool(payload.get("private")),
        "fork": bool(payload.get("fork")),
        "archived": bool(payload.get("archived")),
        "stars": payload.get("stargazers_count", 0),
        "url": payload.get("html_url"),
        "topics": payload.get("topics") or [],
        "pushed_at": _parse_time(payload.get("pushed_at")),
        "created_at": _parse_time(payload.get("created_at")),
    }


def fetch_readme(repo: str, max_chars: int = 8000) -> str:
    """A repo's README as text, or "" if it has none.

    Used to plan the work that is *left* on an existing project. A missing
    README is ordinary, not an error -- half of anyone's repos have none.
    """
    headers = {**_headers(), "Accept": "application/vnd.github.raw"}
    try:
        with httpx.Client(base_url=API_ROOT, headers=headers, timeout=TIMEOUT) as client:
            response = client.get(f"/repos/{repo}/readme")
            if response.status_code == 404:
                return ""
            _raise_for_github(response, repo)
            return response.text[:max_chars]
    except (httpx.HTTPError, RuntimeError):
        return ""


# --- Working out whose account this is --------------------------------------

# owner/name out of any of the URL forms git writes:
#   https://github.com/OWNER/REPO.git
#   git@github.com:OWNER/REPO.git
#   ssh://git@github.com/OWNER/REPO.git
REMOTE_OWNER = re.compile(
    r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.IGNORECASE
)


def owner_from_git_remote() -> Optional[str]:
    """The GitHub account this checkout belongs to, read from .git/config.

    The tracker is itself in a git repository with a GitHub remote, so the
    account is already on disk and asking for it again is asking twice. Read
    rather than shelled out to, so it works whether or not git is on PATH.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        config = parent / ".git" / "config"
        if not config.is_file():
            continue
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # Every remote, not just origin: a fork's origin may be someone else's
        # account while the one you push to is named something else.
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("url"):
                continue
            match = REMOTE_OWNER.search(line.split("=", 1)[-1].strip())
            if match:
                return match.group(1)
        return None
    return None


def owner_from_token() -> Optional[str]:
    """Who the configured token belongs to.

    The most authoritative answer when there is one, and the only one that
    also unlocks private repos.
    """
    if not os.getenv("GITHUB_TOKEN"):
        return None
    try:
        with httpx.Client(base_url=API_ROOT, headers=_headers(), timeout=TIMEOUT) as client:
            response = client.get("/user")
            if response.is_error:
                return None
            return response.json().get("login")
    except httpx.HTTPError:
        return None


def resolve_account(db: Session, asked: Optional[str] = None) -> tuple[Optional[str], str]:
    """Whose repos to list, and how that was decided.

    Ordered by how much it can be trusted, and returned with its reason so the
    UI can say why it is showing that account rather than silently picking one.
    """
    if asked:
        return asked, "asked for"
    configured = github_user()
    if configured:
        return configured, "PP_GITHUB_USER"
    from_token = owner_from_token()
    if from_token:
        return from_token, "the GITHUB_TOKEN account"
    from_remote = owner_from_git_remote()
    if from_remote:
        return from_remote, "this repository's git remote"
    from_projects = owner_from_projects(db)
    if from_projects:
        return from_projects, "a repo already mapped to a project"
    return None, "nothing to go on"
