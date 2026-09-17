"use client";

import { Check, Sparkles, X } from "lucide-react";

import { useAiStatus, useLiveSuggestions } from "@/lib/ai";
import { Badge } from "@/components/ui";

/** Field names as the forms label them. */
const LABELS = {
  summary: "Summary",
  objective: "Objective",
  definition_of_done: "Definition of done",
  category: "Category",
  priority: "Priority",
  tech_stack: "Tools & stack",
  title: "Title",
  notes: "Notes",
  estimate_hours: "Estimate (hours)",
};

/** Suggestions the form applies to a field, versus ones it turns into rows of
 *  their own. Keeping them apart matters because "apply" means something
 *  different for each. */
const LIST_FIELDS = ["tasks", "subtasks"];

function preview(value) {
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

/**
 * Live suggestions for a form in progress.
 *
 * Nothing here changes the form on its own. Every suggestion is a button,
 * which is the same bargain the Review page makes: the analyst proposes and
 * you decide. A panel that quietly rewrote what you were typing would be
 * unusable, and worse, you would stop being able to tell which words were
 * yours.
 */
export default function AiSuggestions({ kind, draft, projectId, onApply, onApplyList }) {
  const status = useAiStatus();
  const enabled = Boolean(status?.configured);
  const { fields, loading, error, dismissed, dismiss } = useLiveSuggestions({
    kind,
    draft,
    projectId,
    enabled,
  });

  // Switched off, or the key is missing: say nothing at all rather than
  // advertising a feature that isn't available.
  if (!enabled || dismissed) return null;

  const entries = Object.entries(fields || {});
  const questions = fields?.questions || [];
  const hasAnything = entries.some(([key]) => key !== "questions");

  if (!loading && !error && !hasAnything && !questions.length) return null;

  return (
    <div className="rounded-lg border border-[var(--accent-soft)] bg-[var(--accent-soft)]/40 p-3">
      <div className="mb-2 flex items-center gap-2">
        <Sparkles size={14} className="text-[var(--accent)]" />
        <span className="text-xs font-medium text-[var(--accent)]">
          {loading ? "Thinking…" : "Suggestions"}
        </span>
        <span className="ml-auto">
          <button
            type="button"
            onClick={dismiss}
            aria-label="Stop suggesting for this form"
            className="rounded p-0.5 text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
          >
            <X size={13} />
          </button>
        </span>
      </div>

      {error ? (
        <p className="text-xs text-[var(--text-muted)]">
          Couldn&apos;t get suggestions: {error.message}
        </p>
      ) : null}

      <div className="space-y-1.5">
        {entries
          .filter(([key]) => key !== "questions")
          .map(([key, value]) => {
            const isList = LIST_FIELDS.includes(key);
            return (
              <div key={key} className="flex items-start gap-2">
                <button
                  type="button"
                  onClick={() =>
                    isList ? onApplyList?.(key, value) : onApply?.(key, value)
                  }
                  className="mt-0.5 shrink-0 rounded border border-[var(--accent)] p-0.5 text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white"
                  aria-label={`Use this ${LABELS[key] || key}`}
                >
                  <Check size={12} />
                </button>
                <div className="min-w-0 text-xs">
                  <Badge tone="neutral">
                    {LABELS[key] || key.replace(/_/g, " ")}
                    {isList && Array.isArray(value) ? ` ×${value.length}` : ""}
                  </Badge>{" "}
                  <span className="text-[var(--text-secondary)]">{preview(value)}</span>
                </div>
              </div>
            );
          })}
      </div>

      {questions.length ? (
        <div className="mt-2 border-t border-[var(--border)] pt-2">
          <p className="text-[11px] font-medium text-[var(--text-muted)]">
            Worth deciding
          </p>
          <ul className="mt-0.5 space-y-0.5">
            {questions.map((question) => (
              <li key={question} className="text-xs text-[var(--text-muted)]">
                — {question}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
