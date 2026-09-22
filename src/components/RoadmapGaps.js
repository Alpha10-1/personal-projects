"use client";

/**
 * What this project has not planned yet.
 *
 * The same contract as the repository survey, pointed at the board: read
 * what is already there first, propose only what is not, and show what was
 * discarded so the filter can be judged.
 *
 * Two checks run behind each proposal and they are reported separately,
 * because they fail differently. The board check compares titles and is
 * fuzzy; the repository check greps for identifiers and is exact. A
 * proposal that survived only the first is marked as such rather than
 * being presented as though both had passed.
 */

import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Flag,
  ListChecks,
  Search,
  XCircle,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAiStatus } from "@/lib/ai";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ErrorNote,
  Spinner,
} from "@/components/ui";
import Markdown from "@/components/Markdown";

const VALUE_TONE = { high: "critical", medium: "warning", low: "neutral" };

function Proposal({ item, icon: Icon }) {
  return (
    <li className="space-y-1 border-t border-[var(--border)] py-2 first:border-t-0">
      <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium">
        <Icon size={13} className="text-[var(--accent)]" />
        {item.title}
        {item.value ? <Badge tone={VALUE_TONE[item.value]}>{item.value} value</Badge> : null}
        {item.estimate_hours ? (
          <Badge tone="neutral">{item.estimate_hours}h</Badge>
        ) : null}
      </p>

      <Markdown className="text-xs text-[var(--text-secondary)]">{item.why}</Markdown>

      {item.detail ? (
        <p className="text-[11px] text-[var(--text-muted)]">
          Met when: {item.detail}
        </p>
      ) : null}
      {item.milestone ? (
        <p className="text-[11px] text-[var(--text-muted)]">
          Belongs under <strong>{item.milestone}</strong>
        </p>
      ) : null}

      <p className="flex items-start gap-1 text-[10px] text-[var(--text-muted)]">
        <CheckCircle2 size={11} className="mt-px shrink-0 text-[var(--good)]" />
        Checked against every milestone and task already on the board.
      </p>

      {item.checked?.length ? (
        <p className="flex items-start gap-1 text-[10px] text-[var(--text-muted)]">
          <CheckCircle2 size={11} className="mt-px shrink-0 text-[var(--good)]" />
          <span>
            Searched the repository for{" "}
            {item.checked.map((term) => (
              <code key={term} className="mr-1">
                {term}
              </code>
            ))}
            — none found.
          </span>
        </p>
      ) : (
        <p className="flex items-start gap-1 text-[10px] text-[var(--warning)]">
          <AlertTriangle size={11} className="mt-px shrink-0" />
          Not checked against the code — no local folder, or nothing specific
          was named to look for.
        </p>
      )}

      {item.possibly_related?.length ? (
        <p className="flex items-start gap-1 text-[10px] text-[var(--warning)]">
          <AlertTriangle size={11} className="mt-px shrink-0" />
          <span>
            Worth a look first — the repository already defines{" "}
            {item.possibly_related.slice(0, 5).map((name) => (
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

function Results({ result }) {
  const {
    where_it_stands: standing,
    milestones = [],
    tasks = [],
    already_there: dropped = [],
    code_was_read: codeRead,
    reason,
  } = result;

  if (reason) {
    return (
      <p className="px-4 pb-4 text-xs text-[var(--text-muted)]">
        This needs the model. {reason}
      </p>
    );
  }

  const nothing = !milestones.length && !tasks.length;

  return (
    <div className="space-y-4 px-4 pb-4">
      {standing ? (
        <Markdown className="text-xs text-[var(--text-secondary)]">{standing}</Markdown>
      ) : null}

      {!codeRead ? (
        <p className="text-[11px] text-[var(--warning)]">
          This project has no local folder, so proposals were checked against
          the board only — not against the code.
        </p>
      ) : null}

      {milestones.length ? (
        <div className="space-y-1">
          <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
            Milestones it does not have ({milestones.length})
          </p>
          <ul>
            {milestones.map((item) => (
              <Proposal key={item.title} item={item} icon={Flag} />
            ))}
          </ul>
        </div>
      ) : null}

      {tasks.length ? (
        <div className="space-y-1">
          <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
            Work it has not written down ({tasks.length})
          </p>
          <ul>
            {tasks.map((item) => (
              <Proposal key={item.title} item={item} icon={ListChecks} />
            ))}
          </ul>
        </div>
      ) : null}

      {nothing ? (
        <p className="text-xs text-[var(--text-muted)]">
          Nothing survived the check. Either the board already covers the
          work, or everything proposed turned out to exist — the list below
          says which.
        </p>
      ) : (
        <p className="text-[11px] text-[var(--text-muted)]">
          Raised as suggestions. Accepting one adds the milestone or task;
          nothing is added until you do.
        </p>
      )}

      {dropped.length ? (
        <details className="rounded border border-[var(--border)] bg-[var(--surface-2)]/50 px-3 py-2">
          <summary className="cursor-pointer text-[11px] font-medium text-[var(--text-secondary)]">
            Proposed and discarded for already existing ({dropped.length})
          </summary>
          <ul className="mt-2 space-y-1.5">
            {dropped.map((item) => (
              <li key={`${item.kind}-${item.title}`} className="flex items-start gap-1.5 text-xs">
                <XCircle size={13} className="mt-px shrink-0 text-[var(--text-muted)]" />
                <span>
                  <Badge tone="neutral">{item.kind}</Badge>{" "}
                  <span className="text-[var(--text-secondary)]">{item.title}</span>
                  <span className="text-[var(--text-muted)]">
                    {" "}
                    — {item.already_there_because}
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

export default function RoadmapGaps({ project, onProposed }) {
  const ai = useAiStatus();
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const body = await api.post(`/projects/${project.id}/roadmap`);
      setResult(body);
      if (onProposed) onProposed();
    } catch (err) {
      setError(err);
    } finally {
      setRunning(false);
    }
  };

  return (
    <Card>
      <CardHeader
        title="What is not planned yet"
        subtitle="Reads every milestone and task you already have — and the code, if there is a checkout — then proposes only what is on neither."
        action={
          <Button onClick={run} disabled={running || (ai && !ai.configured)}>
            <Search size={13} />
            {running ? "Reading…" : result ? "Look again" : "Find what is missing"}
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
          <Spinner label="Reading the board, then working out what is missing" />
        </div>
      ) : null}

      {result && !running ? <Results result={result} /> : null}

      {!result && !running && ai?.configured ? (
        <p className="px-4 pb-4 text-xs text-[var(--text-muted)]">
          Finished work counts as there. Anything already on the board or
          already built is discarded rather than proposed.
        </p>
      ) : null}
    </Card>
  );
}
