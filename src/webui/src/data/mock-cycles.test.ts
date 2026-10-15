import { describe, expect, it } from "vitest";

import { DEFAULT_MOCK_CYCLE, MOCK_CYCLE_LABELS, mockCycleError, mockPeriods, normaliseMockCycle } from "./mock-cycles";
import type { MockCycle } from "./mock-cycles";

const NOW = new Date("2026-10-14T09:30:00Z");

describe("mock budget periods", () => {
  it("month: six started months, the current one last, and the next one", () => {
    const r = mockPeriods(DEFAULT_MOCK_CYCLE, NOW);
    expect(r.period_label).toBe("monthly");
    expect(r.periods.map((p) => p.label)).toEqual(["May 2026", "Jun 2026", "Jul 2026", "Aug 2026", "Sep 2026", "Oct 2026"]);
    const cur = r.periods[5];
    expect(cur).toMatchObject({
      key: "month-2026-10", is_current: true, days_total: 31, days_elapsed: 14,
      from: "2026-10-01T00:00:00Z", end_exclusive: "2026-11-01T00:00:00Z",
    });
    // the current period is cut at "now" (its period-to-date); a finished one ends at its end
    expect(cur.to).toBe("2026-10-14T09:30:00.000Z");
    expect(r.periods[4]).toMatchObject({ is_current: false, days_elapsed: 30, days_total: 30, to: "2026-10-01T00:00:00Z" });
    expect(r.next).toMatchObject({ label: "Nov 2026", is_current: false, days_elapsed: 0, to: "2026-12-01T00:00:00Z" });
  });

  it("honours `history`", () => {
    expect(mockPeriods(DEFAULT_MOCK_CYCLE, NOW, 2).periods.map((p) => p.label)).toEqual(["Sep 2026", "Oct 2026"]);
  });

  it("quarter, half and year roll over the calendar year", () => {
    const jan = new Date("2026-01-10T00:00:00Z");
    expect(mockPeriods({ kind: "quarter", recurrence: "recurring" }, jan, 3).periods.map((p) => p.label))
      .toEqual(["Q3 2025", "Q4 2025", "Q1 2026"]);
    const half = mockPeriods({ kind: "half", recurrence: "recurring" }, NOW, 2);
    expect(half.periods.map((p) => p.label)).toEqual(["H1 2026", "H2 2026"]);
    expect(half.next?.label).toBe("H1 2027");
    const year = mockPeriods({ kind: "year", recurrence: "recurring" }, NOW, 2);
    expect(year.periods.map((p) => p.label)).toEqual(["2025", "2026"]);
    expect(year.periods[1].days_total).toBe(365);
  });

  it("sprint: numbered from the anchor, never below sprint 1", () => {
    const cycle: MockCycle = { kind: "sprint", recurrence: "recurring", sprint_weeks: 2, anchor_number: 233, anchor_date: "2026-10-05" };
    const r = mockPeriods(cycle, NOW, 3);
    expect(r.period_label).toBe("sprint");
    expect(r.periods.map((p) => p.label)).toEqual(["Sprint 231", "Sprint 232", "Sprint 233"]);
    expect(r.periods[2]).toMatchObject({ is_current: true, days_total: 14, days_elapsed: 10, from: "2026-10-05T00:00:00Z" });
    expect(r.next?.label).toBe("Sprint 234");
    const early = mockPeriods({ ...cycle, anchor_number: 2, anchor_date: "2026-10-05" }, NOW, 6);
    expect(early.periods.map((p) => p.label)).toEqual(["Sprint 1", "Sprint 2"]);
  });

  it("sprint: before the anchor there is no period, only the next one", () => {
    const r = mockPeriods({ kind: "sprint", recurrence: "recurring", sprint_weeks: 1, anchor_number: 1, anchor_date: "2026-11-02" }, NOW);
    expect(r.periods).toEqual([]);
    expect(r.next?.label).toBe("Sprint 1");
  });

  it("custom: labelled by its dates, one-time gives a single period", () => {
    const cycle: MockCycle = { kind: "custom", recurrence: "one-time", anchor_date: "2026-09-01", custom_end: "2026-11-30" };
    const r = mockPeriods(cycle, NOW);
    expect(r.periods).toHaveLength(1);
    expect(r.periods[0]).toMatchObject({ label: "01 Sep 2026 – 30 Nov 2026", days_total: 91, is_current: true });
    expect(r.next).toBeNull();
    const future = mockPeriods({ ...cycle, anchor_date: "2027-01-01", custom_end: "2027-01-31" }, NOW);
    expect(future.periods).toEqual([]);
    expect(future.next?.label).toBe("01 Jan 2027 – 31 Jan 2027");
    const over = mockPeriods({ ...cycle, anchor_date: "2026-01-01", custom_end: "2026-01-31" }, NOW);
    expect(over.periods).toHaveLength(1);
    expect(over.periods[0].is_current).toBe(false);
  });

  it("custom recurring repeats with its own length", () => {
    const r = mockPeriods({ kind: "custom", recurrence: "recurring", anchor_date: "2026-10-01", custom_end: "2026-10-10" }, NOW, 6);
    expect(r.periods.map((p) => p.from.slice(0, 10))).toEqual(["2026-10-01", "2026-10-11"]);
    expect(r.periods[1].is_current).toBe(true);
  });

  it("every kind has a stored label", () => {
    expect(Object.values(MOCK_CYCLE_LABELS).sort()).toEqual(["custom", "half-yearly", "monthly", "quarterly", "sprint", "yearly"]);
  });
});

describe("mock cycle validation", () => {
  it("mirrors the server's reasons", () => {
    expect(mockCycleError(DEFAULT_MOCK_CYCLE)).toBeNull();
    expect(mockCycleError({ kind: "quarter", recurrence: "one-time" })).toMatch(/always recurring/);
    expect(mockCycleError({ kind: "sprint", recurrence: "recurring" })).toMatch(/needs anchor_date/);
    expect(mockCycleError({ kind: "sprint", recurrence: "recurring", anchor_date: "2026-10-05", sprint_weeks: 60 })).toMatch(/1 and 52/);
    expect(mockCycleError({ kind: "sprint", recurrence: "recurring", anchor_date: "2026-10-05", anchor_number: 0 })).toMatch(/anchor_number/);
    expect(mockCycleError({ kind: "custom", recurrence: "recurring", anchor_date: "2026-10-05" })).toMatch(/needs custom_end/);
    expect(mockCycleError({ kind: "custom", recurrence: "recurring", anchor_date: "2026-10-05", custom_end: "2026-10-01" }))
      .toMatch(/before anchor_date/);
    expect(mockCycleError({ kind: "sprint", recurrence: "recurring", anchor_date: "1999-01-01" })).toMatch(/between/);
    expect(mockCycleError({ kind: "sprint", recurrence: "recurring", anchor_date: "2026-10-05", sprint_weeks: 1.5 })).toBe("sprint_weeks must be an integer");
  });

  it("rejects an unknown kind or recurrence in the server's words", () => {
    expect(mockCycleError({ kind: "week" as MockCycle["kind"], recurrence: "recurring" }))
      .toBe("unknown cycle kind 'week'; expected one of ('month', 'quarter', 'half', 'year', 'sprint', 'custom')");
    expect(mockCycleError({ kind: "month", recurrence: "weekly" as MockCycle["recurrence"] }))
      .toBe("unknown recurrence 'weekly'; expected one of ('recurring', 'one-time')");
  });

  it("normalises like the server: unused fields null, sprint defaults filled", () => {
    expect(normaliseMockCycle({ kind: "month", recurrence: "recurring", sprint_weeks: 3, anchor_date: "2026-10-05", anchor_number: 4, custom_end: "2026-11-01" }))
      .toEqual(DEFAULT_MOCK_CYCLE);
    expect(normaliseMockCycle({ kind: "sprint", recurrence: "recurring", anchor_date: "2026-10-05", custom_end: "2026-11-01" }))
      .toEqual({ kind: "sprint", recurrence: "recurring", sprint_weeks: 2, anchor_number: 1, anchor_date: "2026-10-05", custom_end: null });
    expect(normaliseMockCycle({ kind: "custom", recurrence: "one-time", sprint_weeks: 2, anchor_number: 9, anchor_date: "2026-10-01", custom_end: "2026-10-31" }))
      .toEqual({ kind: "custom", recurrence: "one-time", sprint_weeks: null, anchor_number: null, anchor_date: "2026-10-01", custom_end: "2026-10-31" });
  });
});
