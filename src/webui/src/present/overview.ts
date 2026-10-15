/**
 * Overview display derivations, pure and tested (`overview.test.ts`). The API returns the daily
 * series; anything shaped for a particular chart is built here.
 */
import { fmtCompactUsd, fmtIsoDay, isCurrentPeriod, monthName, periodOf, runwaySentence } from "./forecast";
import type { Schemas } from "../data/source";
import { fmtCount, fmtMoneyMicros, fmtPct, fmtTokens } from "./format";
import { RECOVERABLE_CAUSE_PHRASE, agentName, assignAgentHues, categoryHue, sessionTypeName } from "./palette";
import type { Hue } from "./palette";

// ── Overview v2 (Track C) ────────────────────────────────────────────────────────────────
// Everything the KPI cards, the forecast card footer, the team bars and the team table show is
// decided here, from the generated `OrgOverview` / `ForecastView` types; the components only lay
// it out. Dollars stay integer micro-USD until the last formatter.

type TeamSummary = Schemas["TeamSummary"];
type ModelUsage = Schemas["ModelUsage"];
type TaskMix = Schemas["AgentTaskMix"];
type ForecastView = Schemas["ForecastView"];

const DASH = "—";

// ── budget ──────────────────────────────────────────────────────────────────────────────
/** A budget status colour. `muted` = no budget. */
export type BudgetTone = "green" | "amber" | "red" | "muted";

/** Today's thresholds (the old Spent tile): red above 95% of budget, amber above 80%. */
export function budgetTone(pct: number | null | undefined): BudgetTone {
  if (pct == null || !isFinite(pct)) return "muted";
  if (pct > 0.95) return "red";
  if (pct > 0.8) return "amber";
  return "green";
}

/**
 * The window's budget in micro-USD, recovered the way the team bars always have: spend divided by
 * `budget_pct` (the API sends the share, not the amount). `null` when it cannot be recovered: no
 * budget (`budget_pct` null), no spend figure, or `budget_pct` 0 (zero spend says nothing about
 * the budget).
 */
export function budgetMicros(spendMicros: number | null | undefined, budgetPct: number | null | undefined): number | null {
  if (spendMicros == null || budgetPct == null || !isFinite(spendMicros) || !isFinite(budgetPct) || budgetPct <= 0) return null;
  return Math.round(spendMicros / budgetPct);
}

export type GaugeView = {
  /** Arc fill, 0..1, capped at 1 however far over budget. */
  fraction: number;
  /** The uncapped share of budget (1.12 = 12% over). */
  pct: number;
  tone: BudgetTone;
  over: boolean;
  budgetMicros: number | null;
  /** "86% of $15,000" (just "86% of budget" when the amount cannot be recovered). */
  caption: string;
  /** "$2,160 left" / "$600 over"; `null` without a recoverable budget. */
  pill: { text: string; variant: "green" | "amber" | "red" } | null;
  ariaLabel: string;
};

/** The spend & budget gauge. `null` when there is no budget (`budget_pct` null) or no spend figure: the card shows spend only, no gauge. */
export function spendGauge(spendMicros: number | null | undefined, budgetPct: number | null | undefined): GaugeView | null {
  if (spendMicros == null || !isFinite(spendMicros)) return null;
  if (budgetPct == null || !isFinite(budgetPct) || budgetPct < 0) return null;
  const budget = budgetMicros(spendMicros, budgetPct);
  const over = budgetPct > 1;
  const tone = over ? "red" : budgetTone(budgetPct);
  const caption = budget != null ? `${fmtPct(budgetPct)} of ${fmtMoneyMicros(budget)}` : `${fmtPct(budgetPct)} of budget`;
  let pill: GaugeView["pill"] = null;
  if (budget != null) {
    pill = over
      ? { text: `${fmtMoneyMicros(spendMicros - budget)} over`, variant: "red" }
      : { text: `${fmtMoneyMicros(budget - spendMicros)} left`, variant: tone === "red" ? "red" : tone === "amber" ? "amber" : "green" };
  }
  return {
    fraction: Math.min(1, budgetPct), pct: budgetPct, tone, over, budgetMicros: budget, caption, pill,
    ariaLabel: `Spend ${fmtMoneyMicros(spendMicros)}, ${caption}${pill ? `, ${pill.text}` : ""}`,
  };
}

// ── KPI cards ───────────────────────────────────────────────────────────────────────────
/** "2 of 5 teams over budget" (red) / "All teams on track" (green) / "No teams" (muted, nothing to
 * be on track), the Teams card's banner. */
export function teamsBanner(teamsOverBudget: number, teamCount: number): { text: string; variant: "red" | "green" | "muted" } {
  if (teamCount <= 0) return { text: "No teams", variant: "muted" };
  if (teamsOverBudget <= 0) return { text: "All teams on track", variant: "green" };
  return { text: `${teamsOverBudget} of ${teamCount} ${teamCount === 1 ? "team" : "teams"} over budget`, variant: "red" };
}

/** "across 7 repos". */
export function reposText(n: number): string {
  return `across ${n} ${n === 1 ? "repo" : "repos"}`;
}

/** "≈ 6.2 per day" for merged PRs over the days of the window that have happened; `null` for a
 * window with no elapsed days, no count, or a rate under 0.05 a day (never "≈ 0 per day", never a
 * divide by zero). One decimal below ten; thousands separators above. */
export function mergedPerDay(merged: number | null | undefined, daysElapsed: number): string | null {
  if (merged == null || !isFinite(merged) || !isFinite(daysElapsed) || daysElapsed <= 0) return null;
  const rate = merged / daysElapsed;
  if (rate < 0.05) return null;
  return `≈ ${rate < 10 ? String(Math.round(rate * 10) / 10) : Math.round(rate).toLocaleString("en-US")} per day`;
}

/** "256K (4%)" / "256K": reasoning tokens and their share. The API's `pct` is already a percent. */
export function reasoningText(r: { reasoning_tokens: number; pct?: number | null }): string {
  return `${fmtTokens(r.reasoning_tokens)}${r.pct != null ? ` (${Math.round(r.pct * 10) / 10}%)` : ""}`;
}

/** `claude-opus-4-8` -> `opus-4-8` (a display trim; the API sends the model id as-is). */
export function shortModel(model: string): string {
  return model.replace(/^claude-/, "");
}

export type ModelSlice = { key: string; name: string; pct: number; hue: string; title?: string };

/** The "+N other" slice: a pale neutral, distinct from every tier colour (the low tier is the muted grey). */
const OTHER_HUE = "color-mix(in srgb, var(--muted-foreground) 40%, var(--card))";

/** How many models the legend lists by name before the rest roll up into "+N other". */
export const MODEL_LEGEND_MAX = 4;

/**
 * The model-mix donut and its legend (one list: the slices ARE the legend rows). Biggest share
 * first; beyond `max` the rest join one "+N other" slice whose `title` lists every one of them.
 * Hues are the model tier's (`categoryHue("modelTier")`); the roll-up is neutral. Empty or
 * missing mix -> `[]`.
 */
export function modelSlices(mix: ModelUsage[] | null | undefined, max = MODEL_LEGEND_MAX): ModelSlice[] {
  if (!mix || mix.length === 0) return [];
  const sorted = [...mix].sort((a, b) => b.pct - a.pct);
  const one = (m: ModelUsage, i: number): ModelSlice => ({
    key: `${m.model}-${i}`, name: shortModel(m.model), pct: m.pct, hue: categoryHue("modelTier", m.tier, i),
  });
  if (sorted.length <= max) return sorted.map(one);
  const rest = sorted.slice(max);
  return [
    ...sorted.slice(0, max).map(one),
    {
      key: "other", name: `+${rest.length} other`, pct: rest.reduce((a, m) => a + m.pct, 0),
      hue: OTHER_HUE,
      title: rest.map((m) => `${shortModel(m.model)} ${fmtPct(m.pct)}`).join(", "),
    },
  ];
}

/** The model-mix donut's one-line description for assistive technology. */
export function modelMixLabel(total: string, slices: ModelSlice[]): string {
  return `${total} tokens. Model mix: ${slices.map((s) => `${s.name} ${fmtPct(s.pct)}${s.title ? ` (${s.title})` : ""}`).join(", ")}`;
}

// ── forecast card ───────────────────────────────────────────────────────────────────────
/** "Under budget" / "At risk" / "Over budget" for a runway status; `null` for anything else. */
export function runwayPill(status: string | null | undefined): { text: string; variant: "green" | "amber" | "red" } | null {
  switch (status) {
    case "lasts": return { text: "Under budget", variant: "green" };
    case "at_risk": return { text: "At risk", variant: "amber" };
    case "runs_out":
    case "exhausted": return { text: "Over budget", variant: "red" };
    default: return null;
  }
}

function viewPeriods(view: ForecastView) {
  return (view.series.find((s) => s.periods.length > 0) ?? view.series[0])?.periods ?? [];
}

/** Which forecast period the Overview card draws: the one under way, else the first. */
export function forecastPeriodIndex(view: ForecastView): number {
  const current = viewPeriods(view).findIndex(isCurrentPeriod);
  return current >= 0 ? current : 0;
}

/** The card's title: "Budget forecast · October" (the calendar month it draws), or just
 * "Budget forecast" without a run. */
export function forecastCardTitle(view: ForecastView | null | undefined): string {
  const period = view ? viewPeriods(view)[forecastPeriodIndex(view)] : undefined;
  return period ? `Budget forecast · ${monthName(period.start)}` : "Budget forecast";
}

/** The card's left footer when the forecast says whether the budget lasts: the pill and the
 * sentence. `null` (no budget, no runway, or no period under way: the month-end case) means the
 * card shows the "Budget status" fallback instead. */
export function forecastRunway(view: ForecastView, periodIndex: number, presetId = "baseline"):
  { pill: { text: string; variant: "green" | "amber" | "red" }; sentence: string } | null {
  const sentence = runwaySentence(view, presetId, periodIndex);
  const pill = runwayPill(periodOf(view, presetId, periodIndex)?.runway?.status);
  return sentence && pill ? { pill, sentence } : null;
}

/** The "Budget status" fallback footer: the window's own `budget_pct`. */
export function budgetStatus(budgetPct: number | null | undefined):
  { label: string; text: string; variant: "green" | "amber" | "red" | "muted" } {
  if (budgetPct == null || !isFinite(budgetPct)) return { label: "Budget status", text: "No budget", variant: "muted" };
  const tone = budgetPct > 1 ? "red" : budgetTone(budgetPct);
  return { label: "Budget status", text: `${fmtPct(budgetPct)} of budget used`, variant: tone };
}

/** The right footer: "Recoverable · could save 17% · $2,180 · mostly resuming after pauses". The
 * share is the could-save figure over the headline spend (both the one cost source). It, and the
 * cause, are left out when unknown, when nothing is recoverable, or (the cause) when it has no
 * wording; a share that rounds to nothing says "<1%". The figure always shows. */
export function recoverableLine(
  couldSaveMicros: number | null | undefined, spendMicros: number | null | undefined, cause: string | null | undefined,
): string {
  const parts = ["Recoverable"];
  const some = couldSaveMicros != null && couldSaveMicros > 0;
  if (some && spendMicros != null && spendMicros > 0) {
    parts.push(`could save ${Math.round((100 * couldSaveMicros) / spendMicros) < 1 ? "<1%" : fmtPct(couldSaveMicros, spendMicros)}`);
  }
  parts.push(fmtMoneyMicros(couldSaveMicros));
  const phrase = cause ? RECOVERABLE_CAUSE_PHRASE[cause] : undefined;
  if (some && phrase) parts.push(`mostly ${phrase}`);
  return parts.join(" · ");
}

// ── spend by team ───────────────────────────────────────────────────────────────────────
export type TeamBar = {
  id: string; name: string;
  /** Whole dollars (chart scale). */
  spent: number; budget: number | null; over: boolean;
  /** Dollars over (+) or left (-) of budget, whole dollars; `null` without a budget. */
  delta: number | null;
  /** The same in micro-USD, unrounded; `null` without a budget. */
  deltaMicros: number | null;
};

/** The team bars: spend and the recovered budget in whole dollars, as the chart has always drawn them. */
export function teamSpendBars(teams: TeamSummary[]): TeamBar[] {
  return teams.map((t) => {
    const spendMicros = t.spend.usd_micros ?? 0;
    const bMicros = budgetMicros(spendMicros, t.budget_pct);
    const spent = Math.round(spendMicros / 1_000_000);
    const budget = bMicros != null ? Math.round(bMicros / 1_000_000) : null;
    return {
      id: t.team_id, name: t.name, spent, budget, over: t.over_budget,
      delta: budget != null ? spent - budget : null,
      deltaMicros: bMicros != null ? spendMicros - bMicros : null,
    };
  });
}

/** The tooltip of one team bar: spent, budget, and what is over or left. "Over" follows the
 * server's own flag, so a team flagged over by a few cents never reads as "$0 remaining". */
export function teamTip(b: TeamBar): { title: string; rows: { label: string; value: string; tone?: "red" | "green" }[] } {
  const rows: { label: string; value: string; tone?: "red" | "green" }[] = [{ label: "Spent", value: fmtMoneyMicros(b.spent * 1_000_000) }];
  if (b.budget != null && b.deltaMicros != null) {
    rows.push({ label: "Budget", value: fmtMoneyMicros(b.budget * 1_000_000) });
    if (b.over) {
      rows.push({ label: "Over", value: b.deltaMicros >= 500_000 ? `+${fmtMoneyMicros(b.deltaMicros)}` : "<$1", tone: "red" });
    } else {
      rows.push({ label: "Remaining", value: fmtMoneyMicros(Math.max(0, -b.deltaMicros)), tone: "green" });
    }
  }
  return { title: b.name, rows };
}

// `Intl.Segmenter` is newer than this project's TS lib setting, hence the narrow local type.
type Segmenter = { segment(s: string): Iterable<{ segment: string }> };
const SegmenterCtor = (typeof Intl !== "undefined" ? (Intl as unknown as { Segmenter?: new (l?: string, o?: { granularity: "grapheme" }) => Segmenter }).Segmenter : undefined);
const graphemes = SegmenterCtor ? new SegmenterCtor(undefined, { granularity: "grapheme" }) : null;

/** A name as user-perceived characters: a flag or a ZWJ emoji family is one, not several code points. */
function glyphs(name: string): string[] {
  return graphemes ? Array.from(graphemes.segment(name), (g) => g.segment) : Array.from(name);
}

/** A team name cut to `max` characters with an ellipsis (no space left dangling before it). */
export function truncateName(name: string, max: number): string {
  const chars = glyphs(name);
  return chars.length > max ? `${chars.slice(0, Math.max(1, max - 1)).join("").trimEnd()}…` : name;
}

/** Pixels of the team chart's width the bars do not get: the 48px y axis, the margins (8 right, -6
 * left) = 50, rounded up to 56 as slack so a label is never planned into room it does not have. */
export const TEAM_CHART_INSET = 56;

/**
 * How the team bars label themselves for a chart `widthPx` wide: the room each bar has decides
 * how many characters of the name fit flat (rotated, when fewer than 5 would, with a fixed
 * allowance) and whether the value labels fit at all (the tooltip and the aria label carry the
 * figures either way). The y axis and margins take `TEAM_CHART_INSET`.
 */
export function teamLabelPlan(widthPx: number, count: number): { chars: number; rotate: boolean; valueLabels: boolean } {
  if (!(widthPx > 0) || count <= 0) return { chars: 10, rotate: false, valueLabels: true };
  const perBar = (widthPx - TEAM_CHART_INSET) / count;
  const chars = Math.floor(perBar / 6.4);
  return { chars: chars < 5 ? 12 : Math.min(chars, 14), rotate: chars < 5, valueLabels: perBar >= 32 };
}

/** The bar chart's description for assistive technology. */
export function teamBarsLabel(bars: TeamBar[]): string {
  const one = (b: TeamBar) => `${b.name} ${fmtMoneyMicros(b.spent * 1_000_000)}${
    b.budget != null ? ` of ${fmtMoneyMicros(b.budget * 1_000_000)} budget${b.over ? ", over budget" : ""}` : `${b.over ? ", over budget" : ", no budget"}`}`;
  return `Spend by team: ${bars.map(one).join("; ")}`;
}

/** "2 teams over budget" for the card's pill; `null` when none. */
export function teamsOverPill(n: number): string | null {
  return n > 0 ? `${n} ${n === 1 ? "team" : "teams"} over budget` : null;
}

// ── team breakdown table ────────────────────────────────────────────────────────────────
export type TeamTableRow = {
  id: string; name: string; initials: string;
  members: string; spend: string; mergedPrs: string;
  /** "86%" or "—" without a budget. */
  budgetUsed: string;
  /** Whether there is a budget share to draw as a bar (not without `budget_pct`, whatever the status). */
  hasBar: boolean;
  /** Bar fill 0..1 (capped; 0 without a budget). */
  barFraction: number;
  tone: BudgetTone;
  status: { text: string; variant: "green" | "red" | "muted" };
};

/** "Platform" -> "PL", "Data Science" -> "DS", "" -> "?". */
export function initialsOf(name: string): string {
  const words = name.trim().split(/[\s_-]+/).filter(Boolean);
  if (words.length === 0) return "?";
  const chars = (w: string) => Array.from(w);
  const letters = words.length === 1 ? chars(words[0]).slice(0, 2).join("") : chars(words[0])[0] + chars(words[1])[0];
  return letters.toUpperCase();
}

/** One table row per team, in the order the API sent them. */
export function teamTableRows(teams: TeamSummary[]): TeamTableRow[] {
  return teams.map((t) => {
    const pct = t.budget_pct ?? null;
    const spend = t.spend.usd_micros;
    const budget = budgetMicros(spend, pct);
    // Over budget is the server's flag or a share above 100%, as the gauge decides it.
    const over = t.over_budget || (pct != null && pct > 1);
    let status: TeamTableRow["status"];
    if (over) {
      const overBy = budget != null && spend != null ? spend - budget : 0;
      status = { text: overBy > 0 ? `${fmtMoneyMicros(overBy)} over budget` : "Over budget", variant: "red" };
    } else if (pct == null) {
      status = { text: "No budget", variant: "muted" };
    } else {
      status = { text: "On track", variant: "green" };
    }
    return {
      id: t.team_id, name: t.name, initials: initialsOf(t.name),
      members: fmtCount(t.members), spend: fmtMoneyMicros(spend), mergedPrs: fmtCount(t.merged_prs),
      budgetUsed: pct != null ? fmtPct(pct) : DASH,
      hasBar: pct != null,
      barFraction: pct != null ? Math.min(1, Math.max(0, pct)) : 0,
      tone: over ? "red" : budgetTone(pct),
      status,
    };
  });
}

// ── spend over time ─────────────────────────────────────────────────────────────────────
/** The daily chart's aside: "14 days · daily spend". */
export function dailySpendAside(days: number): string {
  return `${days} ${days === 1 ? "day" : "days"} · daily spend`;
}

export type DailySpendRow = { day: string; usd: number };

/** `daily_spend` as chart rows: micros become dollars here, at the chart's edge, and nowhere else. */
export function dailySpendRows(daily: { day: string; spend_usd_micros: number }[]): DailySpendRow[] {
  return daily.map((p) => ({ day: p.day, usd: p.spend_usd_micros / 1_000_000 }));
}

/** One day's spend in dollars, with cents while it is small enough for cents to matter:
 * "$0", "$0.05", "$12.34", "$410", "$1.5k". (Whole-dollar `fmtUSD` would print "$0" for 5 cents.) */
export function fmtDailyUsd(usd: number): string {
  if (!isFinite(usd)) return DASH;
  if (usd === 0) return "$0";
  if (Math.abs(usd) >= 999.5) return fmtCompactUsd(usd * 1_000_000);
  if (Math.abs(usd) >= 100) return `$${Math.round(usd)}`;
  return `$${usd.toFixed(2)}`;
}

/** A y-axis tick of the daily chart: whole dollars when it is one, else cents ("$0.25"); compact above $1k. */
export function fmtDailyAxis(usd: number): string {
  if (!isFinite(usd)) return DASH;
  if (Math.abs(usd) >= 999.5) return fmtCompactUsd(usd * 1_000_000);
  return Number.isInteger(usd) ? `$${usd}` : `$${usd.toFixed(2).replace(/0$/, "")}`;
}

/** The daily chart's y domain: from zero up to a rounded-up maximum ("auto"), with a $1 floor so a
 * series of all zero days still has distinct ticks ($0 ... $1) instead of a stack of "$0". */
export function dailyAxisDomain(rows: DailySpendRow[]): [number, "auto" | number] {
  return [0, rows.some((r) => r.usd > 0) ? "auto" : 1];
}

/** The daily chart for assistive technology: the range, the total, and the peak day (the first
 * of equal ones). */
export function dailySpendLabel(daily: { day: string; spend_usd_micros: number }[]): string {
  if (daily.length === 0) return "No daily spend";
  const usd = (micros: number) => fmtDailyUsd(micros / 1_000_000);
  const total = daily.reduce((a, p) => a + p.spend_usd_micros, 0);
  if (daily.length === 1) return `Spend on ${fmtIsoDay(daily[0].day)}: ${usd(total)}`;
  const peak = daily.reduce((m, p) => (p.spend_usd_micros > m.spend_usd_micros ? p : m));
  return `Daily spend over ${daily.length} days, ${fmtIsoDay(daily[0].day)} to ${fmtIsoDay(daily[daily.length - 1].day)}: `
    + `total ${usd(total)}, highest ${fmtIsoDay(peak.day)} at ${usd(peak.spend_usd_micros)}`;
}

// ── spend by agent ──────────────────────────────────────────────────────────────────────
export type AgentSlice = { name: string; value: number; fill: string };

/** Agent display name -> colour, one map for everything the Spend by agent card shows. */
export type AgentHues = Record<string, Hue>;

/** One colour per agent across the donut, its list, the task-usage tabs and the bars: the map is
 * built from every agent in `agent_spend` and in `agent_task_mix` (rows with sessions), so an
 * agent keeps its colour in all of them and no two share one (see `assignAgentHues`). */
export function agentHueMap(agentSpend: Schemas["AgentSpend"][], mix: TaskMix[]): AgentHues {
  return assignAgentHues([...agentSpend.map((a) => a.agent), ...mix.filter((r) => r.count > 0).map((r) => r.agent)]);
}

/** The spend-by-agent donut and list: display names (an id and its display name are one agent),
 * dollars, biggest first. The slices sum to `agent_spend`, which sums to the headline. A negative
 * spend (a credit) counts as 0 so it never draws a backwards slice. */
export function agentSlices(agentSpend: Schemas["AgentSpend"][], hues?: AgentHues): AgentSlice[] {
  const by = new Map<string, number>();
  for (const a of agentSpend) {
    const name = agentName(a.agent);
    by.set(name, (by.get(name) ?? 0) + Math.max(0, a.spend.usd_micros ?? 0));
  }
  const fill = hues ?? assignAgentHues([...by.keys()]);
  return [...by.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, micros]) => ({ name, value: micros / 1_000_000, fill: fill[name] ?? categoryHue("agent", name) }));
}

// ── PR costs & forecast ─────────────────────────────────────────────────────────────────
type CostEnvelope = Schemas["CostEnvelope"];
type ConePoint = Schemas["ForecastConePoint"];

/** The cost-per-PR envelope as chart rows, dollars: "now" (median, p25-p75 band, p90/p99), then
 * the forecast cone's later steps (step 0 is the handoff, so it is skipped). */
export function costEnvelopePoints(env: CostEnvelope, cone: ConePoint[]): Record<string, number | string | boolean>[] {
  return [
    {
      label: "now",
      bandLo: env.p25_usd_micros / 1_000_000,
      band: (env.p75_usd_micros - env.p25_usd_micros) / 1_000_000,
      median: env.median_usd_micros / 1_000_000,
      p90: env.p90_usd_micros / 1_000_000,
      p99: env.p99_usd_micros / 1_000_000,
    },
    ...cone.slice(1).map((c) => ({
      label: `+${c.step}`,
      forecast: true,
      fMedian: c.median_usd_micros / 1_000_000,
      fBandLo: c.band_lo_usd_micros / 1_000_000,
      fBand: (c.band_hi_usd_micros - c.band_lo_usd_micros) / 1_000_000,
    })),
  ];
}

/** The envelope chart for assistive technology. */
export function costEnvelopeLabel(env: CostEnvelope, cone: ConePoint[]): string {
  const m = (micros: number) => fmtMoneyMicros(micros);
  const last = cone.length > 1 ? cone[cone.length - 1] : null;
  return `Cost per PR: median ${m(env.median_usd_micros)}, middle half ${m(env.p25_usd_micros)} to ${m(env.p75_usd_micros)}, `
    + `90th percentile ${m(env.p90_usd_micros)}, 99th ${m(env.p99_usd_micros)}`
    + (last ? `. Forecast: median ${m(last.median_usd_micros)} after ${last.step} steps, likely ${m(last.band_lo_usd_micros)} to ${m(last.band_hi_usd_micros)}` : "");
}

// ── task usage ──────────────────────────────────────────────────────────────────────────
export const ALL_AGENTS = "all";

export type TaskTab = { id: string; label: string };

type AgentTotal = { name: string; count: number };

/** Sessions per agent display name (ids and display names merge), biggest first, then by name.
 * Agents with no sessions (every row 0) are left out. */
function agentTotals(mix: TaskMix[]): AgentTotal[] {
  const by = new Map<string, number>();
  for (const r of mix) {
    if (r.count > 0) by.set(agentName(r.agent), (by.get(agentName(r.agent)) ?? 0) + r.count);
  }
  return [...by.entries()].map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

/** "All" then one tab per agent in `agent_task_mix` (the tab id is the agent's display name). */
export function taskUsageTabs(mix: TaskMix[]): TaskTab[] {
  return [{ id: ALL_AGENTS, label: "All" }, ...agentTotals(mix).map((a) => ({ id: a.name, label: a.name }))];
}

export type TaskBar = {
  /** The raw session-type label. */
  type: string;
  name: string;
  count: number;
  /** Share of the tab's sessions, 0..1 (by SESSION COUNT, as Usage's breakdown is). */
  share: number;
  /** Bar length relative to the longest bar, 0..1. */
  length: number;
  /** One segment per agent on "All" (agent hues); a single type-coloured segment on an agent's tab. */
  segments: { agent: string; count: number; hue: Hue }[];
  /** "Develop: 388 sessions (Claude Code 350, Codex 35, ...)" */
  title: string;
};

/** The bars of one tab, biggest first (ties by type). `tabId` is `ALL_AGENTS` or an agent display name;
 * `hues` is the card-wide agent colour map (`agentHueMap`), derived from this mix alone when omitted. */
export function taskUsageBars(mix: TaskMix[], tabId: string, hues?: AgentHues): TaskBar[] {
  const order = agentTotals(mix).map((a) => a.name);
  const agentHue = hues ?? assignAgentHues(order);
  const types = new Map<string, Map<string, number>>();
  for (const r of mix) {
    const agent = agentName(r.agent);
    if (tabId !== ALL_AGENTS && agent !== tabId) continue;
    if (r.count <= 0) continue;
    const per = types.get(r.type) ?? new Map<string, number>();
    per.set(agent, (per.get(agent) ?? 0) + r.count);
    types.set(r.type, per);
  }
  const rows = [...types.entries()].map(([type, per]) => ({ type, per, count: [...per.values()].reduce((a, b) => a + b, 0) }))
    .sort((a, b) => b.count - a.count || a.type.localeCompare(b.type));
  const total = rows.reduce((a, r) => a + r.count, 0);
  const max = rows[0]?.count ?? 0;
  return rows.map((r, i) => {
    const name = sessionTypeName(r.type);
    const segments = tabId === ALL_AGENTS
      ? order.filter((a) => r.per.has(a)).map((a) => ({
          agent: a, count: r.per.get(a) as number, hue: agentHue[a] ?? categoryHue("agent", a),
        }))
      : [{ agent: tabId, count: r.count, hue: categoryHue("sessionType", r.type, i) }];
    const split = tabId === ALL_AGENTS && segments.length > 1
      ? ` (${segments.map((s) => `${s.agent} ${s.count}`).join(", ")})` : "";
    return {
      type: r.type, name, count: r.count, share: total > 0 ? r.count / total : 0, length: max > 0 ? r.count / max : 0,
      segments, title: `${name}: ${r.count} ${r.count === 1 ? "session" : "sessions"}${split}`,
    };
  });
}
