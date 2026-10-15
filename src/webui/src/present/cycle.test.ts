import { describe, expect, it } from "vitest";

import { assertLexicon } from "./lexicon";
import {
  SAME_KIND_NOTICE, amountLabel, forecastCycleNote, buildCycleRequest, cycleChange, draftFromCycle, findCycleProblem, inWindow, kindChangeWarning,
  offersOneTime, periodNoun, validateCycleDraft, windowName,
} from "./cycle";
import type { CycleDraft } from "./cycle";
import { rollingSelection, selectionFromPeriod } from "./timeframe";
import { mockPeriods } from "../data/mock-cycles";

const NOW = new Date("2026-10-14T09:30:00Z");
const monthly = mockPeriods({ kind: "month", recurrence: "recurring" }, NOW, 2).periods;

const MONTH = { kind: "month", recurrence: "recurring", sprint_weeks: null, anchor_date: null, anchor_number: null, custom_end: null } as const;
const SPRINT = { kind: "sprint", recurrence: "recurring", sprint_weeks: 2, anchor_date: "2026-10-05", anchor_number: 233, custom_end: null } as const;
const CUSTOM = { kind: "custom", recurrence: "one-time", sprint_weeks: null, anchor_date: "2026-09-01", anchor_number: null, custom_end: "2026-11-30" } as const;

describe("wording for any cycle", () => {
  it("names what a budget is per, from the stored label", () => {
    expect(["monthly", "quarterly", "half-yearly", "yearly", "sprint", "custom"].map(periodNoun))
      .toEqual(["month", "quarter", "half-year", "year", "sprint", "period"]);
    expect(periodNoun(null)).toBe("period");
    expect(periodNoun("fortnightly")).toBe("period");
  });
  it("is not fooled by names every object has", () => {
    for (const name of ["constructor", "toString", "hasOwnProperty", "__proto__", "valueOf"]) {
      expect(periodNoun(name)).toBe("period");
      expect(amountLabel(name)).toBe("Budget per period (USD)");
    }
  });
  it("labels the amount field after the cycle", () => {
    expect(amountLabel("sprint")).toBe("Budget per sprint (USD)");
    expect(amountLabel("monthly")).toBe("Budget per month (USD)");
    expect(amountLabel(undefined)).toBe("Budget per period (USD)");
  });
  it("words the selected window", () => {
    const current = selectionFromPeriod(monthly[1]);
    const past = selectionFromPeriod(monthly[0]);
    expect(windowName(current)).toBe("Oct 2026");
    expect(inWindow(current)).toBe("so far in Oct 2026");
    expect(inWindow(past)).toBe("in Sep 2026");
    expect(windowName(rollingSelection(30, NOW))).toBe("the last 30 days");
    expect(inWindow(rollingSelection(15, NOW))).toBe("in the last 15 days");
  });
  it("words a sprint, a custom date range and a period not started yet", () => {
    const sprint = mockPeriods({ kind: "sprint", recurrence: "recurring", sprint_weeks: 2, anchor_number: 233, anchor_date: "2026-10-05" }, NOW, 2);
    expect(inWindow(selectionFromPeriod(sprint.periods[1]))).toBe("so far in Sprint 233");
    expect(inWindow(selectionFromPeriod(sprint.periods[0]))).toBe("in Sprint 232");
    const custom = mockPeriods({ kind: "custom", recurrence: "one-time", anchor_date: "2026-09-01", custom_end: "2026-11-30" }, NOW).periods[0];
    expect(windowName(selectionFromPeriod(custom))).toBe("01 Sep 2026 – 30 Nov 2026");
    expect(inWindow(selectionFromPeriod(custom))).toBe("so far in 01 Sep 2026 – 30 Nov 2026");
    // not under way (the upcoming period): never "so far"
    expect(inWindow(selectionFromPeriod(monthly[1]))).toBe("so far in Oct 2026");
    const upcoming = mockPeriods({ kind: "month", recurrence: "recurring" }, NOW, 1).next!;
    expect(inWindow(selectionFromPeriod(upcoming))).toBe("in Nov 2026");
  });
});

describe("forecast copy", () => {
  it("says the forecast is by calendar month for any known cycle that is not monthly", () => {
    for (const label of ["quarterly", "half-yearly", "yearly", "sprint", "custom"]) {
      expect(forecastCycleNote(label)).toBe("Forecast is by calendar month");
    }
  });
  it("says nothing for a monthly, unknown or missing cycle", () => {
    expect(forecastCycleNote("monthly")).toBeNull();
    expect(forecastCycleNote(null)).toBeNull();
    expect(forecastCycleNote(undefined)).toBeNull();
    expect(forecastCycleNote("fortnightly")).toBeNull();
    expect(forecastCycleNote("constructor")).toBeNull();
  });
  it("passes the tone lexicon", () => {
    assertLexicon(forecastCycleNote("sprint")!);
  });
});

describe("the draft", () => {
  it("opens on the saved cycle, with defaults for what a kind does not store", () => {
    expect(draftFromCycle(MONTH)).toEqual({
      kind: "month", recurrence: "recurring", sprintWeeks: "2", anchorNumber: "1", anchorDate: "", customEnd: "",
    });
    expect(draftFromCycle(SPRINT)).toMatchObject({ sprintWeeks: "2", anchorNumber: "233", anchorDate: "2026-10-05" });
    expect(draftFromCycle(CUSTOM)).toMatchObject({ recurrence: "one-time", anchorDate: "2026-09-01", customEnd: "2026-11-30" });
  });

  it("offers one-time for sprint and custom only", () => {
    expect([offersOneTime("month"), offersOneTime("quarter"), offersOneTime("half"), offersOneTime("year")]).toEqual([false, false, false, false]);
    expect([offersOneTime("sprint"), offersOneTime("custom")]).toEqual([true, true]);
  });

  it("builds only the fields the kind uses, calendar kinds always recurring", () => {
    expect(buildCycleRequest({ ...draftFromCycle(SPRINT), kind: "quarter", recurrence: "one-time" }))
      .toEqual({ kind: "quarter", recurrence: "recurring" });
    expect(buildCycleRequest(draftFromCycle(SPRINT))).toEqual({
      kind: "sprint", recurrence: "recurring", sprint_weeks: 2, anchor_number: 233, anchor_date: "2026-10-05",
    });
    expect(buildCycleRequest(draftFromCycle(CUSTOM))).toEqual({
      kind: "custom", recurrence: "one-time", anchor_date: "2026-09-01", custom_end: "2026-11-30",
    });
  });
});

describe("validation mirrors the server's rules", () => {
  const sprint: CycleDraft = draftFromCycle(SPRINT);
  const custom: CycleDraft = draftFromCycle(CUSTOM);

  it("accepts a valid draft of every kind", () => {
    expect(validateCycleDraft(draftFromCycle(MONTH))).toBeNull();
    expect(validateCycleDraft(sprint)).toBeNull();
    expect(validateCycleDraft(custom)).toBeNull();
  });
  it("does not check fields the kind does not use", () => {
    expect(validateCycleDraft({ ...draftFromCycle(MONTH), sprintWeeks: "x", anchorDate: "nope", customEnd: "" })).toBeNull();
    expect(validateCycleDraft({ ...sprint, customEnd: "garbage" })).toBeNull();
  });
  it("sprint length is whole weeks, 1 to 52", () => {
    for (const bad of ["", "0", "53", "1.5", "-1", "two"]) expect(validateCycleDraft({ ...sprint, sprintWeeks: bad })).toMatch(/Sprint length/);
    expect(validateCycleDraft({ ...sprint, sprintWeeks: "52" })).toBeNull();
    expect(validateCycleDraft({ ...sprint, sprintWeeks: "1" })).toBeNull();
  });
  it("sprint number is a whole number from 1 to 100000", () => {
    for (const bad of ["", "0", "100001", "2.5"]) expect(validateCycleDraft({ ...sprint, anchorNumber: bad })).toMatch(/sprint number/);
    expect(validateCycleDraft({ ...sprint, anchorNumber: "100000" })).toBeNull();
  });
  it("needs a real start date inside 2000..2100", () => {
    expect(validateCycleDraft({ ...sprint, anchorDate: "" })).toMatch(/Pick the date/);
    expect(validateCycleDraft({ ...sprint, anchorDate: "2026-02-30" })).toMatch(/Pick the date/);
    expect(validateCycleDraft({ ...sprint, anchorDate: "1999-12-31" })).toMatch(/2000 and 2100/);
    expect(validateCycleDraft({ ...sprint, anchorDate: "2101-01-01" })).toMatch(/2000 and 2100/);
    expect(validateCycleDraft({ ...sprint, anchorDate: "2100-12-31" })).toBeNull();
  });
  it("custom needs both days, the last not before the first", () => {
    expect(validateCycleDraft({ ...custom, anchorDate: "" })).toMatch(/first day/);
    expect(validateCycleDraft({ ...custom, customEnd: "" })).toMatch(/last day of/);
    expect(validateCycleDraft({ ...custom, customEnd: "2026-08-31" })).toMatch(/not be before/);
    expect(validateCycleDraft({ ...custom, customEnd: "2026-09-01" })).toBeNull();
    expect(validateCycleDraft({ ...custom, customEnd: "2101-01-01" })).toMatch(/2000 and 2100/);
  });
});

describe("which field a problem belongs to", () => {
  const sprint = draftFromCycle(SPRINT);
  const custom = draftFromCycle(CUSTOM);
  it("names the field, so the form can mark it invalid", () => {
    expect(findCycleProblem(draftFromCycle(MONTH))).toBeNull();
    expect(findCycleProblem({ ...sprint, sprintWeeks: "0" })?.field).toBe("sprintWeeks");
    expect(findCycleProblem({ ...sprint, anchorNumber: "" })?.field).toBe("anchorNumber");
    expect(findCycleProblem({ ...sprint, anchorDate: "" })?.field).toBe("anchorDate");
    expect(findCycleProblem({ ...custom, anchorDate: "" })?.field).toBe("anchorDate");
    expect(findCycleProblem({ ...custom, customEnd: "" })?.field).toBe("customEnd");
    expect(findCycleProblem({ ...custom, customEnd: "2026-08-01" })?.field).toBe("customEnd");
    expect(findCycleProblem({ ...custom, customEnd: "2101-01-01" })?.field).toBe("customEnd");
    expect(findCycleProblem({ ...custom, anchorDate: "1999-01-01" })?.field).toBe("anchorDate");
  });
  it("agrees with validateCycleDraft", () => {
    for (const d of [sprint, { ...sprint, sprintWeeks: "99" }, custom, { ...custom, customEnd: "" }]) {
      expect(validateCycleDraft(d)).toBe(findCycleProblem(d)?.message ?? null);
    }
  });
});

describe("what a save would change", () => {
  it("nothing when the draft equals the saved cycle (including fields a kind ignores)", () => {
    expect(cycleChange(MONTH, draftFromCycle(MONTH))).toBe("none");
    expect(cycleChange(SPRINT, draftFromCycle(SPRINT))).toBe("none");
    expect(cycleChange(MONTH, { ...draftFromCycle(MONTH), sprintWeeks: "4", anchorDate: "2026-01-01", recurrence: "one-time" })).toBe("none");
  });
  it("a different kind is a kind change", () => {
    expect(cycleChange(MONTH, { ...draftFromCycle(MONTH), kind: "quarter" })).toBe("kind");
    expect(cycleChange(SPRINT, { ...draftFromCycle(SPRINT), kind: "custom", customEnd: "2026-12-31" })).toBe("kind");
  });
  it("the same kind with other values is an edit", () => {
    expect(cycleChange(SPRINT, { ...draftFromCycle(SPRINT), sprintWeeks: "3" })).toBe("edit");
    expect(cycleChange(SPRINT, { ...draftFromCycle(SPRINT), anchorNumber: "234" })).toBe("edit");
    expect(cycleChange(SPRINT, { ...draftFromCycle(SPRINT), recurrence: "one-time" })).toBe("edit");
    expect(cycleChange(CUSTOM, { ...draftFromCycle(CUSTOM), customEnd: "2026-12-31" })).toBe("edit");
  });
});

describe("messages before a save", () => {
  it("the kind-change warning says nothing is converted and what to do", () => {
    const w = kindChangeWarning("monthly", "sprint");
    expect(w).toContain("not converted");
    expect(w).toContain("stay stored but are not used");
    expect(w).toContain("per month");
    expect(w).toContain("per sprint");
    expect(kindChangeWarning("sprint", "custom")).toContain("per period");
  });
  it("the same-kind notice says amounts now apply per period of the changed cycle", () => {
    expect(SAME_KIND_NOTICE).toContain("now apply per period of the changed cycle");
  });
  it("pass the tone lexicon", () => {
    for (const text of [kindChangeWarning("monthly", "sprint"), SAME_KIND_NOTICE, amountLabel("sprint")]) assertLexicon(text);
  });
});
