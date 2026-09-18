"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Flag, ListChecks, Sparkles, Wand2, X } from "lucide-react";

import { api } from "@/lib/api";
import { Badge, Button, Card, CardHeader, ErrorNote, Field, Spinner } from "@/components/ui";

/**
 * An idea, or a repo, into a project with milestones and tasks.
 *
 * Two buttons on purpose. "Draft it" shows the plan and writes nothing;
 * "Just build it" creates everything in one call. The preview is the default
 * because a plan you haven't read is a dozen tasks you'll have to delete,
 * but the impatient path is real and worth having.
 */
export default function Scaffold({ repo = null, onCreated }) {
  const router = useRouter();
  const [idea, setIdea] = useState("");
  const [plan, setPlan] = useState(null);
  const [dropped, setDropped] = useState(() => new Set());
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const run = async (apply) => {
    setBusy(apply ? "build" : "draft");
    setError(null);
    try {
      const body = await api.post("/ai/scaffold", {
        idea: idea.trim(),
        repo,
        apply,
        workspace: "personal",
      });
      if (body.applied) {
        onCreated?.(body.project_id);
        router.push(`/projects/${body.project_id}`);
      } else {
        setPlan(body.plan);
        setDropped(new Set());
      }
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const create = async () => {
    setBusy("apply");
    setError(null);
    try {
      // Posted back rather than regenerated, so what gets built is exactly
      // what was on screen — including the rows dropped.
      const kept = {
        ...plan,
        tasks: (plan.tasks || []).filter((_, i) => !dropped.has(i)),
      };
      const body = await api.post("/ai/scaffold/apply", {
        plan: kept,
        repo,
        workspace: "personal",
      });
      onCreated?.(body.project_id);
      router.push(`/projects/${body.project_id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const toggle = (index) =>
    setDropped((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  const keptCount = (plan?.tasks || []).length - dropped.size;

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <Field
          label={repo ? `Plan the work left on ${repo}` : "What do you want to build?"}
          hint={
            repo
              ? "It reads the README and plans what's left, not what's already done."
              : "A sentence is enough. A paragraph is better."
          }
        >
          <textarea
            rows={3}
            value={idea}
            onChange={(event) => setIdea(event.target.value)}
            placeholder="e.g. A small app that tracks my runs and tells me when I'm ramping up too fast"
          />
        </Field>

        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            variant="primary"
            busy={busy === "draft"}
            disabled={Boolean(busy) || (!idea.trim() && !repo)}
            onClick={() => run(false)}
          >
            <Sparkles size={14} /> Draft a plan
          </Button>
          <Button
            busy={busy === "build"}
            disabled={Boolean(busy) || (!idea.trim() && !repo)}
            onClick={() => run(true)}
          >
            <Wand2 size={14} /> Just build it
          </Button>
        </div>

        <ErrorNote error={error} onDismiss={() => setError(null)} />
      </Card>

      {busy === "draft" ? <Spinner label="Thinking it through" /> : null}

      {plan ? (
        <Card>
          <CardHeader
            title={plan.name}
            subtitle={plan.summary}
            action={
              <Button variant="primary" busy={busy === "apply"} onClick={create}>
                <Check size={13} /> Create {keptCount} task{keptCount === 1 ? "" : "s"}
              </Button>
            }
          />

          <div className="space-y-3 px-4 py-3">
            {plan.first_step ? (
              <p className="text-sm">
                <span className="font-medium">Start with:</span> {plan.first_step}
              </p>
            ) : null}
            {plan.objective ? (
              <p className="text-xs text-[var(--text-muted)]">{plan.objective}</p>
            ) : null}

            {plan.milestones?.length ? (
              <div>
                <p className="mb-1 flex items-center gap-1.5 text-xs font-medium">
                  <Flag size={12} /> Milestones
                </p>
                <ul className="space-y-0.5">
                  {plan.milestones.map((milestone) => (
                    <li key={milestone.title} className="text-xs">
                      {milestone.title}
                      {milestone.detail ? (
                        <span className="text-[var(--text-muted)]"> — {milestone.detail}</span>
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
                {(plan.tasks || []).map((task, index) => {
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
                      <div className={`min-w-0 text-xs ${out ? "opacity-40 line-through" : ""}`}>
                        <span className="font-medium">{task.title}</span>
                        {task.estimate_hours ? (
                          <Badge tone="neutral"> {task.estimate_hours}h</Badge>
                        ) : null}
                        {task.milestone ? (
                          <span className="text-[var(--text-muted)]"> · {task.milestone}</span>
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
          </div>
        </Card>
      ) : null}
    </div>
  );
}
