import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RoadmapGaps from "@/components/RoadmapGaps";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("@/lib/ai", () => ({ useAiStatus: () => ({ configured: true }) }));

const PROJECT = { id: 3, name: "Demo" };

const RESULT = {
  board: { totals: { milestones: 1, tasks: 2 } },
  where_it_stands: "Design is done; nothing is shipped.",
  milestones: [
    {
      title: "Beta released to the first users",
      detail: "Ten people using it daily.",
      why: "There is no checkpoint between design and done.",
      checked: ["release_notes", "CHANGELOG"],
      verified: true,
      possibly_related: [],
    },
  ],
  tasks: [
    {
      title: "Add a health check endpoint",
      why: "Nothing says whether the service is up.",
      checked: ["healthz", "health_check"],
      verified: true,
      possibly_related: [],
      estimate_hours: 2,
      value: "medium",
      milestone: "Beta released to the first users",
    },
  ],
  already_there: [
    {
      kind: "task",
      title: "Draw the wireframes",
      already_there_because: 'task "Draw the wireframes" covers this and is already done',
    },
  ],
  code_was_read: true,
  suggestions: [{ id: 1, field: "task", proposed_value: "Add a health check endpoint" }],
  reason: null,
};

const run = async (result = RESULT) => {
  vi.mocked(api.post).mockResolvedValue(result);
  render(<RoadmapGaps project={PROJECT} />);
  await userEvent.click(
    await screen.findByRole("button", { name: /Find what is missing/i }),
  );
};

beforeEach(() => vi.clearAllMocks());

describe("before it runs", () => {
  it("promises that finished work counts as already there", () => {
    render(<RoadmapGaps project={PROJECT} />);
    expect(screen.getByText(/Finished work counts as there/)).toBeInTheDocument();
  });

  it("says it reads the board first", () => {
    render(<RoadmapGaps project={PROJECT} />);
    expect(
      screen.getByText(/Reads every milestone and task you already have/),
    ).toBeInTheDocument();
  });

  it("costs nothing until pressed", () => {
    render(<RoadmapGaps project={PROJECT} />);
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe("what it proposes", () => {
  it("separates milestones from tasks", async () => {
    await run();
    expect(
      await screen.findByText(/Milestones it does not have \(1\)/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Work it has not written down \(1\)/)).toBeInTheDocument();
  });

  it("shows the reasoning and the estimate", async () => {
    await run();
    expect(await screen.findByText(/no checkpoint between design and done/)).toBeInTheDocument();
    expect(screen.getByText("2h")).toBeInTheDocument();
  });

  it("says what a milestone is met by, and where a task belongs", async () => {
    await run();
    expect(await screen.findByText(/Ten people using it daily/)).toBeInTheDocument();
    expect(screen.getByText(/Belongs under/)).toBeInTheDocument();
  });

  it("reports both checks, board and code, separately", async () => {
    await run();
    expect(
      (await screen.findAllByText(/Checked against every milestone and task/)).length,
    ).toBe(2);
    expect(screen.getAllByText(/Searched the repository for/).length).toBe(2);
    expect(screen.getAllByText("healthz").length).toBe(1);
  });

  it("says when a proposal was not checked against the code", async () => {
    await run({
      ...RESULT,
      milestones: [],
      tasks: [{ title: "Something", why: "x", verified: false, checked: [] }],
    });
    expect(await screen.findByText(/Not checked against the code/)).toBeInTheDocument();
  });

  it("warns when the repository already defines something similar", async () => {
    await run({
      ...RESULT,
      milestones: [],
      tasks: [
        {
          ...RESULT.tasks[0],
          possibly_related: ["health_router", "healthcheck"],
        },
      ],
    });
    expect(await screen.findByText(/Worth a look first/)).toBeInTheDocument();
    expect(screen.getByText("healthcheck")).toBeInTheDocument();
  });

  it("says nothing was added until a suggestion is accepted", async () => {
    await run();
    expect(await screen.findByText(/nothing is added until you do/)).toBeInTheDocument();
  });
});

describe("what it discards", () => {
  it("shows everything dropped, with the reason", async () => {
    await run();
    expect(
      await screen.findByText(/Proposed and discarded for already existing \(1\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("Draw the wireframes")).toBeInTheDocument();
    expect(screen.getByText(/covers this and is already done/)).toBeInTheDocument();
  });

  it("says plainly when nothing survived", async () => {
    await run({ ...RESULT, milestones: [], tasks: [] });
    expect(await screen.findByText(/Nothing survived the check/)).toBeInTheDocument();
  });
});

describe("edges", () => {
  it("warns when there was no code to check against", async () => {
    await run({ ...RESULT, code_was_read: false });
    expect(
      await screen.findByText(/checked against the board only/),
    ).toBeInTheDocument();
  });

  it("explains itself when the model is unavailable", async () => {
    await run({ ...RESULT, reason: "No ANTHROPIC_API_KEY is set." });
    expect(await screen.findByText(/needs the model/)).toBeInTheDocument();
  });

  it("reports a failure rather than pretending it worked", async () => {
    vi.mocked(api.post).mockRejectedValue(new Error("Rate limited by the API."));
    render(<RoadmapGaps project={PROJECT} />);
    await userEvent.click(screen.getByRole("button", { name: /Find what is missing/i }));
    expect(await screen.findByText(/Rate limited/)).toBeInTheDocument();
  });
});
