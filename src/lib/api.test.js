import { afterEach, describe, expect, it, vi } from "vitest";

import { API_URL, ApiError, api } from "@/lib/api";

function respondWith({ status = 200, body = null, text } = {}) {
  const payload = text ?? (body === null ? "" : JSON.stringify(body));
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    statusText: status === 500 ? "Internal Server Error" : "",
    text: async () => payload,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("query strings", () => {
  it("drops empty filters so a cleared search box doesn't filter on ''", () => {
    const fetchMock = respondWith({ body: [] });

    api.get("/tasks", { q: "", status: "todo", project_id: null, overdue: false });

    const url = new URL(fetchMock.mock.calls[0][0]);
    expect(url.pathname).toBe("/tasks");
    expect([...url.searchParams]).toEqual([
      ["status", "todo"],
      ["overdue", "false"],
    ]);
  });
});

describe("failures", () => {
  it("says the backend isn't running rather than 'Failed to fetch'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(api.get("/tasks")).rejects.toThrow(
      `Can't reach the API at ${API_URL}. Is the backend running?`,
    );
    await expect(api.get("/tasks")).rejects.toMatchObject({ status: 0 });
  });

  it("names the field a validation error is about", async () => {
    respondWith({
      status: 422,
      body: { detail: [{ loc: ["body", "hours"], msg: "Input should be greater than 0" }] },
    });

    await expect(api.post("/time-logs", { hours: 0 })).rejects.toThrow(
      "hours: Input should be greater than 0",
    );
  });

  it("passes a plain API message through untouched", async () => {
    respondWith({ status: 404, body: { detail: "Unknown project" } });

    await expect(api.get("/projects/9")).rejects.toBeInstanceOf(ApiError);
    await expect(api.get("/projects/9")).rejects.toThrow("Unknown project");
  });

  it("falls back to the status text when the body isn't JSON", async () => {
    respondWith({ status: 500, text: "<html>nginx</html>" });

    await expect(api.get("/tasks")).rejects.toThrow("Internal Server Error");
  });
});

describe("empty responses", () => {
  it("treats a 204 as null rather than trying to parse it", async () => {
    respondWith({ status: 204 });

    await expect(api.del("/tasks/1")).resolves.toBeNull();
  });
});
