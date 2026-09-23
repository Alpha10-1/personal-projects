/**
 * The model's read of a repository's recent commits.
 *
 * It costs money, so nothing happens until it is asked for -- that is the
 * behaviour worth locking down. The rest is making sure the answer stays
 * checkable: how many commits it actually read, and whether it saw the
 * diffs or only the messages.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RepoInsights from "@/components/RepoInsights";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { post: vi.fn() } }));

const configured = { configured: true, reason: null };
vi.mock("@/lib/ai", () => ({ useAiStatus: () => configured }));

const PROJECT = { id: 3, name: "Demo", repo: "owner/demo" };

const RESULT = {
  summary: "Most of the work went into the review rules.",
  commits_reviewed: 12,
  events_considered: 40,
  diffs_included: true,
  themes: ["review rules", "the code tab"],
  risks: [{ title: "No tests on schemas.py", detail: "Seven commits, no test." }],
  improvements: [{ title: "Add a changelog", detail: "Releases are hard to follow." }],
};

const run = async () =>
  userEvent.click(screen.getByRole("button", { name: /Review|Read/i }));

beforeEach(() => {
  vi.clearAllMocks();
  configured.configured = true;
  vi.mocked(api.post).mockResolvedValue(RESULT);
});

describe("before it is asked", () => {
  it("costs nothing until pressed", () => {
    render(<RepoInsights project={PROJECT} />);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("names the repository it would read", () => {
    render(<RepoInsights project={PROJECT} />);
    expect(screen.getByText("owner/demo")).toBeInTheDocument();
  });
});

describe("a project with no repository", () => {
  it("explains instead of offering a review it cannot do", () => {
    render(<RepoInsights project={{ id: 3, name: "Demo", repo: null }} />);
    expect(screen.getByText("No repo linked")).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe("the answer", () => {
  it("leads with the summary", async () => {
    render(<RepoInsights project={PROJECT} />);
    await run();
    expect(await screen.findByText(/went into the review rules/)).toBeInTheDocument();
  });

  it("says how much it actually read, so the answer can be weighed", async () => {
    render(<RepoInsights project={PROJECT} />);
    await run();
    expect(await screen.findByText(/12 commit\(s\) of 40/)).toBeInTheDocument();
  });

  it("distinguishes reading the diffs from reading the messages", async () => {
    vi.mocked(api.post).mockResolvedValue({ ...RESULT, diffs_included: false });
    render(<RepoInsights project={PROJECT} />);
    await run();
    // A review of commit messages is a much weaker claim than one of the
    // code, and the difference must not be invisible.
    expect(await screen.findByText(/message/i)).toBeInTheDocument();
  });

  it("shows the risks and what was proposed", async () => {
    render(<RepoInsights project={PROJECT} />);
    await run();
    expect(await screen.findByText(/No tests on schemas.py/)).toBeInTheDocument();
    expect(screen.getByText(/Add a changelog/)).toBeInTheDocument();
  });

  it("counts them, so an empty section is not mistaken for an unread one", async () => {
    vi.mocked(api.post).mockResolvedValue({
      ...RESULT,
      risks: [
        { title: "No tests on schemas.py", detail: "Seven commits, no test." },
        { title: "Committed key", detail: "serviceAccountKey.json" },
      ],
    });
    render(<RepoInsights project={PROJECT} />);
    await run();
    // Two risks and one improvement: the badges are the counts.
    expect(await screen.findByText("2")).toBeInTheDocument();
  });

  it("copes with an answer that found nothing", async () => {
    vi.mocked(api.post).mockResolvedValue({
      ...RESULT,
      risks: [],
      improvements: [],
      themes: [],
    });
    render(<RepoInsights project={PROJECT} />);
    await run();
    expect(await screen.findByText(/went into the review rules/)).toBeInTheDocument();
  });
});

describe("failure", () => {
  it("reports it rather than showing an empty review", async () => {
    vi.mocked(api.post).mockRejectedValue(new Error("No ANTHROPIC_API_KEY is set."));
    render(<RepoInsights project={PROJECT} />);
    await run();
    expect(await screen.findByText(/ANTHROPIC_API_KEY/)).toBeInTheDocument();
  });
});
