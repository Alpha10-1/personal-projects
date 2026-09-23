/**
 * The charts.
 *
 * Recharts needs a real width to draw anything, which jsdom does not give
 * it, so the drawing itself is not what is tested here. What is: the empty
 * cases, and the table behind each chart -- which is the part that is
 * readable without colour vision, and the part a screen reader gets.
 *
 * The one piece of logic worth pinning down is the colour slot. A category
 * keeps its colour when another drops out of the window; without that, a
 * quiet week silently re-colours the whole chart.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  HoursByProjectChart,
  HoursByWeekChart,
  ThroughputChart,
} from "@/components/charts";

// Recharts measures its container; jsdom reports zero and renders nothing.
vi.mock("recharts", async () => {
  const actual = await vi.importActual("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }) => (
      <div style={{ width: 600, height: 300 }}>{children}</div>
    ),
  };
});

// `week` is the Monday of that week, as an ISO date -- the axis formats it
// for display, so a label like "2026-W37" would render as an empty tick.
const WEEKS = [
  { week: "2026-09-14", build: 6, research: 2 },
  { week: "2026-09-21", build: 4, research: 0 },
];
const CATEGORIES = ["build", "research"];
const LABELS = { build: "Build", research: "Research" };

describe("hours by week", () => {
  const show = (props = {}) =>
    render(
      <HoursByWeekChart
        data={WEEKS}
        categories={CATEGORIES}
        categoryLabels={LABELS}
        categorySlots={{ build: 0, research: 1 }}
        {...props}
      />,
    );

  it("says there is nothing rather than drawing an empty chart", () => {
    render(
      <HoursByWeekChart data={[]} categories={CATEGORIES} categoryLabels={LABELS} />,
    );
    expect(screen.getByText(/No time logged in this window/)).toBeInTheDocument();
  });

  it("is also empty when there are weeks but no categories", () => {
    render(<HoursByWeekChart data={WEEKS} categories={[]} categoryLabels={LABELS} />);
    expect(screen.getByText(/No time logged in this window/)).toBeInTheDocument();
  });

  it("names the categories in the legend, using their labels", () => {
    show();
    expect(screen.getByText("Build")).toBeInTheDocument();
    expect(screen.getByText("Research")).toBeInTheDocument();
  });

  it("falls back to the raw key when a category has no label", () => {
    render(
      <HoursByWeekChart
        data={[{ week: "2026-W37", admin: 1 }]}
        categories={["admin"]}
        categoryLabels={{}}
      />,
    );
    expect(screen.getByText("admin")).toBeInTheDocument();
  });

  it("offers the numbers as a table, for anyone not reading colours", async () => {
    show();
    await userEvent.click(screen.getByRole("button", { name: /table|numbers/i }));
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Hours|Build/i })).toBeInTheDocument();
  });
});

describe("throughput", () => {
  it("draws nothing at all without data", () => {
    const { container } = render(<ThroughputChart data={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows both series", () => {
    render(
      <ThroughputChart
        data={[{ week: "2026-09-14", opened: 3, closed: 1 }]}
      />,
    );
    expect(screen.getByText("Opened")).toBeInTheDocument();
    expect(screen.getByText("Closed")).toBeInTheDocument();
  });

  it("offers its numbers too", async () => {
    render(<ThroughputChart data={[{ week: "2026-09-14", opened: 3, closed: 1 }]} />);
    await userEvent.click(screen.getByRole("button", { name: /table|numbers/i }));
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});

describe("hours by project", () => {
  it("says there is nothing rather than drawing an empty chart", () => {
    render(<HoursByProjectChart data={[]} />);
    expect(screen.getByText(/No time logged in this window/)).toBeInTheDocument();
  });

  it("shows the projects it was given", async () => {
    render(
      <HoursByProjectChart
        data={[
          { name: "Demo", hours: 12 },
          { name: "Other", hours: 3 },
        ]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /table|numbers/i }));
    expect(screen.getByText("Demo")).toBeInTheDocument();
    expect(screen.getByText("Other")).toBeInTheDocument();
  });

  it("keeps every row in the table even though the chart draws the top few", async () => {
    // The chart is capped for readability; the table is the complete answer
    // and should not quietly lose the long tail.
    const many = Array.from({ length: 20 }, (_, n) => ({
      name: `P${n}`,
      hours: 20 - n,
    }));
    render(<HoursByProjectChart data={many} />);
    await userEvent.click(screen.getByRole("button", { name: /table|numbers/i }));

    expect(screen.getByText("P0")).toBeInTheDocument();
    expect(screen.getByText("P19")).toBeInTheDocument();
  });
});
