import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiSource, ORG_SCOPE_ID, forecastScope } from "../data/source";
import type { Schemas } from "../data/source";
import { MockSource } from "../data/mock-source";
import { forecastFixture, planFixture } from "../data/mock";
import {
  addDays, assertLexicon, bandLabel, burnUpMarkers, burnUpRows, dailyRows, defaultPlanPeriod, eachDay,
  effectivePresetId, fmtCompactUsd, fmtIsoDay, forecastFootnote, groupPlanRows, groupPresets,
  headlineSentences, incompleteNote, isIncomplete, monthName, nextSortDir, periodLabels, planForHeader,
  planGapLabel, planRows, probabilityLabel, runwaySentence, sortPlanRows, tokensFootnote,
  type PlanTableRow, burnUpLegend, dailyLegend, fmtPlanTokens, gapSortLabel,
} from "./index";
import { addDays as isoAddDays } from "../data/isoDay";

type View = Schemas["ForecastView"];
type Period = Schemas["ForecastPeriod"];
type Runway = Schemas["Runway"];
type Plan = Schemas["BudgetPlan"];

const M = 1_000_000; // one dollar, in micros

// ── a small hand-built run ───────────────────────────────────────────────────────────────
// October is under way: days 1..3 are actual ($2, $3, $4), the forecast starts on the 4th and
// the period ends on the 5th. November is a future period of three days. Every figure is
// chosen so the expected rows can be checked by hand.
const pt = (date: string, expected: number, low: number, high: number) =>
  ({ date, expected_usd_micros: expected * M, low_usd_micros: low * M, high_usd_micros: high * M });
const landing = (expected: number, p10: number, p50: number, p90: number) =>
  ({ expected_usd_micros: expected * M, p10_usd_micros: p10 * M, p50_usd_micros: p50 * M, p90_usd_micros: p90 * M });
const atRisk: Runway = {
  status: "at_risk", runs_out_probability: 0.38,
  runs_out_on: { p10: "2026-10-04", p50: "2026-10-05", p90: "2026-10-05" }, days_short: 0,
};

function octoberBase(over: Partial<Period> = {}): Period {
  return {
    start: "2026-10-01", end: "2026-10-05", forecast_from: "2026-10-04", actual_to_date_usd_micros: 9 * M,
    projected: [pt("2026-10-04", 14, 12, 16), pt("2026-10-05", 19, 15, 23)],
    landing: landing(19, 15, 18.5, 23), within_budget_probability: 0.62, delta_vs_baseline_usd_micros: 0,
    runway: atRisk, ...over,
  };
}
const novemberBase = (over: Partial<Period> = {}): Period => ({
  start: "2026-11-01", end: "2026-11-03", forecast_from: "2026-11-01", actual_to_date_usd_micros: 0,
  projected: [pt("2026-11-01", 6, 5, 7), pt("2026-11-02", 12, 10, 14), pt("2026-11-03", 18, 14, 22)],
  landing: landing(18, 14, 17.5, 22), within_budget_probability: 0.9, delta_vs_baseline_usd_micros: 0,
  runway: null, ...over,
});

const PRESETS: View["presets"] = [
  { id: "baseline", label: "Current trajectory", group: null, description: "No change.", assumptions: [] },
  { id: "usage-up-20", label: "Usage grows 20%", group: "Usage", description: "", assumptions: ["growth"] },
  { id: "opus-to-sonnet-30", label: "Move 30% of Opus work to Sonnet", group: "Model mix", description: "", assumptions: [] },
  { id: "usage-down-15", label: "Usage drops 15%", group: "Usage", description: "", assumptions: ["growth"] },
];

const actualDaily = (): View["actual_daily"] => {
  const days = eachDay("2026-09-03", "2026-10-03");
  return days.map((date) => ({
    date, usd_micros: date === "2026-10-01" ? 2 * M : date === "2026-10-02" ? 3 * M : date === "2026-10-03" ? 4 * M : M,
  }));
};
const daily = (): Schemas["ForecastSeries"]["daily"] =>
  [...eachDay("2026-10-01", "2026-10-05"), ...eachDay("2026-11-01", "2026-11-03")].map((d) => pt(d, 5, 3, 7));

function mkView(over: Partial<View> = {}): View {
  return {
    origin: "2026-09-30", data_through: "2026-10-03", timezone: "UTC", band_low: 10, band_high: 90,
    budget_usd_micros: 20 * M, presets: PRESETS, actual_daily: actualDaily(),
    series: [
      { preset_id: "baseline", status: "complete", daily: daily(), periods: [octoberBase(), novemberBase()] },
      {
        preset_id: "usage-up-20", status: "complete", daily: daily(), periods: [
          octoberBase({
            projected: [pt("2026-10-04", 16, 13, 19), pt("2026-10-05", 23, 17, 29)], landing: landing(23, 17, 22, 29),
            within_budget_probability: 0.3, delta_vs_baseline_usd_micros: 4 * M,
            runway: { status: "runs_out", runs_out_probability: 0.7, runs_out_on: { p10: "2026-10-04", p50: "2026-10-04", p90: "2026-10-05" }, days_short: 1 },
          }),
          novemberBase({ landing: landing(21.6, 17, 21, 26), delta_vs_baseline_usd_micros: 3.6 * M }),
        ],
      },
      {
        preset_id: "opus-to-sonnet-30", status: "complete", daily: daily(), periods: [
          octoberBase({
            projected: [pt("2026-10-04", 12, 11, 13), pt("2026-10-05", 16, 13, 19)], landing: landing(16, 13, 15.5, 19),
            within_budget_probability: 0.95, delta_vs_baseline_usd_micros: -3 * M,
            runway: { status: "lasts", runs_out_probability: 0.05, runs_out_on: { p10: null, p50: null, p90: null }, days_short: null },
          }),
          novemberBase({ delta_vs_baseline_usd_micros: 300_000 }),
        ],
      },
      { preset_id: "usage-down-15", status: "incomplete", daily: [], periods: [] },
    ],
    ...over,
  };
}

/** The same view with the baseline October period (and, if given, a preset's) edited. */
function withPeriod(view: View, presetId: string, pi: number, over: Partial<Period>): View {
  return {
    ...view,
    series: view.series.map((s) => s.preset_id !== presetId ? s
      : { ...s, periods: s.periods.map((p, i) => i === pi ? { ...p, ...over } : p) }),
  };
}
const withRunway = (view: View, runway: Runway | null, presetId = "baseline") =>
  withPeriod(view, presetId, 0, { runway });

/** The month-end case: the data ends on the last day of September, so both periods are future. */
function monthEndView(): View {
  const v = mkView({ data_through: "2026-09-30", actual_daily: eachDay("2026-09-03", "2026-09-30").map((date) => ({ date, usd_micros: M })) });
  return {
    ...v,
    series: v.series.map((s) => s.periods.length === 0 ? s : {
      ...s,
      periods: s.periods.map((p, i) => i === 0
        ? { ...p, forecast_from: "2026-10-01", actual_to_date_usd_micros: 0, runway: null,
            projected: [pt("2026-10-01", 5, 4, 6), pt("2026-10-02", 10, 8, 12), pt("2026-10-03", 15, 11, 19), pt("2026-10-04", 20, 14, 26), pt("2026-10-05", 25, 17, 33)],
            landing: landing(25, 17, 24, 33) }
        : p),
    }),
  };
}

// ── numbers ──────────────────────────────────────────────────────────────────────────────
describe("fmtCompactUsd", () => {
  it("compacts micro-dollars", () => {
    expect(fmtCompactUsd(950 * M)).toBe("$950");
    expect(fmtCompactUsd(18_200 * M)).toBe("$18.2k");
    expect(fmtCompactUsd(16_000 * M)).toBe("$16.0k");
    expect(fmtCompactUsd(1_200_000 * M)).toBe("$1.2M");
    expect(fmtCompactUsd(0)).toBe("$0");
    expect(fmtCompactUsd(842.4 * M)).toBe("$842");
  });
  it("never prints a four-digit dollar figure or a 1000.0k", () => {
    expect(fmtCompactUsd(999.4 * M)).toBe("$999");
    expect(fmtCompactUsd(999.6 * M)).toBe("$1.0k");
    expect(fmtCompactUsd(999_949 * M)).toBe("$999.9k");
    expect(fmtCompactUsd(999_950 * M)).toBe("$1.0M");
  });
  it("has billion and trillion tiers instead of printing 1000.0M", () => {
    expect(fmtCompactUsd(999_949_000 * M)).toBe("$999.9M");
    expect(fmtCompactUsd(999_950_000 * M)).toBe("$1.0B");
    expect(fmtCompactUsd(3_400_000_000 * M)).toBe("$3.4B");
    expect(fmtCompactUsd(999_950_000_000 * M)).toBe("$1.0T");
    expect(fmtCompactUsd(1_250_000_000_000 * M)).toBe("$1.3T");
  });
  it("keeps the sign and tolerates a missing value", () => {
    expect(fmtCompactUsd(-842 * M)).toBe("-$842");
    expect(fmtCompactUsd(null)).toBe("—");
    expect(fmtCompactUsd(Number.NaN)).toBe("—");
  });
});

describe("probabilityLabel", () => {
  it("rounds, but never shows certainty that is not there", () => {
    expect(probabilityLabel(0.5)).toBe("50%");
    expect(probabilityLabel(0.62)).toBe("62%");
    expect(probabilityLabel(0.994)).toBe("99%");
    expect(probabilityLabel(0.004)).toBe("<1%");
    expect(probabilityLabel(0.996)).toBe(">99%");
    expect(probabilityLabel(0)).toBe("0%");
    expect(probabilityLabel(1)).toBe("100%");
  });
  it("is a dash for something that is not a number", () => {
    expect(probabilityLabel(Number.NaN)).toBe("—");
    expect(probabilityLabel(Infinity)).toBe("—");
  });
});

it("bandLabel names the percentile pair", () => {
  expect(bandLabel(10, 90)).toEqual({ legend: "Likely range", tooltip: "10th to 90th percentile" });
});

// ── dates ────────────────────────────────────────────────────────────────────────────────
describe("dates", () => {
  it("formats an ISO day without a time zone", () => {
    expect(fmtIsoDay("2026-10-23")).toBe("Oct 23");
    expect(fmtIsoDay("2026-01-05")).toBe("Jan 5");
    expect(fmtIsoDay("2026-10-23T00:00:00Z")).toBe("Oct 23");
    expect(fmtIsoDay(null)).toBe("—");
    expect(fmtIsoDay("not a date")).toBe("—");
    expect(fmtIsoDay("2026-13-01")).toBe("—");
    expect(fmtIsoDay("2026-02-30")).toBe("—");
    expect(fmtIsoDay("2026-04-31")).toBe("—");
    expect(fmtIsoDay("2024-02-29")).toBe("Feb 29");
    expect(fmtIsoDay("2026-02-29")).toBe("—");
    expect(monthName("2026-02-30")).toBe("—");
    expect(addDays("2026-02-30", 1)).toBe("2026-02-30");
  });
  it("the shared day helpers are the same functions", () => {
    expect(isoAddDays).toBe(addDays);
  });
  it("does not shift the day west of UTC", () => {
    const previous = process.env.TZ;
    process.env.TZ = "America/Los_Angeles";
    try {
      // The control: the naive way really does show the day before in this zone.
      expect(new Date("2026-10-23").getDate()).toBe(22);
      expect(fmtIsoDay("2026-10-23")).toBe("Oct 23");
      expect(monthName("2026-11-01")).toBe("November");
      expect(addDays("2026-11-01", -1)).toBe("2026-10-31");
      expect(eachDay("2026-10-30", "2026-11-02")).toEqual(["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]);
    } finally {
      if (previous === undefined) delete process.env.TZ;
      else process.env.TZ = previous;
    }
  });
  it("month names and day arithmetic", () => {
    expect(monthName("2026-10-01")).toBe("October");
    expect(monthName("2026-12-31")).toBe("December");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2024-03-01", -1)).toBe("2024-02-29");
    expect(eachDay("2026-10-05", "2026-10-04")).toEqual([]);
  });
});

// ── presets and periods ──────────────────────────────────────────────────────────────────
describe("presets and periods", () => {
  it("groups presets: ungrouped (baseline) first, groups in order of first appearance", () => {
    const groups = groupPresets(PRESETS);
    expect(groups.map((g) => g.group)).toEqual([null, "Usage", "Model mix"]);
    expect(groups[0].presets.map((p) => p.id)).toEqual(["baseline"]);
    expect(groups[1].presets.map((p) => p.id)).toEqual(["usage-up-20", "usage-down-15"]);
    expect(groups[2].presets.map((p) => p.id)).toEqual(["opus-to-sonnet-30"]);
  });
  it("falls back to the baseline for an id the run does not have", () => {
    expect(effectivePresetId(PRESETS, "usage-up-20")).toBe("usage-up-20");
    expect(effectivePresetId(PRESETS, "gone")).toBe("baseline");
  });
  it("labels the periods", () => {
    expect(periodLabels(mkView().series[0].periods)).toEqual(["This month", "Next month"]);
    expect(periodLabels(monthEndView().series[0].periods)).toEqual(["October", "November"]);
  });
  it("detects a series without numbers", () => {
    expect(isIncomplete(mkView(), "usage-down-15")).toBe(true);
    expect(isIncomplete(mkView(), "baseline")).toBe(false);
    expect(isIncomplete(mkView(), "unknown")).toBe(true);
    expect(incompleteNote("usage-down-15")).toBe("No forecast for this change yet.");
    expect(incompleteNote("baseline")).toBe("No forecast for this scope yet.");
  });
});

// ── burn-up rows ─────────────────────────────────────────────────────────────────────────
describe("burnUpRows", () => {
  it("current period: cumulative actual, a join row with no step, then the projection", () => {
    expect(burnUpRows(mkView(), "baseline", 0)).toEqual([
      { date: "2026-10-01", actual: 2 * M },
      { date: "2026-10-02", actual: 5 * M },
      // the last actual day: the projection starts here at actual_to_date, equal to `actual`
      { date: "2026-10-03", actual: 9 * M, expected: 9 * M, low: 9 * M, high: 9 * M, bandBase: 9 * M, bandSpan: 0 },
      { date: "2026-10-04", expected: 14 * M, low: 12 * M, high: 16 * M, bandBase: 12 * M, bandSpan: 4 * M },
      { date: "2026-10-05", expected: 19 * M, low: 15 * M, high: 23 * M, bandBase: 15 * M, bandSpan: 8 * M },
    ]);
  });
  it("the cumulative actual meets actual_to_date exactly", () => {
    const view = mkView();
    const join = burnUpRows(view, "baseline", 0)[2];
    expect(join.actual).toBe(view.series[0].periods[0].actual_to_date_usd_micros);
    expect(join.expected).toBe(join.actual);
  });
  it("adds the baseline's expected only for another preset, joined the same way", () => {
    const base = burnUpRows(mkView(), "baseline", 0);
    expect(base.every((r) => r.baselineExpected === undefined)).toBe(true);
    const rows = burnUpRows(mkView(), "usage-up-20", 0);
    expect(rows.map((r) => r.baselineExpected)).toEqual([undefined, undefined, 9 * M, 14 * M, 19 * M]);
    expect(rows.map((r) => r.expected)).toEqual([undefined, undefined, 9 * M, 16 * M, 23 * M]);
    expect(rows[4]).toMatchObject({ low: 17 * M, high: 29 * M, bandBase: 17 * M, bandSpan: 12 * M });
  });
  it("a future period has no actual rows and no join row", () => {
    const rows = burnUpRows(mkView(), "baseline", 1);
    expect(rows.map((r) => r.date)).toEqual(["2026-11-01", "2026-11-02", "2026-11-03"]);
    expect(rows.every((r) => r.actual === undefined)).toBe(true);
    expect(rows[0]).toEqual({ date: "2026-11-01", expected: 6 * M, low: 5 * M, high: 7 * M, bandBase: 5 * M, bandSpan: 2 * M });
    expect(burnUpRows(mkView(), "usage-up-20", 1)[0].baselineExpected).toBe(6 * M);
  });
  it("month-end case: no period is current, so no actual rows and no join row", () => {
    const rows = burnUpRows(monthEndView(), "baseline", 0);
    expect(rows).toHaveLength(5);
    expect(rows.every((r) => r.actual === undefined)).toBe(true);
    expect(rows[0]).toEqual({ date: "2026-10-01", expected: 5 * M, low: 4 * M, high: 6 * M, bandBase: 4 * M, bandSpan: 2 * M });
  });
  it("merges the join row with the last actual day when only one day is actual", () => {
    const view = withPeriod(mkView(), "baseline", 0, {
      forecast_from: "2026-10-02", actual_to_date_usd_micros: 2 * M,
      projected: [pt("2026-10-02", 7, 6, 8), pt("2026-10-03", 12, 9, 15), pt("2026-10-04", 17, 12, 22), pt("2026-10-05", 22, 15, 29)],
    });
    const rows = burnUpRows(view, "baseline", 0);
    expect(rows).toHaveLength(5);
    expect(rows[0]).toEqual({ date: "2026-10-01", actual: 2 * M, expected: 2 * M, low: 2 * M, high: 2 * M, bandBase: 2 * M, bandSpan: 0 });
    expect(rows[1]).toMatchObject({ date: "2026-10-02", expected: 7 * M });
    expect(rows[1].actual).toBeUndefined();
  });
  it("an incomplete series or a missing period gives no rows", () => {
    expect(burnUpRows(mkView(), "usage-down-15", 0)).toEqual([]);
    expect(burnUpRows(mkView(), "baseline", 7)).toEqual([]);
  });
});

describe("burnUpMarkers", () => {
  it("budget line and the runway marker with its range", () => {
    expect(burnUpMarkers(mkView(), "baseline", 0)).toEqual({
      budget: 20 * M, runsOutOn: "2026-10-05", range: { from: "2026-10-04", to: "2026-10-05" },
    });
  });
  it("nothing when the runway has no dates, none for a future period, none without a budget", () => {
    expect(burnUpMarkers(mkView(), "opus-to-sonnet-30", 0)).toEqual({ budget: 20 * M, runsOutOn: null, range: null });
    expect(burnUpMarkers(mkView(), "baseline", 1)).toEqual({ budget: 20 * M, runsOutOn: null, range: null });
    const none = mkView({ budget_usd_micros: null });
    expect(burnUpMarkers(withRunway(none, null), "baseline", 0)).toEqual({ budget: null, runsOutOn: null, range: null });
  });
  it("a single-day range (exhausted) draws the marker only", () => {
    const view = withRunway(mkView(), {
      status: "exhausted", runs_out_probability: 1, days_short: 3,
      runs_out_on: { p10: "2026-10-02", p50: "2026-10-02", p90: "2026-10-02" },
    });
    expect(burnUpMarkers(view, "baseline", 0)).toEqual({ budget: 20 * M, runsOutOn: "2026-10-02", range: null });
  });
});

describe("legends list only the series that are drawn", () => {
  it("burn-up: a current period has every entry", () => {
    const view = mkView();
    expect(burnUpLegend(burnUpRows(view, "baseline", 0), burnUpMarkers(view, "baseline", 0), false))
      .toEqual(["actual", "expected", "band", "budget", "runway"]);
    expect(burnUpLegend(burnUpRows(view, "usage-up-20", 0), burnUpMarkers(view, "usage-up-20", 0), true))
      .toEqual(["actual", "expected", "band", "baseline", "budget", "runway"]);
  });
  it("burn-up: no 'spent so far' in a future month or the month-end case", () => {
    const view = mkView();
    expect(burnUpLegend(burnUpRows(view, "baseline", 1), burnUpMarkers(view, "baseline", 1), false))
      .toEqual(["expected", "band", "budget"]);
    const end = monthEndView();
    expect(burnUpLegend(burnUpRows(end, "baseline", 0), burnUpMarkers(end, "baseline", 0), false))
      .toEqual(["expected", "band", "budget"]);
  });
  it("burn-up: no baseline entry unless a baseline line is drawn, no budget entry without a budget", () => {
    const view = mkView({ budget_usd_micros: null });
    const keys = burnUpLegend(burnUpRows(view, "usage-up-20", 1), burnUpMarkers(view, "usage-up-20", 1), true);
    expect(keys).toEqual(["expected", "band", "baseline"]);
    expect(burnUpLegend([], burnUpMarkers(view, "usage-down-15", 0), false)).toEqual([]);
  });
  it("daily: actual, expected and band", () => {
    expect(dailyLegend(dailyRows(mkView(), "baseline"))).toEqual(["actual", "expected", "band"]);
    expect(dailyLegend(dailyRows(mkView(), "usage-down-15"))).toEqual(["actual"]);
  });
});

// ── daily rows ───────────────────────────────────────────────────────────────────────────
describe("dailyRows", () => {
  it("the last 28 actual days, then the forecast after data_through", () => {
    const rows = dailyRows(mkView(), "baseline");
    expect(mkView().actual_daily).toHaveLength(31);
    const actual = rows.filter((r) => r.actual !== undefined);
    expect(actual).toHaveLength(28);
    expect(actual[0].date).toBe("2026-09-06");
    expect(actual[27]).toEqual({ date: "2026-10-03", actual: 4 * M });
    const forecast = rows.filter((r) => r.expected !== undefined);
    expect(forecast.map((r) => r.date)).toEqual(["2026-10-04", "2026-10-05", "2026-11-01", "2026-11-02", "2026-11-03"]);
    expect(forecast[0]).toEqual({ date: "2026-10-04", expected: 5 * M, low: 3 * M, high: 7 * M, bandBase: 3 * M, bandSpan: 4 * M });
    expect(rows).toHaveLength(33);
  });
  it("keeps all rows when there are fewer than 28, and sorts them", () => {
    const view = mkView({ actual_daily: [{ date: "2026-10-02", usd_micros: M }, { date: "2026-10-01", usd_micros: 2 * M }] });
    expect(dailyRows(view, "baseline").slice(0, 2).map((r) => r.date)).toEqual(["2026-10-01", "2026-10-02"]);
  });
  it("month-end case: 28 actual days and a forecast that all lies after data_through", () => {
    const rows = dailyRows(monthEndView(), "baseline");
    expect(rows.filter((r) => r.actual !== undefined)).toHaveLength(28);
    expect(rows.filter((r) => r.expected !== undefined)).toHaveLength(8);
  });
  it("an incomplete series has actuals only", () => {
    expect(dailyRows(mkView(), "usage-down-15").every((r) => r.expected === undefined)).toBe(true);
  });
});

// ── sentences ────────────────────────────────────────────────────────────────────────────
describe("headlineSentences", () => {
  it("baseline with a budget", () => {
    expect(headlineSentences(mkView(), "baseline", 0)).toEqual([
      "On the current trajectory, October lands at $19 (likely $15–$23).",
      "62% likely to stay within the $20 budget.",
    ]);
  });
  it("uses compact thousands for larger landings", () => {
    const view = withPeriod(mkView(), "baseline", 0, { landing: landing(18_200, 16_000, 18_000, 21_100) });
    expect(headlineSentences(view, "baseline", 0)[0]).toBe("On the current trajectory, October lands at $18.2k (likely $16.0k–$21.1k).");
  });
  it("without a budget there is no probability sentence", () => {
    const view = mkView({ budget_usd_micros: null });
    expect(headlineSentences(withPeriod(view, "baseline", 0, { within_budget_probability: null, runway: null }), "baseline", 0))
      .toEqual(["On the current trajectory, October lands at $19 (likely $15–$23)."]);
  });
  it("no probability sentence without a budget, even if the period carries a probability", () => {
    const view = mkView({ budget_usd_micros: null });
    expect(view.series[0].periods[0].within_budget_probability).toBe(0.62);
    expect(headlineSentences(view, "baseline", 0)).toEqual(["On the current trajectory, October lands at $19 (likely $15–$23)."]);
    expect(headlineSentences(view, "usage-up-20", 0)).toHaveLength(2);
  });
  it("a future period names its month", () => {
    expect(headlineSentences(mkView(), "baseline", 1)).toEqual([
      "On the current trajectory, November lands at $18 (likely $14–$22).",
      "90% likely to stay within the $20 budget.",
    ]);
  });
  it("month-end case names the months and has no runway", () => {
    expect(headlineSentences(monthEndView(), "baseline", 0)[0]).toBe("On the current trajectory, October lands at $25 (likely $17–$33).");
    expect(runwaySentence(monthEndView(), "baseline", 0)).toBeNull();
  });
  it("another preset: more, less, and the same as the baseline", () => {
    expect(headlineSentences(mkView(), "usage-up-20", 0)).toEqual([
      "With \"Usage grows 20%\", October lands at $23 (likely $17–$29).",
      "$4 more than the current trajectory.",
      "30% likely to stay within the $20 budget.",
    ]);
    expect(headlineSentences(mkView(), "opus-to-sonnet-30", 0)).toEqual([
      "With \"Move 30% of Opus work to Sonnet\", October lands at $16 (likely $13–$19).",
      "$3 less than the current trajectory.",
      "95% likely to stay within the $20 budget.",
    ]);
    // $0.30 is zero at the displayed precision
    expect(headlineSentences(mkView(), "opus-to-sonnet-30", 1)[1]).toBe("Same as the current trajectory.");
    // exactly zero too
    expect(headlineSentences(withPeriod(mkView(), "usage-up-20", 1, { delta_vs_baseline_usd_micros: 0 }), "usage-up-20", 1)[1])
      .toBe("Same as the current trajectory.");
  });
  it("larger deltas are compact", () => {
    const view = withPeriod(mkView(), "usage-up-20", 0, { delta_vs_baseline_usd_micros: 1_406 * M });
    expect(headlineSentences(view, "usage-up-20", 0)[1]).toBe("$1.4k more than the current trajectory.");
    const less = withPeriod(mkView(), "usage-up-20", 0, { delta_vs_baseline_usd_micros: -842 * M });
    expect(headlineSentences(less, "usage-up-20", 0)[1]).toBe("$842 less than the current trajectory.");
  });
  it("omits the comparison when the baseline has no numbers", () => {
    const view = mkView();
    const broken: View = { ...view, series: view.series.map((s) => s.preset_id === "baseline" ? { ...s, status: "incomplete", daily: [], periods: [] } : s) };
    expect(headlineSentences(broken, "usage-up-20", 0)).toEqual([
      "With \"Usage grows 20%\", October lands at $23 (likely $17–$29).",
      "30% likely to stay within the $20 budget.",
    ]);
  });
  it("probability edges", () => {
    const at = (p: number) => headlineSentences(withPeriod(mkView(), "baseline", 0, { within_budget_probability: p }), "baseline", 0)[1];
    expect(at(0.004)).toBe("<1% likely to stay within the $20 budget.");
    expect(at(0.996)).toBe(">99% likely to stay within the $20 budget.");
    expect(at(0)).toBe("0% likely to stay within the $20 budget.");
    expect(at(1)).toBe("100% likely to stay within the $20 budget.");
    expect(at(0.5)).toBe("50% likely to stay within the $20 budget.");
  });
  it("drops the probability sentence once the budget is used up", () => {
    const view = withRunway(withPeriod(mkView(), "baseline", 0, { within_budget_probability: 0 }), {
      status: "exhausted", runs_out_probability: 1, days_short: 3,
      runs_out_on: { p10: "2026-10-02", p50: "2026-10-02", p90: "2026-10-02" },
    });
    expect(headlineSentences(view, "baseline", 0)).toEqual(["On the current trajectory, October lands at $19 (likely $15–$23)."]);
  });
  it("an incomplete series has no sentences", () => {
    expect(headlineSentences(mkView(), "usage-down-15", 0)).toEqual([]);
  });
});

describe("runwaySentence", () => {
  const run = (runway: Runway | null, presetId = "baseline", view = mkView()) => runwaySentence(withRunway(view, runway, presetId), presetId, 0);
  it("lasts: the budget minus the median landing is what is likely left", () => {
    const lasts: Runway = { status: "lasts", runs_out_probability: 0.04, runs_out_on: { p10: null, p50: null, p90: null }, days_short: null };
    // budget $20, p50 landing $18.5 -> $1.5 left
    expect(run(lasts)).toBe("The $20 budget lasts the month; $2 likely left.");
    const big = withPeriod(mkView({ budget_usd_micros: 9_000 * M }), "baseline", 0, { landing: landing(6_100, 5_500, 6_200, 6_900) });
    expect(runwaySentence(withRunway(big, lasts), "baseline", 0)).toBe("The $9,000 budget lasts the month; $2.8k likely left.");
  });
  it("lasts floors what is left at zero", () => {
    const lasts: Runway = { status: "lasts", runs_out_probability: 0.1, runs_out_on: { p10: null, p50: null, p90: null }, days_short: null };
    const view = withPeriod(mkView(), "baseline", 0, { landing: landing(25, 20, 22, 30) });
    expect(runwaySentence(withRunway(view, lasts), "baseline", 0)).toBe("The $20 budget lasts the month; $0 likely left.");
  });
  it("at risk: with dates, days early, the last day, and without dates", () => {
    const base: Runway = { status: "at_risk", runs_out_probability: 0.45, runs_out_on: { p10: "2026-10-04", p50: "2026-10-04", p90: "2026-10-05" }, days_short: 1 };
    expect(run(base)).toBe("The $20 budget could run out around Oct 4, 1 day early.");
    expect(run({ ...base, days_short: 8, runs_out_on: { p10: "2026-10-04", p50: "2026-10-04", p90: "2026-10-05" } }))
      .toBe("The $20 budget could run out around Oct 4, 8 days early.");
    expect(run(atRisk)).toBe("The $20 budget could run out around Oct 5.");
    expect(run({ ...base, runs_out_on: { p10: null, p50: null, p90: null }, days_short: null }))
      .toBe("The $20 budget could run out before the month ends (45% likely).");
  });
  it("runs out: with dates, singular day, and without dates", () => {
    const base: Runway = { status: "runs_out", runs_out_probability: 0.93, runs_out_on: { p10: "2026-10-04", p50: "2026-10-04", p90: "2026-10-05" }, days_short: 8 };
    expect(run(base)).toBe("At this pace the $20 budget runs out around Oct 4, 8 days early.");
    expect(run({ ...base, days_short: 1 })).toBe("At this pace the $20 budget runs out around Oct 4, 1 day early.");
    expect(run({ ...base, days_short: 0 })).toBe("At this pace the $20 budget runs out around Oct 4.");
    expect(run({ ...base, runs_out_on: { p10: null, p50: null, p90: null }, days_short: null }))
      .toBe("At this pace the $20 budget runs out before the month ends.");
  });
  it("exhausted names the day, or says it is used up", () => {
    const ex: Runway = { status: "exhausted", runs_out_probability: 1, days_short: 3, runs_out_on: { p10: "2026-10-02", p50: "2026-10-02", p90: "2026-10-02" } };
    expect(run(ex)).toBe("The $20 budget was used up on Oct 2.");
    expect(run({ ...ex, runs_out_on: { p10: null, p50: null, p90: null } })).toBe("The $20 budget is already used up.");
  });
  it("another preset names itself instead of saying \"at this pace\"", () => {
    expect(runwaySentence(mkView(), "usage-up-20", 0)).toBe("With \"Usage grows 20%\" the $20 budget runs out around Oct 4, 1 day early.");
    const lasts: Runway = { status: "lasts", runs_out_probability: 0.04, runs_out_on: { p10: null, p50: null, p90: null }, days_short: null };
    expect(runwaySentence(mkView(), "opus-to-sonnet-30", 0)).toBe("With \"Move 30% of Opus work to Sonnet\" the $20 budget lasts the month; $5 likely left.");
    const risk = withRunway(mkView(), { ...atRisk, days_short: 2 }, "usage-up-20");
    expect(runwaySentence(risk, "usage-up-20", 0)).toBe("With \"Usage grows 20%\" the $20 budget could run out around Oct 5, 2 days early.");
    expect(lasts.status).toBe("lasts");
  });
  it("no sentence for a period that is not under way, even if it carries a runway object", () => {
    const future = withPeriod(mkView(), "baseline", 1, { runway: atRisk });
    expect(runwaySentence(future, "baseline", 1)).toBeNull();
    const monthEnd = monthEndView();
    const withRunwayObject = withPeriod(monthEnd, "baseline", 0, { runway: atRisk });
    expect(runwaySentence(withRunwayObject, "baseline", 0)).toBeNull();
  });
  it("no sentence for a future period, without a runway or without a budget", () => {
    expect(runwaySentence(mkView(), "baseline", 1)).toBeNull();
    expect(run(null)).toBeNull();
    expect(runwaySentence(mkView({ budget_usd_micros: null }), "baseline", 0)).toBeNull();
    expect(runwaySentence(mkView(), "usage-down-15", 0)).toBeNull();
  });
});

it("forecastFootnote says when the forecast starts and what actuals it sits on", () => {
  expect(forecastFootnote(mkView())).toBe(
    "Forecast as of Sep 30. Actual spend through Oct 3. Model moves assume the same token volume.");
});

// ── planner ──────────────────────────────────────────────────────────────────────────────
const figures = (expected: number, planFor: number, gap: number | null): Schemas["PlanPeriodFigures"] => ({
  needed: { expected_usd_micros: expected * M, p50_usd_micros: expected * M, p80_usd_micros: planFor * M, p90_usd_micros: planFor * M },
  plan_for_usd_micros: planFor * M, gap_usd_micros: gap == null ? null : gap * M,
  tokens_by_model: [{ model: "opus-4.8", expected: 1_000, p90: 1_300 }],
});
function planRow(scopeType: string, id: string, name: string, budget: number | null, gap: number | null, empty = false): Schemas["PlanRow"] {
  return {
    scope_type: scopeType, scope_id: id, name, budget_usd_micros: budget == null ? null : budget * M,
    by_preset: [
      { preset_id: "baseline", periods: empty ? [] : [figures(10, 12, gap), figures(20, 24, gap == null ? null : gap + 1)] },
      { preset_id: "usage-up-20", periods: empty ? [] : [figures(12, 14, gap), figures(25, 29, gap == null ? null : gap + 2)] },
    ],
  };
}
const PLAN: Plan = {
  planning_confidence: 0.8,
  periods: [
    { start: "2026-10-01", end: "2026-10-31", forecast_from: "2026-10-09", current: true },
    { start: "2026-11-01", end: "2026-11-30", forecast_from: "2026-11-01", current: false },
  ],
  presets: PRESETS,
  rows: [
    planRow("team", "a", "Alpha", 10, 5),
    planRow("team", "b", "Bravo", null, null),
    planRow("team", "c", "Charlie", 7, 5),
    planRow("team", "d", "Delta", 14, -2),
    planRow("repo", "r1", "proj-one", 9, 1),
    planRow("repo", "r2", "proj-two", null, null),
    planRow("repo", "r3", "proj-three", 4, 3),
    planRow("repo", "r4", "proj-four", 4, null, true),
  ],
};

describe("planner helpers", () => {
  it("opens on the first period that is not under way", () => {
    expect(defaultPlanPeriod(PLAN.periods)).toBe(1);
    expect(defaultPlanPeriod([{ current: false }, { current: false }])).toBe(0);
    expect(defaultPlanPeriod([{ current: true }])).toBe(0);
  });
  it("builds a row per team and project, in response order, for a preset and period", () => {
    const rows = planRows(PLAN, "baseline", 1);
    expect(rows.map((r) => `${r.group}:${r.name}`)).toEqual([
      "Teams:Alpha", "Teams:Bravo", "Teams:Charlie", "Teams:Delta",
      "Projects:proj-one", "Projects:proj-two", "Projects:proj-three", "Projects:proj-four",
    ]);
    expect(rows[0]).toEqual({
      key: "team:a", scopeType: "team", group: "Teams", name: "Alpha", budget: 10 * M,
      expected: 20 * M, planFor: 24 * M, gap: 6 * M, tokens: [{ model: "opus-4.8", expected: 1_000, p90: 1_300 }],
    });
    expect(rows[1]).toMatchObject({ budget: null, gap: null, planFor: 24 * M });
    expect(planRows(PLAN, "usage-up-20", 0)[0]).toMatchObject({ expected: 12 * M, planFor: 14 * M, gap: 5 * M });
  });
  it("a preset with no figures keeps the name and budget, with empty figures; an unknown preset falls back", () => {
    const row = planRows(PLAN, "baseline", 0)[7];
    expect(row).toMatchObject({ name: "proj-four", budget: 4 * M, expected: null, planFor: null, gap: null, tokens: [] });
    expect(planRows(PLAN, "no-such-preset", 0)[0].expected).toBe(10 * M);
  });
  it("sort: descending, ascending, nulls always last, stable, inside each group", () => {
    const rows = planRows(PLAN, "baseline", 0);
    const names = (list: PlanTableRow[]) => list.map((r) => r.name);
    expect(names(sortPlanRows(rows, null))).toEqual(names(rows));
    expect(names(sortPlanRows(rows, "desc"))).toEqual([
      "Alpha", "Charlie", "Delta", "Bravo", "proj-three", "proj-one", "proj-two", "proj-four"]);
    expect(names(sortPlanRows(rows, "asc"))).toEqual([
      "Delta", "Alpha", "Charlie", "Bravo", "proj-one", "proj-three", "proj-two", "proj-four"]);
    expect(names(rows)).toEqual(["Alpha", "Bravo", "Charlie", "Delta", "proj-one", "proj-two", "proj-three", "proj-four"]);
  });
  it("the sort header cycles none -> descending -> ascending -> none", () => {
    expect(nextSortDir(null)).toBe("desc");
    expect(nextSortDir("desc")).toBe("asc");
    expect(nextSortDir("asc")).toBeNull();
  });
  it("uses plan_for_usd_micros, not the 80th percentile, and the response's confidence", () => {
    const f = (planFor: number): Schemas["PlanPeriodFigures"] => ({
      needed: { expected_usd_micros: 10 * M, p50_usd_micros: 11 * M, p80_usd_micros: 13 * M, p90_usd_micros: 15 * M },
      plan_for_usd_micros: planFor * M, gap_usd_micros: (planFor - 12) * M,
      tokens_by_model: [],
    });
    const plan: Plan = {
      ...PLAN, planning_confidence: 0.9,
      rows: [{ scope_type: "team", scope_id: "a", name: "Alpha", budget_usd_micros: 12 * M,
        by_preset: [{ preset_id: "baseline", periods: [f(15), f(14)] }] }],
    };
    const row = planRows(plan, "baseline", 0)[0];
    expect(row.planFor).toBe(15 * M);           // the p90 here, not the p80 (13)
    expect(row.planFor).not.toBe(plan.rows[0].by_preset[0].periods[0].needed.p80_usd_micros);
    expect(row.gap).toBe(3 * M);
    expect(planForHeader(plan.planning_confidence)).toBe("Plan for (90% likely enough)");
    expect(planRows({ ...plan, planning_confidence: 0.5 }, "baseline", 1)[0].planFor).toBe(14 * M);
  });
  it("the gap sort button names its state", () => {
    expect(gapSortLabel(null)).toBe("Sort by gap, currently not sorted");
    expect(gapSortLabel("desc")).toBe("Sort by gap, currently descending");
    expect(gapSortLabel("asc")).toBe("Sort by gap, currently ascending");
  });
  it("forecast token counts get a billions tier", () => {
    expect(fmtPlanTokens(812)).toBe("812");
    expect(fmtPlanTokens(320_000)).toBe("320K");
    expect(fmtPlanTokens(2_400_000)).toBe("2.4M");
    expect(fmtPlanTokens(999_949_999)).toBe("999.9M");
    expect(fmtPlanTokens(1_302_400_000)).toBe("1.3B");
    expect(fmtPlanTokens(1_562_800_000)).toBe("1.6B");
    expect(fmtPlanTokens(null)).toBe("—");
  });
  it("groups consecutive rows under Teams and Projects", () => {
    const groups = groupPlanRows(sortPlanRows(planRows(PLAN, "baseline", 0), "desc"));
    expect(groups.map((g) => [g.group, g.rows.length])).toEqual([["Teams", 4], ["Projects", 4]]);
  });
  it("the gap cell", () => {
    expect(planGapLabel(null)).toEqual({ tone: "none", text: "—" });
    expect(planGapLabel(740 * M)).toEqual({ tone: "over", text: "Over by $740" });
    expect(planGapLabel(1_400 * M)).toEqual({ tone: "over", text: "Over by $1.4k" });
    expect(planGapLabel(-500 * M)).toEqual({ tone: "room", text: "Room $500" });
    expect(planGapLabel(0)).toEqual({ tone: "on", text: "On budget" });
    expect(planGapLabel(400_000)).toEqual({ tone: "on", text: "On budget" });
    expect(planGapLabel(-400_000)).toEqual({ tone: "on", text: "On budget" });
  });
  it("header and token footnote", () => {
    expect(planForHeader(0.8)).toBe("Plan for (80% likely enough)");
    expect(planForHeader(0.825)).toBe("Plan for (83% likely enough)");
    expect(tokensFootnote(PLAN.periods[0])).toBe("Tokens cover Oct 9 to month end.");
    expect(tokensFootnote(PLAN.periods[1])).toBeNull();
  });
});

// ── the data seam ────────────────────────────────────────────────────────────────────────
describe("ApiSource forecast calls", () => {
  afterEach(() => vi.unstubAllGlobals());
  const stub = () => {
    const fetchMock = vi.fn(async (_url: string, _init?: unknown) => ({ ok: true, json: async () => ({ mode: {} }) }));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };
  it("forecast sends scope_type and scope_id, encoding a repo id", async () => {
    const f = stub();
    await new ApiSource().forecast({ scopeType: "repo", scopeId: "github:acme/auth-service" });
    expect(f.mock.calls[0][0]).toBe("/v1/forecast?scope_type=repo&scope_id=github%3Aacme%2Fauth-service");
    await new ApiSource().forecast({ scopeType: "team", scopeId: "development" });
    expect(f.mock.calls[1][0]).toBe("/v1/forecast?scope_type=team&scope_id=development");
  });
  it("forecastPlan takes no parameters", async () => {
    const f = stub();
    await new ApiSource().forecastPlan();
    expect(f.mock.calls[0][0]).toBe("/v1/forecast/plan");
  });
  it("the org uses the real org id for the forecast, not the economics placeholder", () => {
    expect(forecastScope({ scopeType: "org", scopeId: "org" })).toEqual({ scopeType: "org", scopeId: ORG_SCOPE_ID });
    expect(ORG_SCOPE_ID).toBe("default");
    const team = { scopeType: "team" as const, scopeId: "platform" };
    expect(forecastScope(team)).toBe(team);
  });
});

// ── the mock fixture ─────────────────────────────────────────────────────────────────────
describe("mock forecast fixture", () => {
  const scopes: [string, string][] = [
    ["org", ORG_SCOPE_ID], ["team", "platform"], ["team", "growth"], ["team", "data"], ["team", "infra"],
    ["team", "mobile"], ["repo", "api-gateway"], ["repo", "auth-service"],
  ];
  const views = scopes.map(([t, i]) => forecastFixture(t, i) as View);

  it("has a run for the org, the teams and two projects, and none for growth-web", () => {
    expect(views.every((v) => v !== null)).toBe(true);
    expect(forecastFixture("repo", "growth-web")).toBeNull();
    expect(forecastFixture("team", "no-such-team")).toBeNull();
  });
  it("serves envelopes through the mock source", async () => {
    const src = new MockSource();
    const org = await src.forecast(forecastScope({ scopeType: "org", scopeId: "org" }));
    expect(org.data?.forecast?.series).toHaveLength(7);
    const gone = await src.forecast({ scopeType: "repo", scopeId: "growth-web" });
    expect(gone.data?.forecast).toBeNull();
    expect(gone.unavailable).toBeNull();
    const plan = await src.forecastPlan();
    expect(plan.data?.rows.map((r) => r.scope_id)).toEqual(["platform", "growth", "data", "infra", "mobile", "api-gateway", "auth-service"]);
  });
  it("is internally consistent", () => {
    for (const view of views) {
      expect(view.actual_daily).toHaveLength(36);
      expect(view.series[0].preset_id).toBe("baseline");
      expect(view.series.map((s) => s.preset_id)).toEqual(view.presets.map((p) => p.id));
      for (const s of view.series.filter((x) => x.status === "complete")) {
        expect(s.daily).toHaveLength(61);
        s.periods.forEach((p, i) => {
          const last = p.projected[p.projected.length - 1];
          expect(last.date).toBe(p.end);
          expect(p.projected[0].date).toBe(p.forecast_from);
          expect(p.landing.expected_usd_micros).toBe(last.expected_usd_micros);
          expect(p.landing.p10_usd_micros).toBe(last.low_usd_micros);
          expect(p.landing.p90_usd_micros).toBe(last.high_usd_micros);
          expect(p.landing.p10_usd_micros).toBeLessThanOrEqual(p.landing.p50_usd_micros);
          expect(p.landing.p50_usd_micros).toBeLessThanOrEqual(p.landing.p90_usd_micros);
          p.projected.forEach((r, k) => {
            expect(r.low_usd_micros).toBeLessThanOrEqual(r.expected_usd_micros);
            expect(r.expected_usd_micros).toBeLessThanOrEqual(r.high_usd_micros);
            if (k > 0) expect(r.expected_usd_micros).toBeGreaterThan(p.projected[k - 1].expected_usd_micros);
          });
          const current = p.forecast_from > p.start;
          expect(current).toBe(i === 0);
          const spent = view.actual_daily.filter((a) => a.date >= p.start && a.date < p.forecast_from).reduce((n, a) => n + a.usd_micros, 0);
          expect(p.actual_to_date_usd_micros).toBe(spent);
          expect(p.delta_vs_baseline_usd_micros === 0).toBe(s.preset_id === "baseline" || p.landing.expected_usd_micros === view.series[0].periods[i].landing.expected_usd_micros);
          if (view.budget_usd_micros == null) {
            expect(p.within_budget_probability).toBeNull();
            expect(p.runway).toBeNull();
          } else {
            expect(p.within_budget_probability).not.toBeNull();
            if (p.runway) {
              expect(current).toBe(true);
              const { runs_out_on: on } = p.runway;
              if (p.runway.status !== "exhausted") {
                expect(p.runway.runs_out_probability + (p.within_budget_probability ?? 0)).toBeCloseTo(1, 12);
              }
              if (on.p10 && on.p50 && on.p90) {
                expect(on.p10 <= on.p50 && on.p50 <= on.p90).toBe(true);
                expect(p.runway.days_short).toBe(eachDay(on.p50, p.end).length - 1);
              } else {
                expect(p.runway.days_short).toBeNull();
              }
            } else {
              expect(current).toBe(false);
            }
          }
        });
      }
    }
  });
  it("the burn-up's actual line meets the projection exactly, for every scope", () => {
    for (const view of views) {
      const rows = burnUpRows(view, "baseline", 0);
      const join = rows.find((r) => r.actual !== undefined && r.expected !== undefined);
      expect(join?.actual).toBe(view.series[0].periods[0].actual_to_date_usd_micros);
      expect(join?.expected).toBe(join?.actual);
    }
  });
  it("covers every runway state, a scope without a budget and an incomplete series", () => {
    const statuses = new Set(views.flatMap((v) => v.series.flatMap((s) => s.periods.map((p) => p.runway?.status))));
    for (const status of ["lasts", "at_risk", "runs_out", "exhausted"]) expect(statuses.has(status as Runway["status"])).toBe(true);
    expect(views.some((v) => v.budget_usd_micros === null)).toBe(true);
    expect(views.some((v) => v.series.some((s) => s.status === "incomplete" && s.periods.length === 0))).toBe(true);
    const baselineStatuses = views.map((v) => v.series[0].periods[0].runway?.status);
    expect(baselineStatuses).toContain("exhausted");
    expect(baselineStatuses).toContain("runs_out");
  });
  it("every generated sentence passes the lexicon gate", () => {
    const sentences: string[] = [];
    for (const view of views) {
      for (const preset of view.presets) {
        sentences.push(preset.label, preset.description, incompleteNote(preset.id));
        for (let pi = 0; pi < 2; pi++) {
          sentences.push(...headlineSentences(view, preset.id, pi));
          const r = runwaySentence(view, preset.id, pi);
          if (r) sentences.push(r);
        }
      }
      sentences.push(forecastFootnote(view), ...periodLabels(view.series[0].periods));
    }
    sentences.push(...headlineSentences(mkView(), "usage-up-20", 0), planForHeader(0.8), planGapLabel(M).text);
    expect(sentences.length).toBeGreaterThan(100);
    for (const text of sentences) expect(() => assertLexicon(text)).not.toThrow();
  });
  it("the plan fixture agrees with the run: gap is plan-for minus budget, tokens cover the forecast", () => {
    const plan = planFixture();
    expect(plan.planning_confidence).toBe(0.8);
    expect(plan.periods.map((p) => p.current)).toEqual([true, false]);
    for (const row of plan.rows) {
      const view = forecastFixture(row.scope_type, row.scope_id) as View;
      expect(row.budget_usd_micros ?? null).toBe(view.budget_usd_micros ?? null);
      for (const preset of row.by_preset) {
        const s = view.series.find((x) => x.preset_id === preset.preset_id) as Schemas["ForecastSeries"];
        expect(preset.periods).toHaveLength(s.periods.length);
        preset.periods.forEach((f, i) => {
          expect(f.needed.expected_usd_micros).toBe(s.periods[i].landing.expected_usd_micros);
          expect(f.needed.p50_usd_micros).toBeLessThanOrEqual(f.plan_for_usd_micros);
          expect(f.plan_for_usd_micros).toBeLessThanOrEqual(f.needed.p90_usd_micros);
          expect(f.gap_usd_micros).toBe(row.budget_usd_micros == null ? null : f.plan_for_usd_micros - row.budget_usd_micros);
          expect(f.tokens_by_model.length).toBeGreaterThan(0);
        });
      }
    }
    expect(planRows(plan, "baseline", 1).length).toBe(7);
  });
});
