/**
 * Logging hours.
 *
 * The figures on the Insights page are a sum over these rows, so what gets
 * posted matters more than what gets drawn. The one genuinely subtle thing
 * is the task list: offering a task from another project would silently
 * reassign the entry, because the API takes the task's project.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TimeLogPanel from "@/components/TimeLogPanel";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

const LOGS = [
  {
    id: 1,
    work_date: "2026-09-21",
    hours: 3.5,
    category: "build",
    note: "wrote the join logic",
    project_id: 3,
    project_name: "Demo",
  },
  {
    id: 2,
    work_date: "2026-09-20",
    hours: 1,
    category: "research",
    note: null,
    project_id: 3,
    project_name: "Demo",
  },
];

const PROJECTS = [
  { id: 3, name: "Demo" },
  { id: 4, name: "Other" },
];
const TASKS = [
  { id: 10, title: "Tidy the loader", project_id: 3 },
  { id: 11, title: "Elsewhere", project_id: 4 },
];

const show = (props = {}) =>
  render(<TimeLogPanel projects={PROJECTS} tasks={TASKS} {...props} />);

const openForm = async () =>
  userEvent.click(await screen.findByRole("button", { name: /Log time|Add/i }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue(LOGS);
  vi.mocked(api.post).mockResolvedValue({ id: 3 });
  vi.mocked(api.del).mockResolvedValue({});
});

describe("what it shows", () => {
  it("lists the entries and their notes", async () => {
    show();
    expect(await screen.findByText(/wrote the join logic/)).toBeInTheDocument();
  });

  it("totals the hours rather than leaving them to be added up", async () => {
    show();
    await screen.findByText(/wrote the join logic/);
    expect(screen.getByText(/4\.5h/)).toBeInTheDocument();
  });

  it("asks only for this project's hours when it is on a project", async () => {
    show({ projectId: 3 });
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        "/time-logs",
        expect.objectContaining({ project_id: 3 }),
      ),
    );
  });

  it("says so when nothing has been logged", async () => {
    vi.mocked(api.get).mockResolvedValue([]);
    show();
    expect(await screen.findByText(/No time logged|Nothing/i)).toBeInTheDocument();
  });
});

describe("logging an entry", () => {
  it("sends hours as a number and the date as given", async () => {
    show({ projectId: 3 });
    await openForm();
    await userEvent.type(screen.getByLabelText(/Hours/i), "2.5");
    await userEvent.click(screen.getByRole("button", { name: "Log it" }));

    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const payload = vi.mocked(api.post).mock.calls.at(-1)[1];
    expect(payload.hours).toBe(2.5);
    expect(payload.project_id).toBe(3);
    expect(typeof payload.work_date).toBe("string");
  });

  it("sends null rather than an empty string for what was left out", async () => {
    show();
    await openForm();
    await userEvent.type(screen.getByLabelText(/Hours/i), "1");
    await userEvent.click(screen.getByRole("button", { name: "Log it" }));

    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const payload = vi.mocked(api.post).mock.calls.at(-1)[1];
    expect(payload.task_id).toBeNull();
    expect(payload.note).toBeNull();
    expect(payload.project_id).toBeNull();
  });

  it("only offers tasks from the project being logged against", async () => {
    // Offering one from another project would silently move the entry,
    // because the API takes the project from the task.
    show({ projectId: 3 });
    await openForm();
    const options = [...screen.getByLabelText(/Task/i).querySelectorAll("option")].map(
      (option) => option.textContent,
    );
    expect(options).toContain("Tidy the loader");
    expect(options).not.toContain("Elsewhere");
  });

  it("tells the page it changed, so the totals elsewhere catch up", async () => {
    const onChanged = vi.fn();
    show({ projectId: 3, onChanged });
    await openForm();
    await userEvent.type(screen.getByLabelText(/Hours/i), "1");
    await userEvent.click(screen.getByRole("button", { name: "Log it" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("reports a failure rather than pretending it saved", async () => {
    // A valid entry that the server refuses. Typing something the input
    // itself rejects would test the browser, not this component.
    vi.mocked(api.post).mockRejectedValue(new Error("That day is already full."));
    show({ projectId: 3 });
    await openForm();
    await userEvent.type(screen.getByLabelText(/Hours/i), "2");
    await userEvent.click(screen.getByRole("button", { name: "Log it" }));
    expect(await screen.findByText(/already full/)).toBeInTheDocument();
  });
});

describe("deleting an entry", () => {
  it("removes the one that was chosen", async () => {
    show();
    await screen.findByText(/wrote the join logic/);
    const [remove] = screen.getAllByRole("button", { name: "Delete time entry" });
    await userEvent.click(remove);
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/time-logs/1"));
  });

  it("reports a failed delete", async () => {
    vi.mocked(api.del).mockRejectedValue(new Error("That entry is gone already."));
    show();
    await screen.findByText(/wrote the join logic/);
    await userEvent.click(screen.getAllByRole("button", { name: "Delete time entry" })[0]);
    expect(await screen.findByText(/gone already/)).toBeInTheDocument();
  });
});
