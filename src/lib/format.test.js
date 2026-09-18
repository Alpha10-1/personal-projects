import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatDate,
  formatHours,
  parseDate,
  relativeDue,
  toIso,
} from "@/lib/format";

describe("parseDate", () => {
  it("reads an API date as a local calendar date, not as UTC", () => {
    // The bug this guards: `new Date("2026-03-01")` is midnight UTC, which is
    // the previous day anywhere west of Greenwich -- a due date off by one.
    const date = parseDate("2026-03-01");

    expect(date.getFullYear()).toBe(2026);
    expect(date.getMonth()).toBe(2);
    expect(date.getDate()).toBe(1);
  });

  it("ignores a time component, because only the calendar day matters", () => {
    expect(toIso(parseDate("2026-03-01T22:30:00"))).toBe("2026-03-01");
  });

  it("returns null for anything that isn't a date", () => {
    expect(parseDate(null)).toBeNull();
    expect(parseDate("")).toBeNull();
    expect(parseDate("not a date")).toBeNull();
  });
});

describe("relativeDue", () => {
  const isoDaysFromNow = (days) => {
    const date = new Date();
    date.setHours(0, 0, 0, 0);
    date.setDate(date.getDate() + days);
    return toIso(date);
  };

  it("names today, tomorrow and overdue distinctly", () => {
    expect(relativeDue(isoDaysFromNow(0)).text).toBe("Due today");
    expect(relativeDue(isoDaysFromNow(1)).text).toBe("Due tomorrow");
    expect(relativeDue(isoDaysFromNow(5)).text).toBe("In 5 days");
    expect(relativeDue(isoDaysFromNow(-1)).text).toBe("1 day overdue");
    expect(relativeDue(isoDaysFromNow(-3)).text).toBe("3 days overdue");
  });

  it("marks overdue work critical and due work merely warning", () => {
    expect(relativeDue(isoDaysFromNow(-1)).tone).toBe("critical");
    expect(relativeDue(isoDaysFromNow(0)).tone).toBe("warning");
    expect(relativeDue(isoDaysFromNow(9)).tone).toBe("neutral");
  });

  it("is null when nothing is due, so a task with no date shows no badge", () => {
    expect(relativeDue(null)).toBeNull();
  });
});

describe("numbers and missing values", () => {
  it("drops a pointless decimal from whole hours", () => {
    expect(formatHours(2)).toBe("2h");
    expect(formatHours(2.25)).toBe("2.3h");
    expect(formatHours(0)).toBe("0h");
    expect(formatHours(null)).toBe("0h");
  });

  it("scales bytes to the unit a person would say out loud", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("shows a dash rather than 'Invalid Date' when there is no date", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("")).toBe("—");
  });
});
