/**
 * Budget-cycle presentation (Overview refresh A3b/A3c), pure and unit-tested.
 *
 *  - Wording that stays true for any cycle: `periodNoun` (what a budget is "per"), `windowName` /
 *    `inWindow` (the selected window as words), `amountLabel` ("Budget per sprint (USD)").
 *  - The Configure form's draft: `draftFromCycle`, `validateCycleDraft`, `buildCycleRequest`,
 *    `cycleChange` and the two messages shown before a save.
 *
 * The validation here only MIRRORS the server's rules (`server/api/manager/budget_cycles.py`:
 * sprint length 1..52, sprint number 1..100000, dates 2000..2100, the last day not before the
 * first) so a mistake is caught before the request. The server stays the authority and its own
 * message is always shown when it rejects a save.
 */
import type { Schemas } from "../data/source";
import { splitIso } from "../data/isoDay";
import type { TimeframeSelection } from "./timeframe";

export type CycleKind = Schemas["BudgetCycleWriteRequest"]["kind"];
export type CycleRecurrence = Schemas["BudgetCycleWriteRequest"]["recurrence"];
export type CycleRequest = Schemas["BudgetCycleWriteRequest"];
type SavedCycle = Pick<Schemas["BudgetCycleResponse"], "kind" | "recurrence" | "sprint_weeks" | "anchor_date" | "anchor_number" | "custom_end">;

// ── wording that holds for any cycle ─────────────────────────────────────────────────────
/** The kinds the form offers, in order. */
export const CYCLE_KINDS: { kind: CycleKind; label: string }[] = [
  { kind: "month", label: "Month" },
  { kind: "quarter", label: "Quarter" },
  { kind: "half", label: "Half-year" },
  { kind: "year", label: "Year" },
  { kind: "sprint", label: "Sprint" },
  { kind: "custom", label: "Custom" },
];

const KIND_NOUN: Record<CycleKind, string> = {
  month: "month", quarter: "quarter", half: "half-year", year: "year", sprint: "sprint", custom: "period",
};
/** The label budgets are stored under (`period_label` on the API), to what a budget is "per". */
const LABEL_NOUN = new Map<string, string>([
  ["monthly", "month"], ["quarterly", "quarter"], ["half-yearly", "half-year"], ["yearly", "year"], ["sprint", "sprint"], ["custom", "period"],
]);

/** "month" / "quarter" / "half-year" / "year" / "sprint" / "period" (custom, and anything unknown
 * or unavailable): what a budget amount is per. */
export function periodNoun(periodLabel: string | null | undefined): string {
  return (periodLabel != null && LABEL_NOUN.get(periodLabel)) || "period";
}

/** "Budget per sprint (USD)". */
export function amountLabel(periodLabel: string | null | undefined): string {
  return `Budget per ${periodNoun(periodLabel)} (USD)`;
}

/** The selected window as a noun phrase: "Oct 2026", "Sprint 233", "the last 30 days". */
export function windowName(sel: TimeframeSelection): string {
  return sel.kind === "rolling" ? `the last ${sel.daysTotal} days` : sel.label;
}

/** "so far in Oct 2026" (the period under way), "in Sep 2026", "in the last 30 days". */
export function inWindow(sel: TimeframeSelection): string {
  return `${sel.kind === "budget-period" && sel.isCurrent ? "so far in" : "in"} ${windowName(sel)}`;
}

// ── forecast copy ────────────────────────────────────────────────────────────────────────
/** The forecast runs by CALENDAR MONTH whatever the org's cycle (the server prorates the cycle's
 * budget to the month; per-cycle forecasting is a follow-up, TODO(A4) on the server). So a cycle
 * that is known and is not the monthly one says so next to the forecast; a monthly cycle, an
 * unknown one (periods unavailable) and `null` say nothing. Only the wording is decided here: the
 * forecast maths is untouched. */
export function forecastCycleNote(periodLabel: string | null | undefined): string | null {
  return periodLabel != null && LABEL_NOUN.has(periodLabel) && periodLabel !== "monthly"
    ? "Forecast is by calendar month"
    : null;
}

// ── the Configure draft ──────────────────────────────────────────────────────────────────
/** The form's state: strings, as typed. */
export type CycleDraft = {
  kind: CycleKind;
  recurrence: CycleRecurrence;
  sprintWeeks: string;
  anchorNumber: string;
  anchorDate: string;
  customEnd: string;
};

export const DEFAULT_SPRINT_WEEKS = "2";
export const DEFAULT_ANCHOR_NUMBER = "1";
const MAX_SPRINT_WEEKS = 52;
const MAX_ANCHOR_NUMBER = 100_000;
/** The server's date bounds (inclusive), also the date inputs' `min` / `max`. */
export const MIN_CYCLE_DATE = "2000-01-01";
export const MAX_CYCLE_DATE = "2100-12-31";

const isCalendar = (k: CycleKind) => k === "month" || k === "quarter" || k === "half" || k === "year";
/** One-time is offered for sprint and custom only. */
export function offersOneTime(kind: CycleKind): boolean {
  return !isCalendar(kind);
}

/** The draft that stands for a saved cycle (what the form opens on). */
export function draftFromCycle(c: SavedCycle): CycleDraft {
  return {
    kind: c.kind, recurrence: c.recurrence,
    sprintWeeks: c.sprint_weeks != null ? String(c.sprint_weeks) : DEFAULT_SPRINT_WEEKS,
    anchorNumber: c.anchor_number != null ? String(c.anchor_number) : DEFAULT_ANCHOR_NUMBER,
    anchorDate: c.anchor_date ?? "",
    customEnd: c.custom_end ?? "",
  };
}

function wholeNumber(text: string): number | null {
  return /^\d+$/.test(text.trim()) ? Number(text.trim()) : null;
}
const validDay = (iso: string) => /^\d{4}-\d{2}-\d{2}$/.test(iso) && splitIso(iso) != null;
const inBounds = (iso: string) => iso >= MIN_CYCLE_DATE && iso <= MAX_CYCLE_DATE;

/** The draft field a problem belongs to, so the form can mark that input invalid. */
export type CycleDraftField = "sprintWeeks" | "anchorNumber" | "anchorDate" | "customEnd";
export type CycleDraftProblem = { field: CycleDraftField; message: string };

/** The first problem with the draft, with the field it is about, or `null` when it can be saved.
 * Fields the kind does not use are not checked. */
export function findCycleProblem(d: CycleDraft): CycleDraftProblem | null {
  if (isCalendar(d.kind)) return null;
  if (d.kind === "sprint") {
    const weeks = wholeNumber(d.sprintWeeks);
    if (weeks == null || weeks < 1 || weeks > MAX_SPRINT_WEEKS) {
      return { field: "sprintWeeks", message: `Sprint length must be a whole number of weeks, 1 to ${MAX_SPRINT_WEEKS}.` };
    }
    const n = wholeNumber(d.anchorNumber);
    if (n == null || n < 1 || n > MAX_ANCHOR_NUMBER) {
      return { field: "anchorNumber", message: `The current sprint number must be a whole number, 1 to ${MAX_ANCHOR_NUMBER}.` };
    }
    if (!validDay(d.anchorDate)) return { field: "anchorDate", message: "Pick the date the current sprint started." };
    if (!inBounds(d.anchorDate)) return { field: "anchorDate", message: "Dates must fall between 2000 and 2100." };
    return null;
  }
  if (!validDay(d.anchorDate)) return { field: "anchorDate", message: "Pick the first day of the period." };
  if (!validDay(d.customEnd)) return { field: "customEnd", message: "Pick the last day of the period." };
  if (!inBounds(d.anchorDate)) return { field: "anchorDate", message: "Dates must fall between 2000 and 2100." };
  if (!inBounds(d.customEnd)) return { field: "customEnd", message: "Dates must fall between 2000 and 2100." };
  if (d.customEnd < d.anchorDate) return { field: "customEnd", message: "The last day must not be before the first day." };
  return null;
}

/** The first problem with the draft in plain words, or `null` when it can be saved. */
export function validateCycleDraft(d: CycleDraft): string | null {
  return findCycleProblem(d)?.message ?? null;
}

/** The request body for a draft that `validateCycleDraft` accepted: only the fields its kind
 * uses (the API replaces the whole cycle and rejects unknown fields), calendar kinds always
 * recurring. */
export function buildCycleRequest(d: CycleDraft): CycleRequest {
  if (isCalendar(d.kind)) return { kind: d.kind, recurrence: "recurring" };
  if (d.kind === "sprint") {
    return {
      kind: "sprint", recurrence: d.recurrence, sprint_weeks: Number(d.sprintWeeks),
      anchor_number: Number(d.anchorNumber), anchor_date: d.anchorDate,
    };
  }
  return { kind: "custom", recurrence: d.recurrence, anchor_date: d.anchorDate, custom_end: d.customEnd };
}

/** What saving the draft would do to the saved cycle: nothing (`none`), switch the kind
 * (`kind`: budget amounts are not converted) or edit the same kind (`edit`: amounts now apply per
 * period of the changed cycle). Only meaningful for a draft that passes `validateCycleDraft`. */
export function cycleChange(saved: SavedCycle, draft: CycleDraft): "none" | "kind" | "edit" {
  if (draft.kind !== saved.kind) return "kind";
  const before = JSON.stringify(buildCycleRequest(draftFromCycle(saved)));
  return JSON.stringify(buildCycleRequest(draft)) === before ? "none" : "edit";
}

/** Shown, with a confirming click, before a save that changes the kind. */
export function kindChangeWarning(savedPeriodLabel: string, nextKind: CycleKind): string {
  return `Budget amounts are not converted. Amounts you set per ${periodNoun(savedPeriodLabel)} stay stored but are not used, `
    + `and scopes show no budget until you enter amounts per ${KIND_NOUN[nextKind]}.`;
}

/** Shown beside a save that edits the same kind (no confirming click). */
export const SAME_KIND_NOTICE = "Existing budget amounts stay as they are and now apply per period of the changed cycle.";
