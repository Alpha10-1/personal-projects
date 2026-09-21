import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DiffView, { countChanges } from "@/components/DiffView";

const DIFF = [
  "--- a/src/main.py",
  "+++ b/src/main.py",
  "@@ -1,3 +1,3 @@",
  " def greet():",
  "-    return 'hello'",
  "+    return 'hi'",
  " ",
].join("\n");

/** The classes carry the meaning here, so the tests read them. A diff whose
 *  additions are not green is the exact complaint this component exists to
 *  answer, and it would pass every test that only checked the text. */
const lineFor = (container, text) =>
  [...container.querySelectorAll("span.block")].find((el) =>
    el.textContent.includes(text),
  );

describe("counting", () => {
  it("counts added and removed lines, ignoring the file headers", () => {
    expect(countChanges(DIFF)).toEqual({ added: 1, removed: 1 });
  });

  it("is zero for nothing", () => {
    expect(countChanges("")).toEqual({ added: 0, removed: 0 });
    expect(countChanges(null)).toEqual({ added: 0, removed: 0 });
  });
});

describe("rendering", () => {
  it("colours additions and removals differently", () => {
    const { container } = render(<DiffView diff={DIFF} />);
    const added = lineFor(container, "return 'hi'");
    const removed = lineFor(container, "return 'hello'");
    expect(added.className).toContain("--good");
    expect(removed.className).toContain("--critical");
  });

  it("sets hunk and file headers apart from the code", () => {
    const { container } = render(<DiffView diff={DIFF} />);
    expect(lineFor(container, "@@ -1,3").className).toContain("--surface-2");
    expect(lineFor(container, "+++ b/src/main.py").className).toContain("font-medium");
  });

  it("leaves context lines uncoloured", () => {
    const { container } = render(<DiffView diff={DIFF} />);
    const context = lineFor(container, "def greet():");
    expect(context.className).not.toContain("--good");
    expect(context.className).not.toContain("--critical");
  });

  it("says so when there is nothing to show", () => {
    render(<DiffView diff="" />);
    expect(screen.getByText("No changes to show.")).toBeInTheDocument();
  });

  it("does not mistake +++ for an added line", () => {
    expect(countChanges("+++ b/x\n+one\n")).toEqual({ added: 1, removed: 0 });
  });

  it("does not mistake --- for a removed line", () => {
    expect(countChanges("--- a/x\n-one\n")).toEqual({ added: 0, removed: 1 });
  });
});
