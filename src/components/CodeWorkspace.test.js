/**
 * The working copy: branch, changes, the file tree and search.
 *
 * Two things here are easy to get quietly wrong and worth pinning down.
 * The poll has to be quiet -- a spinner every four seconds is worse than no
 * polling -- and a project with no checkout has to say so rather than
 * render an empty tree that looks like an empty repository.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CodeWorkspace from "@/components/CodeWorkspace";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
// Each has its own tests; this file is about the shell around them.
vi.mock("@/components/CodeEditor", () => ({
  default: ({ path }) => <div>editing {path}</div>,
}));
vi.mock("@/components/CodeChanges", () => ({ default: () => <div>changes panel</div> }));
vi.mock("@/components/AgentRuns", () => ({ default: () => <div>agent panel</div> }));

const PROJECT = { id: 3, name: "Demo" };

const STATE = {
  available: true,
  root: "C:/code/demo",
  branch: "main",
  detached: false,
  last_commit: { sha: "abc1234", subject: "Read the board first" },
  changes: [
    { path: "backend/app/board.py", state: "modified", staged: false, code: " M" },
  ],
};

const TREE = {
  entries: [
    { path: "backend", name: "backend", type: "dir" },
    { path: "README.md", name: "README.md", type: "file" },
  ],
};

function respond(state = STATE) {
  vi.mocked(api.get).mockImplementation((url) => {
    if (url.endsWith("/workspace")) return Promise.resolve(state);
    if (url.includes("/tree")) return Promise.resolve(TREE);
    if (url.includes("/diff")) return Promise.resolve({ diff: "--- a\n+++ b\n" });
    if (url.includes("/search")) return Promise.resolve({ hits: [] });
    return Promise.resolve({});
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  respond();
});

afterEach(() => vi.useRealTimers());

describe("what it shows", () => {
  it("names the branch and the last commit", async () => {
    render(<CodeWorkspace project={PROJECT} />);
    expect(await screen.findByText("main")).toBeInTheDocument();
    expect(screen.getByText(/Read the board first/)).toBeInTheDocument();
  });

  it("says detached rather than showing a branch that is not one", async () => {
    respond({ ...STATE, detached: true, branch: null });
    render(<CodeWorkspace project={PROJECT} />);
    expect(await screen.findByText("detached HEAD")).toBeInTheDocument();
  });

  it("copes with a repository that has no commits yet", async () => {
    respond({ ...STATE, last_commit: null });
    render(<CodeWorkspace project={PROJECT} />);
    expect(await screen.findByText(/No commits yet/)).toBeInTheDocument();
  });

  it("lists what has changed", async () => {
    render(<CodeWorkspace project={PROJECT} />);
    expect(await screen.findByText("backend/app/board.py")).toBeInTheDocument();
  });

  it("shows the file tree", async () => {
    render(<CodeWorkspace project={PROJECT} />);
    expect(await screen.findByText("README.md")).toBeInTheDocument();
  });
});

describe("a project with no checkout", () => {
  it("explains rather than showing an empty tree", async () => {
    respond({ available: false, reason: "No local folder is set." });
    render(<CodeWorkspace project={PROJECT} />);

    expect(await screen.findByText(/No local folder for this project/)).toBeInTheDocument();
    expect(screen.getByText("No local folder is set.")).toBeInTheDocument();
  });

  it("does not ask for a tree or a diff it cannot have", async () => {
    respond({ available: false, reason: "No local folder is set." });
    render(<CodeWorkspace project={PROJECT} />);
    await screen.findByText(/No local folder for this project/);

    const asked = vi.mocked(api.get).mock.calls.map(([url]) => url);
    expect(asked.some((url) => url.includes("/tree"))).toBe(false);
    expect(asked.some((url) => url.includes("/diff"))).toBe(false);
  });

  it("falls back to a general explanation when the server gave no reason", async () => {
    respond({ available: false });
    render(<CodeWorkspace project={PROJECT} />);
    expect(await screen.findByText(/Set the project's local folder/)).toBeInTheDocument();
  });
});

describe("keeping up with the checkout", () => {
  it("re-reads on a timer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<CodeWorkspace project={PROJECT} />);
    await screen.findByText("main");

    const before = vi.mocked(api.get).mock.calls.filter(([u]) =>
      u.endsWith("/workspace"),
    ).length;
    await vi.advanceTimersByTimeAsync(10_000);
    const after = vi.mocked(api.get).mock.calls.filter(([u]) =>
      u.endsWith("/workspace"),
    ).length;

    expect(after).toBeGreaterThan(before);
  });

  it("polls quietly, without replacing the page with a spinner", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<CodeWorkspace project={PROJECT} />);
    await screen.findByText("main");

    await vi.advanceTimersByTimeAsync(10_000);
    // Still the branch, not "Reading the checkout".
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.queryByText(/Reading the checkout/)).not.toBeInTheDocument();
  });

  it("stops polling once it is gone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { unmount } = render(<CodeWorkspace project={PROJECT} />);
    await screen.findByText("main");

    unmount();
    const before = vi.mocked(api.get).mock.calls.length;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(vi.mocked(api.get).mock.calls.length).toBe(before);
  });
});

describe("opening a file in VS Code", () => {
  it("asks the server to open it", async () => {
    vi.mocked(api.post).mockResolvedValue({ ok: true });
    render(<CodeWorkspace project={PROJECT} />);
    await screen.findByText("backend/app/board.py");

    await userEvent.click(screen.getByRole("button", { name: /Open repo/i }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/projects/3/workspace/open",
        expect.objectContaining({ path: expect.any(String) }),
      ),
    );
  });

  it("offers the deep link when there is no CLI to call", async () => {
    // A 503 means `code` is not on the PATH. The link needs no server at
    // all, so reporting the failure without it would be half an answer.
    vi.mocked(api.post).mockRejectedValue(new Error("VS Code was not found."));
    render(<CodeWorkspace project={PROJECT} />);
    await screen.findByText("backend/app/board.py");

    await userEvent.click(screen.getByRole("button", { name: /Open repo/i }));

    expect(await screen.findByText(/VS Code was not found/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /vscode:\/\/ link/i });
    expect(link.getAttribute("href")).toMatch(/^vscode:\/\/file\//);
  });
});

describe("failure", () => {
  it("reports it rather than showing an empty workspace", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("The repository could not be read."));
    render(<CodeWorkspace project={PROJECT} />);
    expect(await screen.findByText(/could not be read/)).toBeInTheDocument();
  });
});
