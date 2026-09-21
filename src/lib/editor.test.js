import { describe, expect, it } from "vitest";

import {
  darkChrome,
  languageFor,
  languageName,
  lightChrome,
  selectedLines,
  syntaxTheme,
  themeFor,
} from "@/lib/editor";

describe("choosing a language", () => {
  it("recognises the file types in these repositories", () => {
    expect(languageName("app/main.py")).toBe("Python");
    expect(languageName("src/components/Card.jsx")).toBe("JavaScript");
    expect(languageName("src/lib/api.ts")).toBe("TypeScript");
    expect(languageName("package.json")).toBe("JSON");
    expect(languageName("README.md")).toBe("Markdown");
  });

  it("is case-insensitive about the extension", () => {
    expect(languageName("SETUP.PY")).toBe("Python");
  });

  it("returns null rather than guessing at an unknown type", () => {
    expect(languageName("data.parquet")).toBeNull();
    expect(languageName("Dockerfile")).toBeNull();
    expect(languageName("")).toBeNull();
  });

  it("gives no extension to an unknown type, so the editor stays plain", () => {
    expect(languageFor("data.parquet")).toEqual([]);
  });

  it("builds an extension for a known type", () => {
    expect(languageFor("app/main.py")).toHaveLength(1);
  });
});

describe("the editor theme", () => {
  it("hands back the dark chrome in dark mode and the light one otherwise", () => {
    // The two differ only in CodeMirror's own `dark` flag; the colours are
    // custom properties either way. This is what the white-background bug
    // came down to, so it is worth asserting rather than assuming.
    expect(themeFor(true)[0]).toBe(darkChrome);
    expect(themeFor(false)[0]).toBe(lightChrome);
  });

  it("includes the syntax highlighting alongside the chrome", () => {
    expect(themeFor(false)).toHaveLength(2);
  });
});

describe("the syntax theme", () => {
  it("is defined entirely in custom properties, so dark mode follows the app", () => {
    // The whole reason the theme is not hex values: the editor has to
    // change with the page, and re-mounting it on a theme toggle would
    // lose the cursor and the scroll position.
    const specs = syntaxTheme.specs ?? [];
    const colours = specs.map((spec) => spec.color).filter(Boolean);
    expect(colours.length).toBeGreaterThan(5);
    expect(colours.every((colour) => colour.startsWith("var(--syn-"))).toBe(true);
  });
});

/** A stand-in for CodeMirror's EditorState: only the parts read here. */
function state({ from, to, doc }) {
  const lines = doc.split("\n");
  const lineAt = (offset) => {
    let seen = 0;
    for (let index = 0; index < lines.length; index += 1) {
      seen += lines[index].length + 1;
      if (offset < seen) return { number: index + 1 };
    }
    return { number: lines.length };
  };
  return {
    selection: { main: { from, to, empty: from === to } },
    doc: { lineAt },
    sliceDoc: (a, b) => doc.slice(a, b),
  };
}

describe("turning a selection into line numbers", () => {
  const doc = "one\ntwo\nthree\nfour";

  it("is null when nothing is selected, so no button appears", () => {
    expect(selectedLines(state({ from: 5, to: 5, doc }))).toBeNull();
  });

  it("is null when there is no state at all", () => {
    expect(selectedLines(undefined)).toBeNull();
    expect(selectedLines({})).toBeNull();
  });

  it("reports one line when the selection is within it", () => {
    const result = selectedLines(state({ from: 4, to: 7, doc }));
    expect(result).toMatchObject({ from: 2, to: 2, text: "two" });
  });

  it("reports the span when the selection crosses lines", () => {
    const result = selectedLines(state({ from: 0, to: 13, doc }));
    expect(result.from).toBe(1);
    expect(result.to).toBe(3);
  });
});
