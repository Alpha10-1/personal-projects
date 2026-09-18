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
    UniqueConstraint,
)

from app.db import Base


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


# Who put a row here: the person using the app, or an agent acting for them.
# Recorded so generated content is never indistinguishable from what was
# entered by hand -- the moment those blur, none of the numbers mean anything.
SOURCES = ("human", "agent")

# Which side of the app a project belongs to.
WORKSPACES = ("work", "personal")


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

    # work | personal. Personal projects are deliberately outside the
    # analyst's reach: no findings, no suggestion rules, no scheduled run.
    # A hobby repo you touch every few months is not "stalled", and a system
    # that says it is teaches you to ignore it.
    workspace = Column(String(20), nullable=False, default="work", index=True)

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

    # Resolved from `actor` when a person with that github_login exists. Stored
    # rather than joined at read time so the attribution survives someone
    # renaming their GitHub account, and re-resolved by /people/relink when a
    # person is added after their work was already ingested.
    person_id = Column(Integer, ForeignKey("people.id"), nullable=True, index=True)

    created_at = Column(DateTime, default=utcnow)


class Suggestion(Base):
    """A change the analyst thinks should happen, waiting on a decision.

    Suggestions are never applied on their own. The agent can see the same
    evidence you can and is often right, but "often right" applied silently
    to the numbers you steer by is worse than nothing -- so the row sits here
    with its reasoning until it is accepted or dismissed.

    `fingerprint` is unique, which does double duty: refreshing does not
    produce duplicates, and a suggestion you dismissed is never raised again,
    because the dismissed row still occupies its fingerprint.
    """

    __tablename__ = "suggestions"

    id = Column(Integer, primary_key=True)

    # Which rule produced this, so a noisy one can be found and turned off.
    rule = Column(String(40), nullable=False, index=True)
    fingerprint = Column(String(300), nullable=False, unique=True)

    # task | project
    target_type = Column(String(20), nullable=False)
    target_id = Column(Integer, nullable=False)

    field = Column(String(40), nullable=False)
    current_value = Column(String(255), nullable=True)
    proposed_value = Column(String(255), nullable=False)

    # Why, in the user's terms, and what it was read off. Both stored: a
    # suggestion you cannot audit is one you have to take on trust.
    rationale = Column(Text, nullable=False)
    evidence = Column(Text, nullable=True)

    # pending | accepted | dismissed
    status = Column(String(20), nullable=False, default="pending", index=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("fingerprint", name="uq_suggestion_fingerprint"),)


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


# --- People -----------------------------------------------------------------

# What a person is on a project. A viewer is shown progress; a contributor is
# expected to appear in the git history. The distinction is about what you
# expect from them, not about permissions -- there is no login to permit.
MEMBER_ROLES = ("viewer", "contributor")


class Person(Base):
    """Someone involved in the work who is not you.

    `github_login` is the join back to the git history: it is what turns an
    `actor` string on an activity event into a named person. Without it a
    person can still be a viewer and receive digests, they just have no
    contributions, which is the honest result rather than an empty guess.
    """

    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)

    # One login belongs to one person. Null is allowed and common -- a
    # stakeholder who only ever reads a digest has no GitHub account here.
    github_login = Column(String(100), nullable=True, unique=True, index=True)

    # "Data engineering lead" -- their role in the organisation, not in this app.
    role_title = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    archived_at = Column(DateTime, nullable=True, index=True)


class ProjectMember(Base):
    """A person linked to a project, and in what capacity."""

    __tablename__ = "project_members"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    person_id = Column(Integer, ForeignKey("people.id"), nullable=False, index=True)

    role = Column(String(20), nullable=False, default="viewer", index=True)
    added_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "person_id", name="uq_member_project_person"),
    )


# Where a piece of feedback came from. All but `manual` are read out of git,
# which is the point: a suggestion made in a pull request is already recorded
# somewhere durable, and this only mirrors it.
FEEDBACK_SOURCES = ("pr_review", "pr_body", "issue_comment", "manual")
FEEDBACK_STATUSES = ("open", "actioned", "declined")


class Feedback(Base):
    """Something a person said should happen.

    Deliberately not the same table as `Suggestion`. A Suggestion is a
    machine-proposed change to one field, with a fingerprint so it can be
    applied or suppressed. This is prose from a human -- there is no field to
    set and no rule that produced it, and flattening the two would mean
    either losing the words or pretending a sentence is a field change.

    `author_login` is kept even when no person matches, so feedback from
    someone you have not added yet is recorded rather than dropped. Adding
    that person later attaches it.
    """

    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True)

    person_id = Column(Integer, ForeignKey("people.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)

    source = Column(String(20), nullable=False, index=True)
    # GitHub's own id for the comment, so syncing twice does not duplicate.
    # Null for anything entered by hand.
    external_id = Column(String(255), nullable=True, unique=True)

    author_login = Column(String(100), nullable=True, index=True)
    body = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    occurred_at = Column(DateTime, nullable=False, index=True)

    status = Column(String(20), nullable=False, default="open", index=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


# --- Brainstorming ----------------------------------------------------------


class Brainstorm(Base):
    """A saved thinking session about an idea.

    Separate from the chat floater, which is deliberately throwaway. A
    brainstorm is kept because the interesting part is usually the third
    exchange, not the first, and because what comes out of it should be able
    to become a project without retyping it.
    """

    __tablename__ = "brainstorms"

    id = Column(Integer, primary_key=True)
    # Optional: the best ideas start before there is a project to file under.
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)

    topic = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class BrainstormMessage(Base):
    __tablename__ = "brainstorm_messages"

    id = Column(Integer, primary_key=True)
    brainstorm_id = Column(
        Integer, ForeignKey("brainstorms.id"), nullable=False, index=True
    )
    # user | assistant
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, index=True)


# --- Dashboards -------------------------------------------------------------

# manual: a link you pasted. powerbi: mirrored from the Power BI REST API.
DASHBOARD_SOURCES = ("manual", "powerbi")


class Dashboard(Base):
    """A report or dashboard, linked to the work it reports on.

    One table for both halves deliberately. A link you paste today and a
    report synced from the Power BI Service tomorrow are the same thing to
    everyone reading the project -- the difference is only whether anything
    can be known about its refresh state, and a null there says exactly that.

    Matching a pasted link to a synced report is by `external_id`: paste a
    report URL now and the sync fills in the workspace, dataset and refresh
    state later rather than creating a duplicate beside it.
    """

    __tablename__ = "dashboards"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)

    name = Column(String(255), nullable=False)
    url = Column(Text, nullable=True)
    note = Column(Text, nullable=True)

    source = Column(String(20), nullable=False, default="manual", index=True)
    # The provider's report id. Unique so a sync cannot duplicate a row, and
    # nullable because a pasted link may not have one yet.
    external_id = Column(String(255), nullable=True, unique=True)

    workspace_id = Column(String(255), nullable=True)
    workspace_name = Column(String(255), nullable=True)
    dataset_id = Column(String(255), nullable=True)
    dataset_name = Column(String(255), nullable=True)

    # What the provider last said about the data behind it. Null means
    # unknown, which is the honest state for a link nobody can check.
    last_refresh_at = Column(DateTime, nullable=True)
    refresh_status = Column(String(40), nullable=True, index=True)
    refresh_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
