"use client";

/**
 * Asking the agent to make a change, and deciding what to do with the answer.
 *
 * The shape of this panel is the shape of the guarantee: a run produces a
 * *proposal*, and applying it is a separate press. Nothing on the disk
 * changes between starting a run and pressing Apply, and the copy says so
 * rather than leaving it to be inferred.
 *
 * "Apply automatically" exists because most changes are small and reviewing
 * a one-line edit is friction for its own sake. It is overridden whenever
 * the run touches a path the project marks as core -- and when that happens
 * the panel says which path did it, because a guard that fires silently
 * teaches you nothing.
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ExternalLink,
  FileCode,
  Loader2,
  Play,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAiStatus } from "@/lib/ai";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
} from "@/components/ui";
import DiffView, { countChanges } from "@/components/DiffView";

// A run takes minutes. Polling every two seconds is well inside what a
// local request costs and keeps the turn counter moving, which is the only
// sign of life there is while the model works.
const POLL_MS = 2000;

const STATUS_TONE = {
  running: "info",
  proposed: "warning",
  applied: "success",
  discarded: "neutral",
  failed: "critical",
};

const STATUS_WORDS = {
  running: "working",
  proposed: "waiting on you",
  applied: "applied",
  discarded: "discarded",
  failed: "failed",
};

function RunRow({ run, selected, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(run.id)}
      className={`flex w-full items-start gap-2 rounded px-2 py-1.5 text-left text-xs ${
        selected ? "bg-[var(--accent-soft)]" : "hover:bg-[var(--surface-2)]"
      }`}
    >
      <Badge tone={STATUS_TONE[run.status] || "neutral"}>
        {STATUS_WORDS[run.status] || run.status}
      </Badge>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[var(--text-primary)]">
          {run.instruction}
        </span>
        <span className="text-[11px] text-[var(--text-muted)]">
          {formatDate(run.created_at)}
          {run.file_count ? ` · ${run.file_count} file${run.file_count === 1 ? "" : "s"}` : ""}
        </span>
      </span>
    </button>
  );
}

/** The proposal itself: what it says it did, what it would change, and the
 *  two decisions available. */
function RunDetail({ runId, onChanged }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [applied, setApplied] = useState(null);

  const run = useAsync(() => api.get(`/agent/runs/${runId}`), [runId]);
  const { reload } = run;
  const status = run.data?.status;

  // Only while it is running. A finished run never changes again, so
  // continuing to poll would be pure noise.
  useEffect(() => {
    if (status !== "running") return undefined;
    const timer = setInterval(() => reload({ quiet: true }), POLL_MS);
    return () => clearInterval(timer);
  }, [status, reload]);

  const act = async (verb) => {
    setBusy(verb);
    setError(null);
    try {
      const body = await api.post(`/agent/runs/${runId}/${verb}`);
      if (verb === "apply") setApplied(body);
      await reload({ quiet: true });
      if (onChanged) onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  if (run.loading && !run.data) return <Spinner label="Loading the run" />;
  if (run.error) return <ErrorNote error={run.error} />;

  const data = run.data;
  const counts = countChanges(data.diff);

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-[var(--text-primary)]">
            {data.instruction}
          </p>
          <p className="text-[11px] text-[var(--text-muted)]">
            {data.model} · {data.turns} turn{data.turns === 1 ? "" : "s"}
            {data.base_sha ? ` · from ${data.base_sha}` : ""}
          </p>
        </div>
        <Badge tone={STATUS_TONE[data.status] || "neutral"}>
          {STATUS_WORDS[data.status] || data.status}
        </Badge>
      </div>

      {data.status === "running" ? (
        <p className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <Loader2 size={13} className="animate-spin" />
          Reading the repository and drafting a change. Nothing has been
          written to disk.
        </p>
      ) : null}

      {data.error ? (
        <p className="flex items-start gap-1.5 rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1.5 text-xs text-[var(--serious)]">
          <AlertTriangle size={13} className="mt-px shrink-0" />
          {data.error}
        </p>
      ) : null}

      {data.review_required ? (
        <p className="flex items-start gap-1.5 rounded border border-[var(--warning)] bg-[color-mix(in_srgb,var(--warning)_12%,transparent)] px-2 py-1.5 text-xs">
          <ShieldAlert size={13} className="mt-px shrink-0" />
          <span>
            <strong>Needs your approval.</strong> {data.review_reason} It will
            not be applied automatically, whatever the run asked for.
          </span>
        </p>
      ) : null}

      {/* Two different claims, and the order says which to trust. The suite
          is evidence; the summary below it is the model's account. */}
      {data.tests_passed === true ? (
        <p className="flex items-start gap-1.5 text-xs text-[var(--good)]">
          <CheckCircle2 size={13} className="mt-px shrink-0" />
          <span>
            <strong>The tests pass</strong> with this change applied.
          </span>
        </p>
      ) : null}

      {data.tests_passed === false ? (
        <div className="space-y-1">
          <p className="flex items-start gap-1.5 text-xs text-[var(--critical)]">
            <XCircle size={13} className="mt-px shrink-0" />
            <span>
              <strong>The tests fail</strong> with this change applied. Read
              the output before approving anything.
            </span>
          </p>
          {data.test_output ? (
            <details>
              <summary className="cursor-pointer text-[11px] text-[var(--text-muted)]">
                What the run printed
              </summary>
              <pre className="mt-1 max-h-64 overflow-auto rounded bg-[var(--surface-2)] p-2 text-[10px] leading-relaxed">
                {data.test_output}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}

      {/* Null covers two different things — never asked for, and asked for
          but refused — and the reason is the useful half. Without it a
          broken test command looks exactly like no test command. */}
      {data.tests_passed === null || data.tests_passed === undefined ? (
        <p className="text-[11px] text-[var(--text-muted)]">
          {data.test_output
            ? data.test_output
            : "The tests were not run, so nothing here has been verified. Set a test command on the project and the agent can check its own work."}
        </p>
      ) : null}

      {data.summary ? (
        <div className="space-y-1">
          <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
            What it says it did
          </p>
          <p className="whitespace-pre-wrap text-xs text-[var(--text-secondary)]">
            {data.summary}
          </p>
        </div>
      ) : null}

      {data.changes?.length ? (
        <div className="space-y-2">
          <p className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--text-muted)]">
            <FileCode size={12} />
            {data.changes.length} file{data.changes.length === 1 ? "" : "s"}
            <span className="text-[var(--good)]">+{counts.added}</span>
            <span className="text-[var(--critical)]">−{counts.removed}</span>
            {data.changes.map((change) => (
              <span key={change.path}>
                <Badge tone={change.action === "delete" ? "critical" : "neutral"}>
                  {change.action}
                </Badge>{" "}
                <code>{change.path}</code>
              </span>
            ))}
          </p>
          <DiffView diff={data.diff} className="max-h-[28rem] overflow-auto" />
        </div>
      ) : null}

      {error ? <ErrorNote error={error} onDismiss={() => setError(null)} /> : null}

      {applied ? (
        <p className="rounded border border-[var(--good)] bg-[color-mix(in_srgb,var(--good)_10%,transparent)] px-2 py-1.5 text-xs">
          Written to the working copy, uncommitted — review it with{" "}
          <code>git diff</code>, undo it with <code>git checkout</code>.
          {applied.note ? ` ${applied.note}` : ""}
        </p>
      ) : null}

      {data.status === "proposed" && data.changes?.length ? (
        <div className="flex flex-wrap gap-2 border-t border-[var(--border)] pt-3">
          <Button onClick={() => act("apply")} disabled={busy !== null}>
            <Check size={13} /> Apply to the working copy
          </Button>
          <Button
            variant="ghost"
            onClick={() => act("discard")}
            disabled={busy !== null}
          >
            <Trash2 size={13} /> Discard
          </Button>
          <p className="basis-full text-[11px] text-[var(--text-muted)]">
            Applying writes the files but does not commit them.
          </p>
        </div>
      ) : null}
    </Card>
  );
}

export default function AgentRuns({ project }) {
  const ai = useAiStatus();
  const [instruction, setInstruction] = useState("");
  const [autoApply, setAutoApply] = useState(false);
  const [selected, setSelected] = useState(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);

  // Asked for here rather than handed down: the Code view above polls this
  // same endpoint, but coupling the two panels through page state meant a
  // setState in an effect on every poll, which re-rendered the whole page
  // four times a minute to answer one boolean.
  const workspace = useAsync(
    () => api.get(`/projects/${project.id}/workspace`),
    [project.id],
  );

  const runs = useAsync(
    () => api.get(`/projects/${project.id}/agent/runs`),
    [project.id],
  );
  const { reload: reloadRuns } = runs;

  const anyRunning = (runs.data || []).some((r) => r.status === "running");
  useEffect(() => {
    if (!anyRunning) return undefined;
    const timer = setInterval(() => reloadRuns({ quiet: true }), POLL_MS);
    return () => clearInterval(timer);
  }, [anyRunning, reloadRuns]);

  const changed = useCallback(() => {
    reloadRuns({ quiet: true });
  }, [reloadRuns]);

  const start = async (event) => {
    event.preventDefault();
    setStarting(true);
    setError(null);
    try {
      const run = await api.post(`/projects/${project.id}/agent/runs`, {
        instruction: instruction.trim(),
        auto_apply: autoApply,
      });
      setInstruction("");
      setSelected(run.id);
      await reloadRuns({ quiet: true });
    } catch (err) {
      setError(err);
    } finally {
      setStarting(false);
    }
  };

  if (!workspace.data?.available) return null;

  if (ai && !ai.configured) {
    return (
      <Card>
        <EmptyState
          icon={ShieldAlert}
          title="The agent needs the model to be configured"
          description={ai.reason}
        />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Ask for a change"
          subtitle="The agent reads the repository and drafts an edit. Nothing is written until you apply it."
        />
        <form onSubmit={start} className="space-y-3 px-4 pb-4">
          <Field
            label="What should change"
            hint="Be specific about the file or behaviour. It has no shell, so it cannot run your tests."
          >
            <textarea
              rows={3}
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              placeholder="Add a limit parameter to the tasks endpoint, defaulting to 50."
              className="w-full rounded border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1.5 text-xs"
            />
          </Field>
          <label className="flex items-start gap-2 text-xs text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={autoApply}
              onChange={(event) => setAutoApply(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              Apply automatically when it finishes.{" "}
              <span className="text-[var(--text-muted)]">
                Ignored if the change touches a protected path — those always
                wait for you.
              </span>
            </span>
          </label>
          {error ? <ErrorNote error={error} onDismiss={() => setError(null)} /> : null}
          <Button type="submit" disabled={starting || instruction.trim().length < 10}>
            {starting ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
            Start
          </Button>
        </form>
      </Card>

      {selected ? <RunDetail runId={selected} onChanged={changed} /> : null}

      <Card>
        <CardHeader title="Runs" subtitle="Everything the agent has been asked to do here." />
        <div className="space-y-px px-2 pb-3">
          {runs.loading && !runs.data ? <Spinner label="Loading runs" /> : null}
          {runs.data?.length === 0 ? (
            <p className="px-2 py-3 text-xs text-[var(--text-muted)]">
              Nothing yet.
            </p>
          ) : null}
          {(runs.data || []).map((run) => (
            <RunRow
              key={run.id}
              run={run}
              selected={selected === run.id}
              onSelect={setSelected}
            />
          ))}
        </div>
      </Card>
    </div>
  );
}
