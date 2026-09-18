"""What a repository's history actually says, before anyone interprets it.

The shape of a project's past is arithmetic: which months had commits, which
parts of the tree they touched, when an area first appeared and when it was
last worked on. None of that needs a model, and computing it here means the
model is handed facts to narrate rather than a pile of commit messages to
guess from -- the same split `review.py` makes between findings and prose.

It also means an answer can be checked. "The portal work started in August"
is either in the timeline or it is not.
"""

import json
from collections import Counter, defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

# Two segments is usually the meaningful unit -- `backend/app`, `src/components`
# -- where one is too coarse and three splits a feature across rows.
AREA_DEPTH = 2

# Noise in every repository. Counted, but never described as where the work went.
NOISE = ("__pycache__", "node_modules", ".venv", "dist", "build", ".next")

TOP_AREAS = 12
TOP_FILES = 15
SUBJECTS_PER_PERIOD = 6


def area_of(path: str) -> str:
    """The part of the tree a file belongs to."""
    parts = [p for p in path.split("/") if p]
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[:AREA_DEPTH][: max(1, len(parts) - 1)])


def is_noise(path: str) -> bool:
    return any(marker in path for marker in NOISE)


def subject(title: Optional[str]) -> str:
    """A commit's first line."""
    return (title or "").splitlines()[0].strip() if title else ""


def commits_for(db: Session, project_id: int) -> list[models.ActivityEvent]:
    return list(
        db.execute(
            select(models.ActivityEvent)
            .where(
                models.ActivityEvent.project_id == project_id,
                models.ActivityEvent.kind == "commit",
            )
            .order_by(models.ActivityEvent.occurred_at)
        ).scalars()
    )


def timeline(db: Session, project: models.Project) -> dict:
    """The history as numbers: periods, areas, files, people.

    `detailed` counts how many commits had their file-level detail fetched.
    A summary built from 6 of 46 is a different claim from one built on all
    of them, and the caller is told which it has rather than left to assume.
    """
    commits = commits_for(db, project.id)
    if not commits:
        return {"commits": 0, "detailed": 0, "periods": [], "areas": [], "files": [],
                "contributors": [], "span": None}

    periods: dict[str, dict] = defaultdict(
        lambda: {"commits": 0, "additions": 0, "deletions": 0,
                 "areas": Counter(), "subjects": []}
    )
    areas: dict[str, dict] = defaultdict(
        lambda: {"commits": 0, "additions": 0, "deletions": 0,
                 "first": None, "last": None}
    )
    files = Counter()
    file_churn = Counter()
    contributors = Counter()
    detailed = 0

    for event in commits:
        month = f"{event.occurred_at:%Y-%m}"
        bucket = periods[month]
        bucket["commits"] += 1
        if len(bucket["subjects"]) < SUBJECTS_PER_PERIOD:
            line = subject(event.title)
            if line:
                bucket["subjects"].append(line[:110])
        contributors[event.actor or "unknown"] += 1

        if not event.file_stats:
            continue
        try:
            stats = json.loads(event.file_stats)
        except ValueError:
            continue
        detailed += 1

        bucket["additions"] += stats.get("additions", 0)
        bucket["deletions"] += stats.get("deletions", 0)

        touched = set()
        for changed in stats.get("files") or []:
            path = changed.get("path") or ""
            if not path or is_noise(path):
                continue
            churn = changed.get("additions", 0) + changed.get("deletions", 0)
            files[path] += 1
            file_churn[path] += churn

            name = area_of(path)
            touched.add(name)
            row = areas[name]
            row["additions"] += changed.get("additions", 0)
            row["deletions"] += changed.get("deletions", 0)
            if row["first"] is None or event.occurred_at < row["first"]:
                row["first"] = event.occurred_at
            if row["last"] is None or event.occurred_at > row["last"]:
                row["last"] = event.occurred_at

        for name in touched:
            areas[name]["commits"] += 1
            bucket["areas"][name] += 1

    return {
        "commits": len(commits),
        "detailed": detailed,
        "span": (commits[0].occurred_at, commits[-1].occurred_at),
        "periods": [
            {
                "month": month,
                **{k: v for k, v in data.items() if k != "areas"},
                "areas": [a for a, _ in data["areas"].most_common(5)],
            }
            for month, data in sorted(periods.items())
        ],
        "areas": [
            {
                "area": name,
                "commits": data["commits"],
                "additions": data["additions"],
                "deletions": data["deletions"],
                "first": data["first"],
                "last": data["last"],
            }
            for name, data in sorted(
                areas.items(), key=lambda kv: kv[1]["commits"], reverse=True
            )[:TOP_AREAS]
        ],
        "files": [
            {"path": path, "commits": count, "churn": file_churn[path]}
            for path, count in files.most_common(TOP_FILES)
        ],
        "contributors": [
            {"actor": actor, "commits": count} for actor, count in contributors.most_common()
        ],
    }


def as_text(data: dict, project: models.Project) -> str:
    """The timeline as prose-shaped text, for a prompt.

    Deliberately states how complete it is at the top: a model told it is
    looking at 6 commits of 46 can say so, where one handed the same numbers
    without that line will happily describe them as the whole story.
    """
    if not data["commits"]:
        return f"No commits recorded for {project.name}."

    start, end = data["span"]
    lines = [
        f"REPOSITORY: {project.repo}",
        f"{data['commits']} commits from {start:%d %b %Y} to {end:%d %b %Y}.",
    ]
    if data["detailed"] < data["commits"]:
        lines.append(
            f"File-level detail is available for {data['detailed']} of them; "
            f"the other {data['commits'] - data['detailed']} are known only by "
            "their commit message. Say so if it limits an answer."
        )

    lines.append("\nCONTRIBUTORS")
    for c in data["contributors"]:
        lines.append(f"  {c['actor']}: {c['commits']} commits")

    lines.append("\nTIMELINE BY MONTH")
    for period in data["periods"]:
        churn = (
            f", +{period['additions']}/-{period['deletions']} lines"
            if period["additions"] or period["deletions"]
            else ""
        )
        lines.append(f"  {period['month']}: {period['commits']} commits{churn}")
        if period["areas"]:
            lines.append(f"      areas: {', '.join(period['areas'])}")
        for line in period["subjects"]:
            lines.append(f"      - {line}")

    # Both lists below are rankings, and both say so. Without that, absence
    # from a top-N list reads as absence from the repository, and a plan gets
    # written to build something that is already there.
    if data["areas"]:
        lines.append(
            f"\nWHERE THE WORK WENT (the {len(data['areas'])} busiest parts of "
            "the tree, not every part)"
        )
        for area in data["areas"]:
            lines.append(
                f"  {area['area']}: {area['commits']} commits, "
                f"+{area['additions']}/-{area['deletions']}, "
                f"first {area['first']:%b %Y}, last {area['last']:%b %Y}"
            )

    if data["files"]:
        lines.append(
            f"\nMOST-CHANGED FILES (the top {len(data['files'])} by churn. A "
            "file missing from this list was changed less often; it does not "
            "mean the file does not exist, and says nothing about whether a "
            "feature was built)"
        )
        for item in data["files"]:
            lines.append(
                f"  {item['path']} ({item['commits']} commits, {item['churn']} lines)"
            )

    return "\n".join(lines)
