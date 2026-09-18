"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { API_URL, ApiError, api } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";

/** How long typing has to stop before a suggestion is worth asking for.
 *  Short enough to feel live, long enough that a sentence costs one call
 *  rather than forty. */
export const SUGGEST_DELAY = 900;

/** Matches MIN_DRAFT_CHARS on the server, which is the real gate; this one
 *  just avoids the round trip. */
const MIN_DRAFT_CHARS = 12;

const DRAFT_FIELDS = [
  "name",
  "title",
  "summary",
  "objective",
  "definition_of_done",
  "notes",
];

function draftText(draft) {
  return DRAFT_FIELDS.map((key) => (draft?.[key] || "").trim())
    .filter(Boolean)
    .join(" ")
    .trim();
}

/**
 * Whether the assistant is switched on at all.
 *
 * Fetched once per page load and shared: several components ask, and the
 * answer can't change without a server restart. Failure is treated as "off"
 * rather than surfaced -- if the backend is unreachable the rest of the app
 * is already saying so, and a second error about the assistant adds nothing.
 */
let statusPromise = null;

export function useAiStatus() {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    let alive = true;
    statusPromise = statusPromise || api.get("/ai/status").catch(() => ({ configured: false }));
    statusPromise.then((value) => {
      if (alive) setStatus(value);
    });
    return () => {
      alive = false;
    };
  }, []);

  return status;
}

/**
 * Suggestions for a form that is being filled in.
 *
 * Fires on a pause rather than a keystroke, and abandons the previous request
 * when a newer one starts -- otherwise a slow early answer can land after a
 * fast later one and overwrite it with staler advice.
 */
export function useLiveSuggestions({ kind, draft, projectId, enabled = true }) {
  const [result, setResult] = useState({ key: null, fields: null, error: null });
  const [dismissed, setDismissed] = useState(false);
  const abortRef = useRef(null);

  const text = draftText(draft);
  const settled = useDebounced(text, SUGGEST_DELAY);
  const ready = enabled && !dismissed && settled.length >= MIN_DRAFT_CHARS;

  // The request is identified by the *settled* text, so a keystroke does not
  // start a new one. The draft itself is read from a ref when the call fires,
  // which keeps the payload current without making every character a new
  // request key.
  const key = ready ? `${kind}|${projectId ?? ""}|${settled}` : null;

  const draftRef = useRef(draft);
  useEffect(() => {
    draftRef.current = draft;
  });

  useEffect(() => {
    if (!key) return undefined;

    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;

    fetch(`${API_URL}/ai/suggest/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        draft: draftRef.current || {},
        project_id: projectId ?? null,
      }),
      signal: controller.signal,
      cache: "no-store",
    })
      .then(async (response) => {
        const body = await response.json().catch(() => null);
        if (!response.ok) {
          throw new ApiError(body?.detail || response.statusText, response.status);
        }
        return body;
      })
      .then((body) => setResult({ key, fields: body?.fields || {}, error: null }))
      .catch((error) => {
        // An abort is this hook superseding itself, not something to report.
        if (error?.name === "AbortError" || controller.signal.aborted) return;
        setResult({ key, fields: null, error });
      });

    return () => controller.abort();
  }, [key, kind, projectId]);

  // Derived rather than stored: "loading" is exactly "the answer I have isn't
  // for the question I'm asking", which the key already tells us. Keeping it
  // in state would mean setting state from inside the effect.
  const current = result.key === key;

  return {
    fields: current ? result.fields : null,
    error: current ? result.error : null,
    loading: Boolean(key) && !current,
    dismissed,
    dismiss: useCallback(() => {
      abortRef.current?.abort();
      setDismissed(true);
    }, []),
  };
}

/**
 * Streams a chat answer, calling `onDelta` as it arrives.
 *
 * Server-Sent Events are parsed by hand rather than with EventSource, which
 * only does GET and so can't carry the conversation.
 */
async function streamSSE(path, payload, { onDelta, signal }) {
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
      cache: "no-store",
    });
  } catch (error) {
    if (error?.name === "AbortError") return;
    throw new ApiError(`Can't reach the API at ${API_URL}. Is the backend running?`, 0);
  }

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

    // Frames are separated by a blank line; anything after the last one is a
    // partial frame and has to wait for the next chunk.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let event = null;
      let data = null;
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data = line.slice(6);
      }
      if (!event || data === null) continue;

      let parsed;
      try {
        parsed = JSON.parse(data);
      } catch {
        continue;
      }
      if (event === "delta") onDelta(parsed);
      // A failure mid-stream arrives as an event, because the 200 is long gone.
      if (event === "error") throw new ApiError(parsed, 502);
    }
  }
}

/** The floater: a throwaway conversation, nothing persisted. */
export function streamChat({ messages, onDelta, signal }) {
  return streamSSE("/ai/chat", { messages }, { onDelta, signal });
}

/** A saved brainstorm. Only the new message is sent — the history is on the
 *  server, which is what makes the session resumable from another tab. */
export function streamBrainstorm({ brainstormId, content, onDelta, signal }) {
  return streamSSE(
    `/ai/brainstorms/${brainstormId}/turn`,
    { content },
    { onDelta, signal },
  );
}
