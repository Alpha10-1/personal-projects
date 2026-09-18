import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProjectPlan from "@/components/ProjectPlan";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { post: vi.fn(), get: vi.fn() } }));
vi.mock("@/lib/ai", () => ({ useAiStatus: () => ({ configured: true }) }));

const PLAN = {
  project_id: 1,
  reading: "A working app with no tests.",
  recommended: "Harden the core — cheapest thing with a real downside.",
  hours_per_week: 10,
  grounded_in: {
    readme: true,
    commits: 53,
    commits_detailed: 53,
    open_tasks: 0,
    notes: 1,
    estimates_calibrated: false,
  },
  research: null,
  not_worth_doing: [],
  unknowns: [],
  options: [
    {
      title: "Harden the core",
      what_it_is_for: "Make the main flow provably correct.",
      why_this_project_needs_it: "marksToAPS.js changed in 15 commits, no test file.",
      confidence: "high",
      total_hours: 8,
      milestones: [{ title: "Tests exist", estimated_hours: 8, due_date: "2026-10-01" }],
      tasks: [
        { title: "Unit tests for APS", estimate_hours: 3, milestone: "Tests exist" },
        { title: "Unit tests for matching", estimate_hours: 3 },
        { title: "Spot-check the data", estimate_hours: 2 },
      ],
    },
    {
      title: "Ship the admin panel",
      what_it_is_for: "Finish what the README promises.",
      why_this_project_needs_it: "The README describes it; no commit builds it.",
      confidence: "medium",
      total_hours: 12,
      milestones: [],
      tasks: [{ title: "Build the editor", estimate_hours: 12 }],
    },
  ],
};

const project = { id: 1, name: "Course finder", repo: "me/courses" };

beforeEach(() => {
  vi.mocked(api.post).mockReset();
});

async function planIt(user) {
  vi.mocked(api.post).mockResolvedValueOnce(PLAN);
  await user.click(screen.getByRole("button", { name: /Plan the next stretch/ }));
  await screen.findByText("Harden the core");
}

describe("asking for a plan", () => {
  it("does not search the web unless asked", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);

    await planIt(user);

    expect(api.post).toHaveBeenCalledWith(
      "/ai/projects/1/plan",
      expect.objectContaining({ research: false }),
    );
  });

  it("sends the focus and the weekly hours it was given", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);

    await user.type(screen.getByPlaceholderText(/Leave empty/), "security");
    await user.clear(screen.getByLabelText(/Hours a week/));
    await user.type(screen.getByLabelText(/Hours a week/), "4");
    await user.click(screen.getByLabelText(/Search the web first/));
    await planIt(user);

    expect(api.post).toHaveBeenCalledWith("/ai/projects/1/plan", {
      research: true,
      focus: "security",
      hours_per_week: 4,
    });
  });

  it("shows every option rather than one answer", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);

    await planIt(user);

    expect(screen.getByText("Harden the core")).toBeInTheDocument();
    expect(screen.getByText("Ship the admin panel")).toBeInTheDocument();
  });

  it("says what the plan was read from, including what was missing", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);

    await planIt(user);

    expect(screen.getByText(/53 commits/)).toBeInTheDocument();
    expect(
      screen.getByText(/estimates not yet calibrated against your logged time/),
    ).toBeInTheDocument();
  });

  it("writes nothing while it is only showing options", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);

    await planIt(user);

    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalledWith(
      "/ai/projects/1/plan/apply",
      expect.anything(),
    );
  });
});

describe("choosing one", () => {
  it("creates only the tasks left after dropping", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);
    await planIt(user);

    await user.click(screen.getAllByRole("button", { name: /Look at the tasks/ })[0]);
    await user.click(screen.getByRole("button", { name: "Drop Spot-check the data" }));
    vi.mocked(api.post).mockResolvedValueOnce({
      option: "Harden the core",
      tasks_created: 2,
      milestones_created: 1,
    });
    await user.click(screen.getByRole("button", { name: /Create 2 tasks/ }));

    const [path, body] = vi.mocked(api.post).mock.calls.at(-1);
    expect(path).toBe("/ai/projects/1/plan/apply");
    expect(body.option.tasks.map((t) => t.title)).toEqual([
      "Unit tests for APS",
      "Unit tests for matching",
    ]);
  });

  it("re-totals the hours as tasks are dropped", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);
    await planIt(user);

    await user.click(screen.getAllByRole("button", { name: /Look at the tasks/ })[0]);
    expect(screen.getByRole("button", { name: /Create 3 tasks \(8h\)/ })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Drop Unit tests for APS" }));
    expect(screen.getByRole("button", { name: /Create 2 tasks \(5h\)/ })).toBeTruthy();
  });

  it("keeps each option's dropped tasks to itself", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);
    await planIt(user);

    await user.click(screen.getAllByRole("button", { name: /Look at the tasks/ })[0]);
    await user.click(screen.getByRole("button", { name: "Drop Unit tests for APS" }));
    await user.click(screen.getByRole("button", { name: /Look at the tasks/ }));

    // The second option's own single task is untouched by the first's drops.
    expect(screen.getByRole("button", { name: /Create 1 task \(12h\)/ })).toBeTruthy();
  });

  it("confirms what was created", async () => {
    const user = userEvent.setup();
    render(<ProjectPlan project={project} />);
    await planIt(user);

    await user.click(screen.getAllByRole("button", { name: /Look at the tasks/ })[0]);
    vi.mocked(api.post).mockResolvedValueOnce({
      option: "Harden the core",
      tasks_created: 3,
      milestones_created: 1,
      finishes: "2026-10-01",
    });
    await user.click(screen.getByRole("button", { name: /Create 3 tasks/ }));

    expect(await screen.findByText(/3 tasks and 1 milestone/)).toBeInTheDocument();
  });
});

describe("research", () => {
  it("lists the sources it read, so a claim can be checked", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValueOnce({
      ...PLAN,
      research: {
        searches: 2,
        sources: [{ url: "https://example.com/a", title: "Ignore .firebase" }],
        text: "…",
      },
    });
    render(<ProjectPlan project={project} />);
    await user.click(screen.getByRole("button", { name: /Plan the next stretch/ }));

    expect(await screen.findByText("Ignore .firebase")).toHaveAttribute(
      "href",
      "https://example.com/a",
    );
  });

  it("says when the search failed rather than pretending it ran", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValueOnce({
      ...PLAN,
      research: { searches: 0, sources: [], text: "", error: "Rate limited" },
    });
    render(<ProjectPlan project={project} />);
    await user.click(screen.getByRole("button", { name: /Plan the next stretch/ }));

    expect(
      await screen.findByText(/web search failed.*from the project alone/i),
    ).toBeInTheDocument();
  });
});

describe("when it fails", () => {
  it("shows the reason and keeps the form", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValueOnce(new Error("The model call failed"));
    render(<ProjectPlan project={project} />);

    await user.click(screen.getByRole("button", { name: /Plan the next stretch/ }));

    await waitFor(() =>
      expect(screen.getByText(/The model call failed/)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /Plan the next stretch/ })).toBeTruthy();
  });
});
