"use client";

/**
 * What the repository contains, and what it is still missing.
 *
 * The inventory at the top is read from the files and costs nothing, so it
 * loads on its own. The survey below it is a model call and a deliberate
 * press.
 *
 * The part worth looking at is the list of proposals that were *discarded*
 * for already existing. It is shown rather than hidden, because a filter
 * nobody can see is a filter nobody can correct -- and because the whole
 * value of the feature rests on that filter working.
 */

import { useState } from "react";
import {
  AlertTriangle,
  Boxes,
  CheckCircle2,
  Database,
  FileText,
  Layers,
  Lightbulb,
  Route,
  Search,
  Settings2,
  XCircle,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAiStatus } from "@/lib/ai";
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
import Markdown from "@/components/Markdown";

const STATE_TONE = { complete: "success", partial: "warning", scaffolded: "neutral" };
const VALUE_TONE = { high: "critical", medium: "warning", low: "neutral" };

function Count({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <Icon size={13} className="text-[var(--text-muted)]" />
      <span className="font-medium text-[var(--text-primary)]">{value}</span>
      <span className="text-[var(--text-muted)]">{label}</span>
    </div>
  );
}

function Inventory({ data }) {
  const { counts, areas } = data;
  return (
    <div className="space-y-3 px-4 pb-4">
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        <Count icon={FileText} label="source files" value={counts.source_files} />
        <Count icon={CheckCircle2} label="test files" value={counts.test_files} />
        <Count icon={Layers} label="lines" value={counts.lines.toLocaleString()} />
        <Count icon={Route} label="HTTP routes" value={counts.routes} />
        <Count icon={Database} label="tables" value={counts.tables} />
        <Count icon={Boxes} label="components" value={data.components.length} />
      </div>

      <table className="w-full text-xs">
        <tbody>
          {areas.map((area) => (
            <tr key={area.area} className="border-t border-[var(--border)]">
              <td className="py-1 pr-3">
                <code className="text-[var(--text-secondary)]">{area.area}</code>
              </td>
              <td className="py-1 pr-3 text-right text-[var(--text-muted)]">
                {area.files} files
              </td>
              <td className="py-1 text-right text-[var(--text-muted)]">
                {area.lines.toLocaleString()} lines
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {data.env_vars.length ? (
        <p className="flex flex-wrap items-baseline gap-1 text-[11px]">
          <Settings2 size={12} className="text-[var(--text-muted)]" />
          <span className="text-[var(--text-muted)]">Configuration it reads:</span>
          {data.env_vars.map((name) => (
            <code key={name} className="text-[var(--syn-constant)]">
              {name}
            </code>
          ))}
        </p>
      ) : null}

      {data.truncated ? (
        <p className="text-[11px] text-[var(--warning)]">
          Only the first part of the repository was read, so treat anything
          missing here as unknown rather than absent.
        </p>
      ) : null}
    </div>
  );
}

function Gap({ gap }) {
  return (
    <li className="space-y-1 border-t border-[var(--border)] py-2 first:border-t-0">
      <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium">
        <Lightbulb size={13} className="text-[var(--warning)]" />
        {gap.title}
        {gap.value ? <Badge tone={VALUE_TONE[gap.value]}>{gap.value} value</Badge> : null}
        {gap.size ? <Badge tone="neutral">{gap.size}</Badge> : null}
      </p>
      <Markdown className="text-xs text-[var(--text-secondary)]">{gap.why}</Markdown>
      {gap.checked?.length ? (
        <p className="text-[10px] text-[var(--text-muted)]">
          Searched this repository for{" "}
          {gap.checked.map((term) => (
            <code key={term} className="mr-1">
              {term}
            </code>
          ))}
          — none found.
        </p>
      ) : (
        <p className="text-[10px] text-[var(--warning)]">
          Not verified: nothing specific was named to check for.
        </p>
      )}
      {gap.possibly_related?.length ? (
        <p className="flex items-start gap-1 text-[10px] text-[var(--warning)]">
          <AlertTriangle size={11} className="mt-px shrink-0" />
          <span>
            Worth a look first — this repository already defines{" "}
            {gap.possibly_related.slice(0, 5).map((name) => (
              <code key={name} className="mr-1">
                {name}
              </code>
            ))}
          </span>
        </p>
      ) : null}
    </li>
  );
}

function Survey({ result }) {
  const { outline, gaps = [], already_done: dropped = [], reason } = result;

  if (!outline) {
    return (
      <p className="px-4 pb-4 text-xs text-[var(--text-muted)]">
        The outline needs the model. {reason}
      </p>
    );
  }

  return (
    <div className="space-y-4 px-4 pb-4">
      <Markdown className="text-xs text-[var(--text-secondary)]">
        {outline.what_it_is}
      </Markdown>

      <div className="space-y-1">
        <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
          What it already does ({outline.features.length})
        </p>
        <ul className="space-y-1">
          {outline.features.map((feature) => (
            <li key={feature.name} className="text-xs">
              <span className="font-medium text-[var(--text-primary)]">
                {feature.name}
              </span>
              {feature.state !== "complete" ? (
                <Badge tone={STATE_TONE[feature.state]} className="ml-1.5">
                  {feature.state}
                </Badge>
              ) : null}
              <span className="text-[var(--text-secondary)]"> — {feature.what_it_does}</span>
              {feature.where?.length ? (
                <span className="ml-1 text-[var(--text-muted)]">
                  {feature.where.slice(0, 3).map((file) => (
                    <code key={file} className="mr-1 text-[10px]">
                      {file}
                    </code>
                  ))}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
        <p className="text-[10px] text-[var(--text-muted)]">
          Written to this project&apos;s notes as “What this project does”.
        </p>
      </div>

      <div className="space-y-1">
        <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
          Raised as suggestions ({gaps.length})
        </p>
        {gaps.length ? (
          <ul>
            {gaps.map((gap) => (
              <Gap key={gap.title} gap={gap} />
            ))}
          </ul>
        ) : (
          <p className="text-xs text-[var(--text-muted)]">
            Nothing survived the check. Either the project is in good shape or
            everything proposed already exists — the list below says which.
          </p>
        )}
      </div>

      {dropped.length ? (
        <details className="rounded border border-[var(--border)] bg-[var(--surface-2)]/50 px-3 py-2">
          <summary className="cursor-pointer text-[11px] font-medium text-[var(--text-secondary)]">
            Proposed and discarded for already existing ({dropped.length})
          </summary>
          <ul className="mt-2 space-y-1.5">
            {dropped.map((gap) => (
              <li key={gap.title} className="flex items-start gap-1.5 text-xs">
                <XCircle size={13} className="mt-px shrink-0 text-[var(--text-muted)]" />
                <span>
                  <span className="text-[var(--text-secondary)]">{gap.title}</span>
                  <span className="text-[var(--text-muted)]">
                    {" "}
                    — {gap.already_done_because}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

export default function ProjectInventory({ project, onSurveyed }) {
  const ai = useAiStatus();
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const scan = useAsync(
    () => api.get(`/projects/${project.id}/inventory`),
    [project.id],
  );

  const survey = async () => {
    setRunning(true);
    setError(null);
    try {
      const body = await api.post(`/projects/${project.id}/survey`);
      setResult(body);
      if (onSurveyed) onSurveyed();
    } catch (err) {
      setError(err);
    } finally {
      setRunning(false);
    }
  };

  if (scan.loading && !scan.data) return <Spinner label="Reading the repository" />;
  if (scan.error) {
    return (
      <Card>
        <EmptyState
          icon={Boxes}
          title="No local folder for this project"
          description={scan.error.message}
        />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="What is in this repository"
          subtitle="Read from the files themselves — every module, route, table and setting. No model, no cost."
        />
        <Inventory data={scan.data} />
      </Card>

      <Card>
        <CardHeader
          title="Features and gaps"
          subtitle="Reads the whole inventory, writes down what the project does, and proposes only what it does not do yet."
          action={
            <Button onClick={survey} disabled={running || (ai && !ai.configured)}>
              <Search size={13} />
              {running ? "Reading…" : result ? "Survey again" : "Survey the repository"}
            </Button>
          }
        />
        {error ? (
          <div className="px-4 pb-3">
            <ErrorNote error={error} onDismiss={() => setError(null)} />
          </div>
        ) : null}
        {ai && !ai.configured ? (
          <p className="px-4 pb-4 text-xs text-[var(--text-muted)]">{ai.reason}</p>
        ) : null}
        {running ? (
          <div className="px-4 pb-4">
            <Spinner label="Reading every module, then working out what is missing" />
          </div>
        ) : null}
        {result && !running ? <Survey result={result} /> : null}
        {!result && !running && ai?.configured ? (
          <p className="px-4 pb-4 text-xs text-[var(--text-muted)]">
            Every proposal is searched for in the repository before you see it,
            so anything already built is discarded rather than suggested.
          </p>
        ) : null}
      </Card>
    </div>
  );
}
