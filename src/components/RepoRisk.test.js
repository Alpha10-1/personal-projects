import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RepoRisk from "@/components/RepoRisk";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn() } }));

const PROJECT = { id: 3, name: "Demo" };

const CHURN = {
  project_id: 3,
  window_days: 90,
  files_touched: 42,
  commits_scanned: 20,
  commits_without_detail: 0,
  code_was_read: true,
  files: [
    {
      path: "backend/app/schemas.py",
      commits: 7,
      lines_changed: 544,
      authors: 1,
      tested: false,
      test_files: [],
      why_unknown: null,
      exists: true,
      at_risk: true,
    },
    {
      path: "backend/app/models.py",
      commits: 8,
      lines_changed: 493,
      authors: 1,
      tested: true,
      test_files: ["tests/test_models.py"],
      why_unknown: null,
      exists: true,
      at_risk: false,
    },
    {
      path: "README.md",
      commits: 16,
      lines_changed: 780,
      authors: 1,
      tested: null,
      test_files: [],
      why_unknown: "the outline does not read this language yet",
      exists: true,
      at_risk: false,
    },
  ],
  at_risk: [{ path: "backend/app/schemas.py" }],
  limits: ["Renames are not followed.", "Tested means a test file mentions a name."],
};

const HYGIENE = {
  project_id: 3,
  clean: false,
  secrets: [
    {
      path: "serviceAccountKey.json",
      commits: 3,
      first_sha: "9c2bf4849f12",
      still_present: false,
      filled_keys: [],
    },
  ],
  noise: [{ path: ".firebase/hosting.cache", commits: 38 }],
  templates: [{ path: ".env.example", commits: 1, filled_keys: [] }],
  commits_scanned: 20,
  commits_without_detail: 0,
};

const respond = (churn = CHURN, hygiene = HYGIENE) =>
  vi.mocked(api.get).mockImplementation((url) =>
    Promise.resolve(url.includes("churn") ? churn : hygiene),
  );

beforeEach(() => vi.clearAllMocks());

describe("what changes", () => {
  it("lists the busy files with how often they changed", async () => {
    respond();
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText("backend/app/schemas.py")).toBeInTheDocument();
    expect(screen.getByText(/7× · 544 lines/)).toBeInTheDocument();
  });

  it("counts the busy and untested ones", async () => {
    respond();
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText(/1 busy and untested/)).toBeInTheDocument();
  });

  it("separates no test from could not tell", async () => {
    respond();
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText("no test")).toBeInTheDocument();
    expect(screen.getByText("tested")).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("re-reads when the window changes", async () => {
    respond();
    render(<RepoRisk project={PROJECT} />);
    await screen.findByText("backend/app/schemas.py");
    await userEvent.click(screen.getByRole("button", { name: "30 days" }));
    expect(api.get).toHaveBeenCalledWith("/projects/3/churn", { window_days: 30 });
  });

  it("says when there was no checkout to check against", async () => {
    respond({ ...CHURN, code_was_read: false });
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText(/unknown, not empty/)).toBeInTheDocument();
  });

  it("declares the commits it could not read", async () => {
    respond({ ...CHURN, commits_without_detail: 5 });
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText(/5 of 20 commits have no file detail/)).toBeInTheDocument();
  });

  it("says plainly when there is nothing to count", async () => {
    respond({ ...CHURN, files: [], at_risk: [] });
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText(/No commits with file detail/)).toBeInTheDocument();
  });
});

describe("what was committed", () => {
  it("names a committed credential and insists history is the problem", async () => {
    respond();
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText("serviceAccountKey.json")).toBeInTheDocument();
    expect(screen.getByText(/does not remove it from history/)).toBeInTheDocument();
    expect(screen.getByText(/rotated/)).toBeInTheDocument();
  });

  it("marks one that is still on disk", async () => {
    respond(CHURN, {
      ...HYGIENE,
      secrets: [{ ...HYGIENE.secrets[0], still_present: true }],
    });
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText("still on disk")).toBeInTheDocument();
  });

  it("flags a template that has real values in it", async () => {
    respond(CHURN, {
      ...HYGIENE,
      secrets: [],
      templates: [{ path: ".env.example", commits: 1, filled_keys: ["STRIPE_SECRET_KEY"] }],
    });
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText("real values")).toBeInTheDocument();
    expect(screen.getByText(/STRIPE_SECRET_KEY/)).toBeInTheDocument();
  });

  it("keeps build output collapsed and calls it what it is", async () => {
    respond();
    render(<RepoRisk project={PROJECT} />);
    expect(await screen.findByText(/Build output committed \(1 path\)/)).toBeInTheDocument();
  });

  it("says so when nothing was committed that should not have been", async () => {
    respond(CHURN, { ...HYGIENE, clean: true, secrets: [], noise: [], templates: [] });
    render(<RepoRisk project={PROJECT} />);
    expect(
      await screen.findByText(/No credentials or build output in 20 commits/),
    ).toBeInTheDocument();
  });

  it("does not call a committed .env.example a leak", async () => {
    respond(CHURN, { ...HYGIENE, clean: true, secrets: [], noise: [] });
    render(<RepoRisk project={PROJECT} />);
    await screen.findByText(/No credentials or build output/);
    expect(screen.queryByText(".env.example")).not.toBeInTheDocument();
  });
});

describe("failure", () => {
  it("reports it rather than showing an empty panel", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("Database is locked."));
    render(<RepoRisk project={PROJECT} />);
    expect((await screen.findAllByText(/Database is locked/)).length).toBeGreaterThan(0);
  });
});
