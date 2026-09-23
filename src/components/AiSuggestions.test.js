/**
 * The suggestion strip on a form.
 *
 * Its whole contract is restraint: it proposes and never writes. Applying
 * one has to call back rather than reach into the form, and with no key
 * configured it has to render nothing at all -- a disabled feature should
 * be invisible, not visibly broken.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AiSuggestions from "@/components/AiSuggestions";

const status = { configured: true };
let live = {
  fields: {},
  loading: false,
  error: null,
  dismissed: false,
  dismiss: vi.fn(),
};

vi.mock("@/lib/ai", () => ({
  useAiStatus: () => status,
  useLiveSuggestions: () => live,
}));

const show = (props = {}) =>
  render(<AiSuggestions kind="project" draft={{ name: "x" }} {...props} />);

beforeEach(() => {
  status.configured = true;
  live = {
    fields: {},
    loading: false,
    error: null,
    dismissed: false,
    dismiss: vi.fn(),
  };
});

describe("when it says nothing", () => {
  it("renders nothing at all without a key", () => {
    status.configured = false;
    live.fields = { summary: "A tracker." };
    const { container } = show();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing once dismissed", () => {
    live.dismissed = true;
    live.fields = { summary: "A tracker." };
    const { container } = show();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there is nothing to suggest", () => {
    const { container } = show();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when only an empty question list came back", () => {
    live.fields = { questions: [] };
    const { container } = show();
    expect(container).toBeEmptyDOMElement();
  });
});

describe("what it offers", () => {
  it("shows a suggestion with a readable label", () => {
    live.fields = { summary: "A single-user project tracker." };
    show();
    expect(screen.getByText(/single-user project tracker/)).toBeInTheDocument();
  });

  it("hands the value back rather than writing it", async () => {
    const onApply = vi.fn();
    live.fields = { summary: "A tracker." };
    show({ onApply });

    await userEvent.click(screen.getByRole("button", { name: /Use this/i }));
    expect(onApply).toHaveBeenCalledWith("summary", "A tracker.");
  });

  it("uses the list callback for a list, so the caller can queue them", async () => {
    const onApply = vi.fn();
    const onApplyList = vi.fn();
    live.fields = { tasks: ["Write it", "Test it"] };
    show({ onApply, onApplyList });

    await userEvent.click(screen.getByRole("button", { name: /Use this/i }));
    expect(onApplyList).toHaveBeenCalledWith("tasks", ["Write it", "Test it"]);
    expect(onApply).not.toHaveBeenCalled();
  });

  it("counts the items in a list so its size is visible before applying", () => {
    live.fields = { tasks: ["a", "b", "c"] };
    show();
    expect(screen.getByText(/×3/)).toBeInTheDocument();
  });

  it("says it is thinking rather than looking empty", () => {
    live.loading = true;
    live.fields = { summary: "A tracker." };
    show();
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
  });

  it("can be told to stop", async () => {
    live.fields = { summary: "A tracker." };
    show();
    await userEvent.click(
      screen.getByRole("button", { name: "Stop suggesting for this form" }),
    );
    expect(live.dismiss).toHaveBeenCalled();
  });
});

describe("when the model fails", () => {
  it("says so quietly instead of taking the form down", () => {
    live.error = new Error("Rate limited.");
    show();
    expect(screen.getByText(/Rate limited/)).toBeInTheDocument();
  });

  it("still shows whatever did come back", () => {
    live.error = new Error("Partly failed.");
    live.fields = { summary: "A tracker." };
    show();
    expect(screen.getByText(/A tracker/)).toBeInTheDocument();
  });
});
