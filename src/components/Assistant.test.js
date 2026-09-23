/**
 * The floating assistant.
 *
 * A throwaway conversation, streamed. Three things are worth holding onto:
 * it is invisible without a key, an answer that never arrives must not
 * leave an empty bubble behind, and stopping mid-answer is not an error.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Assistant from "@/components/Assistant";
import { streamChat } from "@/lib/ai";

const status = { configured: true, reason: null };
vi.mock("@/lib/ai", () => ({
  useAiStatus: () => status,
  streamChat: vi.fn(),
}));

const openIt = async () =>
  userEvent.click(screen.getByRole("button", { name: "Ask the analyst" }));

const ask = async (text = "what changed this week?") => {
  await userEvent.type(screen.getByRole("textbox"), text);
  await userEvent.keyboard("{Enter}");
};

beforeEach(() => {
  vi.clearAllMocks();
  status.configured = true;
  status.reason = null;
  vi.mocked(streamChat).mockImplementation(async ({ onDelta }) => {
    onDelta("Six commits, ");
    onDelta("all on the review rules.");
  });
});

describe("when there is no key", () => {
  it("is not there at all", () => {
    status.configured = false;
    const { container } = render(<Assistant />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("opening it", () => {
  it("starts as a button, not a panel", () => {
    render(<Assistant />);
    expect(screen.getByRole("button", { name: "Ask the analyst" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("opens into something you can type in", async () => {
    render(<Assistant />);
    await openIt();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });
});

describe("asking something", () => {
  it("streams the answer in as it arrives", async () => {
    render(<Assistant />);
    await openIt();
    await ask();
    expect(
      await screen.findByText(/Six commits, all on the review rules/),
    ).toBeInTheDocument();
  });

  it("sends the question as the conversation, not on its own", async () => {
    render(<Assistant />);
    await openIt();
    await ask("what changed?");

    await waitFor(() => expect(streamChat).toHaveBeenCalled());
    const { messages } = vi.mocked(streamChat).mock.calls.at(-1)[0];
    expect(messages.at(-1)).toMatchObject({ role: "user", content: "what changed?" });
  });

  it("keeps the thread, so a follow-up has the earlier turns", async () => {
    render(<Assistant />);
    await openIt();
    await ask("first question");
    await screen.findByText(/Six commits/);
    await ask("and then?");

    await waitFor(() => expect(streamChat).toHaveBeenCalledTimes(2));
    const { messages } = vi.mocked(streamChat).mock.calls.at(-1)[0];
    expect(messages.length).toBeGreaterThan(2);
  });

  it("sends nothing for an empty question", async () => {
    render(<Assistant />);
    await openIt();
    await userEvent.keyboard("{Enter}");
    expect(streamChat).not.toHaveBeenCalled();
  });
});

describe("when the answer does not come", () => {
  it("leaves no empty bubble behind", async () => {
    vi.mocked(streamChat).mockRejectedValue(new Error("The model is unavailable."));
    render(<Assistant />);
    await openIt();
    await ask();

    expect(await screen.findByText(/model is unavailable/)).toBeInTheDocument();
    // The placeholder is appended before the stream starts; a failure has to
    // take it away again rather than leave a blank turn in the thread.
    expect(screen.queryByText("", { selector: ".prose" })).not.toBeInTheDocument();
  });

  it("treats being stopped as a decision rather than a failure", async () => {
    const aborted = Object.assign(new Error("aborted"), { name: "AbortError" });
    vi.mocked(streamChat).mockRejectedValue(aborted);
    render(<Assistant />);
    await openIt();
    await ask();

    await waitFor(() => expect(streamChat).toHaveBeenCalled());
    expect(screen.queryByText(/aborted/)).not.toBeInTheDocument();
  });
});
