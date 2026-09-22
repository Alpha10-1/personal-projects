import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ExplainPanel from "@/components/ExplainPanel";

const FACTS = {
  path: "core.py",
  start_line: 4,
  end_line: 8,
  language: "python",
  enclosing: { name: "fetch_rows", kind: "function", signature: "def fetch_rows(source):", line: 4 },
  defines: [],
  shared_values: [{ name: "MAX_ROWS", kind: "constant", line: 1 }],
  imports_used: [],
  references: {},
  consequences: [
    { level: "watch", text: "Changing fetch_rows affects 1 other file(s): report.py" },
  ],
  limits: ["Found by pattern matching, not by parsing."],
};

const EXPLANATION = {
  summary: "Returns at most `MAX_ROWS` items from `source`.",
  walkthrough: [
    { lines: "6-7", what: "Rejects a missing source outright rather than returning empty." },
  ],
  role_in_the_system: "Called by `report.py` to page results.",
  shared_state: "Reads `MAX_ROWS`, which every importer sees.",
  if_you_change_it: ["`report.py` calls it with one argument and would break."],
  watch_out: ["A `None` source raises rather than returning an empty list."],
  unknowns: ["Whether callers rely on the slice being a copy."],
};

const result = (overrides = {}) => ({
  facts: FACTS,
  explanation: EXPLANATION,
  model: "claude-sonnet-5",
  cached: false,
  reason: null,
  ...overrides,
});

describe("the written explanation", () => {
  it("leads with the summary", () => {
    render(<ExplainPanel result={result()} />);
    expect(screen.getByText(/Returns at most/)).toBeInTheDocument();
  });

  it("shows the walkthrough with the lines it refers to", () => {
    render(<ExplainPanel result={result()} />);
    expect(screen.getByText("6-7")).toBeInTheDocument();
    expect(screen.getByText(/Rejects a missing source/)).toBeInTheDocument();
  });

  it("shows why it exists, what it shares, and what breaks", () => {
    render(<ExplainPanel result={result()} />);
    expect(screen.getByText("Why it is here")).toBeInTheDocument();
    expect(screen.getByText("What it shares with the rest of the system")).toBeInTheDocument();
    expect(screen.getByText("If you change it")).toBeInTheDocument();
    expect(screen.getByText(/would break/)).toBeInTheDocument();
  });

  it("shows what the model could not tell", () => {
    render(<ExplainPanel result={result()} />);
    expect(screen.getByText("Could not tell from this")).toBeInTheDocument();
    expect(screen.getByText(/slice being a copy/)).toBeInTheDocument();
  });

  it("names the model and says the facts below can be checked", () => {
    render(<ExplainPanel result={result()} />);
    expect(screen.getByText(/Written by claude-sonnet-5/)).toBeInTheDocument();
    expect(screen.getByText(/It can be wrong/)).toBeInTheDocument();
  });

  it("omits a section the model left empty rather than showing a blank one", () => {
    render(
      <ExplainPanel
        result={result({ explanation: { ...EXPLANATION, shared_state: "", watch_out: [] } })}
      />,
    );
    expect(
      screen.queryByText("What it shares with the rest of the system"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Easy to miss")).not.toBeInTheDocument();
  });
});

describe("the measured facts underneath", () => {
  it("are present as evidence, collapsed", () => {
    render(<ExplainPanel result={result()} />);
    expect(screen.getByText("What this was read from")).toBeInTheDocument();
    expect(screen.getByText(/Inside the function/)).toBeInTheDocument();
    // Once in the model's prose and once in the facts, which is the point:
    // the claim and the evidence for it are both on screen.
    expect(screen.getByText("Module-level values read:")).toBeInTheDocument();
    expect(screen.getAllByText("MAX_ROWS").length).toBeGreaterThan(0);
  });

  it("include the limits of the search", () => {
    render(<ExplainPanel result={result()} />);
    expect(screen.getByText(/not by parsing/)).toBeInTheDocument();
  });
});

describe("when the model is unavailable", () => {
  it("says why, and still shows the facts", () => {
    render(
      <ExplainPanel
        result={result({
          explanation: null,
          model: null,
          reason: "No ANTHROPIC_API_KEY is set.",
        })}
      />,
    );
    expect(screen.getByText("No written explanation.")).toBeInTheDocument();
    expect(screen.getByText(/No ANTHROPIC_API_KEY is set/)).toBeInTheDocument();
    expect(screen.getByText(/Inside the function/)).toBeInTheDocument();
  });

  it("offers no refresh, since there is nothing to ask again", () => {
    render(
      <ExplainPanel
        result={result({ explanation: null, reason: "Rate limited." })}
        onRefresh={() => {}}
      />,
    );
    expect(screen.queryByLabelText("Ask again")).not.toBeInTheDocument();
  });
});

describe("cost", () => {
  it("says when the answer came from the cache and cost nothing", () => {
    render(<ExplainPanel result={result({ cached: true })} />);
    expect(screen.getByText("from cache")).toBeInTheDocument();
  });

  it("does not claim a cache hit on a fresh answer", () => {
    render(<ExplainPanel result={result()} />);
    expect(screen.queryByText("from cache")).not.toBeInTheDocument();
  });

  it("can be asked again deliberately", async () => {
    const onRefresh = vi.fn();
    render(<ExplainPanel result={result({ cached: true })} onRefresh={onRefresh} />);
    await userEvent.click(screen.getByLabelText("Ask again"));
    expect(onRefresh).toHaveBeenCalled();
  });
});

describe("the shell", () => {
  it("shows nothing at all without a result", () => {
    const { container } = render(<ExplainPanel result={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a spinner while thinking", () => {
    render(<ExplainPanel loading />);
    expect(screen.getByText(/Reading the code/)).toBeInTheDocument();
  });

  it("closes", async () => {
    const onClose = vi.fn();
    render(<ExplainPanel result={result()} onClose={onClose} />);
    await userEvent.click(screen.getByLabelText("Close explanation"));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("when the highlight is not a definition", () => {
  const hinted = {
    kind: "imports",
    what: "imports",
    hint: "Imports say what this file uses, not what it does.",
  };

  it("says what was highlighted, gently, above the answer", () => {
    render(
      <ExplainPanel result={result({ facts: { ...FACTS, selection: hinted } })} />,
    );
    expect(screen.getByText(/You highlighted/)).toBeInTheDocument();
    expect(screen.getByText("imports")).toBeInTheDocument();
    expect(screen.getByText(/not what it does/)).toBeInTheDocument();
  });

  it("still shows the explanation rather than replacing it", () => {
    render(
      <ExplainPanel result={result({ facts: { ...FACTS, selection: hinted } })} />,
    );
    expect(screen.getByText(/Returns at most/)).toBeInTheDocument();
  });

  it("says nothing extra when the highlight was a definition", () => {
    const selection = { kind: "definition", what: "the function fetch_rows", hint: null };
    render(
      <ExplainPanel result={result({ facts: { ...FACTS, selection } })} />,
    );
    expect(screen.queryByText(/You highlighted/)).not.toBeInTheDocument();
  });
});

describe("a model answer of the wrong shape", () => {
  // The crash this whole change exists for: a string where a list was
  // expected. The backend reshapes it now; this is the second net.
  it("renders a list field answered as one string", () => {
    render(
      <ExplainPanel
        result={result({
          explanation: { ...EXPLANATION, if_you_change_it: "Only one thing breaks." },
        })}
      />,
    );
    expect(screen.getByText(/Only one thing breaks/)).toBeInTheDocument();
  });

  it("renders a walkthrough of bare sentences", () => {
    render(
      <ExplainPanel
        result={result({
          explanation: { ...EXPLANATION, walkthrough: "It checks, then slices." },
        })}
      />,
    );
    expect(screen.getByText(/It checks, then slices/)).toBeInTheDocument();
  });

  it("omits a section the model left empty", () => {
    render(
      <ExplainPanel
        result={result({ explanation: { ...EXPLANATION, watch_out: "" } })}
      />,
    );
    expect(screen.queryByText("Easy to miss")).not.toBeInTheDocument();
  });
});
