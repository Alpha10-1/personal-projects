import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Markdown from "@/components/Markdown";

const html = (markdown) => render(<Markdown>{markdown}</Markdown>).container;

describe("emphasis", () => {
  it("renders bold and italics rather than showing the asterisks", () => {
    // The bug this fixes: answers arrived as Markdown and were shown as raw
    // text, so emphasis appeared as punctuation.
    const container = html("The **admin-dashboard** repo is *probably* stale.");

    expect(container.querySelector("strong")).toHaveTextContent("admin-dashboard");
    expect(container.querySelector("em")).toHaveTextContent("probably");
    expect(container.textContent).not.toContain("**");
  });

  it("marks inline code so a path does not read as prose", () => {
    const container = html("Look at `backend/app/github.py` first.");
    const code = container.querySelector("code");

    expect(code).toHaveTextContent("backend/app/github.py");
    expect(code.className).toContain("font-mono");
  });

  it("strikes through deleted text", () => {
    expect(html("~~dropped~~ kept").querySelector("del")).toHaveTextContent("dropped");
  });
});

describe("blocks", () => {
  it("does not style a fenced block as if it were inline code", () => {
    // A fence with no language carries no class and react-markdown no longer
    // says whether a node is inline, so this is the case that silently broke
    // when the decision was made from props.
    const container = html("```\nnpm run dev\n```");
    const code = container.querySelector("pre code");

    expect(container.querySelector("pre")).toBeTruthy();
    expect(code).toHaveTextContent("npm run dev");
    // The reset lives on the pre, so the code inside must not keep the
    // inline tint.
    expect(container.querySelector("pre").className).toContain("[&_code]:bg-transparent");
  });

  it("renders a labelled fence the same way", () => {
    const container = html("```python\nprint(1)\n```");

    expect(container.querySelector("pre code")).toHaveTextContent("print(1)");
  });

  it("renders lists as lists", () => {
    const container = html("- one\n- two\n- three");

    expect(container.querySelectorAll("li")).toHaveLength(3);
  });

  it("renders a GFM table", () => {
    const container = html(
      "| repo | commits |\n| --- | --- |\n| ride-native | 66 |\n| admin-dashboard | 5 |",
    );

    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(screen.getByText("ride-native")).toBeInTheDocument();
  });

  it("keeps headings at reading size, because an answer is not a document", () => {
    const container = html("# Findings\n\nSomething happened.");

    expect(container.querySelector("h1")).toBeNull();
    expect(screen.getByText("Findings").className).toContain("font-semibold");
  });
});

describe("safety and edges", () => {
  it("does not render HTML that came from a model", () => {
    const container = html('Careful: <img src=x onerror="alert(1)"> and <b>bold</b>.');

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("opens links in a new tab without leaking the referrer", () => {
    const link = html("[the docs](https://example.com)").querySelector("a");

    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("renders nothing for an empty answer instead of an empty box", () => {
    const { container } = render(<Markdown>{""}</Markdown>);

    expect(container).toBeEmptyDOMElement();
  });

  it("shows a half-finished token as text while it is still streaming", () => {
    // Mid-stream the text is often cut mid-emphasis; it must read as text
    // rather than disappearing until the closing marker arrives.
    expect(html("The **admin-dash").textContent).toContain("admin-dash");
  });
});
