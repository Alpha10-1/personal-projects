"""Request/response shapes.

Update schemas use all-optional fields plus `exclude_unset` at the call site,
so a PATCH that omits a field leaves it alone and one that sends null clears it.

Clearing only makes sense for a column that is actually nullable, so fields
backed by a NOT NULL column carry `NoNull` (see below). They stay optional --
a PATCH may still omit them -- but may not be set to null.
"""

from datetime import date, datetime
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError


def _reject_null(value):
    """Reject an explicit null on a field that has no nullable column.

    Pydantic does not validate a field's default, so this only ever runs on a
    value the client actually sent: omitting the field still means "leave it
    alone" rather than tripping this.

    Raised as a PydanticCustomError rather than a ValueError so the message
    reaches the UI as-is; a plain ValueError arrives prefixed with
    "Value error, ", which reads badly next to the field name.
    """
    if value is None:
        raise PydanticCustomError("not_nullable", "cannot be set to null")
    return value


NoNull = AfterValidator(_reject_null)

ProjectStatus = Literal["idea", "planning", "active", "on_hold", "done", "archived"]
ProjectCategory = Literal["research", "build", "analysis", "learning", "ops", "other"]
Priority = Literal["low", "medium", "high"]
TaskStatus = Literal["todo", "in_progress", "blocked", "done"]
MilestoneStatus = Literal["pending", "done"]
TimeCategory = Literal["research", "build", "analysis", "meeting", "learning", "admin", "other"]
NoteKind = Literal["note", "decision", "result", "blocker", "idea"]
LinkKind = Literal["paper", "repo", "doc", "dataset", "tool", "other"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Projects ---------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    summary: Optional[str] = None
    status: ProjectStatus = "planning"
    category: ProjectCategory = "other"
    priority: Priority = "medium"
    start_date: Optional[date] = None
    target_date: Optional[date] = None
    objective: Optional[str] = None
    definition_of_done: Optional[str] = None
    stakeholder: Optional[str] = None
    tech_stack: Optional[str] = None
    repo: Optional[str] = Field(default=None, max_length=255)
    progress_override: Optional[int] = Field(default=None, ge=0, le=100)
    retro: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Annotated[Optional[str], NoNull] = Field(
        default=None, min_length=1, max_length=255
    )
    summary: Optional[str] = None
    status: Annotated[Optional[ProjectStatus], NoNull] = None
    category: Annotated[Optional[ProjectCategory], NoNull] = None
    priority: Annotated[Optional[Priority], NoNull] = None
    start_date: Optional[date] = None
    target_date: Optional[date] = None
    objective: Optional[str] = None
    definition_of_done: Optional[str] = None
    stakeholder: Optional[str] = None
    tech_stack: Optional[str] = None
    repo: Optional[str] = Field(default=None, max_length=255)
    progress_override: Optional[int] = Field(default=None, ge=0, le=100)
    retro: Optional[str] = None


class ProjectOut(ORMModel):
    id: int
    name: str
    summary: Optional[str]
    status: str
    category: str
    priority: str
    start_date: Optional[date]
    target_date: Optional[date]
    completed_at: Optional[datetime]
    objective: Optional[str]
    definition_of_done: Optional[str]
    stakeholder: Optional[str]
    tech_stack: Optional[str]
    repo: Optional[str]
    progress_override: Optional[int]
    retro: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    archived_at: Optional[datetime]

    # Derived by the route layer, not stored.
    task_total: int = 0
    task_done: int = 0
    milestone_total: int = 0
    milestone_done: int = 0
    open_overdue: int = 0
    hours_logged: float = 0.0
    progress: int = 0
    next_due: Optional[date] = None


# --- Milestones -------------------------------------------------------------

class MilestoneCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    detail: Optional[str] = None
    due_date: Optional[date] = None
    status: MilestoneStatus = "pending"
    position: int = 0


class MilestoneUpdate(BaseModel):
    title: Annotated[Optional[str], NoNull] = Field(
        default=None, min_length=1, max_length=255
    )
    detail: Optional[str] = None
    due_date: Optional[date] = None
    status: Annotated[Optional[MilestoneStatus], NoNull] = None
    position: Annotated[Optional[int], NoNull] = None


class MilestoneOut(ORMModel):
    id: int
    project_id: int
    title: str
    detail: Optional[str]
    due_date: Optional[date]
    status: str
    completed_at: Optional[datetime]
    position: int
    created_at: Optional[datetime]
    task_total: int = 0
    task_done: int = 0


# --- Tasks ------------------------------------------------------------------

class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    project_id: Optional[int] = None
    milestone_id: Optional[int] = None
    parent_task_id: Optional[int] = None
    notes: Optional[str] = None
    status: TaskStatus = "todo"
    priority: Priority = "medium"
    due_date: Optional[date] = None
    estimate_hours: Optional[float] = Field(default=None, ge=0)
    blocked_reason: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Annotated[Optional[str], NoNull] = Field(
        default=None, min_length=1, max_length=255
    )
    project_id: Optional[int] = None
    milestone_id: Optional[int] = None
    parent_task_id: Optional[int] = None
    notes: Optional[str] = None
    status: Annotated[Optional[TaskStatus], NoNull] = None
    priority: Annotated[Optional[Priority], NoNull] = None
    due_date: Optional[date] = None
    estimate_hours: Optional[float] = Field(default=None, ge=0)
    blocked_reason: Optional[str] = None


class TaskOut(ORMModel):
    id: int
    project_id: Optional[int]
    milestone_id: Optional[int]
    parent_task_id: Optional[int]
    title: str
    notes: Optional[str]
    status: str
    priority: str
    due_date: Optional[date]
    estimate_hours: Optional[float]
    blocked_reason: Optional[str]
    completed_at: Optional[datetime]
    created_at: Optional[datetime]
    source: str = "human"
    project_name: Optional[str] = None
    hours_logged: float = 0.0
    subtask_total: int = 0
    subtask_done: int = 0


# --- Time logs --------------------------------------------------------------

class TimeLogCreate(BaseModel):
    work_date: date
    hours: float = Field(gt=0, le=24)
    category: TimeCategory = "build"
    project_id: Optional[int] = None
    task_id: Optional[int] = None
    note: Optional[str] = None


class TimeLogUpdate(BaseModel):
    work_date: Annotated[Optional[date], NoNull] = None
    hours: Annotated[Optional[float], NoNull] = Field(default=None, gt=0, le=24)
    category: Annotated[Optional[TimeCategory], NoNull] = None
    project_id: Optional[int] = None
    task_id: Optional[int] = None
    note: Optional[str] = None


class TimeLogOut(ORMModel):
    id: int
    project_id: Optional[int]
    task_id: Optional[int]
    work_date: date
    hours: float
    category: str
    note: Optional[str]
    created_at: Optional[datetime]
    source: str = "human"
    project_name: Optional[str] = None
    task_title: Optional[str] = None


# --- Notes / links / attachments -------------------------------------------

class NoteCreate(BaseModel):
    body: str = Field(min_length=1)
    title: Optional[str] = Field(default=None, max_length=255)
    project_id: Optional[int] = None
    kind: NoteKind = "note"
    pinned: bool = False


class NoteUpdate(BaseModel):
    body: Annotated[Optional[str], NoNull] = Field(default=None, min_length=1)
    title: Optional[str] = Field(default=None, max_length=255)
    project_id: Optional[int] = None
    kind: Annotated[Optional[NoteKind], NoNull] = None
    pinned: Annotated[Optional[bool], NoNull] = None


class NoteOut(ORMModel):
    id: int
    project_id: Optional[int]
    title: Optional[str]
    body: str
    kind: str
    pinned: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    source: str = "human"
    project_name: Optional[str] = None


class LinkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1)
    project_id: Optional[int] = None
    kind: LinkKind = "other"
    note: Optional[str] = None


class LinkUpdate(BaseModel):
    title: Annotated[Optional[str], NoNull] = Field(
        default=None, min_length=1, max_length=255
    )
    url: Annotated[Optional[str], NoNull] = Field(default=None, min_length=1)
    project_id: Optional[int] = None
    kind: Annotated[Optional[LinkKind], NoNull] = None
    note: Optional[str] = None


class LinkOut(ORMModel):
    id: int
    project_id: Optional[int]
    title: str
    url: str
    kind: str
    note: Optional[str]
    created_at: Optional[datetime]
    project_name: Optional[str] = None


ActivityKind = Literal["commit", "pull_request", "issue"]


class ActivityEventOut(ORMModel):
    id: int
    provider: str
    external_id: str
    kind: str
    repo: Optional[str]
    actor: Optional[str]
    title: str
    url: Optional[str]
    occurred_at: datetime
    project_id: Optional[int]
    task_id: Optional[int]
    # repo | convention | manual -- how the link was arrived at.
    linked_by: Optional[str]
    project_name: Optional[str] = None
    task_title: Optional[str] = None


class SyncResult(BaseModel):
    repo: str
    fetched: int
    added: int
    skipped: int
    linked_to_task: int
    since: Optional[str] = None


class AttachmentOut(ORMModel):
    id: int
    project_id: Optional[int]
    filename: str
    content_type: Optional[str]
    size_bytes: int
    note: Optional[str]
    created_at: Optional[datetime]
    project_name: Optional[str] = None
