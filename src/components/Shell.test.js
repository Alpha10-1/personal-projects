/**
 * The shell: navigation, the mobile menu, and the theme.
 *
 * Written straight after replacing two `setState`-in-effect suppressions --
 * the theme with `useSyncExternalStore` and the menu with a value derived
 * from the path. Both behaviours were previously only asserted by the
 * suppression comment saying what they were for, which is not an assertion.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Shell from "@/components/Shell";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
// The floating assistant talks to the model and mounts here; it is not what
// this file is about.
vi.mock("@/components/Assistant", () => ({ default: () => null }));

beforeEach(() => {
  pathname = "/";
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

afterEach(cleanup);

describe("navigation", () => {
  it("offers every section", () => {
    render(<Shell>page</Shell>);
    for (const label of ["Today", "Projects", "Tasks", "Time", "Insights", "Review"]) {
      expect(screen.getAllByRole("link", { name: label }).length).toBeGreaterThan(0);
    }
  });

  it("marks the current section, and only that one", () => {
    pathname = "/tasks";
    render(<Shell>page</Shell>);
    const current = screen
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page")
      .map((link) => link.textContent);
    // Only the sidebar renders while the mobile menu is closed.
    expect(current).toEqual(["Tasks"]);
  });

  it("treats Today as current only on the root", () => {
    // `/` would otherwise prefix-match every path in the app.
    pathname = "/projects/3";
    render(<Shell>page</Shell>);
    const today = screen.getAllByRole("link", { name: "Today" })[0];
    expect(today).not.toHaveAttribute("aria-current");
  });

  it("marks a section from one of its child pages", () => {
    pathname = "/projects/3";
    render(<Shell>page</Shell>);
    expect(screen.getAllByRole("link", { name: "Projects" })[0]).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("renders what it was given", () => {
    render(<Shell><p>the page</p></Shell>);
    expect(screen.getByText("the page")).toBeInTheDocument();
  });
});

describe("the mobile menu", () => {
  it("starts closed and opens", async () => {
    render(<Shell>page</Shell>);
    const toggle = screen.getByRole("button", { name: "Open navigation" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(toggle);
    expect(
      screen.getByRole("button", { name: "Close navigation" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("closes again when pressed twice", async () => {
    render(<Shell>page</Shell>);
    await userEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    await userEvent.click(screen.getByRole("button", { name: "Close navigation" }));
    expect(
      screen.getByRole("button", { name: "Open navigation" }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("closes itself when the page changes", async () => {
    // Derived from the path rather than closed in an effect, which is what
    // makes this also true of back and forward.
    const { rerender } = render(<Shell>page</Shell>);
    await userEvent.click(screen.getByRole("button", { name: "Open navigation" }));

    pathname = "/tasks";
    rerender(<Shell>page</Shell>);

    expect(
      screen.getByRole("button", { name: "Open navigation" }),
    ).toHaveAttribute("aria-expanded", "false");
  });
});

describe("the theme", () => {
  it("follows the system when nothing has been chosen", () => {
    render(<Shell>page</Shell>);
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });

  it("applies a saved choice on first render", () => {
    window.localStorage.setItem("pp-theme", "dark");
    render(<Shell>page</Shell>);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("remembers a choice", async () => {
    render(<Shell>page</Shell>);
    await userEvent.click(screen.getAllByRole("button", { name: /Toggle colour theme/i })[0]);
    expect(window.localStorage.getItem("pp-theme")).toBe("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("toggles back", async () => {
    window.localStorage.setItem("pp-theme", "dark");
    render(<Shell>page</Shell>);
    await userEvent.click(screen.getAllByRole("button", { name: /Toggle colour theme/i })[0]);
    expect(window.localStorage.getItem("pp-theme")).toBe("light");
  });

  it("ignores a stored value that is not a theme", () => {
    window.localStorage.setItem("pp-theme", "banana");
    render(<Shell>page</Shell>);
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });

  it("still works when storage is unavailable", async () => {
    // Private browsing, or storage blocked by policy. The toggle should
    // work for the session rather than throw.
    const getItem = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    const setItem = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });

    render(<Shell><p>the page</p></Shell>);
    await userEvent.click(screen.getAllByRole("button", { name: /Toggle colour theme/i })[0]);

    // Still rendered, and the choice took effect for this session even
    // though it could not be written down.
    expect(screen.getByText("the page")).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("data-theme");

    getItem.mockRestore();
    setItem.mockRestore();
  });
});
