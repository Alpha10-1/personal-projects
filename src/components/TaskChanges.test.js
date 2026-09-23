/**
 * What actually changed on disk for one task.
 *
 * The grouping is the part with logic in it: the caller reads a project's
 * changes once and hands each task its own, so a list of forty rows is one
 * request rather than forty.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import TaskChanges, { byTask } from "@/components/TaskChanges";

const CHANGE = {
  id: 11,
  path: "backend/app/schemas.py",
  status: "approved",
  origin: "human",
  created_at: "2026-09-21T10:00:00",
  lines: { added: 12, removed: 3 },
};

describe("grouping a project's changes", () => {
  it("keys them by the task they belong to", () => {
    const grouped = byTask([
      { ...CHANGE, id: 1, task_id: 4 },
      { ...CHANGE, id: 2, task_id: 4 },
      { ...CHANGE, id: 3, task_id: 9 },
    ]);
    expect(grouped.get(4)).toHaveLength(2);
    expect(grouped.get(9)).toHaveLength(1);
  });

  it("leaves out the ones that belong to no task", () => {
    // The ordinary case. A change with no task is not an orphan to be
    // filed somewhere; it simply is not part of a task.
    const grouped = byTask([
      { ...CHANGE, id: 1, task_id: null },
      { ...CHANGE, id: 2, task_id: 4 },
    ]);
    expect([...grouped.keys()]).toEqual([4]);
  });

  it("copes with nothing at all", () => {
    expect(byTask([]).size).toBe(0);
    expect(byTask(undefined).size).toBe(0);
    expect(byTask(null).size).toBe(0);
  });
});

describe("what it shows", () => {
  it("renders nothing when a task has no changes", () => {
    const { container } = render(<TaskChanges changes={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when it was given nothing", () => {
    const { container } = render(<TaskChanges />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the files and their state", () => {
    render(<TaskChanges changes={[CHANGE]} />);
    expect(screen.getByText("backend/app/schemas.py")).toBeInTheDocument();
    expect(screen.getByText("approved")).toBeInTheDocument();
  });

  it("totals the lines across the files", () => {
    render(
      <TaskChanges
        changes={[CHANGE, { ...CHANGE, id: 12, lines: { added: 8, removed: 1 } }]}
      />,
    );
    expect(screen.getByText("+20")).toBeInTheDocument();
    expect(screen.getByText("−4")).toBeInTheDocument();
    expect(screen.getByText(/2 files/)).toBeInTheDocument();
  });

  it("says whether a change came from the agent or from you", () => {
    render(<TaskChanges changes={[{ ...CHANGE, origin: "agent" }]} />);
    expect(screen.getByText(/agent/)).toBeInTheDocument();
  });

  it("survives a change with no line counts", () => {
    render(<TaskChanges changes={[{ ...CHANGE, lines: undefined }]} />);
    expect(screen.getByText("backend/app/schemas.py")).toBeInTheDocument();
  });
});

describe("opening one", () => {
  it("hands the whole change back", async () => {
    const onOpen = vi.fn();
    render(<TaskChanges changes={[CHANGE]} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledWith(CHANGE);
  });

  it("is not clickable when the caller cannot do anything with it", () => {
    render(<TaskChanges changes={[CHANGE]} />);
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
