/**
 * The board's central row.
 *
 * The parts worth pinning down are the ones that write: ticking a task off
 * sends a status, which is what drives `completed_at` and every cycle-time
 * figure downstream. Getting that wrong is not a display bug.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TaskList from "@/components/TaskList";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { patch: vi.fn() } }));

const TASK = {
  id: 4,
  title: "Tidy the loader",
  status: "todo",
  priority: "medium",
  project_id: 3,
  project_name: "Demo",
  subtask_total: 0,
  subtask_done: 0,
  hours_logged: 0,
};

const show = (tasks = [TASK], props = {}) =>
  render(<TaskList tasks={tasks} onChanged={vi.fn()} {...props} />);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.patch).mockResolvedValue({ ...TASK, status: "done" });
});

describe("what a row shows", () => {
  it("shows the title and its project", () => {
    show();
    expect(screen.getByText("Tidy the loader")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Demo" })).toHaveAttribute(
      "href",
      "/projects/3",
    );
  });

  it("can be told to leave the project out", () => {
    show([TASK], { showProject: false });
    expect(screen.queryByRole("link", { name: "Demo" })).not.toBeInTheDocument();
  });

  it("says when a task has no project rather than showing nothing", () => {
    show([{ ...TASK, project_id: null, project_name: null }]);
    expect(screen.getByText("No project")).toBeInTheDocument();
  });

  it("marks high priority", () => {
    show([{ ...TASK, priority: "high" }]);
    expect(screen.getByText("High")).toBeInTheDocument();
  });

  it("stops shouting about priority once the task is done", () => {
    show([{ ...TASK, priority: "high", status: "done" }]);
    expect(screen.queryByText("High")).not.toBeInTheDocument();
  });

  it("marks a blocked task", () => {
    show([{ ...TASK, status: "blocked", blocked_reason: "waiting on IT" }]);
    // "Blocked" is also an option in the status dropdown, so the badge
    // has to be picked out rather than matched by text alone.
    expect(screen.getAllByText("Blocked").length).toBeGreaterThan(1);
    expect(screen.getByText(/waiting on IT/)).toBeInTheDocument();
  });

  it("counts subtasks when there are any", () => {
    show([{ ...TASK, subtask_total: 3, subtask_done: 1 }]);
    expect(screen.getByText("1/3 subtasks")).toBeInTheDocument();
  });

  it("shows logged hours against the estimate", () => {
    show([{ ...TASK, hours_logged: 2, estimate_hours: 5 }]);
    expect(screen.getByText(/2h \/ 5h/)).toBeInTheDocument();
  });

  it("says so when there is nothing to list", () => {
    show([], { emptyTitle: "No open tasks", emptyDescription: "Break it down." });
    expect(screen.getByText("No open tasks")).toBeInTheDocument();
    expect(screen.getByText("Break it down.")).toBeInTheDocument();
  });
});

describe("completing one", () => {
  it("sends done, which is what stamps the completion time", async () => {
    show();
    await userEvent.click(screen.getByRole("button", { name: /Complete Tidy/ }));
    expect(api.patch).toHaveBeenCalledWith("/tasks/4", { status: "done" });
  });

  it("reopens a finished one", async () => {
    show([{ ...TASK, status: "done" }]);
    await userEvent.click(screen.getByRole("button", { name: /Reopen Tidy/ }));
    expect(api.patch).toHaveBeenCalledWith("/tasks/4", { status: "todo" });
  });

  it("tells the caller, so the rest of the page can catch up", async () => {
    const onChanged = vi.fn();
    show([TASK], { onChanged });
    await userEvent.click(screen.getByRole("button", { name: /Complete Tidy/ }));
    expect(onChanged).toHaveBeenCalled();
  });

  it("sends the status chosen from the dropdown", async () => {
    show();
    await userEvent.selectOptions(
      screen.getByLabelText("Status of Tidy the loader"),
      "in_progress",
    );
    expect(api.patch).toHaveBeenCalledWith("/tasks/4", { status: "in_progress" });
  });
});

describe("editing and deleting", () => {
  it("offers them only when the caller can handle them", () => {
    show();
    expect(screen.queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });

  it("hands the whole task back rather than just its id", async () => {
    const onEdit = vi.fn();
    show([TASK], { onEdit });
    await userEvent.click(screen.getByRole("button", { name: /Edit Tidy/ }));
    expect(onEdit).toHaveBeenCalledWith(TASK);
  });

  it("asks the caller to delete rather than deleting itself", async () => {
    const onDelete = vi.fn();
    show([TASK], { onDelete });
    await userEvent.click(screen.getByRole("button", { name: /Delete Tidy/ }));
    expect(onDelete).toHaveBeenCalledWith(TASK);
    expect(api.patch).not.toHaveBeenCalled();
  });
});

describe("the code changes a task carries", () => {
  const CHANGE = {
    id: 11,
    path: "backend/app/schemas.py",
    status: "approved",
    origin: "human",
    created_at: "2026-09-21T10:00:00",
    lines: { added: 12, removed: 3 },
  };

  it("shows them when the caller has some", () => {
    show([TASK], { changesByTask: new Map([[4, [CHANGE]]]) });
    expect(screen.getByText("backend/app/schemas.py")).toBeInTheDocument();
    expect(screen.getByText(/What changed for this/)).toBeInTheDocument();
  });

  it("shows nothing at all for a task with none", () => {
    // An empty panel on every task would read as "nothing happened" rather
    // than "nothing was recorded", and those are different.
    show([TASK], { changesByTask: new Map() });
    expect(screen.queryByText(/What changed for this/)).not.toBeInTheDocument();
  });

  it("does not go looking for them itself", () => {
    show();
    expect(screen.queryByText(/What changed for this/)).not.toBeInTheDocument();
  });
});
