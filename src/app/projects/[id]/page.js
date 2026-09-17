"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Copy,
  Flag,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate, formatHours, relativeDue } from "@/lib/format";
import {
  PRIORITIES,
  PROJECT_CATEGORIES,
  PROJECT_STATUSES,
  PROJECT_STATUS_TONE,
  labelFor,
} from "@/lib/constants";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  ProgressBar,
  Spinner,
} from "@/components/ui";
import TaskList from "@/components/TaskList";
import TaskForm from "@/components/TaskForm";
import ProjectForm from "@/components/ProjectForm";
import LibraryPanels from "@/components/LibraryPanels";
import RepoInsights from "@/components/RepoInsights";
import TimeLogPanel from "@/components/TimeLogPanel";

const TABS = [
  { key: "tasks", label: "Tasks" },
  { key: "milestones", label: "Milestones" },
  { key: "time", label: "Time" },
  { key: "library", label: "Notes & files" },
  { key: "repo", label: "Repo" },
  { key: "brief", label: "Brief" },
];

function MilestoneForm({ open, onClose, projectId, milestone, onSaved }) {
  const [form, setForm] = useState({
    title: milestone?.title ?? "",
    detail: milestone?.detail ?? "",
    due_date: milestone?.due_date ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const payload = {
        title: form.title.trim(),
        detail: form.detail.trim() || null,
        due_date: form.due_date || null,
      };
      if (milestone) await api.patch(`/milestones/${milestone.id}`, payload);
      else await api.post(`/projects/${projectId}/milestones`, payload);
      onSaved?.();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={milestone ? "Edit milestone" : "New milestone"}>
      <form onSubmit={submit} className="space-y-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />
        <Field label="Title">
          <input
            type="text"
            required
            autoFocus
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            placeholder="e.g. Model handed to the business for review"
          />
        </Field>
        <Field label="Detail">
          <textarea
            rows={2}
            value={form.detail}
            onChange={(e) => setForm((f) => ({ ...f, detail: e.target.value }))}
          />
        </Field>
        <Field label="Due date">
          <input
            type="date"
            value={form.due_date}
            onChange={(e) => setForm((f) => ({ ...f, due_date: e.target.value }))}
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={saving}>
            Save
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function BriefField({ label, value }) {
  return (
    <div>
      <p className="text-xs font-medium text-[var(--text-secondary)]">{label}</p>
      <p className="mt-0.5 whitespace-pre-wrap text-sm">
        {value || <span className="text-[var(--text-muted)]">Not set</span>}
      </p>
    </div>
  );
}

export default function ProjectDetailPage() {
  const { id } = useParams();
  const router = useRouter();
  const projectId = Number(id);

  const [tab, setTab] = useState("tasks");
  const [editOpen, setEditOpen] = useState(false);
  const [taskForm, setTaskForm] = useState(null); // null | {task}
  const [milestoneForm, setMilestoneForm] = useState(null);
  const [actionError, setActionError] = useState(null);

  const project = useAsync(
    useCallback(() => api.get(`/projects/${projectId}`), [projectId]),
    [projectId],
  );
  const tasks = useAsync(
    useCallback(() => api.get("/tasks", { project_id: projectId }), [projectId]),
    [projectId],
  );
  const milestones = useAsync(
    useCallback(() => api.get(`/projects/${projectId}/milestones`), [projectId]),
    [projectId],
  );
  const allProjects = useAsync(useCallback(() => api.get("/projects"), []), []);

  const refreshAll = () => {
    project.reload({ quiet: true });
    tasks.reload({ quiet: true });
    milestones.reload({ quiet: true });
  };

  const p = project.data;

  const runAction = async (fn) => {
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(err);
    }
  };

  const remove = () =>
    runAction(async () => {
      if (
        !window.confirm(
          `Delete "${p.name}" and everything attached to it? Archiving keeps it recoverable; this does not.`,
        )
      )
        return;
      await api.del(`/projects/${projectId}`);
      router.push("/projects");
    });

  if (project.loading && !p) return <Spinner label="Loading project" />;
  if (project.error) return <ErrorNote error={project.error} />;
  if (!p) return null;

  const taskList = tasks.data || [];
  const openTasks = taskList.filter((t) => t.status !== "done");
  const doneTasks = taskList.filter((t) => t.status === "done");
  const milestoneList = milestones.data || [];

  return (
    <div className="space-y-5">
      <Link
        href="/projects"
        className="inline-flex items-center gap-1 text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
      >
        <ArrowLeft size={13} /> All projects
      </Link>

      <ErrorNote error={actionError} onDismiss={() => setActionError(null)} />

      <Card className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold">{p.name}</h1>
              <Badge tone={PROJECT_STATUS_TONE[p.status] || "neutral"}>
                {labelFor(PROJECT_STATUSES, p.status)}
              </Badge>
              <Badge>{labelFor(PROJECT_CATEGORIES, p.category)}</Badge>
              <Badge>{labelFor(PRIORITIES, p.priority)} priority</Badge>
            </div>
            {p.summary ? (
              <p className="mt-1.5 max-w-2xl text-sm text-[var(--text-secondary)]">
                {p.summary}
              </p>
            ) : null}
          </div>

          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setEditOpen(true)}>
              <Pencil size={13} /> Edit
            </Button>
            <Button
              onClick={() =>
                runAction(async () => {
                  const clone = await api.post(`/projects/${projectId}/duplicate`);
                  router.push(`/projects/${clone.id}`);
                })
              }
            >
              <Copy size={13} /> Duplicate
            </Button>
            {p.archived_at ? (
              <Button
                onClick={() =>
                  runAction(async () => {
                    await api.post(`/projects/${projectId}/unarchive`);
                    refreshAll();
                  })
                }
              >
                <ArchiveRestore size={13} /> Unarchive
              </Button>
            ) : (
              <Button
                onClick={() =>
                  runAction(async () => {
                    await api.post(`/projects/${projectId}/archive`);
                    refreshAll();
                  })
                }
              >
                <Archive size={13} /> Archive
              </Button>
            )}
            <Button variant="danger" onClick={remove}>
              <Trash2 size={13} /> Delete
            </Button>
          </div>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="sm:col-span-2">
            <div className="mb-1 flex items-center justify-between text-xs text-[var(--text-muted)]">
              <span>
                Progress
                {p.progress_override !== null ? " (manually set)" : ""}
              </span>
              <span className="tabular-nums">{p.progress}%</span>
            </div>
            <ProgressBar value={p.progress} tone={p.progress >= 100 ? "good" : "accent"} />
          </div>
          <div className="text-xs">
            <p className="text-[var(--text-muted)]">Tasks</p>
            <p className="mt-0.5 text-sm">
              {p.task_done} of {p.task_total} done
              {p.open_overdue > 0 ? (
                <span className="ml-1.5 font-medium text-[var(--critical)]">
                  · {p.open_overdue} overdue
                </span>
              ) : null}
            </p>
          </div>
          <div className="text-xs">
            <p className="text-[var(--text-muted)]">Time logged</p>
            <p className="mt-0.5 text-sm">{formatHours(p.hours_logged)}</p>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-[var(--text-muted)]">
          <span>Start {formatDate(p.start_date)}</span>
          <span>Target {formatDate(p.target_date)}</span>
          {p.next_due ? <span>Next task due {formatDate(p.next_due)}</span> : null}
          {p.stakeholder ? <span>For {p.stakeholder}</span> : null}
          {p.tech_stack ? <span>{p.tech_stack}</span> : null}
        </div>
      </Card>

      <div className="flex flex-wrap gap-1 border-b">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            aria-current={tab === item.key ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${
              tab === item.key
                ? "border-[var(--accent)] font-medium text-[var(--accent)]"
                : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "tasks" ? (
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Open tasks"
              subtitle={`${openTasks.length} open`}
              action={
                <Button size="sm" variant="primary" onClick={() => setTaskForm({})}>
                  <Plus size={13} /> Add
                </Button>
              }
            />
            <TaskList
              tasks={openTasks}
              showProject={false}
              onChanged={refreshAll}
              onEdit={(task) => setTaskForm({ task })}
              onDelete={(task) =>
                runAction(async () => {
                  if (!window.confirm(`Delete "${task.title}"?`)) return;
                  await api.del(`/tasks/${task.id}`);
                  refreshAll();
                })
              }
              emptyTitle="No open tasks"
              emptyDescription="Break the project into the next few concrete steps."
            />
          </Card>

          {doneTasks.length ? (
            <Card>
              <CardHeader title="Done" subtitle={`${doneTasks.length} completed`} />
              <TaskList
                tasks={doneTasks}
                showProject={false}
                onChanged={refreshAll}
                onEdit={(task) => setTaskForm({ task })}
              />
            </Card>
          ) : null}
        </div>
      ) : null}

      {tab === "milestones" ? (
        <Card>
          <CardHeader
            title="Milestones"
            subtitle="The dated checkpoints you'd report upward."
            action={
              <Button size="sm" variant="primary" onClick={() => setMilestoneForm({})}>
                <Plus size={13} /> Add
              </Button>
            }
          />
          {milestoneList.length ? (
            <ul className="divide-y">
              {milestoneList.map((milestone) => {
                const due = relativeDue(milestone.due_date);
                const done = milestone.status === "done";
                return (
                  <li key={milestone.id} className="flex items-start gap-3 px-3 py-3">
                    <Flag
                      size={15}
                      className={`mt-0.5 shrink-0 ${done ? "text-[var(--good)]" : "text-[var(--text-muted)]"}`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`text-sm font-medium ${done ? "text-[var(--text-muted)] line-through" : ""}`}
                        >
                          {milestone.title}
                        </span>
                        {done ? <Badge tone="good">Done</Badge> : null}
                        {!done && due?.tone === "critical" ? (
                          <Badge tone="critical">{due.text}</Badge>
                        ) : null}
                      </div>
                      {milestone.detail ? (
                        <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
                          {milestone.detail}
                        </p>
                      ) : null}
                      <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">
                        {milestone.due_date ? formatDate(milestone.due_date) : "No date"}
                        {milestone.task_total > 0
                          ? ` · ${milestone.task_done}/${milestone.task_total} tasks`
                          : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        size="sm"
                        onClick={() =>
                          runAction(async () => {
                            await api.patch(`/milestones/${milestone.id}`, {
                              status: done ? "pending" : "done",
                            });
                            refreshAll();
                          })
                        }
                      >
                        {done ? "Reopen" : "Mark done"}
                      </Button>
                      <button
                        type="button"
                        aria-label={`Edit ${milestone.title}`}
                        onClick={() => setMilestoneForm({ milestone })}
                        className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete ${milestone.title}`}
                        onClick={() =>
                          runAction(async () => {
                            if (!window.confirm(`Delete milestone "${milestone.title}"?`))
                              return;
                            await api.del(`/milestones/${milestone.id}`);
                            refreshAll();
                          })
                        }
                        className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--critical)]"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState
              title="No milestones yet"
              description="Add the two or three checkpoints that mark real progress."
            />
          )}
        </Card>
      ) : null}

      {tab === "time" ? (
        <TimeLogPanel
          projectId={projectId}
          tasks={taskList}
          projects={allProjects.data || []}
          onChanged={refreshAll}
        />
      ) : null}

      {tab === "library" ? (
        <LibraryPanels projectId={projectId} projects={allProjects.data || []} />
      ) : null}

      {tab === "repo" ? <RepoInsights project={p} /> : null}

      {tab === "brief" ? (
        <Card className="space-y-4 p-4">
          <BriefField label="Objective" value={p.objective} />
          <BriefField label="Definition of done" value={p.definition_of_done} />
          <BriefField label="Stakeholder" value={p.stakeholder} />
          <BriefField label="Tools & stack" value={p.tech_stack} />
          <BriefField label="Retro / lessons learned" value={p.retro} />
          <Button onClick={() => setEditOpen(true)}>
            <Pencil size={13} /> Edit brief
          </Button>
        </Card>
      ) : null}

      {editOpen ? (
        <ProjectForm
          open
          project={p}
          onClose={() => setEditOpen(false)}
          onSaved={() => project.reload({ quiet: true })}
        />
      ) : null}

      {taskForm ? (
        <TaskForm
          open
          task={taskForm.task || null}
          projects={allProjects.data || []}
          milestones={milestoneList}
          defaults={taskForm.task ? {} : { project_id: projectId }}
          onClose={() => setTaskForm(null)}
          onSaved={refreshAll}
        />
      ) : null}

      {milestoneForm ? (
        <MilestoneForm
          open
          projectId={projectId}
          milestone={milestoneForm.milestone || null}
          onClose={() => setMilestoneForm(null)}
          onSaved={refreshAll}
        />
      ) : null}
    </div>
  );
}
