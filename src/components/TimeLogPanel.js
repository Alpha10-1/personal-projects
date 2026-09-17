"use client";

import { useCallback, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate, formatHours, todayIso } from "@/lib/format";
import { TIME_CATEGORIES, labelFor } from "@/lib/constants";
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  Select,
} from "@/components/ui";

/**
 * Log hours and see them back.
 *
 * Used both on a project (scoped to that project) and on the standalone Time
 * page (everything). The only difference is whether a project picker shows.
 */
export default function TimeLogPanel({
  projectId = null,
  projects = [],
  tasks = [],
  onChanged,
  title = "Time logged",
  days = 60,
}) {
  const [form, setForm] = useState({
    work_date: todayIso(),
    hours: "",
    category: "build",
    project_id: projectId ?? "",
    task_id: "",
    note: "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);

  const logs = useAsync(
    useCallback(
      () =>
        api.get("/time-logs", {
          project_id: projectId ?? "",
          last_days: days,
        }),
      [projectId, days],
    ),
    [projectId, days],
  );

  const rows = logs.data || [];
  const total = useMemo(
    () => rows.reduce((sum, row) => sum + Number(row.hours || 0), 0),
    [rows],
  );

  // Only offer tasks that belong to the project the entry is being logged
  // against -- the API would reassign the project otherwise, silently.
  const taskOptions = useMemo(() => {
    const target = form.project_id === "" ? null : Number(form.project_id);
    return tasks
      .filter((task) => (target === null ? true : task.project_id === target))
      .map((task) => ({ value: task.id, label: task.title }));
  }, [tasks, form.project_id]);

  const set = (field) => (event) =>
    setForm((f) => ({ ...f, [field]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post("/time-logs", {
        work_date: form.work_date,
        hours: Number(form.hours),
        category: form.category,
        project_id: form.project_id === "" ? null : Number(form.project_id),
        task_id: form.task_id === "" ? null : Number(form.task_id),
        note: form.note.trim() || null,
      });
      setForm((f) => ({ ...f, hours: "", note: "", task_id: "" }));
      logs.reload({ quiet: true });
      onChanged?.();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (log) => {
    setError(null);
    try {
      await api.del(`/time-logs/${log.id}`);
      logs.reload({ quiet: true });
      onChanged?.();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <Card>
      <CardHeader
        title={title}
        subtitle={`${formatHours(total)} over the last ${days} days`}
        action={
          <Button
            size="sm"
            variant={showForm ? "secondary" : "primary"}
            onClick={() => setShowForm((open) => !open)}
          >
            <Plus size={13} /> {showForm ? "Hide" : "Log time"}
          </Button>
        }
      />

      {showForm ? (
        <form onSubmit={submit} className="space-y-3 border-b bg-[var(--surface-2)]/50 p-3">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Date">
              <input type="date" required value={form.work_date} onChange={set("work_date")} />
            </Field>
            <Field label="Hours">
              <input
                type="number"
                required
                min="0.25"
                max="24"
                step="0.25"
                value={form.hours}
                onChange={set("hours")}
                placeholder="1.5"
              />
            </Field>
            <Field label="Category">
              <Select value={form.category} onChange={set("category")} options={TIME_CATEGORIES} />
            </Field>
            {projectId === null ? (
              <Field label="Project">
                <Select
                  includeBlank
                  blankLabel="No project"
                  value={form.project_id}
                  onChange={(event) =>
                    setForm((f) => ({ ...f, project_id: event.target.value, task_id: "" }))
                  }
                  options={projects.map((p) => ({ value: p.id, label: p.name }))}
                />
              </Field>
            ) : (
              <Field label="Task">
                <Select
                  includeBlank
                  blankLabel="Project as a whole"
                  value={form.task_id}
                  onChange={set("task_id")}
                  options={taskOptions}
                />
              </Field>
            )}
          </div>
          <Field label="What you did">
            <input
              type="text"
              value={form.note}
              onChange={set("note")}
              placeholder="e.g. cleaned the shift dataset, wrote the join logic"
            />
          </Field>
          <div className="flex justify-end">
            <Button type="submit" variant="primary" busy={saving}>
              Log it
            </Button>
          </div>
        </form>
      ) : (
        <ErrorNote error={error} onDismiss={() => setError(null)} />
      )}

      {rows.length ? (
        <ul className="divide-y">
          {rows.map((log) => (
            <li key={log.id} className="flex items-start gap-3 px-3 py-2.5">
              <span className="w-20 shrink-0 text-xs text-[var(--text-muted)]">
                {formatDate(log.work_date, { withYear: false })}
              </span>
              <span className="w-12 shrink-0 text-sm font-medium tabular-nums">
                {formatHours(log.hours)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">
                  {log.note || labelFor(TIME_CATEGORIES, log.category)}
                </p>
                <p className="truncate text-[11px] text-[var(--text-muted)]">
                  {labelFor(TIME_CATEGORIES, log.category)}
                  {log.project_name ? ` · ${log.project_name}` : ""}
                  {log.task_title ? ` · ${log.task_title}` : ""}
                </p>
              </div>
              <button
                type="button"
                aria-label="Delete time entry"
                onClick={() => remove(log)}
                className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--critical)]"
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          title="No time logged yet"
          description="Logging even roughly makes the weekly split worth looking at."
        />
      )}
    </Card>
  );
}
