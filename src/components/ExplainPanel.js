"use client";

/**
 * What the highlighted code is, and what depends on it.
 *
 * Two halves, in the order they are worth reading. The model's account
 * comes first, because "what does this do" is the question that was
 * actually asked. Underneath it, collapsed, are the measured facts it was
 * given -- the definitions found by pattern, the references found by
 * search. Those are what make the answer checkable: if the explanation
 * names a file, it is because that file was in the list.
 *
 * When the model is unavailable the facts are shown on their own, with the
 * reason. They were useful before the model was wired in, and losing them
 * because a key is missing would be the wrong trade.
 */

import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  FileCode,
  Info,
  RefreshCw,
  Share2,
  Sparkles,
  X,
} from "lucide-react";

import { Badge, Button, Card, ErrorNote, Spinner } from "@/components/ui";
import Markdown from "@/components/Markdown";

const LEVEL = {
  risk: { icon: AlertTriangle, tone: "critical" },
  watch: { icon: Eye, tone: "warning" },
  good: { icon: CheckCircle2, tone: "success" },
  note: { icon: Info, tone: "neutral" },
};

const COLOUR = {
  critical: "text-[var(--critical)]",
  warning: "text-[var(--warning)]",
  success: "text-[var(--good)]",
  neutral: "text-[var(--text-muted)]",
};

/** One consequence, coloured by how much it matters. */
export function EffectLine({ effect }) {
  const { icon: Icon, tone } = LEVEL[effect.level] || LEVEL.note;
  return (
    <li className="flex items-start gap-1.5 text-xs">
      <Icon size={13} className={`mt-px shrink-0 ${COLOUR[tone]}`} />
      <span className="text-[var(--text-secondary)]">{effect.text}</span>
    </li>
  );
}

function Section({ title, children }) {
  if (!children) return null;
  return (
    <div className="space-y-1">
      <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
        {title}
      </p>
      {children}
    </div>
  );
}

/** A list where each item is one sentence of prose, rendered as Markdown so
 *  the identifiers in it come out in code style rather than as words. */
function Points({ items, icon: Icon, colour }) {
  if (!items?.length) return null;
  return (
    <ul className="space-y-1">
      {items.map((item, index) => (
        <li key={index} className="flex items-start gap-1.5 text-xs">
          {Icon ? <Icon size={13} className={`mt-px shrink-0 ${colour}`} /> : null}
          <Markdown className="text-[var(--text-secondary)]">{item}</Markdown>
        </li>
      ))}
    </ul>
  );
}

function Explanation({ explanation }) {
  const {
    summary,
    walkthrough = [],
    role_in_the_system: role,
    shared_state: shared,
    if_you_change_it: consequences = [],
    watch_out: watchOut = [],
    unknowns = [],
  } = explanation;

  return (
    <div className="space-y-3">
      {summary ? (
        <Markdown className="text-sm text-[var(--text-primary)]">{summary}</Markdown>
      ) : null}

      {walkthrough.length ? (
        <Section title="How it works">
          <ol className="space-y-1">
            {walkthrough.map((step, index) => (
              <li key={index} className="flex items-start gap-2 text-xs">
                {step.lines ? (
                  <code className="mt-px shrink-0 rounded bg-[var(--surface-2)] px-1 text-[10px] text-[var(--text-muted)]">
                    {step.lines}
                  </code>
                ) : null}
                <Markdown className="text-[var(--text-secondary)]">{step.what}</Markdown>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {role ? (
        <Section title="Why it is here">
          <Markdown className="text-xs text-[var(--text-secondary)]">{role}</Markdown>
        </Section>
      ) : null}

      {shared ? (
        <Section title="What it shares with the rest of the system">
          <Markdown className="text-xs text-[var(--text-secondary)]">{shared}</Markdown>
        </Section>
      ) : null}

      {consequences.length ? (
        <Section title="If you change it">
          <Points items={consequences} icon={AlertTriangle} colour={COLOUR.warning} />
        </Section>
      ) : null}

      {watchOut.length ? (
        <Section title="Easy to miss">
          <Points items={watchOut} icon={Eye} colour={COLOUR.neutral} />
        </Section>
      ) : null}

      {unknowns.length ? (
        <Section title="Could not tell from this">
          <Points items={unknowns} icon={Info} colour={COLOUR.neutral} />
        </Section>
      ) : null}
    </div>
  );
}

/** The measured half: what the model was handed, and what it can be checked
 *  against. Collapsed, because it is evidence rather than an answer. */
function Facts({ facts }) {
  const {
    enclosing,
    defines = [],
    shared_values: shared = [],
    imports_used: importsUsed = [],
    consequences = [],
    limits = [],
  } = facts;

  return (
    <details className="rounded border border-[var(--border)] bg-[var(--surface-2)]/50 px-3 py-2">
      <summary className="cursor-pointer text-[11px] font-medium text-[var(--text-secondary)]">
        What this was read from
      </summary>
      <div className="mt-2 space-y-2">
        {enclosing ? (
          <p className="text-xs text-[var(--text-secondary)]">
            Inside the {enclosing.kind}{" "}
            <strong className="text-[var(--text-primary)]">{enclosing.name}</strong>
            {enclosing.signature ? (
              <code className="ml-1 text-[11px] text-[var(--text-muted)]">
                {enclosing.signature}
              </code>
            ) : null}
          </p>
        ) : (
          <p className="text-xs text-[var(--text-muted)]">
            Not inside any definition this can recognise.
          </p>
        )}

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
          <p className="flex flex-wrap items-center gap-1.5 text-xs">
            <Share2 size={12} className="text-[var(--text-muted)]" />
            <span className="text-[var(--text-muted)]">Module-level values read:</span>
            {shared.map((symbol) => (
              <code key={symbol.name} className="text-[var(--syn-constant)]">
                {symbol.name}
              </code>
            ))}
          </p>
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
          <ul className="space-y-1">
            {consequences.map((effect, index) => (
              <EffectLine key={`${effect.level}-${index}`} effect={effect} />
            ))}
          </ul>
        ) : null}

        <ul className="ml-4 list-disc space-y-0.5 text-[11px] text-[var(--text-muted)]">
          {limits.map((limit) => (
            <li key={limit}>{limit}</li>
          ))}
        </ul>
      </div>
    </details>
  );
}

export default function ExplainPanel({
  result,
  loading,
  error,
  onClose,
  onRefresh,
}) {
  if (loading) return <Spinner label="Reading the code and what depends on it" />;
  if (error) return <ErrorNote error={error} />;
  if (!result) return null;

  const { facts, explanation, cached, model, reason } = result;

  return (
    <Card className="space-y-3 p-3">
      <div className="flex items-start justify-between gap-2">
        <p className="flex min-w-0 items-center gap-1.5 text-xs font-medium">
          <FileCode size={13} />
          <code className="truncate">{facts.path}</code>
          <span className="text-[var(--text-muted)]">
            lines {facts.start_line}–{facts.end_line}
          </span>
        </p>
        <div className="flex shrink-0 items-center gap-1.5">
          {cached ? (
            <Badge tone="neutral" title="Already explained; nothing was spent">
              from cache
            </Badge>
          ) : null}
          {explanation && onRefresh ? (
            <button
              type="button"
              onClick={onRefresh}
              title="Ask again"
              aria-label="Ask again"
              className="text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            >
              <RefreshCw size={13} />
            </button>
          ) : null}
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close explanation"
              className="text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            >
              <X size={14} />
            </button>
          ) : null}
        </div>
      </div>

      {explanation ? (
        <Explanation explanation={explanation} />
      ) : (
        <p className="flex items-start gap-1.5 rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1.5 text-xs">
          <Info size={13} className="mt-px shrink-0" />
          <span>
            <strong>No written explanation.</strong> {reason} What was measured
            from the repository is below.
          </span>
        </p>
      )}

      <Facts facts={facts} />

      {explanation ? (
        <p className="flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
          <Sparkles size={10} />
          Written by {model} from the measured facts below. It can be wrong;
          they can be checked.
        </p>
      ) : null}
    </Card>
  );
}
