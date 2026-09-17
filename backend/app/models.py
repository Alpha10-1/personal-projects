"""The whole data model for the personal projects system.

Everything hangs off a Project. Tasks, milestones, notes, links, files and
time logs all carry an optional project_id -- optional because plenty of real
work (a one-off request, half an hour reading a paper) doesn't belong to a
project yet, and forcing it to would just mean it never gets recorded.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)

from app.db import Base


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


# Who put a row here: the person using the app, or an agent acting for them.
# Recorded so generated content is never indistinguishable from what was
# entered by hand -- the moment those blur, none of the numbers mean anything.
SOURCES = ("human", "agent")


def source_column():
    return Column(String(10), nullable=False, default="human", index=True)


class Project(Base):
    """A body of work with an outcome -- a model to ship, an analysis to
    deliver, a capability to learn."""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    summary = Column(Text, nullable=True)

    # idea | planning | active | on_hold | done | archived
    status = Column(String(20), nullable=False, default="planning", index=True)
    # research | build | analysis | learning | ops | other
    category = Column(String(30), nullable=False, default="other", index=True)
    # low | medium | high
    priority = Column(String(10), nullable=False, default="medium", index=True)

    start_date = Column(Date, nullable=True)
    target_date = Column(Date, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Why this matters and what "done" looks like -- the two things easiest to
    # lose track of a month in.
    objective = Column(Text, nullable=True)
    definition_of_done = Column(Text, nullable=True)
    # Who asked for it / who sees the result. Free text: on a personal board
    # there is no user table to point at.
    stakeholder = Column(String(255), nullable=True)
    tech_stack = Column(String(255), nullable=True)
    # "owner/name" on GitHub. Activity in this repo is attributed to this
    # project, which is the cheapest linkage that is actually reliable.
    repo = Column(String(255), nullable=True, index=True)

    # Manually set 0-100. Kept alongside the task-derived percentage rather
    # than replacing it, because early-stage work often has real progress and
    # no tasks written down yet.
    progress_override = Column(Integer, nullable=True)

    retro = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    archived_at = Column(DateTime, nullable=True, index=True)


class Milestone(Base):
    """A checkpoint inside a project -- the dated things you'd report upward."""

    __tablename__ = "milestones"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    title = Column(String(255), nullable=False)
    detail = Column(Text, nullable=True)
    due_date = Column(Date, nullable=True, index=True)
    # pending | done
    status = Column(String(20), nullable=False, default="pending", index=True)
    completed_at = Column(DateTime, nullable=True)
    position = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    milestone_id = Column(Integer, ForeignKey("milestones.id"), nullable=True, index=True)
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)

    title = Column(String(255), nullable=False)
    notes = Column(Text, nullable=True)
    # todo | in_progress | blocked | done
    status = Column(String(20), nullable=False, default="todo", index=True)
    priority = Column(String(10), nullable=False, default="medium", index=True)

    due_date = Column(Date, nullable=True, index=True)
    estimate_hours = Column(Float, nullable=True)
    # Filled in when status is blocked, so the reason survives the standup.
    blocked_reason = Column(Text, nullable=True)

    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    source = source_column()


class TimeLog(Base):
    """Hours spent. Attached to a task, a project, or neither."""

    __tablename__ = "time_logs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)

    work_date = Column(Date, nullable=False, index=True)
    hours = Column(Float, nullable=False)
    # research | build | analysis | meeting | learning | admin | other
    category = Column(String(30), nullable=False, default="build", index=True)
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    source = source_column()


class Note(Base):
    """A dated entry: a decision, a result, a dead end worth not repeating."""

    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)

    title = Column(String(255), nullable=True)
    body = Column(Text, nullable=False)
    # note | decision | result | blocker | idea
    kind = Column(String(20), nullable=False, default="note", index=True)
    pinned = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    source = source_column()


class Link(Base):
    """A saved URL -- papers, repos, docs, datasets, dashboards."""

    __tablename__ = "links"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)

    title = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    # paper | repo | doc | dataset | tool | other
    kind = Column(String(20), nullable=False, default="other", index=True)
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow)


class ActivityEvent(Base):
    """Something that happened in another system: a commit, a PR, an issue.

    This table records facts and nothing else. It does not decide what a
    commit means for a project's progress -- that inference belongs further
    up, where it can be reviewed and corrected. Keeping the two apart is what
    makes it safe to re-derive the interpretation later without re-fetching
    the history.

    `external_id` is the provider's own identifier and is unique, so a sync
    that runs twice records each event once.
    """

    __tablename__ = "activity_events"

    id = Column(Integer, primary_key=True)

    # github (more later). Not called "source": that word is already taken by
    # the human/agent provenance flag on the other tables.
    provider = Column(String(20), nullable=False, default="github", index=True)
    external_id = Column(String(255), nullable=False, unique=True)
    # commit | pull_request | issue
    kind = Column(String(20), nullable=False, index=True)

    repo = Column(String(255), nullable=True, index=True)
    actor = Column(String(255), nullable=True)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    occurred_at = Column(DateTime, nullable=False, index=True)

    # The provider's payload, kept verbatim so a later linkage rule can look
    # at fields this schema never thought to store.
    raw = Column(Text, nullable=True)

    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)
    # repo | convention | manual -- how the link above was arrived at, so a
    # guess is never mistaken for something the user stated.
    linked_by = Column(String(20), nullable=True)

    created_at = Column(DateTime, default=utcnow)


class Attachment(Base):
    """A file on local disk. `stored_name` is what's on disk, `filename` is
    what it was called when it arrived."""

    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)

    filename = Column(String(255), nullable=False)
    stored_name = Column(String(255), nullable=False, unique=True)
    content_type = Column(String(120), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow)
