import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SUGGEST_DELAY, streamChat, useLiveSuggestions } from "@/lib/ai";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** A response whose body yields the given strings as separate network chunks. */
function streamOf(chunks) {
  const encoder = new TextEncoder();
  let index = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          index < chunks.length
            ? { done: false, value: encoder.encode(chunks[index++]) }
            : { done: true, value: undefined },
      }),
    },
  };
}

const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function collect() {
  const seen = [];
  return { seen, onDelta: (text) => seen.push(text) };
}

// --- The SSE parser ---------------------------------------------------------

describe("streaming an answer", () => {
  it("delivers the deltas in order", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      streamOf([frame("delta", "Hello"), frame("delta", " there"), frame("done", true)]),
    ));
    const { seen, onDelta } = collect();

    await streamChat({ messages: [{ role: "user", content: "hi" }], onDelta });

    expect(seen.join("")).toBe("Hello there");
  });

  it("holds back a frame that arrives in two pieces", async () => {
    // The real failure this guards: a chunk boundary lands mid-frame, and a
    // parser that reads each chunk on its own drops the text or throws.
    const whole = frame("delta", "indivisible");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      streamOf([whole.slice(0, 12), whole.slice(12)]),
    ));
    const { seen, onDelta } = collect();

    await streamChat({ messages: [], onDelta });

    expect(seen).toEqual(["indivisible"]);
  });

  it("does not deliver a frame that is still incomplete when the stream ends", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      streamOf([frame("delta", "kept"), 'event: delta\ndata: "truncated'])
    ));
    const { seen, onDelta } = collect();

    await streamChat({ messages: [], onDelta });

    expect(seen).toEqual(["kept"]);
  });

  it("surfaces a failure that arrives mid-stream, long after the 200", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      streamOf([frame("delta", "partial"), frame("error", "The model timed out")]),
    ));
    const { seen, onDelta } = collect();

    await expect(streamChat({ messages: [], onDelta })).rejects.toThrow(
      "The model timed out",
    );
    expect(seen).toEqual(["partial"]);
  });

  it("skips a frame it cannot parse rather than abandoning the answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      streamOf(["event: delta\ndata: {not json\n\n", frame("delta", "carried on")]),
    ));
    const { seen, onDelta } = collect();

    await streamChat({ messages: [], onDelta });

    expect(seen).toEqual(["carried on"]);
  });

  it("reports the API's own message when the request is refused outright", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      json: async () => ({ detail: "No ANTHROPIC_API_KEY is set." }),
    }));

    await expect(streamChat({ messages: [], onDelta: () => {} })).rejects.toThrow(
      "No ANTHROPIC_API_KEY is set.",
    );
  });

  it("says the backend isn't running when it can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(streamChat({ messages: [], onDelta: () => {} })).rejects.toThrow(
      /Is the backend running/,
    );
  });

  it("treats an abort as the caller's doing, not an error to show", async () => {
    const aborted = Object.assign(new Error("aborted"), { name: "AbortError" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(aborted));

    await expect(
      streamChat({ messages: [], onDelta: () => {} }),
    ).resolves.toBeUndefined();
  });
});

// --- Suggestions while typing ----------------------------------------------

function suggestionsMock(fields = { summary: "A tidier summary" }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ fields }),
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Type by re-rendering with a longer draft, then let the debounce expire. */
async function type(rerender, text, { settle = true } = {}) {
  rerender({ kind: "project", draft: { name: text } });
  if (settle) {
    await act(async () => {
      vi.advanceTimersByTime(SUGGEST_DELAY);
    });
  }
}

describe("suggestions while typing", () => {
  it("asks for nothing until there is enough of a draft to advise on", async () => {
    const fetchMock = suggestionsMock();
    vi.useFakeTimers();

    const { rerender } = renderHook((props) => useLiveSuggestions(props), {
      initialProps: { kind: "project", draft: { name: "" } },
    });
    await type(rerender, "short");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("asks once for a sentence, not once per keystroke", async () => {
    const fetchMock = suggestionsMock();
    vi.useFakeTimers();

    const { rerender } = renderHook((props) => useLiveSuggestions(props), {
      initialProps: { kind: "project", draft: { name: "" } },
    });
    for (const text of ["Forecasting m", "Forecasting mo", "Forecasting mod"]) {
      await type(rerender, text, { settle: false });
      await act(async () => {
        vi.advanceTimersByTime(100);
      });
    }
    await act(async () => {
      vi.advanceTimersByTime(SUGGEST_DELAY);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).draft).toEqual({
      name: "Forecasting mod",
    });
  });

  it("is loading exactly while the answer it holds is for an older draft", async () => {
    suggestionsMock();
    vi.useFakeTimers();

    const { result, rerender } = renderHook((props) => useLiveSuggestions(props), {
      initialProps: { kind: "project", draft: { name: "" } },
    });
    expect(result.current.loading).toBe(false);

    rerender({ kind: "project", draft: { name: "Forecasting model" } });
    await act(async () => {
      vi.advanceTimersByTime(SUGGEST_DELAY);
    });

    vi.useRealTimers();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.fields).toEqual({ summary: "A tidier summary" });
  });

  it("abandons a request when a newer draft supersedes it", async () => {
    // Otherwise a slow early answer can land after a fast later one and
    // replace good advice with staler advice.
    const aborts = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url, options) => {
        options.signal.addEventListener("abort", () => aborts.push(url));
        return new Promise(() => {});
      }),
    );
    vi.useFakeTimers();

    const { rerender } = renderHook((props) => useLiveSuggestions(props), {
      initialProps: { kind: "project", draft: { name: "" } },
    });
    await type(rerender, "Forecasting model");
    await type(rerender, "Forecasting model for the plant");

    expect(aborts).toHaveLength(1);
  });

  it("stays quiet once dismissed, however much more is typed", async () => {
    const fetchMock = suggestionsMock();
    vi.useFakeTimers();

    const { result, rerender } = renderHook((props) => useLiveSuggestions(props), {
      initialProps: { kind: "project", draft: { name: "" } },
    });
    act(() => result.current.dismiss());
    await type(rerender, "Forecasting model for the plant");

    expect(result.current.dismissed).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("asks for nothing at all when the assistant is switched off", async () => {
    const fetchMock = suggestionsMock();
    vi.useFakeTimers();

    const { rerender } = renderHook((props) => useLiveSuggestions(props), {
      initialProps: { kind: "project", draft: { name: "" }, enabled: false },
    });
    rerender({ kind: "project", draft: { name: "Forecasting model" }, enabled: false });
    await act(async () => {
      vi.advanceTimersByTime(SUGGEST_DELAY);
    });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
