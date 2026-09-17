"use client";

import { useEffect, useRef, useState } from "react";
import { MessageSquare, Send, Square, X } from "lucide-react";

import { streamChat, useAiStatus } from "@/lib/ai";
import { Button } from "@/components/ui";

const OPENERS = [
  "What should I be working on today?",
  "What's slipping?",
  "Summarise this week for a standup",
];

function Bubble({ role, content, streaming }) {
  const mine = role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
          mine
            ? "bg-[var(--accent)] text-white"
            : "bg-[var(--surface-2)] text-[var(--text-primary)]"
        }`}
      >
        {content}
        {streaming ? <span className="ml-0.5 animate-pulse">▌</span> : null}
      </div>
    </div>
  );
}

/**
 * The assistant, reachable from anywhere.
 *
 * It reads the tracker and answers; it cannot change anything. That keeps the
 * same line the rest of the system draws -- a proposal is not a change until
 * you accept it -- and it means a conversation can be had freely without
 * wondering what it quietly did to your board.
 */
export default function Assistant() {
  const status = useAiStatus();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, streaming]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Esc closes, but only when nothing is mid-answer: losing a half-streamed
  // reply to a stray keypress is worse than having to click.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape" && !streaming) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, streaming]);

  useEffect(() => () => abortRef.current?.abort(), []);

  if (!status?.configured) return null;

  const ask = async (question) => {
    const text = (question ?? input).trim();
    if (!text || streaming) return;

    const history = [...messages, { role: "user", content: text }];
    setMessages(history);
    setInput("");
    setError(null);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    // The placeholder is appended once and then filled in place, so each
    // delta is one string concat rather than a new message in the list.
    setMessages([...history, { role: "assistant", content: "" }]);

    try {
      await streamChat({
        messages: history,
        signal: controller.signal,
        onDelta: (chunk) =>
          setMessages((current) => {
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
      // An answer that never arrived leaves an empty bubble behind.
      setMessages((current) =>
        current.filter((m, i) => i !== current.length - 1 || m.content || m.role !== "assistant"),
      );
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setStreaming(false);
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Ask the analyst"
        className="fixed bottom-4 right-4 z-50 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--accent)] text-white shadow-lg transition-transform hover:scale-105"
      >
        <MessageSquare size={20} />
      </button>
    );
  }

  return (
    <div
      role="dialog"
      aria-label="Analyst"
      className="fixed bottom-4 right-4 z-50 flex h-[min(34rem,calc(100dvh-2rem))] w-[min(26rem,calc(100vw-2rem))] flex-col rounded-xl border bg-[var(--surface-1)] shadow-2xl"
    >
      <header className="flex items-center gap-2 border-b px-3 py-2">
        <MessageSquare size={15} className="text-[var(--accent)]" />
        <span className="text-sm font-semibold">Analyst</span>
        <span className="text-[11px] text-[var(--text-muted)]">reads only</span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Close"
          className="ml-auto rounded p-1 hover:bg-[var(--surface-2)]"
        >
          <X size={15} />
        </button>
      </header>

      <div className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <div className="space-y-2">
            <p className="text-xs text-[var(--text-muted)]">
              It can see your projects, open tasks, recent notes and GitHub
              activity. It can&apos;t change anything.
            </p>
            {OPENERS.map((opener) => (
              <button
                key={opener}
                type="button"
                onClick={() => ask(opener)}
                className="block w-full rounded-lg border px-3 py-2 text-left text-xs hover:bg-[var(--surface-2)]"
              >
                {opener}
              </button>
            ))}
          </div>
        ) : null}

        {messages.map((message, index) => (
          <Bubble
            key={index}
            role={message.role}
            content={message.content}
            streaming={streaming && index === messages.length - 1 && message.role === "assistant"}
          />
        ))}

        {error ? (
          <p className="rounded-lg bg-[var(--surface-2)] px-3 py-2 text-xs text-[var(--text-muted)]">
            {error.message}
          </p>
        ) : null}

        <div ref={endRef} />
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          ask();
        }}
        className="flex items-end gap-2 border-t px-3 py-2"
      >
        <textarea
          ref={inputRef}
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends; Shift+Enter is a newline. The box is one line most
            // of the time, so making Enter mean "newline" would be surprising.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              ask();
            }
          }}
          placeholder="Ask about your work…"
          aria-label="Message"
          className="max-h-28 min-h-[2.25rem] flex-1 resize-none"
        />
        {streaming ? (
          <Button onClick={stop} aria-label="Stop">
            <Square size={13} />
          </Button>
        ) : (
          <Button type="submit" variant="primary" disabled={!input.trim()} aria-label="Send">
            <Send size={13} />
          </Button>
        )}
      </form>
    </div>
  );
}
