"use client";

import { useState } from "react";
import {
  Check,
  ChevronRight,
  Clock,
  Globe,
  ListChecks,
  Sparkles,
  Target,
  X,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAiStatus } from "@/lib/ai";
import { formatDate, formatHours } from "@/lib/format";
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

const CONFIDENCE_TONE = { high: "success", medium: "info", low: "warning" };

/** What the plan was built from, said plainly.
 *
 *  A thin plan is usually a thin input rather than a bad model, and the only
 *  way to tell them apart is to show what went in. */
function GroundedIn({ facts, hoursPerWeek }) {
  if (!facts) return null;
  const parts = [
    facts.readme ? "the README" : "no README",
    facts.commits ? `${facts.commits} commits` : "no commit history",
    facts.open_tasks ? `${facts.open_tasks} open tasks` : "an empty board",
    facts.notes ? `${facts.notes} notes` : null,
    facts.estimates_calibrated
      ? "your past estimates"
      : "estimates not yet calibrated against your logged time",
  ].filter(Boolean);

  return (
    <p className="text-xs text-[var(--text-muted)]">
      Read {parts.join(", ")}. Dates assume {hoursPerWeek}h a week and are
      computed here, not guessed by the model.
    </p>
  );
}

function Option({ option, chosen, onChoose, onApply, busy }) {
  const [dropped, setDropped] = useState(() => new Set());

  const toggle = (index) =>
    setDropped((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  const tasks = option.tasks || [];
  const kept = tasks.filter((_, i) => !dropped.has(i));
  const keptHours = kept.reduce((sum, t) => sum + Number(t.estimate_hours || 0), 0);

  return (
    <Card className={chosen ? "ring-1 ring-[var(--accent)]" : ""}>
      <CardHeader
        title={option.title}
        subtitle={option.what_it_is_for}
        action={
          <div className="flex items-center gap-1.5">
            {option.confidence ? (
              <Badge tone={CONFIDENCE_TONE[option.confidence] || "neutral"}>
                {option.confidence} confidence
              </Badge>
            ) : null}
            <Badge tone="neutral">
              {formatHours(option.total_hours)} · {tasks.length} tasks
            </Badge>
          </div>
        }
      />

      <div className="space-y-3 px-4 py-3">
        <p className="text-xs">
          <span className="font-medium">Why this project needs it: </span>
          <span className="text-[var(--text-muted)]">
            {option.why_this_project_needs_it}
          </span>
        </p>
        {option.when_to_pick_it ? (
          <p className="text-xs">
            <span className="font-medium">Pick this when: </span>
            <span className="text-[var(--text-muted)]">{option.when_to_pick_it}</span>
          </p>
        ) : null}
        {option.cost_of_not_doing_it ? (
          <p className="text-xs">
            <span className="font-medium">If you don&apos;t: </span>
            <span className="text-[var(--text-muted)]">
              {option.cost_of_not_doing_it}
            </span>
          </p>
        ) : null}

        {!chosen ? (
          <Button size="sm" onClick={onChoose}>
            <ChevronRight size={13} /> Look at the tasks
          </Button>
        ) : null}

        {chosen ? (
          <>
            {option.milestones?.length ? (
              <div>
                <p className="mb-1 flex items-center gap-1.5 text-xs font-medium">
                  <Target size={12} /> Milestones
                </p>
                <ul className="space-y-0.5">
                  {option.milestones.map((m) => (
                    <li key={m.title} className="text-xs">
                      {m.title}
                      <span className="text-[var(--text-muted)]">
                        {" — "}
                        {formatHours(m.estimated_hours)}, by {formatDate(m.due_date)}
                      </span>
                      {m.detail ? (
                        <p className="text-[11px] text-[var(--text-muted)]">{m.detail}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div>
              <p className="mb-1 flex items-center gap-1.5 text-xs font-medium">
                <ListChecks size={12} /> Tasks
                <span className="font-normal text-[var(--text-muted)]">
                  — drop anything you don&apos;t want
                </span>
              </p>
              <ul className="space-y-1">
                {tasks.map((task, index) => {
                  const out = dropped.has(index);
                  return (
                    <li key={`${task.title}-${index}`} className="flex items-start gap-2">
                      <button
                        type="button"
                        onClick={() => toggle(index)}
                        aria-label={out ? `Put back ${task.title}` : `Drop ${task.title}`}
                        className="mt-0.5 shrink-0 rounded border p-0.5 text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
                      >
                        {out ? <Check size={11} /> : <X size={11} />}
                      </button>
                      <div
                        className={`min-w-0 text-xs ${out ? "opacity-40 line-through" : ""}`}
                      >
                        <span className="font-medium">{task.title}</span>
                        <Badge tone="neutral"> {formatHours(task.estimate_hours)}</Badge>
                        {task.milestone ? (
                          <span className="text-[var(--text-muted)]">
                            {" · "}
                            {task.milestone}
                          </span>
                        ) : null}
                        {task.notes ? (
                          <p className="text-[var(--text-muted)]">{task.notes}</p>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>

            <Button
              variant="primary"
              busy={busy}
              disabled={!kept.length}
              onClick={() => onApply({ ...option, tasks: kept })}
            >
              <Check size={13} /> Create {kept.length} task
              {kept.length === 1 ? "" : "s"} ({formatHours(keptHours)})
            </Button>
          </>
        ) : null}
      </div>
    </Card>
  );
}

/**
 * What to do next on a project, as a choice rather than an instruction.
 *
 * Nothing here writes until you pick an option and press the button, and what
 * is written is exactly what is on screen — including the rows you dropped.
 */
export default function ProjectPlan({ project, onApplied }) {
  const status = useAiStatus();
  const [plan, setPlan] = useState(null);
  const [chosen, setChosen] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [applied, setApplied] = useState(null);
  const [research, setResearch] = useState(false);
  const [focus, setFocus] = useState("");
  const [hoursPerWeek, setHoursPerWeek] = useState(10);

  const generate = async () => {
    setBusy("plan");
    setError(null);
    setApplied(null);
    try {
      const body = await api.post(`/ai/projects/${project.id}/plan`, {
        research,
        focus: focus.trim() || null,
        hours_per_week: Number(hoursPerWeek) || 10,
      });
      setPlan(body);
      setChosen(body.options?.length === 1 ? 0 : null);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const apply = async (option) => {
    setBusy("apply");
    setError(null);
    try {
      const body = await api.post(`/ai/projects/${project.id}/plan/apply`, {
        option,
        hours_per_week: Number(hoursPerWeek) || 10,
      });
      setApplied(body);
      onApplied?.(body);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  if (status && !status.configured) {
    return (
      <EmptyState
        icon={Sparkles}
        title="Planning needs a key"
        description="Set ANTHROPIC_API_KEY in backend/.env to have the assistant read this project and propose what to do next."
      />
    );
  }

  return (
    <div className="space-y-4">
      <ErrorNote error={error} onDismiss={() => setError(null)} />

      <Card className="p-4">
        <CardHeader
          title="What should I do next?"
          subtitle="Reads the README, the commit history, what's already on the board and your notes, then proposes a few different directions."
        />
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Anything particular?" hint="Optional. e.g. security, get it deployable, stop the churn">
            <input
              value={focus}
              onChange={(event) => setFocus(event.target.value)}
              placeholder="Leave empty and it decides"
            />
          </Field>
          <Field label="Hours a week" hint="Turns the estimates into dates. Change it and the dates move.">
            <input
              type="number"
              min="1"
              max="80"
              value={hoursPerWeek}
              onChange={(event) => setHoursPerWeek(event.target.value)}
            />
          </Field>
        </div>

        <label className="mt-2 flex items-start gap-2 text-xs">
          <input
            type="checkbox"
            checked={research}
            onChange={(event) => setResearch(event.target.checked)}
            className="mt-0.5"
          />
          <span>
            <span className="flex items-center gap-1 font-medium">
              <Globe size={12} /> Search the web first
            </span>
            <span className="text-[var(--text-muted)]">
              Looks up current practice for this project&apos;s actual stack and
              cites what it read. Slower, costs more, and sends queries about
              this project to a search engine.
            </span>
          </span>
        </label>

        <div className="mt-3">
          <Button variant="primary" busy={busy === "plan"} onClick={generate}>
            <Sparkles size={14} /> {plan ? "Plan again" : "Plan the next stretch"}
          </Button>
        </div>
      </Card>

      {busy === "plan" ? (
        <Spinner label={research ? "Reading, then searching" : "Reading the project"} />
      ) : null}

      {applied ? (
        <Card className="p-4">
          <p className="text-sm">
            <span className="font-medium">Created.</span> {applied.tasks_created} task
            {applied.tasks_created === 1 ? "" : "s"} and {applied.milestones_created}{" "}
            milestone{applied.milestones_created === 1 ? "" : "s"} from{" "}
            <span className="font-medium">{applied.option}</span>
            {applied.finishes ? `, finishing around ${formatDate(applied.finishes)}` : ""}.
          </p>
        </Card>
      ) : null}

      {plan ? (
        <>
          <Card>
            <CardHeader title="Where this project is" subtitle={plan.reading} />
            <div className="space-y-2 px-4 py-3">
              <GroundedIn facts={plan.grounded_in} hoursPerWeek={plan.hours_per_week} />
              {plan.recommended ? (
                <p className="text-xs">
                  <span className="font-medium">Would pick: </span>
                  {plan.recommended}
                </p>
              ) : null}
              {plan.research?.sources?.length ? (
                <div className="text-xs">
                  <p className="font-medium">
                    Read {plan.research.sources.length} source
                    {plan.research.sources.length === 1 ? "" : "s"} in{" "}
                    {plan.research.searches} search
                    {plan.research.searches === 1 ? "" : "es"}:
                  </p>
                  <ul className="mt-0.5 space-y-0.5">
                    {plan.research.sources.map((s) => (
                      <li key={s.url} className="truncate">
                        <a
                          href={s.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[var(--accent)] hover:underline"
                        >
                          {s.title || s.url}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {plan.research?.error ? (
                <p className="text-xs text-[var(--text-muted)]">
                  The web search failed ({plan.research.error}), so this plan is
                  from the project alone.
                </p>
              ) : null}
            </div>
          </Card>

          {plan.options?.map((option, index) => (
            <Option
              key={option.title || index}
              option={option}
              chosen={chosen === index}
              onChoose={() => setChosen(index)}
              onApply={apply}
              busy={busy === "apply"}
            />
          ))}

          {plan.not_worth_doing?.length || plan.unknowns?.length ? (
            <Card>
              <CardHeader title="Worth knowing" />
              <div className="space-y-2 px-4 py-3 text-xs">
                {plan.not_worth_doing?.length ? (
                  <div>
                    <p className="font-medium">Looks worth doing, isn&apos;t</p>
                    <ul className="ml-4 list-disc text-[var(--text-muted)]">
                      {plan.not_worth_doing.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {plan.unknowns?.length ? (
                  <div>
                    <p className="flex items-center gap-1 font-medium">
                      <Clock size={12} /> Couldn&apos;t see
                    </p>
                    <ul className="ml-4 list-disc text-[var(--text-muted)]">
                      {plan.unknowns.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            </Card>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
