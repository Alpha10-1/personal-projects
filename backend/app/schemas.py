"""Request/response shapes.

Update schemas use all-optional fields plus `exclude_unset` at the call site,
so a PATCH that omits a field leaves it alone and one that sends null clears it.
"""

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    progress_override: Optional[int] = Field(default=None, ge=0, le=100)
    retro: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    summary: Optional[str] = None
    status: Optional[ProjectStatus] = None
    category: Optional[ProjectCategory] = None
    priority: Optional[Priority] = None
    start_date: Optional[date] = None
    target_date: Optional[date] = None
    objective: Optional[str] = None
    definition_of_done: Optional[str] = None
    stakeholder: Optional[str] = None
    tech_stack: Optional[str] = None
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
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    detail: Optional[str] = None
    due_date: Optional[date] = None
    status: Optional[MilestoneStatus] = None
    position: Optional[int] = None


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
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    project_id: Optional[int] = None
    milestone_id: Optional[int] = None
    parent_task_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[Priority] = None
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
    work_date: Optional[date] = None
    hours: Optional[float] = Field(default=None, gt=0, le=24)
    category: Optional[TimeCategory] = None
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
    body: Optional[str] = Field(default=None, min_length=1)
    title: Optional[str] = Field(default=None, max_length=255)
    project_id: Optional[int] = None
    kind: Optional[NoteKind] = None
    pinned: Optional[bool] = None


class NoteOut(ORMModel):
    id: int
    project_id: Optional[int]
    title: Optional[str]
    body: str
    kind: str
    pinned: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    project_name: Optional[str] = None


class LinkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1)
    project_id: Optional[int] = None
    kind: LinkKind = "other"
    note: Optional[str] = None


class LinkUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    url: Optional[str] = Field(default=None, min_length=1)
    project_id: Optional[int] = None
    kind: Optional[LinkKind] = None
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


class AttachmentOut(ORMModel):
    id: int
    project_id: Optional[int]
    filename: str
    content_type: Optional[str]
    size_bytes: int
    note: Optional[str]
    created_at: Optional[datetime]
    project_name: Optional[str] = None
