"""What has been committed that should not have been.

This asks one question of history rather than of the working copy: *has a
credential, or a build cache, ever been committed to this repository*. The
distinction matters more than it first appears. Deleting a key from the
current checkout removes it from `HEAD` and leaves it in every clone, every
fork and every fetch that ever ran. A tool that only looked at what is on
disk today would call that fixed.

So the scan runs over `ActivityEvent.file_stats` -- the per-commit file
lists already ingested by the deep sync -- and reports the commit that
introduced each path. No network, no model, no git invocation: this is
arithmetic over rows that are already stored, which is why it can run on
every review.

**It reports and stops there.** Rewriting history is destructive, has to be
coordinated with anyone who has a clone, and is not something a tracker
should offer to do for you. What it can do is make sure you know, name the
commit, and say plainly that deleting the file now is not enough.

The credential list is `agent.NEVER_READ` -- deliberately the same list the
coding agent refuses to read. One definition of "this is a secret", used by
everything that needs one.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent, models

# Committed build output and caches. Not a security problem -- a noise and
# merge-conflict problem, and a sign that a `.gitignore` is missing an entry
# rather than that anything is wrong with the code.
#
# Kept separate from credentials on purpose: mixing "you have leaked a key"
# with "you have committed a cache" into one list teaches you to skim both.
NOISE = (
    "node_modules/*",
    "**/node_modules/*",
    ".next/*",
    "**/.next/*",
    ".firebase/*",
    "**/.firebase/*",
    "dist/*",
    "**/dist/*",
    "build/*",
    "**/build/*",
    "__pycache__/*",
    "**/__pycache__/*",
    "**/*.pyc",
    ".venv/*",
    "**/.venv/*",
    "coverage/*",
    "**/coverage/*",
    "**/*.log",
    ".DS_Store",
    "**/.DS_Store",
)

# Files whose whole purpose is to be committed. `.env.example` matches the
# credential list by name and is the opposite of a leak: it is the file that
# exists so nobody has to guess what goes in the real one.
#
# Exempting them is not optional politeness. A scan that flags every
# `.env.example` fires on almost every repository including this one, and a
# check that always fires is a check nobody reads -- which would cost more
# than it catches, because the `serviceAccountKey.json` sitting three lines
# below it is real.
#
# They are not dropped silently. A template is still worth one look, because
# a template with real values left in it is a common way to leak a key, and
# `check_present` is where that is decided.
TEMPLATES = (
    "**/*.example",
    "**/*.sample",
    "**/*.template",
    "**/*.dist",
    "**/*.example.*",
    "**/.env.example",
    "**/.env.sample",
    "**/.env.template",
    "**/.env.defaults",
)

# An assignment in a template: `KEY=something`.
FILLED = re.compile(r"^\s*(?!#)([A-Za-z_][\w.]*)\s*=\s*(?P<value>\S.*)$")

# Values that are obviously not real: the placeholders people write.
PLACEHOLDER = re.compile(
    r"^([\"']?)(|x+|y+|\.\.\.|<.*>|\{.*\}|\$\{.*\}|your[-_ ].*|change[-_ ]?me"
    r"|replace[-_ ]?me|todo|tbd|none|null|example.*|sample.*|placeholder.*"
    r"|sk-ant-\.\.\..*|\*+)\1$",
    re.IGNORECASE,
)

# A filled-in value only matters when it is meant to be a secret. A template
# with `ENVIRONMENT=development` in it is a template doing its job, and
# flagging it is how a check earns its way into being ignored.
#
# So two independent signals, either of which is enough: the key is named
# like a secret, or the value is shaped like one.
SECRET_NAME = re.compile(
    r"(^|_)(KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|CREDENTIALS?|PRIVATE|SALT"
    r"|SIGNING|CERT|DSN|AUTH|ACCESS|REFRESH|SESSION|COOKIE_SECRET|CONNECTION"
    r"|SENTRY_DSN|WEBHOOK)(_|$)",
    re.IGNORECASE,
)
SECRET_VALUE = re.compile(
    r"^(sk-[A-Za-z0-9\-_]{16,}"          # OpenAI / Anthropic
    r"|sk_live_[A-Za-z0-9]{10,}"          # Stripe
    r"|gh[pousr]_[A-Za-z0-9]{20,}"        # GitHub
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"      # Slack
    r"|AKIA[0-9A-Z]{16}"                  # AWS
    r"|AIza[0-9A-Za-z\-_]{30,}"           # Google
    r"|eyJ[A-Za-z0-9\-_]{10,}\."          # a JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"     # a key, pasted in
    r"|[A-Za-z0-9+/]{40,}={0,2}$"         # long and random-looking
    r")",
)

# Anything with a real credential in it, regardless of where. A secret in a
# `postgres://user:pass@host` URL is not caught by the key name.
SECRET_IN_URL = re.compile(r"://[^/\s:@]+:([^/\s@]{6,})@")

# A path has to appear in this many commits before it is called a habit
# rather than a slip. One committed cache file is an accident; forty is a
# missing `.gitignore` line, and that is the more useful thing to say.
HABIT = 5

# How many distinct paths to name in one finding. Past this the list stops
# being readable and the count is the information.
MAX_NAMED = 6


@dataclass
class Committed:
    """A path that was committed, and the first commit that did it."""

    path: str
    commits: int = 0
    first_sha: Optional[str] = None
    first_at: Optional[str] = None
    first_title: Optional[str] = None
    still_present: Optional[bool] = None  # None when there is no checkout
    # Templates only, and only with a checkout: a line that looks filled in
    # rather than left as a placeholder.
    filled_keys: list[str] = field(default_factory=list)


@dataclass
class Report:
    project_id: Optional[int]
    repo: Optional[str]
    secrets: list[Committed] = field(default_factory=list)
    noise: list[Committed] = field(default_factory=list)
    # Exempted, and shown anyway, so the filter can be judged rather than
    # trusted -- the same contract the survey and the roadmap keep.
    templates: list[Committed] = field(default_factory=list)
    commits_scanned: int = 0
    commits_without_detail: int = 0

    @property
    def clean(self) -> bool:
        return not self.secrets and not self.noise and not self.filled_templates

    @property
    def filled_templates(self) -> list[Committed]:
        """Templates that appear to have real values in them."""
        return [entry for entry in self.templates if entry.filled_keys]


def paths_of(event: models.ActivityEvent) -> list[str]:
    """The files a commit touched, or nothing if the detail was never fetched.

    `file_stats` is filled in by the deep sync, one request per commit, so
    an ordinary sync leaves it null. That is a gap in coverage rather than
    an absence of findings, and the report says which it is.
    """
    if not event.file_stats:
        return []
    try:
        payload = json.loads(event.file_stats)
    except (ValueError, TypeError):
        return []
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return []
    out = []
    for entry in files:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if isinstance(path, str) and path:
            out.append(path.replace("\\", "/"))
    return out


def sha_of(external_id: str) -> Optional[str]:
    """The commit sha out of `github:commit:<sha>`."""
    parts = (external_id or "").split(":")
    return parts[-1][:12] if len(parts) >= 3 and parts[-1] else None


def scan(db: Session, project_id: Optional[int] = None) -> list[Report]:
    """Every committed secret and committed cache, by project.

    Oldest commit first, so the `first_*` fields on each path really are the
    commit that introduced it rather than whichever one happened to be read
    first.
    """
    stmt = select(models.ActivityEvent).where(models.ActivityEvent.kind == "commit")
    if project_id is not None:
        stmt = stmt.where(models.ActivityEvent.project_id == project_id)
    events = list(
        db.execute(stmt.order_by(models.ActivityEvent.occurred_at.asc())).scalars()
    )

    by_project: dict[Optional[int], Report] = {}
    found: dict[Optional[int], dict[str, Committed]] = defaultdict(dict)

    for event in events:
        report = by_project.setdefault(
            event.project_id, Report(project_id=event.project_id, repo=event.repo)
        )
        report.commits_scanned += 1
        paths = paths_of(event)
        if not paths:
            report.commits_without_detail += 1
            continue

        for path in paths:
            if not (
                agent.matches(path, agent.NEVER_READ) or agent.matches(path, NOISE)
            ):
                continue
            seen = found[event.project_id].get(path)
            if seen is None:
                seen = Committed(
                    path=path,
                    first_sha=sha_of(event.external_id),
                    first_at=event.occurred_at.isoformat() if event.occurred_at else None,
                    first_title=(event.title or "").splitlines()[0][:120] or None,
                )
                found[event.project_id][path] = seen
            seen.commits += 1

    for key, report in by_project.items():
        for path, entry in sorted(found[key].items()):
            if agent.matches(path, TEMPLATES):
                report.templates.append(entry)
            elif agent.matches(path, agent.NEVER_READ):
                report.secrets.append(entry)
            else:
                report.noise.append(entry)
        # Loudest first: a secret in many commits is no worse than one in a
        # single commit, but a cache in forty is a different conversation
        # from a cache in one.
        report.secrets.sort(key=lambda c: c.path)
        report.noise.sort(key=lambda c: (-c.commits, c.path))

    return [by_project[key] for key in sorted(by_project, key=lambda k: (k is None, k))]


def filled_in(text: str) -> list[str]:
    """The keys in a template that look like they hold a real secret.

    Conservative on purpose, in the direction that keeps the check worth
    reading. A comment is not a finding. A placeholder is not a finding.
    `ENVIRONMENT=development` is not a finding. `STRIPE_SECRET_KEY=sk_live_...`
    is, and so is a password sitting inside a connection string -- which is
    how these actually leak.
    """
    out = []
    for line in text.splitlines():
        match = FILLED.match(line)
        if not match:
            continue
        key = match.group(1)
        value = match.group("value").strip().strip("\"'")
        if not value or PLACEHOLDER.match(value):
            continue
        if SECRET_VALUE.match(value) or SECRET_IN_URL.search(value):
            out.append(key)
        elif SECRET_NAME.search(key):
            out.append(key)
    return out


# A template is a handful of lines. Anything larger is not one, and reading
# it would be reading a file nobody asked about.
MAX_TEMPLATE_BYTES = 64_000


def check_present(root, report: Report) -> None:
    """Fill in what the working copy can tell us that history cannot.

    Two things. Whether each named path is still there -- "still committed"
    and "deleted since" need different advice, and both need the same
    warning about history, which is the part people get wrong. And whether
    an exempted template has real values in it, which is the one way a file
    that is *supposed* to be committed becomes a leak.
    """
    def locate(entry: Committed):
        try:
            target = root / entry.path
            entry.still_present = target.is_file()
            return target if entry.still_present else None
        except (OSError, ValueError):
            entry.still_present = None
            return None

    for entry in report.secrets + report.noise:
        locate(entry)

    for entry in report.templates:
        target = locate(entry)
        if target is None:
            continue
        try:
            if target.stat().st_size > MAX_TEMPLATE_BYTES:
                continue
            entry.filled_keys = filled_in(
                target.read_bytes().decode("utf-8", errors="replace")
            )
        except OSError:
            continue
