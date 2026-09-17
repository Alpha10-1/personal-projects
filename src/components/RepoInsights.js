"use client";

import { useState } from "react";
import { AlertTriangle, GitBranch, Lightbulb, RefreshCw, Sparkles } from "lucide-react";

import { api } from "@/lib/api";
import { useAiStatus } from "@/lib/ai";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Spinner,
} from "@/components/ui";

const CONFIDENCE_TONE = { high: "warning", medium: "neutral", low: "neutral" };

/**
 * What the model makes of a project's recent commits.
 *
 * Asked for on a click, never on page load. A repo review reads diffs and
 * costs real money, and the answer barely changes between two visits an hour
 * apart -- so it is a decision, not a side effect of navigating.
 *
 * The result is deliberately not stored. A risk here is an opinion about code
 * as it stood at one moment; persisting it would put it alongside findings
 * from `review.py`, which are deterministic and reproducible, and the two
 * should not be mistaken for each other.
 */
export default function RepoInsights({ project }) {
  const status = useAiStatus();
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setResult(await api.post(`/ai/projects/${project.id}/repo-review`));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  if (!project.repo) {
    return (
      <Card>
        <CardHeader title="Repo" subtitle="Not linked to GitHub." />
        <EmptyState
          icon={GitBranch}
          title="No repo linked"
          description="Add one in the brief to pull in commits and pull requests, and to let the analyst review them."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              <GitBranch size={14} /> {project.repo}
            </span>
          }
          subtitle="A summary, possible bugs and suggested improvements over the latest commits."
          action={
            status?.configured ? (
              <Button variant="primary" size="sm" busy={loading} onClick={run}>
                {result ? <RefreshCw size={13} /> : <Sparkles size={13} />}
                {result ? "Review again" : "Review recent work"}
              </Button>
            ) : null
          }
        />

        <div className="px-4 pb-4">
          <ErrorNote error={error} onDismiss={() => setError(null)} />

          {!status?.configured ? (
            <p className="text-xs text-[var(--text-muted)]">
              The assistant is switched off. Set ANTHROPIC_API_KEY in
              backend/.env to enable reviews.
            </p>
          ) : null}

          {loading ? <Spinner label="Reading the commits" /> : null}

          {result ? (
            <>
              <p className="text-sm">{result.summary}</p>
              <p className="mt-2 text-[11px] text-[var(--text-muted)]">
                {result.commits_reviewed} commit(s) of {result.events_considered}{" "}
                event(s)
                {result.diffs_included
                  ? ", with diffs"
                  : " — commit messages only, diffs weren't available"}
              </p>
              {result.themes?.length ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {result.themes.map((theme) => (
                    <Badge key={theme} tone="neutral">
                      {theme}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </Card>

      {result ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="Possible bugs"
              subtitle="Unverified — read them as questions, not verdicts."
              action={
                result.risks?.length ? (
                  <Badge tone="warning">{result.risks.length}</Badge>
                ) : null
              }
            />
            {result.risks?.length ? (
              result.risks.map((risk, index) => (
                <div
                  key={`${risk.title}-${index}`}
                  className="border-t border-[var(--border)] px-4 py-3 first:border-t-0"
                >
                  <div className="flex items-start gap-2">
                    <AlertTriangle
                      size={14}
                      className="mt-0.5 shrink-0 text-[var(--warning,#d97706)]"
                    />
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{risk.title}</p>
                      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                        {risk.detail}
                      </p>
                      <p className="mt-1 flex flex-wrap items-center gap-1.5">
                        <Badge tone={CONFIDENCE_TONE[risk.confidence] || "neutral"}>
                          {risk.confidence} confidence
                        </Badge>
                        {risk.where ? (
                          <span className="truncate text-[11px] text-[var(--text-muted)]">
                            {risk.where}
                          </span>
                        ) : null}
                      </p>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <EmptyState
                icon={AlertTriangle}
                title="Nothing flagged"
                description="No likely bugs in what it was shown."
              />
            )}
          </Card>

          <Card>
            <CardHeader
              title="Improvements"
              subtitle="Worth doing, but not broken."
              action={
                result.improvements?.length ? (
                  <Badge tone="info">{result.improvements.length}</Badge>
                ) : null
              }
            />
            {result.improvements?.length ? (
              result.improvements.map((item, index) => (
                <div
                  key={`${item.title}-${index}`}
                  className="flex items-start gap-2 border-t border-[var(--border)] px-4 py-3 first:border-t-0"
                >
                  <Lightbulb size={14} className="mt-0.5 shrink-0 text-[var(--accent)]" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{item.title}</p>
                    <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                      {item.detail}
                    </p>
                  </div>
                </div>
              ))
            ) : (
              <EmptyState
                icon={Lightbulb}
                title="Nothing suggested"
                description="No improvements worth raising from these commits."
              />
            )}
          </Card>
        </div>
      ) : null}
    </div>
  );
}
