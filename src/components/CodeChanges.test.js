import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CodeChanges from "@/components/CodeChanges";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const PROJECT = { id: 3, name: "Demo", leader_id: 1 };
const PEOPLE = [
  { id: 1, name: "Alfah Lubisi", role_title: "Data lead" },
  { id: 2, name: "Reviewer Two", role_title: null },
];

const PENDING = {
  id: 7,
  project_id: 3,
  path: "core.py",
  action: "modify",
  origin: "human",
  note: "Cap the rows returned.",
  status: "pending",
  approved_by: null,
  approved_by_id: null,
  created_at: "2026-09-21",
  lines: { added: 2, removed: 1 },
  diff: "--- a/core.py\n+++ b/core.py\n-def fetch(source):\n+def fetch(source, limit):\n",
};

const IMPACT = {
  change_id: 7,
  status: "pending",
  path: "core.py",
  action: "modify",
  lines: { added: 2, removed: 1 },
  still_as_approved: null,
  symbols: [
    { name: "fetch", kind: "function", change: "signature_changed", signature: "def fetch(source, limit):", line: 4 },
  ],
  references: {},
  effects: [
    {
      level: "risk",
      text: "fetch takes different arguments now, and 2 other file(s) mention it: report.py, api.py. Callers may not match.",
    },
    { level: "good", text: "fetch is mentioned by 1 test file(s): tests/test_core.py" },
  ],
  limits: ["Found by pattern matching, not by parsing."],
};

function server({ list = [PENDING], detail = PENDING, impact = IMPACT } = {}) {
  vi.mocked(api.get).mockImplementation((path) => {
    if (path === "/people") return Promise.resolve(PEOPLE);
    if (path.endsWith("/impact")) return Promise.resolve(impact);
    if (path.includes("/code/changes/")) return Promise.resolve(detail);
    return Promise.resolve(list);
  });
}

beforeEach(() => vi.clearAllMocks());

describe("the list", () => {
  it("names the leader who reviews this project", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    expect(
      await screen.findByText(/Alfah Lubisi reviews and approves/),
    ).toBeInTheDocument();
  });

  it("says what to do when no leader is set", async () => {
    server();
    render(<CodeChanges project={{ ...PROJECT, leader_id: null }} />);
    expect(await screen.findByText(/No leader set for this project/)).toBeInTheDocument();
  });

  it("counts what is waiting", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    expect(await screen.findByText("1 waiting")).toBeInTheDocument();
  });

  it("explains itself when nothing has been changed yet", async () => {
    server({ list: [] });
    render(<CodeChanges project={PROJECT} />);
    expect(
      await screen.findByText(/Nothing has been changed here yet/),
    ).toBeInTheDocument();
  });

  it("opens the waiting change without being asked", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    // The diff belongs to the detail panel, so its presence means the
    // change was opened by default.
    expect(await screen.findByText("+def fetch(source, limit):")).toBeInTheDocument();
  });
});

describe("the outline", () => {
  it("leads with what the change reaches outside its own file", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    expect(await screen.findByText(/This one reaches other files/)).toBeInTheDocument();
    expect(screen.getAllByText(/report.py, api.py/)[0]).toBeInTheDocument();
  });

  it("names the definition that changed and how", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    expect(await screen.findByText("function fetch signature changed")).toBeInTheDocument();
  });

  it("shows the reassuring findings too, not only the dangers", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    expect(
      await screen.findByText(/mentioned by 1 test file\(s\)/),
    ).toBeInTheDocument();
  });

  it("says what it did not look at", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    expect(await screen.findByText("What this did not look at")).toBeInTheDocument();
  });

  it("warns when the file has moved on since it was approved", async () => {
    const approved = { ...PENDING, status: "approved", approved_by: "Alfah Lubisi" };
    server({
      list: [approved],
      detail: approved,
      impact: { ...IMPACT, status: "approved", still_as_approved: false },
    });
    render(<CodeChanges project={PROJECT} />);
    expect(
      await screen.findByText(/no longer exactly this/),
    ).toBeInTheDocument();
  });
});

describe("deciding", () => {
  it("approves in the leader's name by default, and says it writes the file", async () => {
    server();
    vi.mocked(api.post).mockResolvedValue({ ...PENDING, status: "approved" });
    render(<CodeChanges project={PROJECT} />);

    const button = await screen.findByRole("button", { name: /Approve and write the file/i });
    expect(screen.getByText(/does not commit or push/i)).toBeInTheDocument();
    await userEvent.click(button);

    expect(api.post).toHaveBeenCalledWith("/code/changes/7/approve", {
      person_id: 1,
      note: null,
    });
  });

  it("lets someone else be named as the approver", async () => {
    server();
    vi.mocked(api.post).mockResolvedValue({ ...PENDING, status: "approved" });
    render(<CodeChanges project={PROJECT} />);

    await screen.findByRole("button", { name: /Approve and write the file/i });
    await userEvent.selectOptions(screen.getByRole("combobox"), "2");
    await userEvent.click(screen.getByRole("button", { name: /Approve and write the file/i }));

    expect(api.post).toHaveBeenCalledWith(
      "/code/changes/7/approve",
      expect.objectContaining({ person_id: 2 }),
    );
  });

  it("marks the leader in the list of approvers", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    const select = await screen.findByRole("combobox");
    expect(within(select).getByText("Alfah Lubisi (leader)")).toBeInTheDocument();
  });

  it("sends the reason with a rejection", async () => {
    server();
    vi.mocked(api.post).mockResolvedValue({ ...PENDING, status: "rejected" });
    render(<CodeChanges project={PROJECT} />);

    await screen.findByRole("button", { name: /Reject/i });
    await userEvent.type(
      screen.getByPlaceholderText(/word on the decision/i),
      "Breaks report.py",
    );
    await userEvent.click(screen.getByRole("button", { name: /Reject/i }));

    expect(api.post).toHaveBeenCalledWith(
      "/code/changes/7/reject",
      expect.objectContaining({ note: "Breaks report.py" }),
    );
  });

  it("shows the failure rather than pretending it worked", async () => {
    server();
    const failure = new Error("This project has no leader set.");
    vi.mocked(api.post).mockRejectedValue(failure);
    render(<CodeChanges project={PROJECT} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Approve and write the file/i }),
    );
    expect(await screen.findByText(/no leader set/)).toBeInTheDocument();
  });
});

describe("undoing", () => {
  const approved = {
    ...PENDING,
    status: "approved",
    approved_by: "Alfah Lubisi",
  };

  it("offers to put the file back, and explains the limit", async () => {
    server({
      list: [approved],
      detail: approved,
      impact: { ...IMPACT, status: "approved", still_as_approved: true },
    });
    render(<CodeChanges project={PROJECT} />);
    expect(
      await screen.findByRole("button", { name: /Put the file back as it was/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Refused if the file has moved on/i)).toBeInTheDocument();
  });

  it("offers no revert on a change that was never approved", async () => {
    server();
    render(<CodeChanges project={PROJECT} />);
    await screen.findByRole("button", { name: /Approve and write the file/i });
    expect(
      screen.queryByRole("button", { name: /Put the file back/i }),
    ).not.toBeInTheDocument();
  });

  it("reports a refusal to revert in full", async () => {
    server({
      list: [approved],
      detail: approved,
      impact: { ...IMPACT, status: "approved", still_as_approved: false },
    });
    vi.mocked(api.post).mockRejectedValue(
      new Error("core.py has changed since this was approved."),
    );
    render(<CodeChanges project={PROJECT} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Put the file back as it was/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/has changed since this was approved/)).toBeInTheDocument(),
    );
  });
});

describe("where a change came from", () => {
  it("says when the agent wrote it", async () => {
    const fromAgent = { ...PENDING, origin: "agent" };
    server({ list: [fromAgent], detail: fromAgent });
    render(<CodeChanges project={PROJECT} />);
    expect(await screen.findByText(/Written by the agent/)).toBeInTheDocument();
  });
});
