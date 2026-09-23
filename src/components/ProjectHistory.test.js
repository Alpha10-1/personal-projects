/**
 * What a repository's history says about a project.
 *
 * The split is the point: the timeline is arithmetic over stored commits
 * and loads on its own, while the written summary and the questions cost
 * money and are buttons. A change that quietly made the timeline paid, or
 * the summary automatic, is the failure worth catching.
 *
 * Also the file where the duplicate SSE parser lived. It uses the shared,
 * tested one now, so the streaming test here is about this component's part
 * of it -- the question going out and the answer arriving.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProjectHistory from "@/components/ProjectHistory";
import { api } from "@/lib/api";
import { streamProjectAsk } from "@/lib/ai";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const status = { configured: true };
vi.mock("@/lib/ai", () => ({
  useAiStatus: () => status,
  streamProjectAsk: vi.fn(),
}));

const PROJECT = { id: 3, name: "Demo", repo: "owner/demo" };

const TIMELINE = {
  commits: 42,
  detailed: 40,
  span: { first: "2026-06-01T09:00:00", last: "2026-09-21T13:00:00" },
  periods: [
    { month: "2026-08", commits: 12, additions: 400, deletions: 50, areas: ["backend"] },
    { month: "2026-09", commits: 30, additions: 900, deletions: 120, areas: ["frontend"] },
  ],
  areas: [{ area: "backend", commits: 20, first: "2026-06-01", last: "2026-09-21" }],
};

beforeEach(() => {
  vi.clearAllMocks();
  status.configured = true;
  vi.mocked(api.get).mockResolvedValue(TIMELINE);
  vi.mocked(api.post).mockResolvedValue({ note: "It started as a tracker.", summary_suggested: true });
  vi.mocked(streamProjectAsk).mockImplementation(async ({ onDelta }) => {
    onDelta("Testing started ");
    onDelta("in August.");
  });
});

describe("the timeline", () => {
  it("loads on its own, because it costs nothing", async () => {
    render(<ProjectHistory project={PROJECT} />);
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith("/ai/projects/3/timeline"),
    );
    expect(api.post).not.toHaveBeenCalled();
  });

  it("shows the span and the shape of the work", async () => {
    render(<ProjectHistory project={PROJECT} />);
    expect(await screen.findByText(/42 commits/)).toBeInTheDocument();
    expect(screen.getByText("2026-08")).toBeInTheDocument();
    expect(screen.getAllByText(/backend/).length).toBeGreaterThan(0);
  });

  it("offers to fetch the detail it is missing", async () => {
    render(<ProjectHistory project={PROJECT} />);
    // 42 commits, 40 with file lists: the other two are known only by
    // their message, and the numbers above are poorer for it.
    expect(await screen.findByText(/2 commits known only by their message/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Fetch detail/i }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/activity/deep-sync?repo=owner%2Fdemo",
      ),
    );
  });

  it("says nothing about missing detail when there is none", async () => {
    vi.mocked(api.get).mockResolvedValue({ ...TIMELINE, detailed: 42 });
    render(<ProjectHistory project={PROJECT} />);
    await screen.findByText(/42 commits/);
    expect(screen.queryByText(/known only by their message/)).not.toBeInTheDocument();
  });

  it("renders nothing at all without a repository", () => {
    // The timeline fetch still fires -- hooks run before the early return --
    // and answers with nothing to show. What matters is that the tab does
    // not offer a history for a project that has none.
    const { container } = render(
      <ProjectHistory project={{ id: 3, name: "Demo", repo: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("the written summary", () => {
  it("is a button, not something that happens", async () => {
    render(<ProjectHistory project={PROJECT} />);
    await screen.findByText(/42 commits/);
    expect(api.post).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /Read the history/i }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/ai/projects/3/history"),
    );
    expect(await screen.findByText(/It started as a tracker/)).toBeInTheDocument();
  });

  it("says when a change to the project's summary was proposed", async () => {
    render(<ProjectHistory project={PROJECT} />);
    await screen.findByText(/42 commits/);
    await userEvent.click(screen.getByRole("button", { name: /Read the history/i }));
    expect(await screen.findByText(/Summary change proposed/)).toBeInTheDocument();
  });

  it("is not offered at all without a key", async () => {
    status.configured = false;
    render(<ProjectHistory project={PROJECT} />);
    await screen.findByText(/42 commits/);
    expect(screen.queryByRole("button", { name: /Read the history/i })).not.toBeInTheDocument();
  });

  it("reports a failure rather than an empty summary", async () => {
    vi.mocked(api.post).mockRejectedValue(new Error("No ANTHROPIC_API_KEY is set."));
    render(<ProjectHistory project={PROJECT} />);
    await screen.findByText(/42 commits/);
    await userEvent.click(screen.getByRole("button", { name: /Read the history/i }));
    expect(await screen.findByText(/ANTHROPIC_API_KEY/)).toBeInTheDocument();
  });
});

describe("asking a question", () => {
  const ask = async (text = "when did testing start?") => {
    render(<ProjectHistory project={PROJECT} />);
    await screen.findByText(/42 commits/);
    const box = screen.getByRole("textbox");
    await userEvent.type(box, text);
    await userEvent.keyboard("{Enter}");
  };

  it("sends the question and streams the answer back", async () => {
    await ask();
    await waitFor(() => expect(streamProjectAsk).toHaveBeenCalled());
    expect(vi.mocked(streamProjectAsk).mock.calls.at(-1)[0]).toMatchObject({
      projectId: 3,
      question: "when did testing start?",
    });
    expect(await screen.findByText(/Testing started in August/)).toBeInTheDocument();
  });

  it("sends nothing for an empty question", async () => {
    render(<ProjectHistory project={PROJECT} />);
    await screen.findByText(/42 commits/);
    await userEvent.click(screen.getByRole("textbox"));
    await userEvent.keyboard("{Enter}");
    expect(streamProjectAsk).not.toHaveBeenCalled();
  });

  it("treats being stopped as a decision, not a failure", async () => {
    const aborted = Object.assign(new Error("aborted"), { name: "AbortError" });
    vi.mocked(streamProjectAsk).mockRejectedValue(aborted);
    await ask();
    await waitFor(() => expect(streamProjectAsk).toHaveBeenCalled());
    expect(screen.queryByText(/aborted/)).not.toBeInTheDocument();
  });

  it("reports a real failure", async () => {
    vi.mocked(streamProjectAsk).mockRejectedValue(new Error("Rate limited."));
    await ask();
    expect(await screen.findByText(/Rate limited/)).toBeInTheDocument();
  });
});
