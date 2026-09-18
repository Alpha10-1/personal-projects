"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Lightbulb, Plus, Send, Sprout, Trash2 } from "lucide-react";

import { api } from "@/lib/api";
import { streamBrainstorm, useAiStatus } from "@/lib/ai";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  Select,
  Spinner,
} from "@/components/ui";

function NewSession({ open, onClose, projects, onCreated }) {
  const [topic, setTopic] = useState("");
  const [projectId, setProjectId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await api.post("/personal/brainstorms", {
        topic: topic.trim(),
        project_id: projectId ? Number(projectId) : null,
      });
      onCreated?.(created);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="What are you thinking about?">
      <form onSubmit={submit} className="space-y-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />
        <Field label="Topic">
          <input
            type="text"
            required
            autoFocus
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="e.g. Should the running app do route planning at all?"
          />
        </Field>
        <Field
          label="About a project"
          hint="Optional — the best ideas start before there's a project to file them under."
        >
          <Select
            includeBlank
            blankLabel="Nothing yet"
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            options={projects.map((p) => ({ value: p.id, label: p.name }))}
          />
        </Field>
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={saving} disabled={!topic.trim()}>
            Start
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function Session({ id, projects, onDeleted }) {
  // What the server has, plus what has been said since this view opened.
  // Derived rather than copied into state on load: the fetched history is
  // already state owned by useAsync, and mirroring it into a second copy is
  // what forces a setState inside an effect.
  const [added, setAdded] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [harvest, setHarvest] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const endRef = useRef(null);
  const abortRef = useRef(null);

  const session = useAsync(
    useCallback(() => api.get(`/personal/brainstorms/${id}`), [id]),
    [id],
  );

  const messages = [...(session.data?.messages || []), ...added];

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, streaming]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const ask = async () => {
    const text = input.trim();
    if (!text || streaming) return;

    setInput("");
    setError(null);
    setStreaming(true);
    setAdded((current) => [
      ...current,
      { id: `local-user-${Date.now()}`, role: "user", content: text },
      { id: `local-reply-${Date.now()}`, role: "assistant", content: "" },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamBrainstorm({
        brainstormId: id,
        content: text,
        signal: controller.signal,
        onDelta: (chunk) =>
          setAdded((current) => {
            const next = [...current];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, content: last.content + chunk };
            }
            return next;
          }),
      });
    } catch (err) {
      if (err?.name !== "AbortError") setError(err);
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  };

  const pull = async (apply) => {
    setBusy(apply ? "apply" : "harvest");
    setError(null);
    try {
      const body = await api.post(`/ai/brainstorms/${id}/harvest`, {
        apply,
        project_id: session.data?.project_id ?? null,
      });
      setHarvest(body);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!window.confirm("Delete this brainstorm and everything said in it?")) return;
    await api.del(`/personal/brainstorms/${id}`);
    onDeleted?.();
  };

  const projectName = projects.find((p) => p.id === session.data?.project_id)?.name;

  return (
    <Card>
      <CardHeader
        title={session.data?.topic || "…"}
        subtitle={projectName ? `About ${projectName}` : "Not filed under a project yet"}
        action={
          <div className="flex gap-1.5">
            <Button
              size="sm"
              busy={busy === "harvest"}
              disabled={!messages.length || streaming}
              onClick={() => pull(false)}
            >
              <Sprout size={13} /> What did we decide?
            </Button>
            <Button size="sm" onClick={remove} aria-label="Delete this brainstorm">
              <Trash2 size={13} />
            </Button>
          </div>
        }
      />

      <div className="max-h-[26rem] space-y-2 overflow-y-auto px-4 py-3">
        {session.loading && !session.data ? <Spinner /> : null}

        {messages.length === 0 && !session.loading ? (
          <p className="text-xs text-[var(--text-muted)]">
            It will argue with you rather than agree. Ask it what would sink
            this, or what it would build first.
          </p>
        ) : null}

        {messages.map((message, index) => (
          <div
            key={message.id ?? index}
            className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                message.role === "user"
                  ? "bg-[var(--accent)] text-white"
                  : "bg-[var(--surface-2)]"
              }`}
            >
              {message.content}
              {streaming && index === messages.length - 1 && message.role === "assistant" ? (
                <span className="ml-0.5 animate-pulse">▌</span>
              ) : null}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <ErrorNote error={error} onDismiss={() => setError(null)} />

      {harvest ? (
        <div className="border-t border-[var(--border)] px-4 py-3">
          {harvest.decisions?.length ? (
            <>
              <p className="text-xs font-medium">Decided</p>
              <ul className="mb-2 mt-0.5 space-y-0.5">
                {harvest.decisions.map((d) => (
                  <li key={d} className="text-xs text-[var(--text-muted)]">
                    — {d}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {harvest.open?.length ? (
            <>
              <p className="text-xs font-medium">Still open</p>
              <ul className="mb-2 mt-0.5 space-y-0.5">
                {harvest.open.map((d) => (
                  <li key={d} className="text-xs text-[var(--text-muted)]">
                    — {d}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {harvest.tasks?.length ? (
            <>
              <p className="text-xs font-medium">
                Tasks{" "}
                {harvest.applied ? (
                  <Badge tone="good">{harvest.tasks_created} created</Badge>
                ) : null}
              </p>
              <ul className="mt-0.5 space-y-0.5">
                {harvest.tasks.map((t) => (
                  <li key={t.title} className="text-xs">
                    {t.title}
                    {t.estimate_hours ? (
                      <span className="text-[var(--text-muted)]"> · {t.estimate_hours}h</span>
                    ) : null}
                  </li>
                ))}
              </ul>
              {!harvest.applied ? (
                <Button
                  className="mt-2"
                  size="sm"
                  variant="primary"
                  busy={busy === "apply"}
                  onClick={() => pull(true)}
                >
                  Add these to the project
                </Button>
              ) : null}
            </>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">
              Nothing was settled yet — which is a fair answer for a
              conversation that hasn&apos;t landed anywhere.
            </p>
          )}
        </div>
      ) : null}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          ask();
        }}
        className="flex items-end gap-2 border-t border-[var(--border)] px-4 py-2"
      >
        <textarea
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              ask();
            }
          }}
          placeholder="Think out loud…"
          aria-label="Message"
          className="max-h-28 min-h-[2.25rem] flex-1 resize-none"
        />
        <Button type="submit" variant="primary" disabled={!input.trim() || streaming}>
          <Send size={13} />
        </Button>
      </form>
    </Card>
  );
}

/**
 * Saved thinking sessions.
 *
 * Kept, unlike the floater, because the useful part of a brainstorm is
 * usually the third exchange rather than the first — and because what comes
 * out of one should be able to become tasks without retyping it.
 */
export default function BrainstormPanel({ projects }) {
  const status = useAiStatus();
  const [openId, setOpenId] = useState(null);
  const [creating, setCreating] = useState(false);

  const sessions = useAsync(useCallback(() => api.get("/personal/brainstorms"), []), []);
  const reload = () => sessions.reload({ quiet: true });

  if (!status?.configured) {
    return (
      <Card>
        <CardHeader title="Brainstorms" />
        <EmptyState
          icon={Lightbulb}
          title="The assistant is switched off"
          description="Set ANTHROPIC_API_KEY in backend/.env to think things through here."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Brainstorms"
          subtitle="Saved, so you can come back to one."
          action={
            <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
              <Plus size={13} /> New
            </Button>
          }
        />
        {sessions.loading && !sessions.data ? <Spinner /> : null}
        {(sessions.data || []).length ? (
          (sessions.data || []).map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setOpenId(item.id === openId ? null : item.id)}
              className={`flex w-full items-center gap-3 border-t border-[var(--border)] px-4 py-2.5 text-left first:border-t-0 hover:bg-[var(--surface-2)] ${
                item.id === openId ? "bg-[var(--surface-2)]" : ""
              }`}
            >
              <Lightbulb size={13} className="shrink-0 text-[var(--accent)]" />
              <span className="min-w-0 flex-1 truncate text-sm">{item.topic}</span>
              <span className="shrink-0 text-xs text-[var(--text-muted)]">
                {item.message_count} message{item.message_count === 1 ? "" : "s"}
              </span>
              <span className="shrink-0 text-xs text-[var(--text-muted)]">
                {formatDate(item.updated_at)}
              </span>
            </button>
          ))
        ) : sessions.data ? (
          <EmptyState
            icon={Lightbulb}
            title="Nothing in progress"
            description="Start one to think an idea through before it becomes a project."
          />
        ) : null}
      </Card>

      {openId ? (
        <Session
          key={openId}
          id={openId}
          projects={projects}
          onDeleted={() => {
            setOpenId(null);
            reload();
          }}
        />
      ) : null}

      {creating ? (
        <NewSession
          open
          projects={projects}
          onClose={() => setCreating(false)}
          onCreated={(created) => {
            reload();
            setOpenId(created.id);
          }}
        />
      ) : null}
    </div>
  );
}
