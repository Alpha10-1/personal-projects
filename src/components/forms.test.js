/**
 * The three forms, tested at the seam where they have actually broken.
 *
 * Not the rendering -- the payload. `protected_paths` reached the model and
 * the form and was silently dropped because it never reached the Pydantic
 * schema, and nothing noticed because nothing asserted what the form sends.
 * So every field a form claims to save is asserted here by name.
 *
 * The other thing worth pinning down is the empty string. A form has no way
 * to express "not set" except `""`, and the API means `null`. Every place
 * that conversion is wrong is a field that silently saves the wrong thing.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PersonForm from "@/components/PersonForm";
import ProjectForm from "@/components/ProjectForm";
import TaskForm from "@/components/TaskForm";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}));
// The suggestion strip calls the model; it has its own tests.
vi.mock("@/components/AiSuggestions", () => ({ default: () => null }));

const sent = () => vi.mocked(api.post).mock.calls.at(-1)[1];
const patched = () => vi.mocked(api.patch).mock.calls.at(-1)[1];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue([]);
  vi.mocked(api.post).mockResolvedValue({ id: 1 });
  vi.mocked(api.patch).mockResolvedValue({ id: 1 });
});

// Exact names: "Add objective, stakeholder…" is also a button, and a
// loose matcher finds it instead of the one that submits.
const save = async (label) =>
  userEvent.click(screen.getByRole("button", { name: label }));

// --- PersonForm ----------------------------------------------------------

describe("adding a person", () => {
  const SAVE = /Add person|Save changes/;
  const open = () => render(<PersonForm open onClose={vi.fn()} onSaved={vi.fn()} />);

  it("sends every field it offers", async () => {
    open();
    await userEvent.type(screen.getByLabelText(/Name/i), "Ada");
    await userEvent.type(screen.getByLabelText(/Email/i), "ada@example.com");
    await userEvent.type(screen.getByLabelText(/GitHub/i), "ada");
    await save(SAVE);

    expect(api.post).toHaveBeenCalledWith("/people", expect.anything());
    expect(sent()).toMatchObject({
      name: "Ada",
      email: "ada@example.com",
      github_login: "ada",
    });
  });

  it("sends null, not an empty string, for what was left blank", async () => {
    open();
    await userEvent.type(screen.getByLabelText(/Name/i), "Ada");
    await save(SAVE);

    expect(sent().email).toBeNull();
    expect(sent().github_login).toBeNull();
    expect(sent().notes).toBeNull();
  });

  it("trims what was typed", async () => {
    open();
    await userEvent.type(screen.getByLabelText(/Name/i), "  Ada  ");
    await save(SAVE);
    expect(sent().name).toBe("Ada");
  });

  it("edits in place rather than creating a second person", async () => {
    render(
      <PersonForm
        open
        person={{ id: 7, name: "Ada", email: null }}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    await save(SAVE);
    expect(api.patch).toHaveBeenCalledWith("/people/7", expect.anything());
    expect(api.post).not.toHaveBeenCalled();
  });

  it("keeps the form open and shows why when saving fails", async () => {
    const onClose = vi.fn();
    vi.mocked(api.post).mockRejectedValue(new Error("Name is already taken."));
    render(<PersonForm open onClose={onClose} onSaved={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/Name/i), "Ada");
    await save(SAVE);

    expect(await screen.findByText(/already taken/)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});

// --- TaskForm ------------------------------------------------------------

describe("adding a task", () => {
  const SAVE = /Create task|Save changes/;
  const PROJECTS = [{ id: 3, name: "Demo" }];
  const MILESTONES = [
    { id: 9, project_id: 3, title: "Beta" },
    { id: 10, project_id: 4, title: "Elsewhere" },
  ];

  const open = (props = {}) =>
    render(
      <TaskForm
        open
        onClose={vi.fn()}
        onSaved={vi.fn()}
        projects={PROJECTS}
        milestones={MILESTONES}
        {...props}
      />,
    );

  it("sends numbers for the ids, not the strings the select holds", async () => {
    open({ defaults: { project_id: 3 } });
    await userEvent.type(screen.getByLabelText(/Title/i), "Write it down");
    await save(SAVE);
    expect(sent().project_id).toBe(3);
  });

  it("sends null for an unset project rather than zero or an empty string", async () => {
    open();
    await userEvent.type(screen.getByLabelText(/Title/i), "Unfiled");
    await save(SAVE);
    expect(sent().project_id).toBeNull();
    expect(sent().milestone_id).toBeNull();
    expect(sent().due_date).toBeNull();
    expect(sent().estimate_hours).toBeNull();
  });

  it("only offers milestones from the project that is selected", async () => {
    open({ defaults: { project_id: 3 } });
    const options = screen
      .getByLabelText(/Milestone/i)
      .querySelectorAll("option");
    const titles = [...options].map((option) => option.textContent);
    expect(titles).toContain("Beta");
    expect(titles).not.toContain("Elsewhere");
  });

  it("sends the blocked reason while the task is blocked", async () => {
    open({ task: { id: 4, title: "Stuck", status: "blocked", blocked_reason: "waiting" } });
    await save(SAVE);
    expect(patched().blocked_reason).toBe("waiting");
  });

  it("drops the blocked reason once it is not blocked", async () => {
    // The reason is stale the moment the task is unblocked, and sending it
    // would leave the row explaining a state it is no longer in.
    open({ task: { id: 4, title: "Stuck", status: "todo", blocked_reason: "waiting" } });
    await save(SAVE);
    expect(patched().blocked_reason).toBeNull();
  });

  it("converts the estimate to a number", async () => {
    open();
    await userEvent.type(screen.getByLabelText(/Title/i), "Measured");
    await userEvent.type(screen.getByLabelText(/Estimate/i), "2.5");
    await save(SAVE);
    expect(sent().estimate_hours).toBe(2.5);
  });
});

// --- ProjectForm ---------------------------------------------------------

describe("adding a project", () => {
  const SAVE = /Create project|Save changes/;
  const open = (props = {}) =>
    render(<ProjectForm open onClose={vi.fn()} onSaved={vi.fn()} {...props} />);

  it("sends the code fields, which is where one was silently dropped before", async () => {
    open();
    await userEvent.type(screen.getByLabelText(/Name/i), "Tracker");
    await userEvent.type(screen.getByLabelText(/Local folder|folder/i), "C:/code/x");
    await userEvent.type(screen.getByLabelText(/Files that need your approval/i), "**/auth*.py");
    await userEvent.type(screen.getByLabelText(/Test command/i), "pytest -q");
    await userEvent.type(screen.getByLabelText(/Run the tests from/i), "backend");
    await save(SAVE);

    expect(sent()).toMatchObject({
      local_path: "C:/code/x",
      protected_paths: "**/auth*.py",
      test_command: "pytest -q",
      test_dir: "backend",
    });
  });

  it("sends null for every optional field left blank", async () => {
    open();
    await userEvent.type(screen.getByLabelText(/Name/i), "Tracker");
    await save(SAVE);

    const payload = sent();
    for (const field of [
      "summary",
      "repo",
      "local_path",
      "protected_paths",
      "test_command",
      "test_dir",
      "objective",
      "stakeholder",
    ]) {
      expect(payload).toHaveProperty(field, null);
    }
    expect(payload.leader_id).toBeNull();
    expect(payload.progress_override).toBeNull();
  });

  it("loads a project into the form rather than starting blank", async () => {
    open({
      project: {
        id: 5,
        name: "Tracker",
        test_command: "npm test",
        protected_paths: "**/x",
      },
    });
    expect(screen.getByLabelText(/Test command/i)).toHaveValue("npm test");
    await save(SAVE);
    expect(api.patch).toHaveBeenCalledWith("/projects/5", expect.anything());
    expect(patched().test_command).toBe("npm test");
  });
});
