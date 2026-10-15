/**
 * The timeframe model (Overview refresh A3a), pure and unit-tested: what a selection is, how it
 * becomes the `{from, to}` window every `DataSource` call takes, which one the app opens on, and
 * how the picker groups the choices. `shell/timeframe.tsx` holds the React state; nothing here
 * touches React or the network.
 *
 * A selection is either a budget period (the server's own `from`/`to`/label, the server being the
 * only place calendar and sprint arithmetic happens) or a rolling "Last N days" range ending at
 * the moment it was selected. Dates are formatted from the ISO text, never through the browser's
 * local zone, so a UTC-midnight `from` never shows as the day before.
 */
import type { Schemas } from "../data/source";
import { addDays, splitIso } from "../data/isoDay";

export type BudgetPeriod = Schemas["BudgetPeriod"];

export type TimeframeSelection = {
  /** Stable id: the server's period key, or `rolling-<N>d`. */
  key: string;
  label: string;
  /** Half-open window `[from, to)`, UTC ISO. */
  from: string;
  to: string;
  kind: "budget-period" | "rolling";
  /** True only for the budget period that is under way. */
  isCurrent: boolean;
  /** Days of the window that have happened (today counts); for a rolling range, `N`. */
  daysElapsed: number;
  /** Days in the whole period; for a rolling range, `N`. */
  daysTotal: number;
};

/** The rolling ranges under "Other ranges". */
export const ROLLING_DAYS = [15, 30, 60] as const;
/** What the app opens on when there is no budget period under way (or the periods are unavailable). */
export const FALLBACK_ROLLING_DAYS = 30;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** The window passed straight to a `DataSource` method. A new object per call: the provider
 * memoises it per selection so its identity only changes when the selection does. */
export function timeframeOf(sel: TimeframeSelection): { from: string; to: string } {
  return { from: sel.from, to: sel.to };
}

/** A budget period from `GET /v1/budget-cycle/periods`, as the server sent it. */
export function selectionFromPeriod(p: BudgetPeriod): TimeframeSelection {
  return {
    key: p.key, label: p.label, from: p.from, to: p.to, kind: "budget-period",
    isCurrent: p.is_current, daysElapsed: p.days_elapsed, daysTotal: p.days_total,
  };
}

/** "Last N days": `to` is `now`, `from` is N days earlier (UTC), as the app has always done.
 * Because `from` is mid-day, the window touches N+1 UTC days (the first one partial); the label
 * stays "Last N days" on purpose, matching how the API buckets such a window. */
export function rollingSelection(days: number, now: Date = new Date()): TimeframeSelection {
  const from = new Date(now);
  from.setUTCDate(from.getUTCDate() - days);
  return {
    key: `rolling-${days}d`, label: `Last ${days} days`, from: from.toISOString(), to: now.toISOString(),
    kind: "rolling", isCurrent: false, daysElapsed: days, daysTotal: days,
  };
}

/** What the app opens on: the current budget period, else rolling "Last 30 days" (no period under
 * way: a sprint before its anchor, a one-time cycle that is over or not started). */
export function defaultSelection(periods: BudgetPeriod[], now: Date = new Date()): TimeframeSelection {
  const current = periods.find((p) => p.is_current);
  return current ? selectionFromPeriod(current) : rollingSelection(FALLBACK_ROLLING_DAYS, now);
}

/** The selection a picker key stands for, or `null` for an unknown key. A rolling key is resolved
 * against `now`, so pick it at click time, never in render. */
export function selectionForKey(key: string, periods: BudgetPeriod[], now: Date = new Date()): TimeframeSelection | null {
  const rolling = /^rolling-(\d+)d$/.exec(key);
  if (rolling) {
    const days = Number(rolling[1]);
    return (ROLLING_DAYS as readonly number[]).includes(days) ? rollingSelection(days, now) : null;
  }
  const period = periods.find((p) => p.key === key);
  return period ? selectionFromPeriod(period) : null;
}

// ── dates ────────────────────────────────────────────────────────────────────────────
function fmtDay(iso: string, withYear: boolean): string {
  const p = splitIso(iso);
  if (!p) return "—";
  return `${MONTHS[p.m - 1]} ${p.d}${withYear ? `, ${p.y}` : ""}`;
}

/** The last day of a budget period (inclusive), from its first day and length. The selection's
 * `to` is "now" for the current period, so the end is derived from `daysTotal`, which is exact. */
export function periodLastDay(sel: TimeframeSelection): string {
  return addDays(sel.from.slice(0, 10), Math.max(0, sel.daysTotal - 1));
}

/** "Oct 1 – Oct 31" for a budget period (its whole length, even while it is under way); for a
 * rolling range the days it covers up to `to`. The year is added when it is not the current year
 * or the range spans two years. */
export function rangeText(sel: TimeframeSelection, now: Date = new Date()): string {
  const start = sel.from.slice(0, 10);
  const end = sel.kind === "budget-period" ? periodLastDay(sel) : sel.to.slice(0, 10);
  const thisYear = now.getUTCFullYear();
  const sy = splitIso(start)?.y, ey = splitIso(end)?.y;
  const withYear = sy !== ey || sy !== thisYear;
  return `${fmtDay(start, withYear)} – ${fmtDay(end, withYear)}`;
}

/** "day 14 of 31": how far into a budget period under way we are; `null` otherwise. */
export function progressText(sel: TimeframeSelection): string | null {
  return sel.kind === "budget-period" && sel.isCurrent ? `day ${sel.daysElapsed} of ${sel.daysTotal}` : null;
}

/** The filter bar's date text (its tooltip): the date range, plus "day N of M" for the period under way. */
export function stripText(sel: TimeframeSelection, now: Date = new Date()): string {
  const range = rangeText(sel, now);
  const progress = progressText(sel);
  return progress ? `${range} · ${progress}` : range;
}

// ── the picker ───────────────────────────────────────────────────────────────────────
export type PickerOption = {
  key: string;
  label: string;
  /** Date range for a budget period; empty for a rolling range. */
  detail: string;
  /** Shown as " · current" after the dates. */
  current: boolean;
};
export type PickerGroup = {
  heading: string;
  options: PickerOption[];
  /** Shown instead of options when the group has none. */
  emptyNote: string | null;
};

export const BUDGET_PERIODS_HEADING = "Budget periods";
export const OTHER_RANGES_HEADING = "Other ranges";
export const PERIODS_UNAVAILABLE_NOTE = "Budget periods unavailable";
export const NO_PERIOD_NOTE = "No budget period under way";

/** "Budget periods" (newest first, the current one marked, each with its dates) then "Other
 * ranges" (rolling 15 / 30 / 60). With no periods the first group carries a note instead of
 * options: `unavailable` (the request failed) or none under way. The upcoming period is not
 * offered: it has no data. */
export function pickerGroups(periods: BudgetPeriod[], unavailable: boolean, now: Date = new Date()): PickerGroup[] {
  // A custom period's server label already IS its dates: no second copy of them in the row.
  const budget: PickerOption[] = [...periods].reverse().map((p) => ({
    key: p.key, label: p.label, current: p.is_current,
    detail: p.key.startsWith("custom-") ? "" : rangeText(selectionFromPeriod(p), now),
  }));
  return [
    {
      heading: BUDGET_PERIODS_HEADING,
      options: budget,
      emptyNote: budget.length > 0 ? null : unavailable ? PERIODS_UNAVAILABLE_NOTE : NO_PERIOD_NOTE,
    },
    {
      heading: OTHER_RANGES_HEADING,
      options: ROLLING_DAYS.map((d) => ({ key: `rolling-${d}d`, label: `Last ${d} days`, detail: "", current: false })),
      emptyNote: null,
    },
  ];
}

/** Every option of the groups, in order: what the keyboard walks. */
export function flatOptions(groups: PickerGroup[]): PickerOption[] {
  return groups.flatMap((g) => g.options);
}

/** The listbox's active index after a key: ArrowDown / ArrowUp step and clamp (no wrap), Home and
 * End jump to the ends; any other key leaves it. `last` is the final index (`-1` when empty). */
export function pickerNav(active: number, key: string, last: number): number {
  if (last < 0) return 0;
  switch (key) {
    case "ArrowDown": return Math.min(active + 1, last);
    case "ArrowUp": return Math.max(active - 1, 0);
    case "Home": return 0;
    case "End": return last;
    default: return active;
  }
}
