"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { PRIORITIES, TASK_STATUSES } from "@/lib/constants";
import { Button, ErrorNote, Field, Modal, Select } from "@/components/ui";

const EMPTY = {
  title: "",
  project_id: "",
  milestone_id: "",
  status: "todo",
  priority: "medium",
  due_date: "",
  estimate_hours: "",
  notes: "",
  blocked_reason: "",
};

function toForm(task) {
  if (!task) return EMPTY;
  return {
    title: task.title ?? "",
    project_id: task.project_id ?? "",
    milestone_id: task.milestone_id ?? "",
    status: task.status ?? "todo",
    priority: task.priority ?? "medium",
    due_date: task.due_date ?? "",
    estimate_hours: task.estimate_hours ?? "",
    notes: task.notes ?? "",
    blocked_reason: task.blocked_reason ?? "",
  };
}

/** Empty strings from the form mean "not set", which the API expects as null. */
function nullable(value) {
  return value === "" || value === undefined ? null : value;
}

export default function TaskForm({
  open,
  onClose,
  onSaved,
  task = null,
  projects = [],
  milestones = [],
  defaults = {},
}) {
  const [form, setForm] = useState(() => ({ ...toForm(task), ...defaults }));
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const set = (field) => (event) =>
    setForm((f) => ({ ...f, [field]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const payload = {
        title: form.title.trim(),
        project_id: form.project_id === "" ? null : Number(form.project_id),
        milestone_id: form.milestone_id === "" ? null : Number(form.milestone_id),
        status: form.status,
        priority: form.priority,
        due_date: nullable(form.due_date),
        estimate_hours:
          form.estimate_hours === "" ? null : Number(form.estimate_hours),
        notes: nullable(form.notes?.trim()),
        blocked_reason:
          form.status === "blocked" ? nullable(form.blocked_reason?.trim()) : null,
      };
      const saved = task
        ? await api.patch(`/tasks/${task.id}`, payload)
        : await api.post("/tasks", payload);
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  // Milestones belong to one project, so only offer the ones that match the
  // project currently selected.
  const milestoneOptions = milestones
    .filter((m) => String(m.project_id) === String(form.project_id))
    .map((m) => ({ value: m.id, label: m.title }));

  return (
    <Modal open={open} onClose={onClose} title={task ? "Edit task" : "New task"}>
      <form onSubmit={submit} className="space-y-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />

        <Field label="Title">
          <input
            type="text"
            required
            autoFocus
            value={form.title}
            onChange={set("title")}
            placeholder="e.g. Baseline the retrieval eval set"
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Project">
            <Select
              includeBlank
              blankLabel="No project"
              value={form.project_id}
              onChange={(event) =>
                setForm((f) => ({
                  ...f,
                  project_id: event.target.value,
                  // A milestone from the old project would no longer be valid.
                  milestone_id: "",
                }))
              }
              options={projects.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Field>
          <Field label="Milestone">
            <Select
              includeBlank
              blankLabel={form.project_id ? "None" : "Pick a project first"}
              disabled={!form.project_id || milestoneOptions.length === 0}
              value={form.milestone_id}
              onChange={set("milestone_id")}
              options={milestoneOptions}
            />
          </Field>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Status">
            <Select value={form.status} onChange={set("status")} options={TASK_STATUSES} />
          </Field>
          <Field label="Priority">
            <Select value={form.priority} onChange={set("priority")} options={PRIORITIES} />
          </Field>
        </div>

        {form.status === "blocked" ? (
          <Field label="What's blocking it" hint="Kept only while the task is blocked.">
            <input
              type="text"
              value={form.blocked_reason}
              onChange={set("blocked_reason")}
              placeholder="e.g. waiting on data access"
            />
          </Field>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Due date">
            <input type="date" value={form.due_date} onChange={set("due_date")} />
          </Field>
          <Field label="Estimate (hours)">
            <input
              type="number"
              min="0"
              step="0.25"
              value={form.estimate_hours}
              onChange={set("estimate_hours")}
            />
          </Field>
        </div>

        <Field label="Notes">
          <textarea rows={3} value={form.notes} onChange={set("notes")} />
        </Field>

        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={saving}>
            {task ? "Save changes" : "Create task"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
