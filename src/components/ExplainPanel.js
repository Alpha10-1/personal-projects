"use client";

/**
 * What the highlighted code is, and what depends on it.
 *
 * Everything shown here was read out of the repository -- definitions by
 * pattern, references by search -- so it is free to ask and the same every
 * time. The panel says so at the bottom rather than letting it be mistaken
 * for a model's opinion, and it lists what the search cannot see, because
 * an analysis that implies completeness is worse than one that admits its
 * edges.
 */

import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  FileCode,
  Info,
  Share2,
  X,
} from "lucide-react";

import { Badge, Card, ErrorNote, Spinner } from "@/components/ui";

const LEVEL = {
  risk: { icon: AlertTriangle, tone: "critical", label: "Breaks things" },
  watch: { icon: Eye, tone: "warning", label: "Worth checking" },
  good: { icon: CheckCircle2, tone: "success", label: "Fine" },
  note: { icon: Info, tone: "neutral", label: "Note" },
};

/** One consequence, coloured by how much it matters. */
export function EffectLine({ effect }) {
  const { icon: Icon, tone } = LEVEL[effect.level] || LEVEL.note;
  const colour = {
    critical: "text-[var(--critical)]",
    warning: "text-[var(--warning)]",
    success: "text-[var(--good)]",
    neutral: "text-[var(--text-muted)]",
  }[tone];
  return (
    <li className="flex items-start gap-1.5 text-xs">
      <Icon size={13} className={`mt-px shrink-0 ${colour}`} />
      <span className="text-[var(--text-secondary)]">{effect.text}</span>
    </li>
  );
}

export default function ExplainPanel({ result, loading, error, onClose }) {
  if (loading) return <Spinner label="Reading the repository" />;
  if (error) return <ErrorNote error={error} />;
  if (!result) return null;

  const {
    enclosing,
    defines = [],
    shared_values: shared = [],
    imports_used: importsUsed = [],
    consequences = [],
    limits = [],
    start_line: start,
    end_line: end,
  } = result;

  return (
    <Card className="space-y-3 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-medium">
            <FileCode size={13} />
            <code className="truncate">{result.path}</code>
            <span className="text-[var(--text-muted)]">
              lines {start}–{end}
            </span>
          </p>
          {enclosing ? (
            <p className="mt-1 text-xs text-[var(--text-secondary)]">
              Inside the {enclosing.kind}{" "}
              <strong className="text-[var(--text-primary)]">{enclosing.name}</strong>
              {enclosing.signature ? (
                <code className="ml-1 text-[11px] text-[var(--text-muted)]">
                  {enclosing.signature}
                </code>
              ) : null}
            </p>
          ) : (
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Not inside any definition this can recognise — top-level code,
              or a file type it does not read.
            </p>
          )}
        </div>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close explanation"
            className="shrink-0 text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          >
            <X size={14} />
          </button>
        ) : null}
      </div>

      {defines.length ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-[var(--text-muted)]">Defines</span>
          {defines.map((symbol) => (
            <Badge key={`${symbol.name}:${symbol.line}`} tone="neutral">
              {symbol.kind} {symbol.name}
            </Badge>
          ))}
        </div>
      ) : null}

      {shared.length ? (
        <div className="space-y-1">
          <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
            <Share2 size={12} /> Shared with other files
          </p>
          <ul className="space-y-0.5">
            {shared.map((symbol) => (
              <li key={symbol.name} className="text-xs text-[var(--text-secondary)]">
                <code className="text-[var(--syn-constant)]">{symbol.name}</code>{" "}
                <span className="text-[var(--text-muted)]">
                  — defined at the top of this file, so it is the same value
                  everywhere it is imported.
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {importsUsed.length ? (
        <p className="text-xs text-[var(--text-muted)]">
          Uses:{" "}
          {importsUsed.map((name) => (
            <code key={name} className="mr-1.5 text-[var(--syn-type)]">
              {name}
            </code>
          ))}
        </p>
      ) : null}

      {consequences.length ? (
        <div className="space-y-1">
          <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
            If you change this
          </p>
          <ul className="space-y-1">
            {consequences.map((effect, index) => (
              <EffectLine key={`${effect.level}-${index}`} effect={effect} />
            ))}
          </ul>
        </div>
      ) : null}

      <details className="text-[11px] text-[var(--text-muted)]">
        <summary className="cursor-pointer">What this did not look at</summary>
        <ul className="ml-4 mt-1 list-disc space-y-0.5">
          {limits.map((limit) => (
            <li key={limit}>{limit}</li>
          ))}
        </ul>
      </details>
    </Card>
  );
}
