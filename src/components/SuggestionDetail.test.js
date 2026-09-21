import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SuggestionDetail from "@/components/SuggestionDetail";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const LONG =
  "A South African-style course/university matching app: users enter exam marks, " +
  "the app computes APS scores and matches subjects to courses, with an admin " +
  "panel for managing course requirements and user permissions.";

const DETAIL = {
  id: 5,
  rule: "history_summary",
  target_type: "project",
  target_id: 10,
  target_title: "course-finder-app",
  field: "summary",
  current_value: "",
  proposed_value: LONG,
  rationale: "Read from the repository's whole commit history.",
  evidence: ["53 commits, Jun 2026 to Aug 2026"],
  status: "pending",
  created_at: "2026-09-18",
  resolved_at: null,
  live_value: "",
  target_exists: true,
  changed_since_raised: false,
  already_applied: false,
  rule_explanation: "The whole commit history of the linked repository was read.",
  applies: "Replaces this project's summary with the proposed text.",
  can_apply: true,
};

const show = (overrides = {}, props = {}) => {
  vi.mocked(api.get).mockResolvedValue({ ...DETAIL, ...overrides });
  return render(
    <SuggestionDetail suggestionId={5} open onClose={() => {}} {...props} />,
  );
};

beforeEach(() => {
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
});

describe("reading it", () => {
  it("shows the proposed text in full rather than clipped into a badge", async () => {
    // The row could only fit a fragment of a 240-character summary, which is
    // the whole reason this dialog exists.
    show();

    expect(await screen.findByText(LONG)).toBeInTheDocument();
  });

  it("names what it is about", async () => {
    show();

    expect(
      await screen.findByRole("dialog", { name: /course-finder-app/ }),
    ).toBeInTheDocument();
  });

  it("says what accepting would actually write", async () => {
    show();

    expect(
      await screen.findByText(/Replaces this project's summary/),
    ).toBeInTheDocument();
    expect(screen.getByText(/project\.summary/)).toBeInTheDocument();
  });

  it("explains the rule in words, keeping the rule name beside it", async () => {
    show();

    expect(
      await screen.findByText(/linked repository was read/),
    ).toBeInTheDocument();
    expect(screen.getByText("(history_summary)")).toBeInTheDocument();
  });

  it("shows every piece of evidence, not the first line of it", async () => {
    show({ evidence: ["53 commits, Jun 2026 to Aug 2026", "a second thing"] });

    expect(await screen.findByText("a second thing")).toBeInTheDocument();
  });

  it("says plainly when there is nothing set yet", async () => {
    show({ live_value: "", current_value: "" });

    expect(await screen.findByText("Nothing set")).toBeInTheDocument();
  });
});

describe("what changed since it was raised", () => {
  it("warns that accepting would overwrite a newer edit", async () => {
    show({
      changed_since_raised: true,
      current_value: "what it said then",
      live_value: "what I wrote yesterday",
    });

    expect(
      await screen.findByText(/would overwrite what is there now/),
    ).toBeInTheDocument();
    // Both are shown: the thing at risk, and the thing the model was judging.
    expect(screen.getByText("what I wrote yesterday")).toBeInTheDocument();
    expect(screen.getByText("what it said then")).toBeInTheDocument();
  });

  it("says when the proposal is already in place", async () => {
    show({ already_applied: true, live_value: LONG });

    expect(await screen.findByText(/already in place/)).toBeInTheDocument();
  });
});

describe("deciding", () => {
  it("accepts from inside the dialog and closes", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onResolved = vi.fn();
    vi.mocked(api.post).mockResolvedValue({});
    show({}, { onClose, onResolved });
    await screen.findByText(LONG);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    expect(api.post).toHaveBeenCalledWith("/suggestions/5/accept");
    expect(onResolved).toHaveBeenCalledWith("accept");
    expect(onClose).toHaveBeenCalled();
  });

  it("will not let you accept something whose target has gone", async () => {
    show({ target_exists: false, live_value: null });
    await screen.findByText(/no longer exists/);

    expect(screen.getByRole("button", { name: /Accept/ })).toBeDisabled();
    // Dismissing is still how you clear it away.
    expect(screen.getByRole("button", { name: /Dismiss/ })).not.toBeDisabled();
  });

  it("will not let you accept a change nothing knows how to apply", async () => {
    show({ can_apply: false, applies: null });

    expect(await screen.findByText(/would fail/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Accept/ })).toBeDisabled();
  });

  it("offers no buttons for something already decided", async () => {
    show({ status: "dismissed", resolved_at: "2026-09-20" });

    expect(await screen.findByText(/Already dismissed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Accept/ })).toBeNull();
  });

  it("keeps the dialog open and says why when the decision fails", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    vi.mocked(api.post).mockRejectedValue(new Error("Suggestion was already accepted"));
    show({}, { onClose });
    await screen.findByText(LONG);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    expect(await screen.findByText(/already accepted/)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("when it is closed", () => {
  it("fetches nothing until it is opened", () => {
    render(<SuggestionDetail suggestionId={5} open={false} onClose={() => {}} />);

    expect(api.get).not.toHaveBeenCalled();
  });
});
