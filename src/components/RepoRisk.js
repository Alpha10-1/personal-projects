"use client";

/**
 * Where a mistake in this repository would survive.
 *
 * Two deterministic reads, side by side because they answer the same
 * question from different ends. Churn says where the work is. Hygiene says
 * what has been committed that should not have been. Neither costs
 * anything and neither involves a model, so this loads with the tab rather
 * than waiting for a button.
 *
 * The distinction the whole panel turns on is **untested** versus
 * **unknown**. A file nothing tests is a finding. A file whose coverage
 * could not be determined -- no checkout, a language the outline does not
 * read, names too common to search -- is not, and showing it as one would
 * be the more damaging mistake. They are coloured and worded differently
 * and never counted together.
 */

import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  FileWarning,
  FlameKindling,
  HelpCircle,
  KeyRound,
  Trash2,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { Badge, Card, CardHeader, ErrorNote, Spinner } from "@/components/ui";

const WINDOWS = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 365, label: "a year" },
];

function Coverage({ file }) {
  if (file.tested === true) {
    return (
      <span
        className="flex items-center gap-1 text-[var(--good)]"
        title={file.test_files.join(", ")}
      >
        <CheckCircle2 size={12} /> tested
      </span>
    );
  }
  if (file.tested === false) {
    return (
      <span className="flex items-center gap-1 text-[var(--warning)]">
        <AlertTriangle size={12} /> no test
      </span>
    );
  }
  return (
    <span
      className="flex items-center gap-1 text-[var(--text-muted)]"
      title={file.why_unknown || "could not be determined"}
    >
      <HelpCircle size={12} /> unknown
    </span>
  );
}

function Churn({ project }) {
  const [days, setDays] = useState(90);
  const churn = useAsync(
    () => api.get(`/projects/${project.id}/churn`, { window_days: days }),
    [project.id, days],
  );

  if (churn.loading && !churn.data) return <Spinner label="Counting what changes" />;
  if (churn.error) return <ErrorNote error={churn.error} />;

  const data = churn.data;
  if (!data?.files?.length) {
    return (
      <p className="text-xs text-[var(--text-muted)]">
        No commits with file detail in this window. Run a deep sync from the
        Repo tab and this fills in.
      </p>
    );
  }

  const busiest = Math.max(...data.files.map((f) => f.commits));

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-[var(--text-muted)]">
          {data.files_touched} file(s) changed
          {data.at_risk.length ? (
            <>
              {" · "}
              <strong className="text-[var(--warning)]">
                {data.at_risk.length} busy and untested
              </strong>
            </>
          ) : null}
        </p>
        <div className="flex items-center gap-1">
          {WINDOWS.map((option) => (
            <button
              key={option.days}
              type="button"
              onClick={() => setDays(option.days)}
              className={`rounded px-1.5 py-0.5 text-[10px] ${
                days === option.days
                  ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <ul className="space-y-1">
        {data.files.map((file) => (
          <li key={file.path} className="space-y-0.5">
            <div className="flex items-center justify-between gap-2 text-xs">
              <code className="truncate text-[var(--text-secondary)]">{file.path}</code>
              <span className="flex shrink-0 items-center gap-2 text-[10px]">
                <span className="text-[var(--text-muted)]">
                  {file.commits}× · {file.lines_changed.toLocaleString()} lines
                </span>
                <Coverage file={file} />
              </span>
            </div>
            <div className="h-0.5 rounded bg-[var(--surface-2)]">
              <div
                className={`h-full rounded ${
                  file.at_risk ? "bg-[var(--warning)]" : "bg-[var(--accent)]"
                }`}
                style={{ width: `${Math.round((file.commits / busiest) * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>

      {!data.code_was_read ? (
        <p className="text-[11px] text-[var(--warning)]">
          This project has no local folder, so nothing could be checked for
          tests — the coverage column is unknown, not empty.
        </p>
      ) : null}

      {data.commits_without_detail ? (
        <p className="text-[11px] text-[var(--text-muted)]">
          {data.commits_without_detail} of {data.commits_scanned} commits have
          no file detail and are not in these numbers.
        </p>
      ) : null}

      <details className="text-[11px] text-[var(--text-muted)]">
        <summary className="cursor-pointer">What this cannot tell you</summary>
        <ul className="ml-4 mt-1 list-disc space-y-0.5">
          {data.limits.map((limit) => (
            <li key={limit}>{limit}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Hygiene({ project }) {
  const scan = useAsync(() => api.get(`/projects/${project.id}/hygiene`), [project.id]);

  if (scan.loading && !scan.data) return <Spinner label="Checking what was committed" />;
  if (scan.error) return <ErrorNote error={scan.error} />;

  const data = scan.data;
  if (!data) return null;

  const filled = (data.templates || []).filter((t) => t.filled_keys?.length);

  if (data.clean && !filled.length) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-[var(--good)]">
        <CheckCircle2 size={13} />
        No credentials or build output in {data.commits_scanned} commits.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {data.secrets.map((entry) => (
        <div key={entry.path} className="space-y-0.5">
          <p className="flex flex-wrap items-center gap-1.5 text-xs">
            <KeyRound size={13} className="text-[var(--critical)]" />
            <code className="font-medium">{entry.path}</code>
            <Badge tone="critical">committed</Badge>
            {entry.still_present ? <Badge tone="warning">still on disk</Badge> : null}
          </p>
          <p className="text-[11px] text-[var(--text-muted)]">
            In {entry.commits} commit(s), first {entry.first_sha}. Deleting it
            now does not remove it from history — anything that was pushed
            should be treated as exposed and rotated.
          </p>
        </div>
      ))}

      {filled.map((entry) => (
        <div key={entry.path} className="space-y-0.5">
          <p className="flex flex-wrap items-center gap-1.5 text-xs">
            <FileWarning size={13} className="text-[var(--warning)]" />
            <code className="font-medium">{entry.path}</code>
            <Badge tone="warning">real values</Badge>
          </p>
          <p className="text-[11px] text-[var(--text-muted)]">
            A template is meant to be committed, which is why a real value
            left in one gets published without a second look:{" "}
            {entry.filled_keys.join(", ")}
          </p>
        </div>
      ))}

      {data.noise.length ? (
        <details>
          <summary className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-secondary)]">
            <Trash2 size={13} className="text-[var(--text-muted)]" />
            Build output committed ({data.noise.length} path
            {data.noise.length === 1 ? "" : "s"})
          </summary>
          <ul className="ml-5 mt-1 space-y-0.5 text-[11px] text-[var(--text-muted)]">
            {data.noise.slice(0, 10).map((entry) => (
              <li key={entry.path}>
                <code>{entry.path}</code> — {entry.commits} commits
              </li>
            ))}
          </ul>
          <p className="ml-5 mt-1 text-[11px] text-[var(--text-muted)]">
            A missing .gitignore entry rather than anything wrong with the code.
          </p>
        </details>
      ) : null}

      {data.commits_without_detail ? (
        <p className="text-[11px] text-[var(--text-muted)]">
          {data.commits_without_detail} of {data.commits_scanned} commits were
          not checked — their file lists have not been fetched.
        </p>
      ) : null}
    </div>
  );
}

export default function RepoRisk({ project }) {
  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title="What changes, and what would catch a mistake"
          subtitle="Counted from commits already stored, and checked against the tests in the checkout. No model, nothing spent."
        />
        <div className="px-4 pb-4">
          <Churn project={project} />
        </div>
      </Card>

      <Card>
        <CardHeader
          title="What has been committed that should not have been"
          subtitle="Read from history, not from the current checkout — because deleting a key today leaves it in every clone."
        />
        <div className="px-4 pb-4">
          <Hygiene project={project} />
        </div>
      </Card>
    </div>
  );
}
