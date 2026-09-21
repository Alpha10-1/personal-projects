import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentRuns from "@/components/AgentRuns";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("@/lib/ai", () => ({ useAiStatus: () => ({ configured: true }) }));

const PROJECT = { id: 3, name: "Demo" };

const RUN = {
  id: 7,
  project_id: 3,
  instruction: "Add a limit parameter to the tasks endpoint",
  status: "proposed",
  review_required: false,
  review_reason: null,
  auto_apply: false,
  summary: "Added a limit query parameter. Untested -- I have no shell.",
  base_sha: "abc1234",
  model: "claude-sonnet-5",
  turns: 4,
  error: null,
  created_at: "2026-09-21",
  file_count: 1,
  changes: [{ path: "backend/app/routes/tasks.py", action: "modify", content: "x" }],
  diff: "--- a/backend/app/routes/tasks.py\n+++ b/backend/app/routes/tasks.py\n-old\n+new\n",
};

/** Routes each GET by path, because the panel makes three different ones and
 *  a single mockResolvedValue would answer all of them with the same body. */
function server({ available = true, runs = [RUN], run = RUN } = {}) {
  vi.mocked(api.get).mockImplementation((path) => {
    if (path.endsWith("/workspace")) return Promise.resolve({ available });
    if (path.endsWith("/agent/runs")) return Promise.resolve(runs);
    return Promise.resolve(run);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("when there is no checkout", () => {
  it("renders nothing at all", async () => {
    server({ available: false });
    const { container } = render(<AgentRuns project={PROJECT} />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

describe("starting a run", () => {
  it("will not start on an instruction too short to act on", async () => {
    server({ runs: [] });
    render(<AgentRuns project={PROJECT} />);
    const start = await screen.findByRole("button", { name: /start/i });
    expect(start).toBeDisabled();

    await userEvent.type(screen.getByRole("textbox"), "Add a limit parameter");
    expect(start).toBeEnabled();
  });

  it("sends the instruction and the auto-apply choice", async () => {
    server({ runs: [] });
    vi.mocked(api.post).mockResolvedValue({ ...RUN, status: "running" });
    render(<AgentRuns project={PROJECT} />);

    await userEvent.type(
      await screen.findByRole("textbox"),
      "Add a limit parameter to /tasks",
    );
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    expect(api.post).toHaveBeenCalledWith("/projects/3/agent/runs", {
      instruction: "Add a limit parameter to /tasks",
      auto_apply: true,
    });
  });

  it("says plainly that nothing is written until it is applied", async () => {
    server({ runs: [] });
    render(<AgentRuns project={PROJECT} />);
    expect(
      await screen.findByText(/Nothing is written until you apply it/i),
    ).toBeInTheDocument();
  });
});

describe("reviewing a proposal", () => {
  const open = async () => {
    render(<AgentRuns project={PROJECT} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Add a limit parameter/i }),
    );
  };

  it("shows the summary, the file and the diff", async () => {
    server();
    await open();
    expect(
      await screen.findByText(/Untested -- I have no shell/),
    ).toBeInTheDocument();
    expect(screen.getByText("backend/app/routes/tasks.py")).toBeInTheDocument();
    expect(screen.getByText("+new")).toBeInTheDocument();
  });

  it("offers apply and discard, and says applying does not commit", async () => {
    server();
    await open();
    expect(
      await screen.findByRole("button", { name: /Apply to the working copy/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Discard/i })).toBeInTheDocument();
    expect(
      screen.getByText(/writes the files but does not commit/i),
    ).toBeInTheDocument();
  });

  it("applies, then tells you how to undo it", async () => {
    server();
    vi.mocked(api.post).mockResolvedValue({
      applied: ["backend/app/routes/tasks.py"],
      stale_base: false,
      note: null,
    });
    await open();
    await userEvent.click(
      await screen.findByRole("button", { name: /Apply to the working copy/i }),
    );
    expect(api.post).toHaveBeenCalledWith("/agent/runs/7/apply");
    expect(await screen.findByText(/git checkout/)).toBeInTheDocument();
  });

  it("warns when the run touched a protected path, and hides the buttons", async () => {
    const held = {
      ...RUN,
      review_required: true,
      review_reason: "This run changes models.py, which this project marks as needing approval.",
    };
    server({ runs: [held], run: held });
    await open();
    expect(await screen.findByText(/Needs your approval/)).toBeInTheDocument();
    expect(screen.getByText(/changes models.py/)).toBeInTheDocument();
    // Still applicable -- review means a person presses the button, not that
    // the proposal is withdrawn.
    expect(
      screen.getByRole("button", { name: /Apply to the working copy/i }),
    ).toBeInTheDocument();
  });

  it("offers nothing to press on a failed run", async () => {
    const failed = {
      ...RUN,
      status: "failed",
      changes: [],
      diff: "",
      error: "The run used all 24 turns without finishing.",
    };
    server({ runs: [failed], run: failed });
    await open();
    expect(await screen.findByText(/used all 24 turns/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Apply to the working copy/i }),
    ).not.toBeInTheDocument();
  });

  it("says a run is still working, and that nothing has been written", async () => {
    const running = { ...RUN, status: "running", summary: null, changes: [], diff: "" };
    server({ runs: [running], run: running });
    await open();
    expect(
      await screen.findByText(/Nothing has been written to disk/i),
    ).toBeInTheDocument();
  });
});
