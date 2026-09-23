/**
 * Who worked on a project, and what they said about it.
 *
 * The interesting part is the gap between the two lists it shows:
 * `members` are people you named, `contributors` are GitHub logins from the
 * commit history. A contributor with no person attached is the useful
 * signal -- someone whose work is recorded and who nobody has identified.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProjectTeam from "@/components/ProjectTeam";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  API_URL: "http://localhost:8000",
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() },
}));

const configured = { configured: true };
vi.mock("@/lib/ai", () => ({ useAiStatus: () => configured }));

const PROJECT = { id: 3, name: "Demo" };

const TEAM = {
  members: [
    {
      id: 1,
      name: "Ada",
      github_login: "ada",
      role: "contributor",
      role_title: "Engineer",
      contribution_count: 12,
    },
    {
      id: 2,
      name: "Grace",
      github_login: null,
      role: "viewer",
      role_title: null,
      contribution_count: 0,
    },
  ],
  contributors: [
    { login: "ada", person_id: 1, events: 12 },
    { login: "drive-by", person_id: null, events: 2 },
  ],
  feedback: [
    {
      id: 5,
      body: "The insights page is empty.",
      status: "open",
      kind: "issue",
      person_name: "Ada",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  configured.configured = true;
  vi.mocked(api.get).mockResolvedValue(TEAM);
  vi.mocked(api.patch).mockResolvedValue({});
  vi.mocked(api.del).mockResolvedValue({});
  vi.mocked(api.post).mockResolvedValue({ note: "A quiet week." });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("the team", () => {
  it("lists the people who were named", async () => {
    render(<ProjectTeam project={PROJECT} />);
    // Ada appears twice: once as a member, once as the author of feedback.
    expect((await screen.findAllByText("Ada")).length).toBeGreaterThan(0);
    expect(screen.getByText("Grace")).toBeInTheDocument();
  });

  it("says when someone has no GitHub login, rather than leaving a gap", async () => {
    render(<ProjectTeam project={PROJECT} />);
    expect(await screen.findByText(/no GitHub login/)).toBeInTheDocument();
  });

  it("counts what each person has actually committed here", async () => {
    render(<ProjectTeam project={PROJECT} />);
    expect(await screen.findByText(/12 here/)).toBeInTheDocument();
  });

  it("surfaces a contributor nobody has identified", async () => {
    // The whole point of holding both lists: work is recorded against a
    // login that is attached to no person.
    render(<ProjectTeam project={PROJECT} />);
    expect(await screen.findByText(/drive-by/)).toBeInTheDocument();
  });

  it("removes a member on request", async () => {
    render(<ProjectTeam project={PROJECT} />);
    await screen.findAllByText("Ada");
    await userEvent.click(
      screen.getByRole("button", { name: "Remove Ada from this project" }),
    );
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/members/1"));
  });
});

describe("feedback", () => {
  it("shows what was said and who said it", async () => {
    render(<ProjectTeam project={PROJECT} />);
    expect(await screen.findByText(/insights page is empty/)).toBeInTheDocument();
    expect(screen.getAllByText("Ada").length).toBeGreaterThan(0);
  });

  it("resolves a piece of feedback rather than deleting it", async () => {
    render(<ProjectTeam project={PROJECT} />);
    await screen.findByText(/insights page is empty/);

    const resolve = screen.getByRole("button", { name: /Actioned/i });
    await userEvent.click(resolve);
    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith("/feedback/5", { status: "actioned" }),
    );
    expect(api.del).not.toHaveBeenCalled();
  });
});

describe("the snapshot and the digest", () => {
  it("links to a snapshot anyone can open without the app", async () => {
    render(<ProjectTeam project={PROJECT} />);
    const link = await screen.findByRole("link", { name: /shareable snapshot/i });
    expect(link).toHaveAttribute("href", "http://localhost:8000/projects/3/share");
  });

  it("writes a digest only when asked", async () => {
    render(<ProjectTeam project={PROJECT} />);
    await screen.findAllByText("Ada");
    expect(api.post).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /digest|update/i }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/ai/projects/3/digest"),
    );
  });

  it("reports a failure instead of showing an empty digest", async () => {
    vi.mocked(api.post).mockRejectedValue(new Error("No ANTHROPIC_API_KEY is set."));
    render(<ProjectTeam project={PROJECT} />);
    await screen.findAllByText("Ada");
    await userEvent.click(screen.getByRole("button", { name: /digest|update/i }));
    expect(await screen.findByText(/ANTHROPIC_API_KEY/)).toBeInTheDocument();
  });
});

describe("failure", () => {
  it("reports a failed load rather than an empty team", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("Project not found."));
    render(<ProjectTeam project={PROJECT} />);
    expect(await screen.findByText(/Project not found/)).toBeInTheDocument();
  });
});
