"use client";

/**
 * Changes waiting on the leader, and what the approved ones actually did.
 *
 * The second half is the one that matters. Anyone can approve a diff they
 * have skimmed; the useful question arrives ten minutes later, and it is
 * "what did I just let through". So every change -- pending or approved --
 * carries the same outline, computed from the repository at the moment you
 * ask, and an approved one can be put back exactly.
 *
 * None of this calls a model. The outline is definitions found by pattern
 * and references found by search, which is why it can be shown on every
 * row without anyone thinking about cost.
 */

import { useCallback, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  History,
  Info,
  RotateCcw,
  ShieldCheck,
  ThumbsDown,
  Undo2,
  UserCheck,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Select,
  Spinner,
} from "@/components/ui";
import DiffView, { countChanges } from "@/components/DiffView";
import { EffectLine } from "@/components/ExplainPanel";

const STATUS_TONE = {
  pending: "warning",
  approved: "success",
  rejected: "neutral",
  reverted: "info",
};

const CHANGE_TONE = {
  removed: "critical",
  signature_changed: "warning",
  added: "success",
  modified: "neutral",
};

const CHANGE_WORDS = {
  removed: "removed",
  signature_changed: "signature changed",
  added: "added",
  modified: "changed",
};

/** The outline, loaded when a change is opened rather than for the whole
 *  list: each one runs a handful of searches over the repository. */
function Impact({ changeId }) {
  const outline = useAsync(
    () => api.get(`/code/changes/${changeId}/impact`),
    [changeId],
  );

  if (outline.loading) return <Spinner label="Working out what this reaches" />;
  if (outline.error) return <ErrorNote error={outline.error} />;
  const data = outline.data;
  if (!data) return null;

  const worst = data.effects.find((e) => e.level === "risk");

  return (
    <div className="space-y-2">
      {data.still_as_approved === false ? (
        <p className="flex items-start gap-1.5 rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1.5 text-xs">
          <Info size={13} className="mt-px shrink-0" />
          The file has been changed again since this was approved, so what is
          on disk is no longer exactly this.
        </p>
      ) : null}

      {worst ? (
        <p className="flex items-start gap-1.5 rounded border border-[var(--critical)] bg-[color-mix(in_srgb,var(--critical)_10%,transparent)] px-2 py-1.5 text-xs">
          <AlertTriangle size={13} className="mt-px shrink-0 text-[var(--critical)]" />
          <span>
            <strong>This one reaches other files.</strong> {worst.text}
          </span>
        </p>
      ) : null}

      {data.symbols?.length ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-[var(--text-muted)]">What changed</span>
          {data.symbols.map((symbol) => (
            <Badge key={`${symbol.name}-${symbol.change}`} tone={CHANGE_TONE[symbol.change]}>
              {symbol.kind} {symbol.name} {CHANGE_WORDS[symbol.change] || symbol.change}
            </Badge>
          ))}
        </div>
      ) : null}

      <ul className="space-y-1">
        {data.effects.map((effect, index) => (
          <EffectLine key={`${effect.level}-${index}`} effect={effect} />
        ))}
      </ul>

      <details className="text-[11px] text-[var(--text-muted)]">
        <summary className="cursor-pointer">What this did not look at</summary>
        <ul className="ml-4 mt-1 list-disc space-y-0.5">
          {data.limits?.map((limit) => (
            <li key={limit}>{limit}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function ChangeDetail({ changeId, leader, people, onDecided }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [approver, setApprover] = useState(leader?.id ? String(leader.id) : "");
  const [note, setNote] = useState("");

  const change = useAsync(() => api.get(`/code/changes/${changeId}`), [changeId]);
  const { reload } = change;

  const act = async (verb) => {
    setBusy(verb);
    setError(null);
    try {
      await api.post(`/code/changes/${changeId}/${verb}`, {
        person_id: approver ? Number(approver) : null,
        note: note.trim() || null,
      });
      await reload({ quiet: true });
      if (onDecided) onDecided();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  if (change.loading && !change.data) return <Spinner label="Loading the change" />;
  if (change.error) return <ErrorNote error={change.error} />;
  const data = change.data;
  const counts = countChanges(data.diff);

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <code className="truncate">{data.path}</code>
            <span className="text-[var(--good)]">+{counts.added}</span>
            <span className="text-[var(--critical)]">−{counts.removed}</span>
          </p>
          <p className="text-[11px] text-[var(--text-muted)]">
            {data.origin === "agent" ? "Written by the agent" : "Edited here"} ·{" "}
            {formatDate(data.created_at)}
            {data.approved_by ? ` · approved by ${data.approved_by}` : ""}
          </p>
        </div>
        <Badge tone={STATUS_TONE[data.status] || "neutral"}>{data.status}</Badge>
      </div>

      {data.note ? (
        <p className="whitespace-pre-wrap rounded bg-[var(--surface-2)] px-2 py-1.5 text-xs text-[var(--text-secondary)]">
          {data.note}
        </p>
      ) : null}

      <Impact changeId={changeId} />

      <DiffView diff={data.diff} className="max-h-[24rem] overflow-auto" />

      {error ? <ErrorNote error={error} onDismiss={() => setError(null)} /> : null}

      {data.status === "pending" ? (
        <div className="space-y-2 border-t border-[var(--border)] pt-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs">
              <span className="mb-1 flex items-center gap-1 text-[var(--text-secondary)]">
                <UserCheck size={12} /> Approving as
              </span>
              <Select
                value={approver}
                onChange={(event) => setApprover(event.target.value)}
                includeBlank
                blankLabel="Nobody chosen"
                options={people.map((p) => ({
                  value: String(p.id),
                  label: p.id === leader?.id ? `${p.name} (leader)` : p.name,
                }))}
              />
            </label>
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="A word on the decision"
              className="min-w-0 flex-1 rounded border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1 text-xs"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => act("approve")} disabled={busy !== null}>
              <ShieldCheck size={13} /> Approve and write the file
            </Button>
            <Button variant="ghost" onClick={() => act("reject")} disabled={busy !== null}>
              <ThumbsDown size={13} /> Reject
            </Button>
          </div>
          <p className="text-[11px] text-[var(--text-muted)]">
            Approving writes the file, uncommitted. It does not commit or push.
          </p>
        </div>
      ) : null}

      {data.status === "approved" ? (
        <div className="border-t border-[var(--border)] pt-3">
          <Button variant="ghost" onClick={() => act("revert")} disabled={busy !== null}>
            <Undo2 size={13} /> Put the file back as it was
          </Button>
          <p className="mt-1 text-[11px] text-[var(--text-muted)]">
            Exact, because both sides were kept. Refused if the file has moved
            on since, rather than throwing away whatever came after.
          </p>
        </div>
      ) : null}
    </Card>
  );
}

function Row({ change, selected, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(change.id)}
      className={`flex w-full items-start gap-2 rounded px-2 py-1.5 text-left text-xs ${
        selected ? "bg-[var(--accent-soft)]" : "hover:bg-[var(--surface-2)]"
      }`}
    >
      <Badge tone={STATUS_TONE[change.status] || "neutral"}>{change.status}</Badge>
      <span className="min-w-0 flex-1">
        <code className="block truncate text-[var(--text-primary)]">{change.path}</code>
        <span className="text-[11px] text-[var(--text-muted)]">
          {change.origin === "agent" ? "agent" : "you"} · {formatDate(change.created_at)} ·{" "}
          <span className="text-[var(--good)]">+{change.lines.added}</span>{" "}
          <span className="text-[var(--critical)]">−{change.lines.removed}</span>
        </span>
      </span>
    </button>
  );
}

export default function CodeChanges({ project, refreshKey }) {
  // `null` means "nothing chosen yet", which is not the same as "nothing
  // selected" -- the default falls through to the oldest pending change.
  const [chosen, setChosen] = useState(null);

  const changes = useAsync(
    () => api.get(`/projects/${project.id}/code/changes`),
    [project.id, refreshKey],
  );
  const people = useAsync(() => api.get("/people"), []);
  const { reload: reloadChanges } = changes;

  const decided = useCallback(() => reloadChanges({ quiet: true }), [reloadChanges]);

  const rows = changes.data || [];
  const pending = rows.filter((c) => c.status === "pending");
  const recent = rows.filter((c) => c.status !== "pending");
  const leader = (people.data || []).find((p) => p.id === project.leader_id);

  // What to show without being asked: whatever is waiting on a person
  // first, and failing that the most recent decision. The second half
  // matters more than it looks -- the case this panel exists for is
  // "I just approved something", and at that moment there is nothing
  // pending and the thing you want is the change you just let through.
  //
  // Derived rather than set in an effect, so the list arriving does not
  // cause a second render pass, and because `set-state-in-effect` is an
  // error in this version of React.
  const fallback = pending.length ? pending[pending.length - 1] : rows[0];
  const selected = chosen ?? fallback?.id ?? null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Changes"
          subtitle={
            leader
              ? `${leader.name} reviews and approves changes to this project.`
              : "No leader set for this project — choose one in the brief, or name an approver on each change."
          }
          action={
            pending.length ? (
              <Badge tone="warning">
                {pending.length} waiting
              </Badge>
            ) : null
          }
        />
        <div className="space-y-px px-2 pb-3">
          {changes.loading && !changes.data ? <Spinner label="Loading changes" /> : null}
          {!changes.loading && !rows.length ? (
            <EmptyState
              icon={History}
              title="Nothing has been changed here yet"
              description="Edits made in the Code tab, and anything the agent proposes, arrive here for approval before they reach disk."
            />
          ) : null}
          {pending.map((change) => (
            <Row
              key={change.id}
              change={change}
              selected={selected === change.id}
              onSelect={setChosen}
            />
          ))}
          {recent.length ? (
            <>
              <p className="px-2 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
                Already decided
              </p>
              {recent.map((change) => (
                <Row
                  key={change.id}
                  change={change}
                  selected={selected === change.id}
                  onSelect={setChosen}
                />
              ))}
            </>
          ) : null}
        </div>
      </Card>

      {selected ? (
        <ChangeDetail
          changeId={selected}
          leader={leader}
          people={people.data || []}
          onDecided={decided}
        />
      ) : null}
    </div>
  );
}
