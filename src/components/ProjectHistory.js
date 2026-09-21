"use client";

import { useCallback, useRef, useState } from "react";
import { BookOpen, Layers, Send, Sparkles } from "lucide-react";

import { API_URL, ApiError, api } from "@/lib/api";
import { useAiStatus } from "@/lib/ai";
import { useAsync } from "@/lib/hooks";
import Markdown from "@/components/Markdown";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Spinner,
} from "@/components/ui";

const SUGGESTED = [
  "What is this project for?",
  "When did testing start, and what was there before?",
  "Which parts have been abandoned?",
];

function Bar({ value, max }) {
  const width = max ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <span className="inline-block h-1.5 w-full overflow-hidden rounded bg-[var(--surface-2)]">
      <span className="block h-full bg-[var(--accent)]" style={{ width: `${width}%` }} />
    </span>
  );
}

/**
 * What a repository's history says about the project.
 *
 * The timeline is arithmetic and loads on its own — it costs nothing and no
 * key is needed for it. Only the written summary and the questions call a
 * model, so those are buttons.
 */
export default function ProjectHistory({ project }) {
  const status = useAiStatus();
  const [summary, setSummary] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [asking, setAsking] = useState(false);
  const abortRef = useRef(null);

  const timeline = useAsync(
    useCallback(() => api.get(`/ai/projects/${project.id}/timeline`), [project.id]),
    [project.id],
  );
  const data = timeline.data;

  const deepSync = async () => {
    setBusy("sync");
    setError(null);
    try {
      await api.post(`/activity/deep-sync?repo=${encodeURIComponent(project.repo)}`);
      timeline.reload({ quiet: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const summarise = async () => {
    setBusy("summary");
    setError(null);
    try {
      setSummary(await api.post(`/ai/projects/${project.id}/history`));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const ask = async (text) => {
    const asked = (text ?? question).trim();
    if (!asked || asking) return;
    setAsking(true);
    setAnswer("");
    setError(null);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${API_URL}/ai/projects/${project.id}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: asked }),
        signal: controller.signal,
        cache: "no-store",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new ApiError(body?.detail || response.statusText, response.status);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          let event = null;
          let payload = null;
          for (const line of frame.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7);
            else if (line.startsWith("data: ")) payload = line.slice(6);
          }
          if (!event || payload === null) continue;
          const parsed = JSON.parse(payload);
          if (event === "delta") setAnswer((current) => current + parsed);
          if (event === "error") throw new ApiError(parsed, 502);
        }
      }
    } catch (err) {
      if (err?.name !== "AbortError") setError(err);
    } finally {
      setAsking(false);
      abortRef.current = null;
    }
  };

  if (!project.repo) return null;

  const maxCommits = Math.max(1, ...(data?.periods || []).map((p) => p.commits));
  const missing = data ? data.commits - data.detailed : 0;

  return (
    <div className="space-y-5">
      <ErrorNote error={error} onDismiss={() => setError(null)} />
      <ErrorNote error={timeline.error} onDismiss={() => timeline.reload()} />

      {timeline.loading && !data ? <Spinner label="Reading the history" /> : null}

      {data ? (
        <Card>
          <CardHeader
            title="How this project was built"
            subtitle={`${data.commits} commits, ${String(data.span.first).slice(0, 10)} to ${String(data.span.last).slice(0, 10)}`}
            action={
              status?.configured ? (
                <Button variant="primary" size="sm" busy={busy === "summary"} onClick={summarise}>
                  <BookOpen size={13} /> {summary ? "Read again" : "Read the history"}
                </Button>
              ) : null
            }
          />

          {missing > 0 ? (
            <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-2">
              <Layers size={13} className="text-[var(--text-muted)]" />
              <span className="text-xs text-[var(--text-muted)]">
                {missing} commit{missing === 1 ? "" : "s"} known only by their message —
                fetch which files they touched for a fuller picture.
              </span>
              <Button size="sm" busy={busy === "sync"} onClick={deepSync}>
                Fetch detail
              </Button>
            </div>
          ) : null}

          <div className="grid gap-5 px-4 py-3 lg:grid-cols-2">
            <div>
              <p className="mb-1.5 text-xs font-medium">By month</p>
              {data.periods.map((p) => (
                <div key={p.month} className="mb-1.5">
                  <div className="flex items-baseline justify-between text-xs">
                    <span>{p.month}</span>
                    <span className="text-[var(--text-muted)]">
                      {p.commits} commit{p.commits === 1 ? "" : "s"}
                      {p.additions ? ` · +${p.additions}/−${p.deletions}` : ""}
                    </span>
                  </div>
                  <Bar value={p.commits} max={maxCommits} />
                  {p.areas?.length ? (
                    <p className="mt-0.5 truncate text-[11px] text-[var(--text-muted)]">
                      {p.areas.join(", ")}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>

            <div>
              <p className="mb-1.5 text-xs font-medium">Where the work went</p>
              <table className="w-full text-xs">
                <tbody>
                  {data.areas.slice(0, 8).map((a) => (
                    <tr key={a.area} className="border-t border-[var(--border)] first:border-t-0">
                      <td className="py-1 pr-2 font-medium">{a.area}</td>
                      <td className="py-1 pr-2 text-right text-[var(--text-muted)]">
                        {a.commits}
                      </td>
                      <td className="py-1 text-right text-[var(--text-muted)]">
                        {String(a.first).slice(0, 7)} → {String(a.last).slice(0, 7)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
      ) : null}

      {summary ? (
        <Card>
          <CardHeader
            title="What it is, and how it got here"
            subtitle="Saved as a note on this project."
            action={
              summary.summary_suggested ? (
                <Badge tone="info">Summary change proposed</Badge>
              ) : null
            }
          />
          <Markdown className="px-4 py-3 text-sm">{summary.note}</Markdown>
        </Card>
      ) : null}

      {status?.configured && data ? (
        <Card>
          <CardHeader
            title="Ask about this project"
            subtitle="Answered from the commit history — what changed, where and when. Not the source code."
          />
          <div className="space-y-2 px-4 py-3">
            <div className="flex flex-wrap gap-1.5">
              {SUGGESTED.map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled={asking}
                  onClick={() => {
                    setQuestion(s);
                    ask(s);
                  }}
                  className="rounded-lg border px-2.5 py-1 text-xs hover:bg-[var(--surface-2)] disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                ask();
              }}
              className="flex items-end gap-2"
            >
              <textarea
                rows={1}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    ask();
                  }
                }}
                placeholder="e.g. Which parts haven't been touched since April?"
                aria-label="Question"
                className="max-h-28 min-h-[2.25rem] flex-1 resize-none"
              />
              <Button type="submit" variant="primary" disabled={!question.trim() || asking}>
                <Send size={13} />
              </Button>
            </form>

            {asking && !answer ? <Spinner label="Reading the history" /> : null}
            {answer ? (
              <div className="rounded-lg bg-[var(--surface-2)] px-3 py-2 text-sm">
                <Markdown>{answer}</Markdown>
                {asking ? <span className="ml-0.5 animate-pulse">▌</span> : null}
              </div>
            ) : null}
          </div>
        </Card>
      ) : null}

      {!status?.configured && data ? (
        <EmptyState
          icon={Sparkles}
          title="The written summary needs a key"
          description="The timeline above is computed and free. Set ANTHROPIC_API_KEY in backend/.env to read the history and ask questions about it."
        />
      ) : null}
    </div>
  );
}
