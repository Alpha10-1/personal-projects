import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Scaffold from "@/components/Scaffold";
import { api } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => ({ api: { post: vi.fn() } }));

const PLAN = {
  name: "Run tracker",
  summary: "Tracks runs and flags a ramp-up that is too fast.",
  tasks: [
    { title: "Import the GPX files" },
    { title: "Compute weekly load" },
    { title: "Warn on a 30% jump" },
  ],
};

beforeEach(() => {
  vi.mocked(api.post).mockReset();
  push.mockReset();
});

async function draft(user) {
  vi.mocked(api.post).mockResolvedValueOnce({ applied: false, plan: PLAN });
  await user.type(screen.getByRole("textbox"), "A run tracker");
  await user.click(screen.getByRole("button", { name: /Draft a plan/ }));
  await screen.findByText("Run tracker");
}

describe("drafting a plan", () => {
  it("writes nothing until the plan has been looked at", async () => {
    const user = userEvent.setup();
    render(<Scaffold />);

    await draft(user);

    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith(
      "/ai/scaffold",
      expect.objectContaining({ apply: false }),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("goes straight to the new project when asked to just build it", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValueOnce({ applied: true, project_id: 12 });
    render(<Scaffold />);

    await user.type(screen.getByRole("textbox"), "A run tracker");
    await user.click(screen.getByRole("button", { name: /Just build it/ }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/projects/12"));
  });

  it("won't ask for a plan for nothing", () => {
    render(<Scaffold />);

    expect(screen.getByRole("button", { name: /Draft a plan/ })).toBeDisabled();
  });

  it("will, for a repo, because the README is the idea", () => {
    render(<Scaffold repo="me/oms" />);

    expect(screen.getByRole("button", { name: /Draft a plan/ })).not.toBeDisabled();
  });
});

describe("dropping tasks from a plan", () => {
  it("builds exactly what is left on screen, in order", async () => {
    const user = userEvent.setup();
    render(<Scaffold />);
    await draft(user);

    await user.click(screen.getByRole("button", { name: "Drop Compute weekly load" }));
    vi.mocked(api.post).mockResolvedValueOnce({ project_id: 7 });
    await user.click(screen.getByRole("button", { name: /Create 2 tasks/ }));

    const [path, body] = vi.mocked(api.post).mock.calls.at(-1);
    expect(path).toBe("/ai/scaffold/apply");
    expect(body.plan.tasks.map((t) => t.title)).toEqual([
      "Import the GPX files",
      "Warn on a 30% jump",
    ]);
    // The rest of the plan is posted back rather than regenerated, so what is
    // built is what was read.
    expect(body.plan.name).toBe("Run tracker");
  });

  it("counts down as rows are dropped and back up when they are put back", async () => {
    const user = userEvent.setup();
    render(<Scaffold />);
    await draft(user);

    expect(screen.getByRole("button", { name: /Create 3 tasks/ })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Drop Import the GPX files" }));
    expect(screen.getByRole("button", { name: /Create 2 tasks/ })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Put back Import the GPX files" }));
    expect(screen.getByRole("button", { name: /Create 3 tasks/ })).toBeTruthy();
  });

  it("drops by position, so two tasks with the same title are distinct", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValueOnce({
      applied: false,
      plan: { ...PLAN, tasks: [{ title: "Write tests" }, { title: "Write tests" }] },
    });
    render(<Scaffold />);
    await user.type(screen.getByRole("textbox"), "A run tracker");
    await user.click(screen.getByRole("button", { name: /Draft a plan/ }));
    await screen.findByText("Run tracker");

    await user.click(screen.getAllByRole("button", { name: "Drop Write tests" })[0]);

    expect(screen.getByRole("button", { name: /Create 1 task$/ })).toBeTruthy();
  });

  it("forgets what was dropped when a new plan replaces the old one", async () => {
    const user = userEvent.setup();
    render(<Scaffold />);
    await draft(user);
    await user.click(screen.getByRole("button", { name: "Drop Compute weekly load" }));

    vi.mocked(api.post).mockResolvedValueOnce({ applied: false, plan: PLAN });
    await user.click(screen.getByRole("button", { name: /Draft a plan/ }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Create 3 tasks/ })).toBeTruthy(),
    );
  });
});

describe("when the call fails", () => {
  it("says so and keeps the draft, rather than clearing the box", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValueOnce(new Error("No ANTHROPIC_API_KEY is set."));
    render(<Scaffold />);

    await user.type(screen.getByRole("textbox"), "A run tracker");
    await user.click(screen.getByRole("button", { name: /Draft a plan/ }));

    expect(await screen.findByText(/No ANTHROPIC_API_KEY is set./)).toBeTruthy();
    expect(screen.getByRole("textbox")).toHaveValue("A run tracker");
  });
});
