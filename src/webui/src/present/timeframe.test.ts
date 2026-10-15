import { describe, expect, it } from "vitest";

import { mockPeriods } from "../data/mock-cycles";
import {
  defaultSelection, flatOptions, pickerGroups, pickerNav, progressText, rangeText, rollingSelection, selectionForKey,
  selectionFromPeriod, stripText, timeframeOf,
} from "./timeframe";

const NOW = new Date("2026-10-14T09:30:00Z");
const monthly = mockPeriods({ kind: "month", recurrence: "recurring" }, NOW, 3);
const sprint = mockPeriods({ kind: "sprint", recurrence: "recurring", sprint_weeks: 2, anchor_number: 233, anchor_date: "2026-10-05" }, NOW, 2);

describe("selection to window", () => {
  it("a budget period carries the server's bounds, label and counters untouched", () => {
    const cur = monthly.periods[2];
    const sel = selectionFromPeriod(cur);
    expect(sel).toEqual({
      key: "month-2026-10", label: "Oct 2026", from: "2026-10-01T00:00:00Z", to: "2026-10-14T09:30:00.000Z",
      kind: "budget-period", isCurrent: true, daysElapsed: 14, daysTotal: 31,
    });
    expect(timeframeOf(sel)).toEqual({ from: "2026-10-01T00:00:00Z", to: "2026-10-14T09:30:00.000Z" });
  });

  it("a rolling range ends now and starts N days earlier (UTC), never current", () => {
    const sel = rollingSelection(15, new Date("2026-09-10T12:00:00Z"));
    expect(sel).toMatchObject({
      key: "rolling-15d", label: "Last 15 days", kind: "rolling", isCurrent: false, daysElapsed: 15, daysTotal: 15,
      from: "2026-08-26T12:00:00.000Z", to: "2026-09-10T12:00:00.000Z",
    });
    expect(rollingSelection(60, new Date("2026-09-10T00:00:00Z")).from).toBe("2026-07-12T00:00:00.000Z");
  });
});

describe("the default selection", () => {
  it("is the current budget period", () => {
    expect(defaultSelection(monthly.periods, NOW).key).toBe("month-2026-10");
    expect(defaultSelection(sprint.periods, NOW).label).toBe("Sprint 233");
  });
  it("is rolling Last 30 days when none is under way or there are none", () => {
    const finished = monthly.periods.slice(0, 2);
    for (const periods of [finished, []]) {
      expect(defaultSelection(periods, NOW)).toMatchObject({ key: "rolling-30d", kind: "rolling", to: NOW.toISOString() });
    }
  });
});

describe("keys", () => {
  it("resolves a period key from the list and a rolling key against now", () => {
    expect(selectionForKey("month-2026-09", monthly.periods, NOW)?.label).toBe("Sep 2026");
    expect(selectionForKey("rolling-60d", monthly.periods, NOW)).toMatchObject({ daysTotal: 60, to: NOW.toISOString() });
  });
  it("is null for an unknown key or an unoffered rolling length", () => {
    expect(selectionForKey("month-2020-01", monthly.periods, NOW)).toBeNull();
    expect(selectionForKey("rolling-7d", monthly.periods, NOW)).toBeNull();
    expect(selectionForKey("", [], NOW)).toBeNull();
  });
});

describe("date text", () => {
  it("shows a budget period's whole range from its ISO text, whatever the local zone", () => {
    expect(rangeText(selectionFromPeriod(monthly.periods[2]), NOW)).toBe("Oct 1 – Oct 31");
    expect(rangeText(selectionFromPeriod(sprint.periods[1]), NOW)).toBe("Oct 5 – Oct 18");
  });
  it("adds the year when it is not this year or the range spans two", () => {
    const dec = mockPeriods({ kind: "quarter", recurrence: "recurring" }, new Date("2025-11-02T00:00:00Z"), 1).periods[0];
    expect(rangeText(selectionFromPeriod(dec), NOW)).toBe("Oct 1, 2025 – Dec 31, 2025");
    const custom = mockPeriods({ kind: "custom", recurrence: "one-time", anchor_date: "2026-12-15", custom_end: "2027-01-15" },
      new Date("2026-12-20T00:00:00Z")).periods[0];
    expect(rangeText(selectionFromPeriod(custom), new Date("2026-12-20T00:00:00Z"))).toBe("Dec 15, 2026 – Jan 15, 2027");
  });
  it("a rolling range reads from its start day to today", () => {
    expect(rangeText(rollingSelection(15, NOW), NOW)).toBe("Sep 29 – Oct 14");
  });
  it("says how far into the current period we are, and only then", () => {
    expect(progressText(selectionFromPeriod(monthly.periods[2]))).toBe("day 14 of 31");
    expect(progressText(selectionFromPeriod(monthly.periods[1]))).toBeNull();
    expect(progressText(rollingSelection(30, NOW))).toBeNull();
    expect(stripText(selectionFromPeriod(monthly.periods[2]), NOW)).toBe("Oct 1 – Oct 31 · day 14 of 31");
    expect(stripText(selectionFromPeriod(monthly.periods[1]), NOW)).toBe("Sep 1 – Sep 30");
  });
});

describe("picker grouping", () => {
  it("lists budget periods newest first with dates and the current one marked, then the rolling ranges", () => {
    const [budget, other] = pickerGroups(monthly.periods, false, NOW);
    expect(budget.heading).toBe("Budget periods");
    expect(budget.emptyNote).toBeNull();
    expect(budget.options.map((o) => [o.label, o.detail, o.current])).toEqual([
      ["Oct 2026", "Oct 1 – Oct 31", true],
      ["Sep 2026", "Sep 1 – Sep 30", false],
      ["Aug 2026", "Aug 1 – Aug 31", false],
    ]);
    expect(other.heading).toBe("Other ranges");
    expect(other.options.map((o) => [o.key, o.label])).toEqual([
      ["rolling-15d", "Last 15 days"], ["rolling-30d", "Last 30 days"], ["rolling-60d", "Last 60 days"],
    ]);
  });
  it("never offers the upcoming period", () => {
    expect(monthly.next).not.toBeNull();
    expect(flatOptions(pickerGroups(monthly.periods, false, NOW)).some((o) => o.label === "Nov 2026")).toBe(false);
  });
  it("with no periods the first group carries a note instead of options", () => {
    expect(pickerGroups([], true, NOW)[0]).toMatchObject({ options: [], emptyNote: "Budget periods unavailable" });
    expect(pickerGroups([], false, NOW)[0]).toMatchObject({ options: [], emptyNote: "No budget period under way" });
    expect(flatOptions(pickerGroups([], true, NOW)).map((o) => o.key)).toEqual(["rolling-15d", "rolling-30d", "rolling-60d"]);
  });
});

describe("picker keyboard", () => {
  it("steps and clamps at both ends without wrapping", () => {
    expect(pickerNav(1, "ArrowDown", 4)).toBe(2);
    expect(pickerNav(4, "ArrowDown", 4)).toBe(4);
    expect(pickerNav(1, "ArrowUp", 4)).toBe(0);
    expect(pickerNav(0, "ArrowUp", 4)).toBe(0);
  });
  it("Home and End jump to the ends; other keys change nothing", () => {
    expect(pickerNav(2, "Home", 4)).toBe(0);
    expect(pickerNav(2, "End", 4)).toBe(4);
    expect(pickerNav(2, "a", 4)).toBe(2);
    expect(pickerNav(2, "Enter", 4)).toBe(2);
  });
  it("copes with an empty list", () => {
    expect(pickerNav(3, "ArrowDown", -1)).toBe(0);
  });
});

describe("custom period rows", () => {
  it("do not repeat their dates under a label that already is the dates", () => {
    const custom = mockPeriods({ kind: "custom", recurrence: "one-time", anchor_date: "2026-09-01", custom_end: "2026-11-30" }, NOW);
    const [row] = pickerGroups(custom.periods, false, NOW)[0].options;
    expect(row).toMatchObject({ label: "01 Sep 2026 – 30 Nov 2026", detail: "", current: true });
  });
});
