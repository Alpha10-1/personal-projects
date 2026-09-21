import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CodeEditor from "@/components/CodeEditor";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

// CodeMirror needs layout APIs jsdom does not have, and none of what is
// worth testing here is inside it. A textarea stands in for the editing,
// and `onUpdate` is exposed so a selection can be simulated.
let lastUpdate;
vi.mock("@uiw/react-codemirror", () => ({
  default: ({ value, onChange, onUpdate, editable }) => {
    lastUpdate = onUpdate;
    return (
      <textarea
        aria-label="code"
        value={value}
        readOnly={editable === false}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  },
}));

const PROJECT = { id: 3, name: "Demo" };
const FILE = {
  path: "core.py",
  content: "MAX = 1\n\n\ndef fetch():\n    return MAX\n",
  binary: false,
  truncated: false,
  size: 40,
  editor_url: "vscode://file/x/core.py",
};

const EXPLANATION = {
  path: "core.py",
  start_line: 4,
  end_line: 5,
  enclosing: { name: "fetch", kind: "function", signature: "def fetch():", line: 4 },
  defines: [],
  shared_values: [{ name: "MAX", kind: "constant", line: 1 }],
  imports_used: [],
  consequences: [
    { level: "watch", text: "Changing fetch affects 2 other file(s): a.py, b.py" },
    { level: "good", text: "1 test file(s) mention fetch: tests/test_core.py" },
  ],
  limits: ["Read from the repository by pattern and text search, not by a model."],
};

function server({ file = FILE, explanation = EXPLANATION } = {}) {
  vi.mocked(api.get).mockImplementation((path) =>
    Promise.resolve(path.endsWith("/explain") ? explanation : file),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  lastUpdate = undefined;
});

async function mount(options) {
  server(options);
  const view = render(<CodeEditor project={PROJECT} path="core.py" />);
  await screen.findByLabelText("code");
  return view;
}

/** Pretend the user dragged over lines 4 to 5. */
function selectLines(from, to) {
  lastUpdate({
    selectionSet: true,
    docChanged: false,
    state: {
      selection: { main: { from, to, empty: from === to } },
      doc: { lineAt: (offset) => ({ number: offset === from ? 4 : 5 }) },
      sliceDoc: () => "def fetch():\n    return MAX",
    },
  });
}

describe("loading a file", () => {
  it("shows its contents and names the language", async () => {
    await mount();
    expect(screen.getByLabelText("code")).toHaveValue(FILE.content);
    expect(screen.getByText("Python")).toBeInTheDocument();
  });

  it("refuses to edit a file that was truncated for size", async () => {
    await mount({ file: { ...FILE, truncated: true, size: 900000 } });
    expect(screen.getByLabelText("code")).toHaveAttribute("readonly");
    expect(screen.getByText(/saving would lose the rest/i)).toBeInTheDocument();
  });

  it("describes a binary file rather than rendering it", async () => {
    server({ file: { ...FILE, binary: true, content: null, size: 2048 } });
    render(<CodeEditor project={PROJECT} path="logo.png" />);
    expect(await screen.findByText(/is a binary file/)).toBeInTheDocument();
  });
});

describe("editing", () => {
  it("offers nothing to save until something changes", async () => {
    await mount();
    expect(screen.queryByRole("button", { name: /Save for review/i })).not.toBeInTheDocument();
  });

  it("marks the file unsaved and says saving does not write it", async () => {
    await mount();
    await userEvent.type(screen.getByLabelText("code"), "# a comment\n");
    expect(screen.getByText("unsaved")).toBeInTheDocument();
    expect(screen.getByText(/does not write the file/i)).toBeInTheDocument();
  });

  it("raises a change rather than writing, and says the file is untouched", async () => {
    await mount();
    vi.mocked(api.post).mockResolvedValue({ id: 12, status: "pending" });

    await userEvent.type(screen.getByLabelText("code"), "x");
    await userEvent.click(screen.getByRole("button", { name: /Save for review/i }));

    expect(api.post).toHaveBeenCalledWith(
      "/projects/3/code/changes",
      expect.objectContaining({ path: "core.py", content: expect.stringContaining("x") }),
    );
    expect(await screen.findByText(/waiting for\s+approval/i)).toBeInTheDocument();
    expect(screen.getByText(/file on disk has not changed/i)).toBeInTheDocument();
  });

  it("sends the reason for the change when one is given", async () => {
    await mount();
    vi.mocked(api.post).mockResolvedValue({ id: 12 });
    await userEvent.type(screen.getByLabelText("code"), "x");
    await userEvent.type(screen.getByRole("textbox", { name: /Why this change/i }), "Tidy up");
    await userEvent.click(screen.getByRole("button", { name: /Save for review/i }));
    expect(api.post).toHaveBeenCalledWith(
      "/projects/3/code/changes",
      expect.objectContaining({ note: "Tidy up" }),
    );
  });

  it("discard puts the original text back", async () => {
    await mount();
    await userEvent.type(screen.getByLabelText("code"), "x");
    await userEvent.click(screen.getByRole("button", { name: /Discard/i }));
    expect(screen.getByLabelText("code")).toHaveValue(FILE.content);
    expect(screen.queryByText("unsaved")).not.toBeInTheDocument();
  });
});

describe("the Explain button", () => {
  it("is absent until something is selected", async () => {
    await mount();
    expect(screen.queryByRole("button", { name: /Explain/i })).not.toBeInTheDocument();
  });

  it("appears on a selection and names the lines", async () => {
    await mount();
    selectLines(10, 30);
    expect(await screen.findByRole("button", { name: /Explain lines 4–5/ })).toBeInTheDocument();
  });

  it("asks the backend for those lines and shows the answer", async () => {
    await mount();
    selectLines(10, 30);
    await userEvent.click(await screen.findByRole("button", { name: /Explain/i }));

    expect(api.get).toHaveBeenCalledWith("/projects/3/code/explain", {
      path: "core.py",
      start_line: 4,
      end_line: 5,
    });
    expect(await screen.findByText(/Inside the function/)).toBeInTheDocument();
    expect(screen.getByText(/affects 2 other file\(s\)/)).toBeInTheDocument();
  });

  it("shows the shared values the selection reads", async () => {
    await mount();
    selectLines(10, 30);
    await userEvent.click(await screen.findByRole("button", { name: /Explain/i }));
    expect(await screen.findByText("Shared with other files")).toBeInTheDocument();
    expect(screen.getByText("MAX")).toBeInTheDocument();
  });

  it("disappears again when the selection is cleared", async () => {
    await mount();
    selectLines(10, 30);
    await screen.findByRole("button", { name: /Explain/i });
    lastUpdate({
      selectionSet: true,
      docChanged: false,
      state: {
        selection: { main: { from: 5, to: 5, empty: true } },
        doc: { lineAt: () => ({ number: 1 }) },
        sliceDoc: () => "",
      },
    });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Explain/i })).not.toBeInTheDocument(),
    );
  });
});
