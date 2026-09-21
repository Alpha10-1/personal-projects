"use client";

import { useCallback, useState } from "react";
import { AlertTriangle, ArrowRight, Check, ExternalLink, X } from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import { Badge, Button, ErrorNote, Modal, Spinner } from "@/components/ui";

/** A value shown in full, or an honest gap where there isn't one.
 *
 *  These are the two things being weighed, so neither is truncated, boxed or
 *  reduced to a badge — the whole reason this dialog exists is that a
 *  240-character summary does not fit in a row. */
function Value({ label, value, tone = "neutral", empty = "Nothing set" }) {
  const filled = Boolean(value && String(value).trim());
  return (
    <div className="min-w-0 flex-1">
      <p className="mb-1 text-xs font-medium text-[var(--text-muted)]">{label}</p>
      <div
        className={`whitespace-pre-wrap break-words rounded-lg border px-3 py-2 text-sm ${
          tone === "proposed"
            ? "border-[var(--accent)] bg-[var(--accent-soft)]"
            : "border-[var(--border)] bg-[var(--surface-2)]"
        } ${filled ? "" : "italic text-[var(--text-muted)]"}`}
      >
        {filled ? value : empty}
      </div>
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div className="flex gap-3 text-xs">
      <span className="w-28 shrink-0 text-[var(--text-muted)]">{label}</span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}

/**
 * One suggestion, with nothing left out, before deciding on it.
 *
 * The list row has to fit on a line, so it showed a 240-character proposed
 * summary inside a badge and clipped the evidence. This shows both values in
 * full, side by side, plus the three things the row cannot: what the field
 * holds *now*, whether that has changed since the suggestion was raised, and
 * what accepting would actually write.
 *
 * Accept and dismiss are here as well as on the row, because having read it
 * is exactly the moment you know which one you want.
 */
export default function SuggestionDetail({ suggestionId, open, onClose, onResolved }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const detail = useAsync(
    useCallback(
      () => (open && suggestionId ? api.get(`/suggestions/${suggestionId}`) : null),
      [open, suggestionId],
    ),
    [open, suggestionId],
  );
  const s = detail.data;

  const resolve = async (decision) => {
    setBusy(decision);
    setError(null);
    try {
      await api.post(`/suggestions/${suggestionId}/${decision}`);
      onResolved?.(decision);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const href =
    s?.target_type === "project"
      ? `/projects/${s.target_id}`
      : s?.target_type === "task"
        ? `/tasks?task=${s.target_id}`
        : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={s ? `Suggestion for ${s.target_title || `${s.target_type} ${s.target_id}`}` : "Suggestion"}
      width="max-w-2xl"
    >
      <div className="space-y-4 px-4 py-4">
        {detail.loading && !s ? <Spinner label="Reading the suggestion" /> : null}
        <ErrorNote error={detail.error} onDismiss={() => detail.reload()} />
        <ErrorNote error={error} onDismiss={() => setError(null)} />

        {s ? (
          <>
            {!s.target_exists ? (
              <div className="flex items-start gap-2 rounded-lg border border-[var(--critical)] px-3 py-2 text-xs">
                <AlertTriangle size={14} className="mt-px shrink-0 text-[var(--critical)]" />
                <span>
                  The {s.target_type} this is about no longer exists, so this
                  cannot be accepted. Dismissing it will clear it away.
                </span>
              </div>
            ) : null}

            {s.changed_since_raised && s.target_exists ? (
              <div className="flex items-start gap-2 rounded-lg border border-[var(--warning)] px-3 py-2 text-xs">
                <AlertTriangle size={14} className="mt-px shrink-0 text-[var(--warning)]" />
                <span>
                  This has changed since the suggestion was raised. Accepting
                  would overwrite what is there now, not what the suggestion
                  was written against.
                </span>
              </div>
            ) : null}

            {s.already_applied ? (
              <div className="rounded-lg border border-[var(--border)] px-3 py-2 text-xs text-[var(--text-muted)]">
                The proposed value is already in place, so accepting would
                change nothing.
              </div>
            ) : null}

            <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
              <Value
                label={s.changed_since_raised ? "What it says now" : "Now"}
                value={s.target_exists ? s.live_value : s.current_value}
              />
              <div className="hidden shrink-0 items-center text-[var(--text-muted)] sm:flex">
                <ArrowRight size={16} />
              </div>
              <Value label="Proposed" value={s.proposed_value} tone="proposed" />
            </div>

            {s.changed_since_raised && s.target_exists ? (
              <Value
                label="What it said when this was raised"
                value={s.current_value}
                empty="Nothing set at the time"
              />
            ) : null}

            <div className="space-y-1.5 border-t border-[var(--border)] pt-3">
              <Row label="Why">{s.rationale}</Row>
              {s.rule_explanation ? (
                <Row label="The rule">
                  {s.rule_explanation}{" "}
                  <span className="text-[var(--text-muted)]">({s.rule})</span>
                </Row>
              ) : (
                <Row label="The rule">{s.rule}</Row>
              )}
              {s.applies ? <Row label="If accepted">{s.applies}</Row> : null}
              {!s.can_apply ? (
                <Row label="If accepted">
                  <span className="text-[var(--critical)]">
                    Nothing here knows how to apply a change to{" "}
                    {s.target_type}.{s.field}, so accepting would fail.
                  </span>
                </Row>
              ) : null}
              <Row label="Changes">
                <code className="font-mono text-[0.9em]">
                  {s.target_type}.{s.field}
                </code>
              </Row>
              <Row label="Raised">{formatDate(s.created_at)}</Row>
              <Row label="Status">
                <Badge tone={s.status === "pending" ? "info" : "neutral"}>
                  {s.status}
                </Badge>
              </Row>
            </div>

            {s.evidence?.length ? (
              <div className="border-t border-[var(--border)] pt-3">
                <p className="mb-1 text-xs font-medium text-[var(--text-muted)]">
                  Evidence
                </p>
                <ul className="space-y-1">
                  {s.evidence.map((item) => (
                    <li
                      key={item}
                      className="break-words rounded bg-[var(--surface-2)] px-2 py-1 text-xs"
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-3">
              {s.status === "pending" ? (
                <>
                  <Button
                    variant="primary"
                    busy={busy === "accept"}
                    disabled={Boolean(busy) || !s.target_exists || !s.can_apply}
                    onClick={() => resolve("accept")}
                  >
                    <Check size={13} /> Accept
                  </Button>
                  <Button
                    busy={busy === "dismiss"}
                    disabled={Boolean(busy)}
                    onClick={() => resolve("dismiss")}
                  >
                    <X size={13} /> Dismiss
                  </Button>
                </>
              ) : (
                <p className="text-xs text-[var(--text-muted)]">
                  Already {s.status}
                  {s.resolved_at ? ` on ${formatDate(s.resolved_at)}` : ""}.
                </p>
              )}
              {href ? (
                <a
                  href={href}
                  className="ml-auto flex items-center gap-1 text-xs text-[var(--accent)] hover:underline"
                >
                  Open the {s.target_type} <ExternalLink size={12} />
                </a>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </Modal>
  );
}
