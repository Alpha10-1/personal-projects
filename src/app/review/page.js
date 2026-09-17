"use client";

import { useCallback, useState } from "react";
import { Check, RefreshCw, Stethoscope, X } from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Spinner,
} from "@/components/ui";

// Warnings are things to act on; notes are things to know. Anything the
// backend adds later that isn't mapped here falls back to neutral rather than
// disappearing.
const SEVERITY_TONE = { warn: "warning", info: "neutral" };

// The rule name is the honest label -- it's what you'd switch off if the rule
// turned out to be noisy, so it's worth showing rather than prettifying away.
const RULE_LABELS = {
  stale_in_progress: "Stalled",
  stale_blocker: "Blocked a while",
  overdue_task: "Overdue",
  estimate_overrun: "Over estimate",
  project_past_target: "Past target",
  unlinked_activity: "Unlinked activity",
};

function Finding({ finding }) {
  return (
    <div className="flex gap-3 border-t border-[var(--border)] px-4 py-3 first:border-t-0">
      <Badge tone={SEVERITY_TONE[finding.severity] || "neutral"}>
        {RULE_LABELS[finding.rule] || finding.rule}
      </Badge>
      <div className="min-w-0">
        <p className="text-sm font-medium">{finding.title}</p>
        {finding.detail ? (
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">{finding.detail}</p>
        ) : null}
      </div>
    </div>
  );
}

function Suggestion({ suggestion, onResolve }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const resolve = async (decision) => {
    setBusy(decision);
    setError(null);
    try {
      await api.post(`/suggestions/${suggestion.id}/${decision}`);
      onResolve();
    } catch (err) {
      setError(err);
      setBusy(null);
    }
  };

  return (
    <div className="border-t border-[var(--border)] px-4 py-3 first:border-t-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">
            {suggestion.target_title || `${suggestion.target_type} ${suggestion.target_id}`}
          </p>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            {suggestion.rationale}
          </p>
          <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-[var(--text-muted)]">{suggestion.field}</span>
            <Badge tone="neutral">{suggestion.current_value || "unset"}</Badge>
            <span aria-hidden className="text-[var(--text-muted)]">
              &rarr;
            </span>
            <Badge tone="info">{suggestion.proposed_value}</Badge>
          </p>
        </div>

        <div className="flex shrink-0 gap-2">
          <Button
            variant="primary"
            size="sm"
            busy={busy === "accept"}
            disabled={Boolean(busy)}
            onClick={() => resolve("accept")}
          >
            <Check size={13} /> Accept
          </Button>
          <Button
            size="sm"
            busy={busy === "dismiss"}
            disabled={Boolean(busy)}
            onClick={() => resolve("dismiss")}
          >
            <X size={13} /> Dismiss
          </Button>
        </div>
      </div>

      {suggestion.evidence?.length ? (
        <ul className="mt-2 space-y-0.5">
          {suggestion.evidence.map((item) => (
            <li key={item} className="truncate text-xs text-[var(--text-muted)]">
              {item}
            </li>
          ))}
        </ul>
      ) : null}

      <ErrorNote error={error} onDismiss={() => setError(null)} />
    </div>
  );
}

export default function ReviewPage() {
  const [refreshing, setRefreshing] = useState(false);

  const review = useAsync(useCallback(() => api.get("/review"), []), []);
  const data = review.data;
  const reload = () => review.reload({ quiet: true });

  // Looking is free; proposing is a write, so it is a button rather than
  // something that happens because you opened the page.
  const lookForSuggestions = async () => {
    setRefreshing(true);
    try {
      await api.post("/suggestions/refresh");
      await reload();
    } finally {
      setRefreshing(false);
    }
  };

  const findings = data?.findings || [];
  const suggestions = data?.suggestions || [];

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Review</h1>
          <p className="text-sm text-[var(--text-muted)]">
            What needs a second look, and what the analyst proposes doing about it.
          </p>
        </div>
        <Button busy={refreshing} onClick={lookForSuggestions}>
          <RefreshCw size={14} /> Look for suggestions
        </Button>
      </header>

      <ErrorNote error={review.error} onDismiss={() => review.reload()} />

      {review.loading && !data ? <Spinner /> : null}

      {data ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="Suggestions"
              subtitle="Nothing here is applied until you accept it."
              action={
                suggestions.length ? (
                  <Badge tone="info">{suggestions.length}</Badge>
                ) : null
              }
            />
            {suggestions.length ? (
              suggestions.map((suggestion) => (
                <Suggestion
                  key={suggestion.id}
                  suggestion={suggestion}
                  onResolve={reload}
                />
              ))
            ) : (
              <EmptyState
                icon={Stethoscope}
                title="Nothing to decide"
                description={
                  "Suggestions come from commits and pull requests linked to a task. " +
                  "Set a project's repo to start collecting them."
                }
              />
            )}
          </Card>

          <Card>
            <CardHeader
              title="Findings"
              subtitle="Observations only — these change nothing on their own."
              action={
                findings.length ? (
                  <Badge tone={findings.some((f) => f.severity === "warn") ? "warning" : "neutral"}>
                    {findings.length}
                  </Badge>
                ) : null
              }
            />
            {findings.length ? (
              findings.map((finding) => (
                <Finding
                  key={`${finding.rule}-${finding.target_id}-${finding.title}`}
                  finding={finding}
                />
              ))
            ) : (
              <EmptyState
                icon={Stethoscope}
                title="Nothing needs attention"
                description="No stalled work, overdue tasks or overruns right now."
              />
            )}
          </Card>
        </div>
      ) : null}
    </div>
  );
}
