"use client";

import { useCallback, useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync, useDebounced } from "@/lib/hooks";
import { PRIORITIES, TASK_STATUSES } from "@/lib/constants";
import { Button, Card, CardHeader, ErrorNote, Select, Spinner } from "@/components/ui";
import TaskList from "@/components/TaskList";
import TaskForm from "@/components/TaskForm";

const VIEWS = [
  { key: "open", label: "Open" },
  { key: "overdue", label: "Overdue" },
  { key: "week", label: "Next 7 days" },
  { key: "done", label: "Done" },
  { key: "all", label: "All" },
];

export default function TasksPage() {
  const [view, setView] = useState("open");
  const [projectId, setProjectId] = useState("");
  const [priority, setPriority] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState(null);

  const q = useDebounced(search, 300);

  const params = useMemo(() => {
    const base = { project_id: projectId, priority, q };
    if (view === "open") return { ...base, open_only: "true" };
    if (view === "overdue") return { ...base, overdue: "true" };
    if (view === "week") return { ...base, open_only: "true", due_within_days: 7 };
    if (view === "done") return { ...base, status: "done" };
    return { ...base, status };
  }, [view, projectId, priority, status, q]);

  const tasks = useAsync(useCallback(() => api.get("/tasks", params), [params]), [params]);
  const projects = useAsync(useCallback(() => api.get("/projects"), []), []);

  const rows = tasks.data || [];
  const refresh = () => tasks.reload({ quiet: true });

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Tasks</h1>
          <p className="text-sm text-[var(--text-muted)]">
            {rows.length} {rows.length === 1 ? "task" : "tasks"}
          </p>
        </div>
        <Button variant="primary" onClick={() => setForm({})}>
          <Plus size={14} /> New task
        </Button>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-lg border bg-[var(--surface-1)] p-0.5">
          {VIEWS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setView(item.key)}
              aria-pressed={view === item.key}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                view === item.key
                  ? "bg-[var(--accent-soft)] font-medium text-[var(--accent)]"
                  : "text-[var(--text-secondary)] hover:bg-[var(--surface-2)]"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="relative min-w-44 flex-1">
          <Search
            size={14}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
          />
          <input
            type="text"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search tasks"
            aria-label="Search tasks"
            className="!pl-8"
          />
        </div>

        <Select
          includeBlank
          blankLabel="Any project"
          aria-label="Filter by project"
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
          options={(projects.data || []).map((p) => ({ value: p.id, label: p.name }))}
          className="!w-auto"
        />
        <Select
          includeBlank
          blankLabel="Any priority"
          aria-label="Filter by priority"
          value={priority}
          onChange={(event) => setPriority(event.target.value)}
          options={PRIORITIES}
          className="!w-auto"
        />
        {view === "all" ? (
          <Select
            includeBlank
            blankLabel="Any status"
            aria-label="Filter by status"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            options={TASK_STATUSES}
            className="!w-auto"
          />
        ) : null}
      </div>

      <ErrorNote error={tasks.error} onDismiss={() => tasks.reload()} />

      {tasks.loading && !tasks.data ? <Spinner /> : null}

      {tasks.data ? (
        <Card>
          <CardHeader title={VIEWS.find((v) => v.key === view)?.label} />
          <TaskList
            tasks={rows}
            onChanged={refresh}
            onEdit={(task) => setForm({ task })}
            onDelete={async (task) => {
              if (!window.confirm(`Delete "${task.title}"?`)) return;
              await api.del(`/tasks/${task.id}`);
              refresh();
            }}
            emptyTitle="Nothing to show"
            emptyDescription="Adjust the filters, or add a task."
          />
        </Card>
      ) : null}

      {form ? (
        <TaskForm
          open
          task={form.task || null}
          projects={projects.data || []}
          onClose={() => setForm(null)}
          onSaved={refresh}
        />
      ) : null}
    </div>
  );
}
