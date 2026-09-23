"""Which files change constantly, and which of those nothing tests.

Two facts that are individually dull and together the most useful thing you
can say about a repository you are about to change. A file that changes
every week is where the work is. A file that nothing tests is where a
mistake survives. The overlap is where a mistake survives *and* is likely,
and that is a short list worth having on a screen.

Both halves are already lying around. Commit file lists come from the deep
sync and are stored on `activity_events`; whether a file has a test is
`impact.py`'s question, answered the way it answers it everywhere else --
by pattern and by search, approximately, and saying so.

**Nothing here is a judgement about the code.** A file at the top of this
list is not badly written; it is busy. The only claim being made is "you
change this a lot and nothing would tell you if you broke it", which is a
fact about the repository rather than an opinion about the author.

It is deterministic and free, which is the point: it can run on every
review, it can be checked by hand, and it costs nothing to be wrong about.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import hygiene, impact, models, workspace

# Files nobody writes by hand. Counting them as churn buries the code you
# actually maintain under lockfiles and generated clients.
IGNORED = hygiene.NOISE + (
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/poetry.lock",
    "**/uv.lock",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.map",
    "**/*.svg",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.ico",
    "**/*.woff*",
    "**/*.pdf",
    "**/*.csv",
    "**/*.snap",
)

# A file has to have changed this often before "it changes a lot" is a
# claim rather than noise. Three is the smallest number that is a pattern.
BUSY = 3

# How far back churn is counted. A year would rank a file you rewrote once
# in January above one you have touched every week since. Ninety days is
# roughly "this quarter", which is the window a person actually means by
# "what am I working on".
WINDOW_DAYS = 90

# How many rows to return. Past this it stops being a list you read and
# becomes a report you skim.
TOP = 20


@dataclass
class File:
    """One source file, how much it moves, and whether anything tests it."""

    path: str
    commits: int = 0
    lines_changed: int = 0
    last_changed: Optional[str] = None
    authors: int = 1
    # None means the question could not be asked: no checkout, an unknown
    # language, or a name too common to search for. Distinct from False,
    # which is a real answer.
    tested: Optional[bool] = None
    test_files: list[str] = field(default_factory=list)
    why_unknown: Optional[str] = None
    exists: bool = True

    @property
    def at_risk(self) -> bool:
        return self.commits >= BUSY and self.tested is False and self.exists


def counted(db: Session, project_id: int, window_days: int = WINDOW_DAYS) -> list[File]:
    """Every file touched in the window, busiest first.

    Renames are not followed -- git records them as a delete and an add, and
    `file_stats` keeps whatever GitHub reported. A file renamed mid-window
    therefore appears twice with its history split, which understates both.
    Worth knowing before reading the numbers as exact.
    """
    cutoff = datetime.combine(date.today() - timedelta(days=window_days), datetime.min.time())
    events = list(
        db.execute(
            select(models.ActivityEvent).where(
                models.ActivityEvent.kind == "commit",
                models.ActivityEvent.project_id == project_id,
                models.ActivityEvent.occurred_at >= cutoff,
            )
        ).scalars()
    )

    files: dict[str, File] = {}
    authors: dict[str, set[str]] = {}
    for event in events:
        if not event.file_stats:
            continue
        try:
            payload = json.loads(event.file_stats)
        except (ValueError, TypeError):
            continue
        for entry in payload.get("files") or []:
            if not isinstance(entry, dict):
                continue
            path = (entry.get("path") or "").replace("\\", "/")
            if not path or matches_ignored(path):
                continue
            held = files.setdefault(path, File(path=path))
            held.commits += 1
            held.lines_changed += int(entry.get("additions") or 0) + int(
                entry.get("deletions") or 0
            )
            when = event.occurred_at.isoformat() if event.occurred_at else None
            if when and (held.last_changed is None or when > held.last_changed):
                held.last_changed = when
            if event.actor:
                authors.setdefault(path, set()).add(event.actor)

    for path, who in authors.items():
        files[path].authors = len(who)

    return sorted(
        files.values(), key=lambda f: (-f.commits, -f.lines_changed, f.path)
    )


def matches_ignored(path: str) -> bool:
    from app import agent

    return agent.matches(path, IGNORED) or impact.is_test(path)


def cover(root: Path, entry: File) -> None:
    """Fill in whether anything tests this file.

    Asked the way the Explain panel asks it: find the definitions in the
    file, search the repository for each, and see whether any of the hits
    are tests. A file whose names are all too common to search cannot be
    answered, and says so rather than being reported as untested -- which
    would be the more damaging of the two possible mistakes.
    """
    try:
        payload = workspace.read_file(root, entry.path)
    except workspace.WorkspaceError:
        entry.exists = False
        entry.why_unknown = "not in the working copy"
        return
    if payload["binary"]:
        entry.why_unknown = "a binary file"
        return

    if impact.language_of(entry.path) is None:
        entry.why_unknown = "the outline does not read this language yet"
        return

    names = [s.name for s in impact.symbols(payload["content"], entry.path)]
    searchable = [name for name in names if impact.searchable(name)]
    if not names:
        entry.why_unknown = "no definitions were found in it"
        return
    if not searchable:
        entry.why_unknown = "its names are too common to search for reliably"
        return

    tests: list[str] = []
    for name in searchable[:10]:
        found = impact.references(root, name, exclude=entry.path)
        if found.searched:
            tests.extend(found.tests)
    entry.test_files = sorted(set(tests))[:5]
    entry.tested = bool(tests)


def as_row(entry: File) -> dict:
    """One file as JSON, with the derived flag the UI sorts on.

    `at_risk` is a property, and `asdict` does not carry properties -- so
    the one field the whole panel is about would have been silently absent
    from the payload.
    """
    return {**asdict(entry), "at_risk": entry.at_risk}


def map_project(
    db: Session, project: models.Project, window_days: int = WINDOW_DAYS, top: int = TOP
) -> dict:
    """The churn list for one project, with coverage where it can be had.

    Coverage is only asked for the busiest files: each answer is a handful
    of `git grep` calls, and the question is uninteresting for a file that
    changed once.
    """
    rows = counted(db, project.id, window_days)
    busiest = rows[:top]

    root = None
    if project.local_path:
        try:
            root = workspace.resolve(project.local_path)
        except workspace.WorkspaceError:
            root = None

    if root is not None:
        for entry in busiest:
            if entry.commits >= BUSY:
                cover(root, entry)
            else:
                entry.why_unknown = "changed too rarely to be worth checking"

    scanned = db.execute(
        select(models.ActivityEvent).where(
            models.ActivityEvent.kind == "commit",
            models.ActivityEvent.project_id == project.id,
        )
    ).scalars()
    total = 0
    without_detail = 0
    for event in scanned:
        total += 1
        if not event.file_stats:
            without_detail += 1

    return {
        "project_id": project.id,
        "window_days": window_days,
        "files": [as_row(entry) for entry in busiest],
        "at_risk": [as_row(entry) for entry in busiest if entry.at_risk],
        "files_touched": len(rows),
        "commits_scanned": total,
        "commits_without_detail": without_detail,
        "code_was_read": root is not None,
        "limits": [
            "Churn is counted from commit file lists, so a commit whose "
            "detail was never fetched is not in these numbers.",
            "Renames are not followed: git records one as a delete and an "
            "add, so a renamed file's history appears split.",
            "“Tested” means a test file mentions a name defined here. It is "
            "a search, not a coverage run, and it cannot tell you whether "
            "the test asserts anything useful.",
        ],
    }
