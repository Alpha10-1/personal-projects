/**
 * Notes, links and files.
 *
 * Three small panels with the same shape, and the same thing worth
 * asserting in each: that a delete asks first, and that what comes back
 * from the server is shown rather than a hopeful local guess.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LibraryPanels from "@/components/LibraryPanels";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  API_URL: "http://localhost:8000",
  downloadUrl: (id) => `http://localhost:8000/files/${id}/download`,
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn(), upload: vi.fn() },
}));

const NOTES = [
  {
    id: 1,
    title: "Decision: no Alembic yet",
    body: "Revisit when the schema moves.",
    kind: "decision",
    pinned: true,
    source: "human",
  },
  {
    id: 2,
    title: "A quiet week",
    body: "Nothing shipped.",
    kind: "update",
    pinned: false,
    source: "agent",
  },
];
const LINKS = [
  { id: 3, title: "The spec", url: "https://example.com/spec", kind: "doc" },
];
const FILES = [
  { id: 4, filename: "export.csv", size: 2048, content_type: "text/csv" },
];

function respond() {
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/notes") return Promise.resolve(NOTES);
    if (url === "/links") return Promise.resolve(LINKS);
    if (url === "/files") return Promise.resolve(FILES);
    return Promise.resolve([]);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  respond();
  vi.mocked(api.patch).mockResolvedValue({});
  vi.mocked(api.del).mockResolvedValue({});
  vi.mocked(api.post).mockResolvedValue({ id: 9 });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => vi.restoreAllMocks());

describe("notes", () => {
  it("shows them with their bodies", async () => {
    render(<LibraryPanels />);
    expect(await screen.findByText("Decision: no Alembic yet")).toBeInTheDocument();
    expect(screen.getByText(/Revisit when the schema moves/)).toBeInTheDocument();
  });

  it("can pin and unpin, which is how the useful ones stay findable", async () => {
    render(<LibraryPanels />);
    await screen.findByText("Decision: no Alembic yet");

    await userEvent.click(screen.getByRole("button", { name: "Unpin note" }));
    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith("/notes/1", { pinned: false }),
    );
  });

  it("asks before deleting", async () => {
    render(<LibraryPanels />);
    await screen.findByText("Decision: no Alembic yet");

    await userEvent.click(screen.getAllByRole("button", { name: "Delete note" })[0]);
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/notes/1"));
  });

  it("does not delete when the question is answered no", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    render(<LibraryPanels />);
    await screen.findByText("Decision: no Alembic yet");

    await userEvent.click(screen.getAllByRole("button", { name: "Delete note" })[0]);
    expect(api.del).not.toHaveBeenCalled();
  });
});

describe("links", () => {
  it("shows them as links that actually go somewhere", async () => {
    render(<LibraryPanels />);
    const link = await screen.findByRole("link", { name: /The spec/ });
    expect(link).toHaveAttribute("href", "https://example.com/spec");
  });

  it("opens them in a new tab, safely", async () => {
    render(<LibraryPanels />);
    const link = await screen.findByRole("link", { name: /The spec/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
  });

  it("asks before deleting one", async () => {
    render(<LibraryPanels />);
    await screen.findByRole("link", { name: /The spec/ });
    await userEvent.click(screen.getByRole("button", { name: "Delete link" }));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/links/3"));
  });
});

describe("files", () => {
  it("lists them by name", async () => {
    render(<LibraryPanels />);
    expect(await screen.findByText("export.csv")).toBeInTheDocument();
  });

  it("offers a download that names the file it is for", async () => {
    render(<LibraryPanels />);
    await screen.findByText("export.csv");
    expect(
      screen.getByRole("link", { name: "Download export.csv" }),
    ).toBeInTheDocument();
  });

  it("asks before deleting one", async () => {
    render(<LibraryPanels />);
    await screen.findByText("export.csv");
    await userEvent.click(screen.getByRole("button", { name: "Delete export.csv" }));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/files/4"));
  });
});

describe("scope", () => {
  it("asks for one project's library when it is on a project", async () => {
    render(<LibraryPanels projectId={3} />);
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith("/notes", { project_id: 3 }),
    );
  });
});
