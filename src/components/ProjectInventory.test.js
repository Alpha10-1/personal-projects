import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProjectInventory from "@/components/ProjectInventory";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("@/lib/ai", () => ({ useAiStatus: () => ({ configured: true }) }));

const PROJECT = { id: 3, name: "Demo" };

const SCAN = {
  counts: {
    tracked_files: 40,
    scanned: 40,
    source_files: 30,
    test_files: 8,
    lines: 4200,
    routes: 12,
    tables: 5,
  },
  truncated: false,
  areas: [{ area: "backend/app", files: 20, lines: 3000, tests: 0 }],
  modules: [],
  routes: [],
  tables: ["widgets"],
  pages: [],
  components: ["src/components/WidgetList.js"],
  env_vars: ["PP_DATA_DIR"],
  docs: [],
  configs: [],
};

const SURVEY = {
  inventory: SCAN,
  outline: {
    what_it_is: "A tracker for personal projects.",
    features: [
      {
        name: "Time tracking",
        what_it_does: "Records hours against tasks.",
        where: ["backend/app/routes/time_logs.py"],
        state: "complete",
      },
      { name: "Search", what_it_does: "Finds things.", where: [], state: "partial" },
    ],
  },
  note_id: 9,
  gaps: [
    {
      title: "Add a budget cap on model spend",
      why: "Spend is recorded but never capped.",
      checked: ["PP_AI_BUDGET", "budget_exceeded"],
      verified: true,
      possibly_related: [],
      size: "small",
      value: "high",
    },
  ],
  already_done: [
    {
      title: "Add an endpoint to list widgets",
      already_done_because: "`list_widgets` is already defined in this repository",
    },
  ],
  suggestions: [{ id: 1, proposed_value: "Add a budget cap on model spend" }],
  reason: null,
};

function server({ scan = SCAN, survey = SURVEY } = {}) {
  vi.mocked(api.get).mockResolvedValue(scan);
  vi.mocked(api.post).mockResolvedValue(survey);
}

beforeEach(() => vi.clearAllMocks());

async function run() {
  render(<ProjectInventory project={PROJECT} />);
  await userEvent.click(
    await screen.findByRole("button", { name: /Survey the repository/i }),
  );
}

describe("the inventory", () => {
  it("loads on its own and says it costs nothing", async () => {
    server();
    render(<ProjectInventory project={PROJECT} />);
    expect(await screen.findByText(/No model, no cost/)).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith("/projects/3/inventory");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("shows the counts and the areas", async () => {
    server();
    render(<ProjectInventory project={PROJECT} />);
    expect(await screen.findByText("30")).toBeInTheDocument();
    expect(screen.getByText("backend/app")).toBeInTheDocument();
    expect(screen.getByText("4,200")).toBeInTheDocument();
  });

  it("names the configuration the project reads", async () => {
    server();
    render(<ProjectInventory project={PROJECT} />);
    expect(await screen.findByText("PP_DATA_DIR")).toBeInTheDocument();
  });

  it("warns when only part of the repository was read", async () => {
    server({ scan: { ...SCAN, truncated: true } });
    render(<ProjectInventory project={PROJECT} />);
    expect(
      await screen.findByText(/unknown rather than absent/),
    ).toBeInTheDocument();
  });

  it("explains itself when there is no folder", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("This project has no local folder set."));
    render(<ProjectInventory project={PROJECT} />);
    expect(await screen.findByText("No local folder for this project")).toBeInTheDocument();
  });
});

describe("the survey", () => {
  it("promises up front that what exists will not be suggested", async () => {
    server();
    render(<ProjectInventory project={PROJECT} />);
    expect(
      await screen.findByText(/anything already built is discarded rather than suggested/),
    ).toBeInTheDocument();
  });

  it("lists the features and says where the outline went", async () => {
    server();
    await run();
    expect(await screen.findByText("Time tracking")).toBeInTheDocument();
    expect(screen.getByText(/Records hours against tasks/)).toBeInTheDocument();
    expect(screen.getByText(/Written to this project.s notes/)).toBeInTheDocument();
  });

  it("marks a feature that is only partly there", async () => {
    server();
    await run();
    expect(await screen.findByText("partial")).toBeInTheDocument();
  });

  it("shows each gap with what was searched for", async () => {
    server();
    await run();
    expect(await screen.findByText("Add a budget cap on model spend")).toBeInTheDocument();
    expect(screen.getByText("PP_AI_BUDGET")).toBeInTheDocument();
    expect(screen.getByText(/none found/)).toBeInTheDocument();
  });

  it("shows what was discarded for already existing", async () => {
    server();
    await run();
    expect(
      await screen.findByText(/Proposed and discarded for already existing \(1\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("Add an endpoint to list widgets")).toBeInTheDocument();
    expect(screen.getByText(/already defined in this repository/)).toBeInTheDocument();
  });

  it("warns on a gap that resembles something already defined", async () => {
    server({
      survey: {
        ...SURVEY,
        gaps: [
          {
            ...SURVEY.gaps[0],
            title: "Configure protected paths per project",
            possibly_related: ["DEFAULT_PROTECTED", "protected_patterns"],
          },
        ],
      },
    });
    await run();
    expect(await screen.findByText(/Worth a look first/)).toBeInTheDocument();
    expect(screen.getByText("protected_patterns")).toBeInTheDocument();
  });

  it("flags a gap that could not be verified", async () => {
    server({
      survey: {
        ...SURVEY,
        gaps: [{ title: "Something vague", why: "x", verified: false, checked: [] }],
      },
    });
    await run();
    expect(await screen.findByText(/Not verified/)).toBeInTheDocument();
  });

  it("says plainly when nothing survived the check", async () => {
    server({ survey: { ...SURVEY, gaps: [] } });
    await run();
    expect(await screen.findByText(/Nothing survived the check/)).toBeInTheDocument();
  });

  it("reports a failure rather than pretending it worked", async () => {
    vi.mocked(api.get).mockResolvedValue(SCAN);
    vi.mocked(api.post).mockRejectedValue(new Error("Rate limited by the API."));
    await run();
    expect(await screen.findByText(/Rate limited/)).toBeInTheDocument();
  });

  it("falls back to the inventory when the model is unavailable", async () => {
    server({ survey: { ...SURVEY, outline: null, reason: "No ANTHROPIC_API_KEY is set." } });
    await run();
    expect(await screen.findByText(/needs the model/)).toBeInTheDocument();
    expect(screen.getByText(/No ANTHROPIC_API_KEY is set/)).toBeInTheDocument();
  });
});
