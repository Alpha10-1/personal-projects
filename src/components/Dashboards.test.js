/**
 * Linked reports, and Power BI's refresh state.
 *
 * The behaviour worth holding onto is the warning when a removal is not
 * really a removal: a report that came from Power BI comes back on the next
 * sync, and deleting it without saying so would look like a bug in the
 * tracker rather than the truth.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Dashboards from "@/components/Dashboards";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

const BOARDS = [
  {
    id: 1,
    name: "Shift forecast",
    url: "https://app.powerbi.com/reports/1",
    source: "powerbi",
    workspace_name: "Operations",
    dataset_name: "Shifts",
    last_refresh_status: "Completed",
  },
  {
    id: 2,
    name: "A link I pasted",
    url: "https://example.com/report",
    source: "manual",
    last_refresh_status: null,
  },
];

function respond({ configured = true, boards = BOARDS } = {}) {
  vi.mocked(api.get).mockImplementation((url) =>
    url.includes("powerbi/status")
      ? Promise.resolve({ configured, reason: configured ? null : "Not signed in." })
      : Promise.resolve(boards),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  respond();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.del).mockResolvedValue({});
  vi.mocked(api.post).mockResolvedValue({ updated: 2 });
});

afterEach(() => vi.restoreAllMocks());

describe("what it lists", () => {
  it("shows every linked report", async () => {
    render(<Dashboards />);
    expect(await screen.findByText("Shift forecast")).toBeInTheDocument();
    expect(screen.getByText("A link I pasted")).toBeInTheDocument();
  });

  it("shows where a Power BI report lives", async () => {
    render(<Dashboards />);
    expect(await screen.findByText(/Operations · Shifts/)).toBeInTheDocument();
  });

  it("narrows to one project when it is on a project", async () => {
    render(<Dashboards projectId={3} />);
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith("/dashboards", { project_id: 3 }),
    );
  });

  it("asks for everything when it is not", async () => {
    render(<Dashboards />);
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/dashboards", undefined));
  });

  it("says so when nothing is linked", async () => {
    respond({ boards: [] });
    render(<Dashboards />);
    expect(await screen.findByText(/No reports|Nothing/i)).toBeInTheDocument();
  });
});

describe("removing one", () => {
  it("warns that a Power BI report will come back on the next sync", async () => {
    render(<Dashboards />);
    await screen.findByText("Shift forecast");

    await userEvent.click(screen.getByRole("button", { name: "Remove Shift forecast" }));
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining("will come back on the next sync"),
    );
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/dashboards/1"));
  });

  it("asks more simply about one that was pasted in", async () => {
    render(<Dashboards />);
    await screen.findByText("A link I pasted");

    await userEvent.click(screen.getByRole("button", { name: "Remove A link I pasted" }));
    expect(window.confirm).toHaveBeenCalledWith(
      expect.not.stringContaining("come back on the next sync"),
    );
  });

  it("does nothing when the question is answered no", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    render(<Dashboards />);
    await screen.findByText("Shift forecast");

    await userEvent.click(screen.getByRole("button", { name: "Remove Shift forecast" }));
    expect(api.del).not.toHaveBeenCalled();
  });
});

describe("Power BI", () => {
  it("offers a sync when the Service is connected", async () => {
    render(<Dashboards />);
    const sync = await screen.findByRole("button", { name: /Sync/i });
    await userEvent.click(sync);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/powerbi/sync"));
  });

  it("says why rather than offering a sync that cannot work", async () => {
    respond({ configured: false });
    render(<Dashboards />);
    expect(await screen.findByText(/Not signed in/)).toBeInTheDocument();
  });

  it("reports a failed sync", async () => {
    vi.mocked(api.post).mockRejectedValue(new Error("The token has expired."));
    render(<Dashboards />);
    await userEvent.click(await screen.findByRole("button", { name: /Sync/i }));
    expect(await screen.findByText(/token has expired/)).toBeInTheDocument();
  });
});
