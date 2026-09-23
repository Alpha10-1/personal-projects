/**
 * Saved brainstorms: thinking an idea through before it becomes a project.
 *
 * Unlike the floating assistant, these persist, so the thing worth pinning
 * down is the boundary: opening one loads it, harvesting proposes tasks,
 * and deleting asks first. None of that should happen on its own.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import BrainstormPanel from "@/components/BrainstormPanel";
import { api } from "@/lib/api";
import { streamBrainstorm } from "@/lib/ai";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

const status = { configured: true };
vi.mock("@/lib/ai", () => ({
  useAiStatus: () => status,
  streamBrainstorm: vi.fn(),
}));

const SESSIONS = [
  { id: 7, topic: "Should the agent run tests?", message_count: 4, updated_at: "2026-09-21" },
];

const ONE = {
  id: 7,
  topic: "Should the agent run tests?",
  messages: [
    { id: 1, role: "user", content: "What would it cost?" },
    { id: 2, role: "assistant", content: "A larger security surface." },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  status.configured = true;
  vi.mocked(api.get).mockImplementation((url) =>
    url.includes("/brainstorms/") ? Promise.resolve(ONE) : Promise.resolve(SESSIONS),
  );
  vi.mocked(api.post).mockResolvedValue({ id: 8, topic: "New one", messages: [] });
  vi.mocked(api.del).mockResolvedValue({});
  vi.mocked(streamBrainstorm).mockImplementation(async ({ onDelta }) => onDelta("Well…"));
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => vi.restoreAllMocks());

describe("without a key", () => {
  it("says the assistant is off rather than offering something that cannot work", () => {
    status.configured = false;
    render(<BrainstormPanel projects={[]} />);
    expect(screen.getByText("The assistant is switched off")).toBeInTheDocument();
  });

  it("does not start a conversation it cannot finish", () => {
    // The saved list is still fetched -- hooks run before the early return,
    // and it is a local endpoint that costs nothing. What must not happen
    // is a call to the model.
    status.configured = false;
    render(<BrainstormPanel projects={[]} />);
    expect(streamBrainstorm).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe("the list", () => {
  it("shows saved sessions with how far each got", async () => {
    render(<BrainstormPanel projects={[]} />);
    expect(await screen.findByText("Should the agent run tests?")).toBeInTheDocument();
    expect(screen.getByText("4 messages")).toBeInTheDocument();
  });

  it("says so when there is nothing in progress", async () => {
    vi.mocked(api.get).mockResolvedValue([]);
    render(<BrainstormPanel projects={[]} />);
    expect(await screen.findByText("Nothing in progress")).toBeInTheDocument();
  });

  it("loads a session only when one is opened", async () => {
    render(<BrainstormPanel projects={[]} />);
    const row = await screen.findByText("Should the agent run tests?");

    expect(api.get).not.toHaveBeenCalledWith("/personal/brainstorms/7");
    await userEvent.click(row);
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith("/personal/brainstorms/7"),
    );
  });

  it("shows the conversation once it is open", async () => {
    render(<BrainstormPanel projects={[]} />);
    await userEvent.click(await screen.findByText("Should the agent run tests?"));
    expect(await screen.findByText(/larger security surface/)).toBeInTheDocument();
  });

  it("closes again when the same one is pressed twice", async () => {
    render(<BrainstormPanel projects={[]} />);
    const row = await screen.findByText("Should the agent run tests?");
    await userEvent.click(row);
    await screen.findByText(/larger security surface/);

    await userEvent.click(row);
    await waitFor(() =>
      expect(screen.queryByText(/larger security surface/)).not.toBeInTheDocument(),
    );
  });
});

describe("an open brainstorm", () => {
  const open = async () => {
    render(<BrainstormPanel projects={[]} />);
    await userEvent.click(await screen.findByText("Should the agent run tests?"));
    await screen.findByText(/larger security surface/);
  };

  it("sends a message and streams the reply", async () => {
    await open();
    await userEvent.type(screen.getByLabelText("Message"), "and the risk?");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(streamBrainstorm).toHaveBeenCalled());
    expect(vi.mocked(streamBrainstorm).mock.calls.at(-1)[0]).toMatchObject({
      brainstormId: 7,
      content: "and the risk?",
    });
  });

  it("asks before deleting the whole thread", async () => {
    await open();
    await userEvent.click(
      screen.getByRole("button", { name: "Delete this brainstorm" }),
    );
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/personal/brainstorms/7"));
  });

  it("keeps it when the question is answered no", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    await open();
    await userEvent.click(
      screen.getByRole("button", { name: "Delete this brainstorm" }),
    );
    expect(api.del).not.toHaveBeenCalled();
  });
});
