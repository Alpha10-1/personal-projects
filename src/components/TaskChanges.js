"use client";

/**
 * What actually changed on disk for one task.
 *
 * The join the board never had. A commit message that mentions a task is a
 * claim; this is the files, with both sides of each change stored, so "what
 * did that task actually do" is answerable from the repository rather than
 * from what somebody typed afterwards.
 *
 * It is handed its rows rather than fetching them. Rendered inside a task
 * list, a component that fetched for itself would fire one request per row
 * for a list that is empty for most tasks; the caller reads the project's
 * changes once and groups them.
 *
 * It renders nothing when a task has none, which is the common case. An
 * empty panel on every task would read as "nothing happened" rather than
 * "nothing was recorded here", and those are different claims.
 */

import { FileCode } from "lucide-react";

import { formatDate } from "@/lib/format";
import { Badge } from "@/components/ui";

const TONE = {
  pending: "warning",
  approved: "success",
  rejected: "neutral",
  reverted: "critical",
};

/** Group a project's changes by the task they belong to. */
export function byTask(changes) {
  const out = new Map();
  for (const change of changes ?? []) {
    if (!change?.task_id) continue;
    const held = out.get(change.task_id);
    if (held) held.push(change);
    else out.set(change.task_id, [change]);
  }
  return out;
}

export default function TaskChanges({ changes, onOpen }) {
  const rows = Array.isArray(changes) ? changes : [];
  if (!rows.length) return null;

  const added = rows.reduce((total, row) => total + (row.lines?.added ?? 0), 0);
  const removed = rows.reduce((total, row) => total + (row.lines?.removed ?? 0), 0);

  return (
    <div className="mt-1.5 space-y-0.5 border-t border-[var(--border)] pt-1.5">
      <p className="flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
        <FileCode size={11} />
        <span className="font-medium">What changed for this</span>
        <span>
          {rows.length} file{rows.length === 1 ? "" : "s"}
          {added || removed ? (
            <>
              {" · "}
              <span className="text-[var(--good)]">+{added}</span>{" "}
              <span className="text-[var(--critical)]">−{removed}</span>
            </>
          ) : null}
        </span>
      </p>

      <ul className="space-y-0.5">
        {rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => onOpen?.(row)}
              disabled={!onOpen}
              className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-[11px] enabled:hover:bg-[var(--surface-2)] disabled:cursor-default"
            >
              <Badge tone={TONE[row.status] || "neutral"}>{row.status}</Badge>
              <code className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
                {row.path}
              </code>
              <span className="shrink-0 text-[10px] text-[var(--text-muted)]">
                {row.origin === "agent" ? "agent" : "you"} ·{" "}
                {formatDate(row.created_at)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
