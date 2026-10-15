/**
 * Forecast presentation helpers (issue #85, T7). Everything that decides a number, a label, a
 * sentence or a chart row for the Economics screen's forecast panel and budget planner lives
 * here -- pure, so it is unit-tested to the character (`forecast.test.ts`); the components only
 * lay the results out.
 *
 * Inputs are the generated `BudgetForecast` / `BudgetPlan` types (`data/api-types.ts`). Dollars
 * are integer micro-USD all the way to the string, so the tests can use exact values.
 *
 * Dates are ISO `YYYY-MM-DD` strings and are never parsed through the local-time `Date`
 * constructor: they are split as text, and day arithmetic goes through `Date.UTC`, so a browser
 * west of UTC never shows the day before.
 */
import type { Schemas } from "../data/source";
import { addDays, eachDay, splitIso } from "../data/isoDay";
import { fmtMoneyMicros, fmtTokens } from "./format";

export { addDays, eachDay };

type View = Schemas["ForecastView"];
type Series = Schemas["ForecastSeries"];
type Period = Schemas["ForecastPeriod"];
type Preset = Schemas["ForecastPreset"];
type Plan = Schemas["BudgetPlan"];

/** The preset every other one is compared with. */
export const BASELINE_ID = "baseline";
/** Days of actual daily spend the daily chart shows. */
export const DAILY_ACTUAL_DAYS = 28;

// ── numbers ─────────────────────────────────────────────────────────────────────────────
const dash = "—";

/** Micro-USD -> "$950" / "$18.2k" / "$1.2M" / "$3.4B" (a model output: compact, one decimal for
 * k, M, B and T). Below $999.50 whole dollars, so `$999.60` never prints as `$1000` and `$999,960` never
 * as `$1000.0k`. Signed; the sentences pass magnitudes. */
export function fmtCompactUsd(usdMicros: number | null | undefined): string {
  if (usdMicros == null || !isFinite(usdMicros)) return dash;
  const sign = usdMicros < 0 ? "-" : "";
  const usd = Math.abs(usdMicros) / 1_000_000;
  if (usd < 999.5) return `${sign}$${Math.round(usd)}`;
  // k, M, B, T: a tier ends where one decimal would round to 1000.0 of it.
  const tiers: [number, number, string][] = [
    [1e3, 999_950, "k"], [1e6, 999_950_000, "M"], [1e9, 999_950_000_000, "B"], [1e12, Infinity, "T"],
  ];
  const [unit, , suffix] = tiers.find(([, limit]) => usd < limit) as [number, number, string];
  return `${sign}$${(usd / unit).toFixed(1)}${suffix}`;
}

/** A probability as a percentage that never overstates certainty: "<1%" for any chance below
 * half a percent, ">99%" for any chance short of one that would round to 100%. */
export function probabilityLabel(p: number): string {
  if (!isFinite(p)) return dash;
  const n = Math.round(100 * p);
  if (p > 0 && n <= 0) return "<1%";
  if (p < 1 && n >= 100) return ">99%";
  return `${n}%`;
}

/** The legend text for the band, and the percentile pair for its tooltip. */
export function bandLabel(bandLow: number, bandHigh: number): { legend: string; tooltip: string } {
  return { legend: "Likely range", tooltip: `${bandLow}th to ${bandHigh}th percentile` };
}

// ── dates (no local-time parsing) ───────────────────────────────────────────────────────
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "2026-10-23" -> "Oct 23" (any time part is ignored; "—" for something that is not a date). */
export function fmtIsoDay(iso: string | null | undefined): string {
  const p = iso ? splitIso(iso) : null;
  return p ? `${MONTHS[p.m - 1].slice(0, 3)} ${p.d}` : dash;
}

/** "2026-10-01" -> "October". */
export function monthName(iso: string): string {
  const p = splitIso(iso);
  return p ? MONTHS[p.m - 1] : dash;
}

// ── presets and periods ─────────────────────────────────────────────────────────────────
export type PresetGroup = { group: string | null; presets: Preset[] };

/** Presets grouped for the button rows: the ungrouped ones (baseline) first, then each group
 * in order of its first preset; response order inside each. */
export function groupPresets(presets: Preset[]): PresetGroup[] {
  const ungrouped: Preset[] = [];
  const named = new Map<string, Preset[]>();
  for (const p of presets) {
    if (p.group == null) ungrouped.push(p);
    else named.set(p.group, [...(named.get(p.group) ?? []), p]);
  }
  return [
    ...(ungrouped.length ? [{ group: null, presets: ungrouped }] : []),
    ...[...named.entries()].map(([group, list]) => ({ group, presets: list })),
  ];
}

/** The selected preset if the run has it, else the baseline. */
export function effectivePresetId(presets: Preset[], id: string): string {
  return presets.some((p) => p.id === id) ? id : BASELINE_ID;
}

/** A period is under way (it holds actual days) exactly when its first forecast day is after
 * its first day. In the month-end case no period is current. */
export function isCurrentPeriod(p: { start: string; forecast_from: string }): boolean {
  return p.forecast_from > p.start;
}

/** Toggle labels: "This month" for the current period, "Next month" for the one right after
 * it, otherwise the month name. */
export function periodLabels(periods: { start: string; forecast_from: string }[]): string[] {
  const current = periods.findIndex(isCurrentPeriod);
  return periods.map((p, i) => {
    if (i === current) return "This month";
    if (current >= 0 && i === current + 1) return "Next month";
    return monthName(p.start);
  });
}

export function seriesOf(view: View, presetId: string): Series | undefined {
  return view.series.find((s) => s.preset_id === presetId);
}

/** One preset's period; `undefined` for a series without numbers (cost run `incomplete`). */
export function periodOf(view: View, presetId: string, periodIndex: number): Period | undefined {
  return seriesOf(view, presetId)?.periods[periodIndex];
}

/** A series with no numbers: the panel shows a note instead of charts. */
export function isIncomplete(view: View, presetId: string): boolean {
  const s = seriesOf(view, presetId);
  return !s || s.periods.length === 0;
}

/** The note for an incomplete series. */
export function incompleteNote(presetId: string): string {
  return presetId === BASELINE_ID ? "No forecast for this scope yet." : "No forecast for this change yet.";
}

// ── chart rows ──────────────────────────────────────────────────────────────────────────
export type BurnUpRow = {
  date: string;
  /** Cumulative actual spend (days before the first forecast day). */
  actual?: number;
  expected?: number;
  low?: number;
  high?: number;
  /** Stack base and height of the band (`low`, `high - low`): the chart draws them as two
   * stacked areas, the first transparent. */
  bandBase?: number;
  bandSpan?: number;
  /** The baseline's expected, only when another preset is selected. */
  baselineExpected?: number;
};

type Point = Schemas["ForecastDailyPoint"];

function projectedByDate(period: Period | undefined): Map<string, Point> {
  return new Map((period?.projected ?? []).map((p) => [p.date, p]));
}

/** The burn-up rows of one period: one row per day `start..end`.
 *
 * Current period: `actual` is the cumulative actual spend for days `start..forecast_from-1`;
 * on the last of those days `expected`, `low` and `high` all equal `actual_to_date`, so the
 * projection leaves the actual line exactly where it ends (no step at `data_through`); from
 * `forecast_from` on, the period's `projected` rows. A future period has no actual rows and no
 * join row. A non-baseline preset adds the baseline's expected as `baselineExpected`, joined
 * the same way. */
export function burnUpRows(view: View, presetId: string, periodIndex: number): BurnUpRow[] {
  const period = periodOf(view, presetId, periodIndex);
  if (!period) return [];
  const baseline = presetId === BASELINE_ID ? undefined : periodOf(view, BASELINE_ID, periodIndex);
  const proj = projectedByDate(period);
  const baseProj = projectedByDate(baseline);
  const spent = new Map(view.actual_daily.map((a) => [a.date, a.usd_micros]));
  const current = isCurrentPeriod(period);
  const joinDay = addDays(period.forecast_from, -1);

  let cumulative = 0;
  return eachDay(period.start, period.end).map((date) => {
    const row: BurnUpRow = { date };
    if (current && date < period.forecast_from) {
      cumulative += spent.get(date) ?? 0;
      row.actual = cumulative;
    }
    if (current && date === joinDay) {
      const a = period.actual_to_date_usd_micros;
      Object.assign(row, { expected: a, low: a, high: a, bandBase: a, bandSpan: 0 });
      if (baseline) row.baselineExpected = baseline.actual_to_date_usd_micros;
    }
    const p = proj.get(date);
    if (p) {
      Object.assign(row, {
        expected: p.expected_usd_micros, low: p.low_usd_micros, high: p.high_usd_micros,
        bandBase: p.low_usd_micros, bandSpan: p.high_usd_micros - p.low_usd_micros,
      });
    }
    const b = baseProj.get(date);
    if (b) row.baselineExpected = b.expected_usd_micros;
    return row;
  });
}

export type BurnUpMarkers = {
  /** The scope's monthly budget (micros), or `null`: no reference line. */
  budget: number | null;
  /** Vertical marker at the median date the budget runs out, or `null`. */
  runsOutOn: string | null;
  /** The shaded p10..p90 range of that date, or `null` (also when it is a single day). */
  range: { from: string; to: string } | null;
};

/** The budget line and runway marker for the selected preset and period. */
export function burnUpMarkers(view: View, presetId: string, periodIndex: number): BurnUpMarkers {
  const period = periodOf(view, presetId, periodIndex);
  const on = period?.runway?.runs_out_on;
  const p50 = on?.p50 ?? null;
  const from = on?.p10 ?? null;
  const to = on?.p90 ?? null;
  return {
    budget: view.budget_usd_micros ?? null,
    runsOutOn: p50,
    range: from && to && from !== to ? { from, to } : null,
  };
}

export type DailyRow = {
  date: string;
  actual?: number;
  expected?: number;
  low?: number;
  high?: number;
  bandBase?: number;
  bandSpan?: number;
};

/** The daily chart: the last 28 days of actual cost, then the preset's expected daily cost with
 * its band for the dates after `data_through`. */
export function dailyRows(view: View, presetId: string): DailyRow[] {
  const actual: DailyRow[] = [...view.actual_daily]
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
    .slice(-DAILY_ACTUAL_DAYS)
    .map((a) => ({ date: a.date, actual: a.usd_micros }));
  const forecast: DailyRow[] = (seriesOf(view, presetId)?.daily ?? [])
    .filter((d) => d.date > view.data_through)
    .map((d) => ({
      date: d.date, expected: d.expected_usd_micros, low: d.low_usd_micros, high: d.high_usd_micros,
      bandBase: d.low_usd_micros, bandSpan: d.high_usd_micros - d.low_usd_micros,
    }));
  return [...actual, ...forecast];
}

// ── sentences ───────────────────────────────────────────────────────────────────────────
function presetLabel(view: View, presetId: string): string {
  return view.presets.find((p) => p.id === presetId)?.label ?? presetId;
}

/** The headline of the selected preset and period, as sentences (the panel joins them):
 *  1. the landing with its likely range -- "On the current trajectory, ..." for the baseline,
 *     `With "{label}", ...` for another preset;
 *  2. for another preset, how far it is from the baseline;
 *  3. with a budget, the chance of staying within it (not once it is already used up). */
export function headlineSentences(view: View, presetId: string, periodIndex: number): string[] {
  const period = periodOf(view, presetId, periodIndex);
  if (!period) return [];
  const { landing } = period;
  const month = monthName(period.start);
  const range = `${fmtCompactUsd(landing.expected_usd_micros)} (likely ${fmtCompactUsd(landing.p10_usd_micros)}–${fmtCompactUsd(landing.p90_usd_micros)})`;
  const out: string[] = [];
  if (presetId === BASELINE_ID) {
    out.push(`On the current trajectory, ${month} lands at ${range}.`);
  } else {
    out.push(`With "${presetLabel(view, presetId)}", ${month} lands at ${range}.`);
    // The API reports 0 when the baseline has no numbers; saying "same" then would be wrong.
    if (periodOf(view, BASELINE_ID, periodIndex)) {
      const delta = period.delta_vs_baseline_usd_micros;
      if (Math.round(Math.abs(delta) / 1_000_000) === 0) out.push("Same as the current trajectory.");
      else if (delta < 0) out.push(`${fmtCompactUsd(-delta)} less than the current trajectory.`);
      else out.push(`${fmtCompactUsd(delta)} more than the current trajectory.`);
    }
  }
  const p = period.within_budget_probability;
  if (view.budget_usd_micros != null && p != null && period.runway?.status !== "exhausted") {
    out.push(`${probabilityLabel(p)} likely to stay within the ${fmtMoneyMicros(view.budget_usd_micros)} budget.`);
  }
  return out;
}

function daysEarly(days: number | null | undefined): string {
  return days != null && days > 0 ? `, ${days} ${days === 1 ? "day" : "days"} early` : "";
}

/** Will the budget last? One sentence for the current period of a scope with a budget, else
 * `null`. The baseline says "At this pace"; another preset names itself. */
export function runwaySentence(view: View, presetId: string, periodIndex: number): string | null {
  const period = periodOf(view, presetId, periodIndex);
  const runway = period?.runway;
  const budgetMicros = view.budget_usd_micros;
  if (!period || !runway || budgetMicros == null || !isCurrentPeriod(period)) return null;
  const budget = fmtMoneyMicros(budgetMicros);
  const baseline = presetId === BASELINE_ID;
  const the = baseline ? `The ${budget} budget` : `With "${presetLabel(view, presetId)}" the ${budget} budget`;
  const pace = baseline ? `At this pace the ${budget} budget` : the;
  const day = runway.runs_out_on.p50;
  switch (runway.status) {
    case "lasts": {
      const left = Math.max(0, budgetMicros - period.landing.p50_usd_micros);
      return `${the} lasts the month; ${fmtCompactUsd(left)} likely left.`;
    }
    case "at_risk":
      return day
        ? `${the} could run out around ${fmtIsoDay(day)}${daysEarly(runway.days_short)}.`
        : `${the} could run out before the month ends (${probabilityLabel(runway.runs_out_probability)} likely).`;
    case "runs_out":
      return day
        ? `${pace} runs out around ${fmtIsoDay(day)}${daysEarly(runway.days_short)}.`
        : `${pace} runs out before the month ends.`;
    case "exhausted":
      return day ? `The ${budget} budget was used up on ${fmtIsoDay(day)}.` : `The ${budget} budget is already used up.`;
  }
}

/** The line under the charts. */
export function forecastFootnote(view: View): string {
  return `Forecast as of ${fmtIsoDay(view.origin)}. Actual spend through ${fmtIsoDay(view.data_through)}. Model moves assume the same token volume.`;
}

// ── budget planner ──────────────────────────────────────────────────────────────────────
export type PlanTokens = Schemas["TokensByModel"];

export type PlanTableRow = {
  key: string;
  scopeType: string;
  /** "Teams" | "Projects" */
  group: string;
  name: string;
  budget: number | null;
  expected: number | null;
  planFor: number | null;
  gap: number | null;
  tokens: PlanTokens[];
};

/** "Teams" for a team row, "Projects" for a repo row. */
export function planGroupLabel(scopeType: string): string {
  return scopeType === "team" ? "Teams" : "Projects";
}

/** The period the planner opens on: the first one that is not under way (the budget still to
 * be set), else the first. */
export function defaultPlanPeriod(periods: { current: boolean }[]): number {
  const i = periods.findIndex((p) => !p.current);
  return i >= 0 ? i : 0;
}

/** One table row per team / project of the plan, in response order, for a preset and period.
 * A row whose preset has no numbers (cost run incomplete) keeps its name and budget with the
 * figures `null`. A preset the row lacks falls back to the baseline's entry. */
export function planRows(plan: Plan, presetId: string, periodIndex: number): PlanTableRow[] {
  return plan.rows.map((r) => {
    const entry = r.by_preset.find((b) => b.preset_id === presetId)
      ?? r.by_preset.find((b) => b.preset_id === BASELINE_ID);
    const f = entry?.periods[periodIndex];
    return {
      key: `${r.scope_type}:${r.scope_id}`,
      scopeType: r.scope_type,
      group: planGroupLabel(r.scope_type),
      name: r.name,
      budget: r.budget_usd_micros ?? null,
      expected: f ? f.needed.expected_usd_micros : null,
      planFor: f ? f.plan_for_usd_micros : null,
      gap: f ? f.gap_usd_micros ?? null : null,
      tokens: f ? f.tokens_by_model : [],
    };
  });
}

export type SortDir = "desc" | "asc" | null;

/** The next state of the gap header: response order -> largest gap first -> smallest first ->
 * response order. */
export function nextSortDir(dir: SortDir): SortDir {
  return dir === null ? "desc" : dir === "desc" ? "asc" : null;
}

/** Sort by gap inside each group (the group order stays the response's); a row without a gap
 * is always last; equal gaps keep the response order. `null` leaves the order alone. */
export function sortPlanRows(rows: PlanTableRow[], dir: SortDir): PlanTableRow[] {
  if (dir === null) return rows;
  const groupOrder: string[] = [];
  for (const r of rows) if (!groupOrder.includes(r.group)) groupOrder.push(r.group);
  return rows
    .map((row, i) => ({ row, i }))
    .sort((a, b) => {
      const g = groupOrder.indexOf(a.row.group) - groupOrder.indexOf(b.row.group);
      if (g !== 0) return g;
      const x = a.row.gap, y = b.row.gap;
      if (x == null && y == null) return a.i - b.i;
      if (x == null) return 1;
      if (y == null) return -1;
      return (dir === "desc" ? y - x : x - y) || a.i - b.i;
    })
    .map((e) => e.row);
}

/** Rows split into their consecutive groups, for the section labels. */
export function groupPlanRows(rows: PlanTableRow[]): { group: string; rows: PlanTableRow[] }[] {
  const out: { group: string; rows: PlanTableRow[] }[] = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    if (last && last.group === r.group) last.rows.push(r);
    else out.push({ group: r.group, rows: [r] });
  }
  return out;
}

export type GapLabel = { tone: "over" | "room" | "on" | "none"; text: string };

/** The gap cell: positive -> "Over by $X" (the plan-for figure is above the budget), negative
 * -> "Room $X", zero at the displayed precision -> "On budget", no budget -> "—". */
export function planGapLabel(gap: number | null): GapLabel {
  if (gap == null) return { tone: "none", text: dash };
  if (Math.round(Math.abs(gap) / 1_000_000) === 0) return { tone: "on", text: "On budget" };
  return gap > 0
    ? { tone: "over", text: `Over by ${fmtCompactUsd(gap)}` }
    : { tone: "room", text: `Room ${fmtCompactUsd(-gap)}` };
}

/** The "plan for" column header. */
export function planForHeader(planningConfidence: number): string {
  return `Plan for (${Math.round(100 * planningConfidence)}% likely enough)`;
}

/** Under an expanded row of the current period: tokens only cover the forecast days. */
export function tokensFootnote(period: { forecast_from: string; start: string }): string | null {
  return isCurrentPeriod(period) ? `Tokens cover ${fmtIsoDay(period.forecast_from)} to month end.` : null;
}

/** A forecast token count: `fmtTokens` below a billion, then "1.6B" (the shared `fmtTokens` has
 * no billions tier and prints "1562.8M"; it is left alone so other screens do not change). */
export function fmtPlanTokens(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return dash;
  return Math.abs(n) >= 999_950_000 ? `${(n / 1_000_000_000).toFixed(1)}B` : fmtTokens(n);
}

/** The gap sort button's accessible name: what it does and the state it is in. */
export function gapSortLabel(dir: SortDir): string {
  const state = dir === "desc" ? "descending" : dir === "asc" ? "ascending" : "not sorted";
  return `Sort by gap, currently ${state}`;
}

// ── legends: only the series the chart actually draws ───────────────────────────────────
export type BurnUpLegendKey = "actual" | "expected" | "band" | "baseline" | "budget" | "runway";

/** Which legend entries the burn-up shows: a series with no rows (no actual days in a future
 * period or the month-end case) or no marker is left out. */
export function burnUpLegend(rows: BurnUpRow[], markers: BurnUpMarkers, showBaseline: boolean): BurnUpLegendKey[] {
  const keys: BurnUpLegendKey[] = [];
  if (rows.some((r) => r.actual !== undefined)) keys.push("actual");
  if (rows.some((r) => r.expected !== undefined)) keys.push("expected");
  if (rows.some((r) => r.bandSpan !== undefined && r.bandSpan > 0)) keys.push("band");
  if (showBaseline && rows.some((r) => r.baselineExpected !== undefined)) keys.push("baseline");
  if (markers.budget != null) keys.push("budget");
  if (markers.range) keys.push("runway");
  return keys;
}

export type DailyLegendKey = "actual" | "expected" | "band";

export function dailyLegend(rows: DailyRow[]): DailyLegendKey[] {
  const keys: DailyLegendKey[] = [];
  if (rows.some((r) => r.actual !== undefined)) keys.push("actual");
  if (rows.some((r) => r.expected !== undefined)) keys.push("expected");
  if (rows.some((r) => r.bandSpan !== undefined && r.bandSpan > 0)) keys.push("band");
  return keys;
}
