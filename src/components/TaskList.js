"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, Circle, Clock, Pencil, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { formatHours, relativeDue } from "@/lib/format";
import { PRIORITY_TONE, TASK_STATUSES } from "@/lib/constants";
import { Badge, EmptyState, Select } from "@/components/ui";

/** One task row: a toggle, the title, and the handful of facts worth seeing
 *  without opening anything. */
function TaskRow({ task, onChanged, onEdit, onDelete, showProject }) {
  const [busy, setBusy] = useState(false);
  const due = relativeDue(task.due_date);
  const done = task.status === "done";

  const patch = async (payload) => {
    setBusy(true);
    try {
      onChanged?.(await api.patch(`/tasks/${task.id}`, payload));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex items-start gap-2.5 border-b px-3 py-2.5 last:border-b-0">
      <button
        type="button"
        disabled={busy}
        onClick={() => patch({ status: done ? "todo" : "done" })}
        aria-label={done ? `Reopen ${task.title}` : `Complete ${task.title}`}
        className="mt-0.5 shrink-0 rounded-full text-[var(--text-muted)] hover:text-[var(--good)] disabled:opacity-40"
      >
        {done ? (
          <Check size={16} className="text-[var(--good)]" />
        ) : (
          <Circle size={16} />
        )}
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span
            className={`text-sm ${done ? "text-[var(--text-muted)] line-through" : ""}`}
          >
            {task.title}
          </span>
          {task.priority === "high" && !done ? (
            <Badge tone={PRIORITY_TONE.high}>High</Badge>
          ) : null}
          {task.status === "blocked" ? <Badge tone="critical">Blocked</Badge> : null}
          {task.subtask_total > 0 ? (
            <span className="text-[11px] text-[var(--text-muted)]">
              {task.subtask_done}/{task.subtask_total} subtasks
            </span>
          ) : null}
        </div>

        <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-[var(--text-muted)]">
          {showProject && task.project_id ? (
            <Link
              href={`/projects/${task.project_id}`}
              className="text-[var(--accent)] hover:underline"
            >
              {task.project_name}
            </Link>
          ) : null}
          {showProject && !task.project_id ? <span>No project</span> : null}
          {due && !done ? (
            <span
              className={
                due.tone === "critical"
                  ? "font-medium text-[var(--critical)]"
                  : due.tone === "warning"
                    ? "font-medium text-[var(--text-secondary)]"
                    : ""
              }
            >
              {due.text}
            </span>
          ) : null}
          {task.hours_logged > 0 ? (
            <span className="inline-flex items-center gap-1">
              <Clock size={11} />
              {formatHours(task.hours_logged)}
              {task.estimate_hours ? ` / ${formatHours(task.estimate_hours)}` : ""}
            </span>
          ) : null}
          {task.blocked_reason ? <span>· {task.blocked_reason}</span> : null}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Select
          aria-label={`Status of ${task.title}`}
          value={task.status}
          disabled={busy}
          onChange={(event) => patch({ status: event.target.value })}
          options={TASK_STATUSES}
          className="!w-auto !py-1 !text-xs"
        />
        {onEdit ? (
          <button
            type="button"
            onClick={() => onEdit(task)}
            aria-label={`Edit ${task.title}`}
            className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
          >
            <Pencil size={13} />
          </button>
        ) : null}
        {onDelete ? (
          <button
            type="button"
            onClick={() => onDelete(task)}
            aria-label={`Delete ${task.title}`}
            className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--critical)]"
          >
            <Trash2 size={13} />
          </button>
        ) : null}
      </div>
    </li>
  );
}

export default function TaskList({
  tasks,
  onChanged,
  onEdit,
  onDelete,
  showProject = true,
  emptyTitle = "Nothing here",
  emptyDescription,
}) {
  if (!tasks?.length) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }
  return (
    <ul>
      {tasks.map((task) => (
        <TaskRow
          key={task.id}
          task={task}
          onChanged={onChanged}
          onEdit={onEdit}
          onDelete={onDelete}
          showProject={showProject}
        />
      ))}
    </ul>
  );
}

export { TaskRow };
