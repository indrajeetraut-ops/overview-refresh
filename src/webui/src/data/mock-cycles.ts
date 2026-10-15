/**
 * MOCK-ONLY budget-period generator for `MockSource` (Overview refresh, A3a). It lets the
 * browser show every cycle kind (month, quarter, half-year, year, sprint, custom) without a
 * backend: pick a kind on Configure and the picker's periods follow.
 *
 * The server (`server/api/manager/budget_cycles.py`) is the ONLY real place for calendar and
 * sprint arithmetic. This is a deliberately small re-statement of it for fixtures; never import
 * it from a screen or from `present/`, and never treat it as the rules. It works on ISO day
 * strings in UTC (`isoDay.ts`), relative to the `now` it is given.
 */
import type { Schemas } from "./source";
import { addDays, splitIso } from "./isoDay";

export type MockCycle = Schemas["BudgetCycleWriteRequest"];
export type MockPeriods = Schemas["BudgetPeriodsResponse"];
type Period = Schemas["BudgetPeriod"];

/** Normalised like the server's `Cycle`: fields a kind does not use are null. */
export const DEFAULT_MOCK_CYCLE: MockCycle = {
  kind: "month", recurrence: "recurring", sprint_weeks: null, anchor_date: null, anchor_number: null, custom_end: null,
};

/** The label budgets are stored under, per kind (the server's `CYCLE_LABELS`). */
export const MOCK_CYCLE_LABELS: Record<MockCycle["kind"], string> = {
  month: "monthly", quarter: "quarterly", half: "half-yearly", year: "yearly", sprint: "sprint", custom: "custom",
};

const KINDS = Object.keys(MOCK_CYCLE_LABELS);
const RECURRENCES = ["recurring", "one-time"];
const CALENDAR_MONTHS: Record<string, number> = { month: 1, quarter: 3, half: 6, year: 12 };
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MIN_DATE = "2000-01-01";
const MAX_DATE = "2100-12-31";

/** Whole days from ISO day `a` to ISO day `b` (b - a). */
function daysBetween(a: string, b: string): number {
  const pa = splitIso(a)!, pb = splitIso(b)!;
  return Math.round((Date.UTC(pb.y, pb.m - 1, pb.d) - Date.UTC(pa.y, pa.m - 1, pa.d)) / 86_400_000);
}

const pad = (n: number) => String(n).padStart(2, "0");
const isoOf = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`;

/** The error the real server would answer with (a 400 `reason`), or `null` when the cycle is
 * acceptable. Same rules as `budget_cycles.validate`, so Configure's error path can be seen. */
export function mockCycleError(c: MockCycle): string | null {
  // the server's wording, Python tuple repr included
  if (!KINDS.includes(c.kind)) return `unknown cycle kind '${c.kind}'; expected one of (${KINDS.map((k) => `'${k}'`).join(", ")})`;
  if (!RECURRENCES.includes(c.recurrence)) {
    return `unknown recurrence '${c.recurrence}'; expected one of (${RECURRENCES.map((r) => `'${r}'`).join(", ")})`;
  }
  if (c.kind in CALENDAR_MONTHS) {
    return c.recurrence === "recurring" ? null
      : `a ${c.kind} cycle is always recurring; one-time applies to sprint and custom only`;
  }
  if (!c.anchor_date) return `a ${c.kind} cycle needs anchor_date` + (c.kind === "custom" ? " (the first day)" : " (the sprint start)");
  if (!splitIso(c.anchor_date)) return "anchor_date must be a calendar date (YYYY-MM-DD)";
  if (c.anchor_date < MIN_DATE || c.anchor_date > MAX_DATE) return `anchor_date must be between ${MIN_DATE} and ${MAX_DATE}`;
  if (c.kind === "sprint") {
    const weeks = c.sprint_weeks ?? 2;
    if (!Number.isInteger(weeks)) return "sprint_weeks must be an integer";
    if (weeks < 1 || weeks > 52) return "sprint_weeks must be between 1 and 52";
    const n = c.anchor_number ?? 1;
    if (!Number.isInteger(n)) return "anchor_number must be an integer";
    if (n < 1 || n > 100_000) return "anchor_number must be between 1 and 100000";
    return null;
  }
  if (!c.custom_end) return "a custom cycle needs custom_end (the last day, inclusive)";
  if (!splitIso(c.custom_end)) return "custom_end must be a calendar date (YYYY-MM-DD)";
  if (c.custom_end > MAX_DATE || c.custom_end < MIN_DATE) return `custom_end must be between ${MIN_DATE} and ${MAX_DATE}`;
  if (c.custom_end < c.anchor_date) return "custom_end must not be before anchor_date";
  return null;
}

/** The cycle as the server stores it (`budget_cycles.validate`): fields the kind does not use are
 * null, a sprint's length defaults to 2 weeks and its number to 1. Call after `mockCycleError`
 * accepted the body. */
export function normaliseMockCycle(c: MockCycle): MockCycle {
  const sprint = c.kind === "sprint";
  return {
    kind: c.kind, recurrence: c.recurrence,
    sprint_weeks: sprint ? (c.sprint_weeks ?? 2) : null,
    anchor_number: sprint ? (c.anchor_number ?? 1) : null,
    anchor_date: sprint || c.kind === "custom" ? (c.anchor_date ?? null) : null,
    custom_end: c.kind === "custom" ? (c.custom_end ?? null) : null,
  };
}

/** Fixed length in days of a sprint or custom period. */
function fixedLength(c: MockCycle): number {
  return c.kind === "sprint" ? (c.sprint_weeks ?? 2) * 7 : daysBetween(c.anchor_date!, c.custom_end!) + 1;
}

function indexOf(c: MockCycle, today: string): number {
  if (c.kind in CALENDAR_MONTHS) {
    const p = splitIso(today)!;
    const months = CALENDAR_MONTHS[c.kind];
    return p.y * (12 / months) + Math.floor((p.m - 1) / months);
  }
  if (c.recurrence === "one-time") return 0;
  return Math.floor(daysBetween(c.anchor_date!, today) / fixedLength(c));
}

function period(key: string, label: string, start: string, endExcl: string, today: string, now: Date): Period {
  const total = daysBetween(start, endExcl);
  const elapsed = today < start ? 0 : today >= endExcl ? total : daysBetween(start, today) + 1;
  const current = start <= today && today < endExcl;
  const end = `${endExcl}T00:00:00Z`;
  return {
    key, label, from: `${start}T00:00:00Z`,
    // the server's rule: a current period is cut at "now", so [from, to) is its period-to-date
    to: current ? now.toISOString() : end,
    end_exclusive: end, is_current: current, days_total: total, days_elapsed: elapsed,
  };
}

function make(c: MockCycle, index: number, today: string, now: Date): Period {
  if (c.kind in CALENDAR_MONTHS) {
    const months = CALENDAR_MONTHS[c.kind];
    const perYear = 12 / months;
    const year = Math.floor(index / perYear);
    const slot = index - year * perYear;
    const start = isoOf(year, slot * months + 1, 1);
    const end = slot === perYear - 1 ? isoOf(year + 1, 1, 1) : isoOf(year, (slot + 1) * months + 1, 1);
    const [label, key] =
      c.kind === "month" ? [`${MONTH_NAMES[slot]} ${year}`, `month-${year}-${pad(slot + 1)}`]
      : c.kind === "quarter" ? [`Q${slot + 1} ${year}`, `quarter-${year}-${slot + 1}`]
      : c.kind === "half" ? [`H${slot + 1} ${year}`, `half-${year}-${slot + 1}`]
      : [`${year}`, `year-${year}`];
    return period(key, label, start, end, today, now);
  }
  const len = fixedLength(c);
  const start = addDays(c.anchor_date!, index * len);
  const end = addDays(start, len);
  if (c.kind === "sprint") {
    const n = (c.anchor_number ?? 1) + index;
    return period(`sprint-${n}`, `Sprint ${n}`, start, end, today, now);
  }
  const fmt = (iso: string) => { const p = splitIso(iso)!; return `${pad(p.d)} ${MONTH_NAMES[p.m - 1]} ${p.y}`; };
  return period(`custom-${start}`, `${fmt(start)} – ${fmt(addDays(end, -1))}`, start, end, today, now);
}

/** The last `history` periods that have started (oldest first, the current one last) plus the
 * next one, as of `now` (UTC). Assumes a cycle that `mockCycleError` accepts. */
export function mockPeriods(cycle: MockCycle, now: Date = new Date(), history = 6): MockPeriods {
  const today = now.toISOString().slice(0, 10);
  const label = MOCK_CYCLE_LABELS[cycle.kind];
  const out = (periods: Period[], next: Period | null): MockPeriods => ({ period_label: label, periods, next });

  if (cycle.recurrence === "one-time" && !(cycle.kind in CALENDAR_MONTHS)) {
    const only = make(cycle, 0, today, now);
    return only.from.slice(0, 10) <= today ? out([only], null) : out([], only);
  }
  const cur = indexOf(cycle, today);
  let lo: number;
  if (cycle.kind in CALENDAR_MONTHS) lo = cur - history + 1;
  else if (cur < 0) return out([], make(cycle, 0, today, now));
  else if (cycle.kind === "sprint") lo = Math.max(cur - history + 1, 1 - (cycle.anchor_number ?? 1));
  else lo = Math.max(cur - history + 1, 0);
  const periods: Period[] = [];
  for (let i = lo; i <= cur; i++) periods.push(make(cycle, i, today, now));
  return out(periods, make(cycle, cur + 1, today, now));
}
