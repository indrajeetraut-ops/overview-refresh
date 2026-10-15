import { describe, expect, it } from "vitest";

import {
  ALL_AGENTS, agentHueMap, agentSlices, costEnvelopeLabel, costEnvelopePoints, budgetMicros, budgetStatus, budgetTone, dailySpendAside, dailyAxisDomain, dailySpendLabel, dailySpendRows, fmtDailyAxis, fmtDailyUsd, forecastCardTitle, forecastPeriodIndex, forecastRunway,
  initialsOf, mergedPerDay, modelMixLabel, modelSlices, reasoningText, recoverableLine, reposText, runwayPill, spendGauge,
  taskUsageBars, taskUsageTabs, teamBarsLabel, teamSpendBars, teamLabelPlan, teamTableRows, teamTip, teamsBanner, teamsOverPill, truncateName,
} from "./overview";
import { assertLexicon } from "./lexicon";
import { categoryHue } from "./palette";
import { forecastFixture, sessionTypes } from "../data/mock";
import { mockAgentTaskMix } from "../data/mock-source";
import type { Schemas } from "../data/source";

const M = 1_000_000;
const team = (over: Partial<Schemas["TeamSummary"]>): Schemas["TeamSummary"] => ({
  team_id: "t", name: "Platform", members: 8, merged_prs: 52, over_budget: false, budget_pct: 0.5,
  spend: { usd_micros: 2_000 * M } as Schemas["Money"], cost_per_pr: { usd_micros: null } as Schemas["Money"],
  rework_rate: null, ...over,
});

describe("budget and gauge", () => {
  it("tones use the old thresholds", () => {
    expect(budgetTone(null)).toBe("muted");
    expect(budgetTone(0.8)).toBe("green");
    expect(budgetTone(0.81)).toBe("amber");
    expect(budgetTone(0.95)).toBe("amber");
    expect(budgetTone(0.96)).toBe("red");
  });
  it("recovers the budget from spend and share, guarded", () => {
    expect(budgetMicros(12_840 * M, 12_840 / 15_000)).toBe(15_000 * M);
    expect(budgetMicros(0, 0)).toBeNull();
    expect(budgetMicros(5 * M, null)).toBeNull();
    expect(budgetMicros(null, 0.5)).toBeNull();
    expect(budgetMicros(5 * M, Infinity)).toBeNull();
  });
  it("under budget: capped arc, left pill, caption", () => {
    const g = spendGauge(12_840 * M, 12_840 / 15_000)!;
    expect(g.fraction).toBeCloseTo(0.856, 3);
    expect(g.tone).toBe("amber");
    expect(g.over).toBe(false);
    expect(g.caption).toBe("86% of $15,000");
    expect(g.pill).toEqual({ text: "$2,160 left", variant: "amber" });
    expect(g.ariaLabel).toBe("Spend $12,840, 86% of $15,000, $2,160 left");
  });
  it("comfortably under budget is green", () => {
    const g = spendGauge(3_000 * M, 0.5)!;
    expect(g.tone).toBe("green");
    expect(g.pill).toEqual({ text: "$3,000 left", variant: "green" });
  });
  it("over budget: red, arc capped at 100%, over pill", () => {
    const g = spendGauge(4_320 * M, 4_320 / 4_000)!;
    expect(g.over).toBe(true);
    expect(g.tone).toBe("red");
    expect(g.fraction).toBe(1);
    expect(g.pct).toBeCloseTo(1.08, 5);
    expect(g.pill).toEqual({ text: "$320 over", variant: "red" });
  });
  it("exactly on budget has $0 left and is red", () => {
    const g = spendGauge(100 * M, 1)!;
    expect(g.over).toBe(false);
    expect(g.pill).toEqual({ text: "$0 left", variant: "red" });
  });
  it("no spend figure: no gauge (the card shows a plain Spent metric)", () => {
    expect(spendGauge(null, 0.5)).toBeNull();
    expect(spendGauge(undefined, 0.5)).toBeNull();
  });
  it("no budget: no gauge; zero share: arc empty, no amount", () => {
    expect(spendGauge(100 * M, null)).toBeNull();
    expect(spendGauge(100 * M, undefined)).toBeNull();
    const z = spendGauge(0, 0)!;
    expect(z.fraction).toBe(0);
    expect(z.pill).toBeNull();
    expect(z.caption).toBe("0% of budget");
  });
});

describe("KPI texts", () => {
  it("teams banner", () => {
    expect(teamsBanner(0, 5)).toEqual({ text: "All teams on track", variant: "green" });
    expect(teamsBanner(2, 5)).toEqual({ text: "2 of 5 teams over budget", variant: "red" });
    expect(teamsBanner(1, 1).text).toBe("1 of 1 team over budget");
    expect(teamsBanner(0, 0)).toEqual({ text: "No teams", variant: "muted" });
    expect(teamsOverPill(0)).toBeNull();
    expect(teamsOverPill(1)).toBe("1 team over budget");
    expect(teamsOverPill(3)).toBe("3 teams over budget");
  });
  it("repos", () => {
    expect(reposText(1)).toBe("across 1 repo");
    expect(reposText(7)).toBe("across 7 repos");
  });
  it("merged per day over elapsed days, never dividing by zero", () => {
    expect(mergedPerDay(187, 14)).toBe("≈ 13 per day");
    expect(mergedPerDay(52, 14)).toBe("≈ 3.7 per day");
    expect(mergedPerDay(14, 14)).toBe("≈ 1 per day");
    expect(mergedPerDay(0, 14)).toBeNull();
    expect(mergedPerDay(1, 60)).toBeNull(); // 0.017 a day: never "≈ 0 per day"
    expect(mergedPerDay(3, 60)).toBe("≈ 0.1 per day");
    expect(mergedPerDay(5_000, 2)).toBe("≈ 2,500 per day");
    expect(mergedPerDay(10, 0)).toBeNull();
    expect(mergedPerDay(null, 5)).toBeNull();
  });
  it("reasoning tokens and share", () => {
    expect(reasoningText({ reasoning_tokens: 256_000, pct: 4 })).toBe("256K (4%)");
    expect(reasoningText({ reasoning_tokens: 900, pct: null })).toBe("900");
    expect(reasoningText({ reasoning_tokens: 256_000, pct: 4.0384 })).toBe("256K (4%)");
    expect(reasoningText({ reasoning_tokens: 256_000, pct: 3.46 })).toBe("256K (3.5%)");
  });
});

describe("model mix slices", () => {
  const mix = (rows: [string, number, string][]): Schemas["ModelUsage"][] =>
    rows.map(([model, pct, tier]) => ({ model, pct, tier, tokens: Math.round(pct * 1000) }));
  it("small mixes list every model, biggest first, in tier colours", () => {
    const s = modelSlices(mix([["claude-sonnet-5", 0.22, "mid"], ["claude-opus-4-8", 0.68, "high"], ["claude-haiku-4-5", 0.1, "low"]]));
    expect(s.map((x) => x.name)).toEqual(["opus-4-8", "sonnet-5", "haiku-4-5"]);
    expect(s.map((x) => x.hue)).toEqual(["var(--primary)", "var(--green)", "var(--muted-foreground)"]);
    expect(s.every((x) => x.title === undefined)).toBe(true);
    expect(modelMixLabel("6.4M", s)).toBe("6.4M tokens. Model mix: opus-4-8 68%, sonnet-5 22%, haiku-4-5 10%");
  });
  it("rolls everything past the fourth into +N other with the full list in its title", () => {
    const s = modelSlices(mix([["a", 0.3, "high"], ["b", 0.25, "high"], ["c", 0.2, "mid"], ["d", 0.1, "mid"], ["e", 0.09, "low"], ["f", 0.06, "low"]]));
    expect(s.map((x) => x.name)).toEqual(["a", "b", "c", "d", "+2 other"]);
    const other = s[4];
    expect(other.pct).toBeCloseTo(0.15, 10);
    expect(other.title).toBe("e 9%, f 6%");
    expect(s.reduce((acc, x) => acc + x.pct, 0)).toBeCloseTo(1, 10);
    // the roll-up is its own neutral, not the low tier's grey
    expect(other.hue).not.toBe(categoryHue("modelTier", "low"));
    // the label names the rolled-up models too
    expect(modelMixLabel("1M", s)).toBe("1M tokens. Model mix: a 30%, b 25%, c 20%, d 10%, +2 other 15% (e 9%, f 6%)");
  });
  it("keys are unique even when a model id repeats", () => {
    const s = modelSlices(mix([["a", 0.5, "high"], ["a", 0.3, "high"], ["b", 0.2, "mid"]]));
    expect(new Set(s.map((x) => x.key)).size).toBe(3);
  });
  it("nothing in, nothing out", () => {
    expect(modelSlices(null)).toEqual([]);
    expect(modelSlices([])).toEqual([]);
  });
});

describe("forecast card helpers", () => {
  it("runway statuses to pills", () => {
    expect(runwayPill("lasts")).toEqual({ text: "Under budget", variant: "green" });
    expect(runwayPill("at_risk")).toEqual({ text: "At risk", variant: "amber" });
    expect(runwayPill("runs_out")).toEqual({ text: "Over budget", variant: "red" });
    expect(runwayPill("exhausted")).toEqual({ text: "Over budget", variant: "red" });
    expect(runwayPill(undefined)).toBeNull();
    expect(runwayPill("nope")).toBeNull();
  });
  it("budget status fallback", () => {
    expect(budgetStatus(0.5)).toEqual({ label: "Budget status", text: "50% of budget used", variant: "green" });
    expect(budgetStatus(1.2).variant).toBe("red");
    expect(budgetStatus(null)).toEqual({ label: "Budget status", text: "No budget", variant: "muted" });
  });
  it("recoverable line", () => {
    expect(recoverableLine(2_180 * M, 12_840 * M, "resume"))
      .toBe("Recoverable · could save 17% · $2,180 · mostly resuming after pauses");
    expect(recoverableLine(2_180 * M, 12_840 * M, null)).toBe("Recoverable · could save 17% · $2,180");
    expect(recoverableLine(null, 12_840 * M, "resume")).toBe("Recoverable · —");
    // a cause with no wording is dropped, not echoed raw
    expect(recoverableLine(500 * M, 0, "elsewhere")).toBe("Recoverable · $500");
    // nothing recoverable: the figure only; a tiny share is "<1%", never "0%"
    expect(recoverableLine(0, 12_840 * M, "resume")).toBe("Recoverable · $0");
    expect(recoverableLine(40 * M, 12_840 * M, "resume")).toBe("Recoverable · could save <1% · $40 · mostly resuming after pauses");
    expect(() => assertLexicon(recoverableLine(2_180 * M, 12_840 * M, "cache_bust"))).not.toThrow();
  });
  it("the mock org forecast: the current period, with a runway sentence", () => {
    const view = forecastFixture("org", "default")!;
    const idx = forecastPeriodIndex(view);
    expect(idx).toBeGreaterThanOrEqual(0);
    const r = forecastRunway(view, idx);
    expect(r).not.toBeNull();
    expect(r!.sentence.length).toBeGreaterThan(10);
    expect(["Under budget", "At risk", "Over budget"]).toContain(r!.pill.text);
  });
  it("each runway status gives its pill and a sentence about the budget", () => {
    const base = forecastFixture("org", "default")!;
    const idx = forecastPeriodIndex(base);
    const withRunway = (status: "lasts" | "at_risk" | "runs_out" | "exhausted", p50: string | null): Schemas["ForecastView"] => ({
      ...base,
      series: base.series.map((s) => ({
        ...s,
        periods: s.periods.map((p, i) => (i === idx
          ? { ...p, runway: { status, days_short: 9, runs_out_probability: 0.7, runs_out_on: { p10: null, p50, p90: null } } }
          : p)),
      })),
    });
    const cases: [Parameters<typeof withRunway>[0], string | null, string, RegExp][] = [
      ["lasts", null, "Under budget", /lasts the month/],
      ["at_risk", "2026-10-24", "At risk", /could run out around Oct 24, 9 days early/],
      ["runs_out", "2026-10-20", "Over budget", /runs out around Oct 20, 9 days early/],
      ["exhausted", "2026-10-06", "Over budget", /was used up on Oct 6/],
    ];
    for (const [status, p50, pill, sentence] of cases) {
      const r = forecastRunway(withRunway(status, p50), idx);
      expect(r?.pill.text, status).toBe(pill);
      expect(r?.sentence, status).toMatch(sentence);
      expect(r?.sentence, status).toMatch(/\$15,000 budget/);
    }
    expect(forecastRunway(withRunway("lasts", null), idx, "no-such-preset")).toBeNull();
  });
  it("the card title names the calendar month being drawn", () => {
    const view = forecastFixture("org", "default")!;
    expect(forecastCardTitle(view)).toBe("Budget forecast · October");
    expect(forecastCardTitle(null)).toBe("Budget forecast");
    expect(forecastCardTitle({ ...view, series: [] })).toBe("Budget forecast");
  });
  it("no budget, or no period under way, means no runway footer", () => {
    const view = forecastFixture("org", "default")!;
    expect(forecastRunway({ ...view, budget_usd_micros: null }, forecastPeriodIndex(view))).toBeNull();
    const ended = { ...view, series: view.series.map((s) => ({
      ...s, periods: s.periods.map((p) => ({ ...p, forecast_from: p.start, runway: null })),
    })) };
    expect(forecastRunway(ended, forecastPeriodIndex(ended))).toBeNull();
    expect(forecastPeriodIndex(ended)).toBe(0);
  });
});

describe("team bars and table", () => {
  const teams = [
    team({ team_id: "p", name: "Platform", spend: { usd_micros: 4_320 * M } as Schemas["Money"], budget_pct: 1.08, over_budget: true }),
    team({ team_id: "g", name: "Data Science", spend: { usd_micros: 1_000 * M } as Schemas["Money"], budget_pct: 0.4 }),
    team({ team_id: "n", name: "growth-web", spend: { usd_micros: 700 * M } as Schemas["Money"], budget_pct: null }),
    team({ team_id: "z", name: "Idle", spend: { usd_micros: 0 } as Schemas["Money"], budget_pct: 0 }),
  ];
  it("bars carry whole-dollar spend, recovered budget and the delta", () => {
    const bars = teamSpendBars(teams);
    expect(bars[0]).toEqual({ id: "p", name: "Platform", spent: 4_320, budget: 4_000, over: true, delta: 320, deltaMicros: 320 * M });
    expect(bars[1]).toMatchObject({ spent: 1_000, budget: 2_500, over: false, delta: -1_500 });
    expect(bars[2]).toMatchObject({ budget: null, delta: null });
    expect(bars[3]).toMatchObject({ spent: 0, budget: null, delta: null });
  });
  it("tooltip facts: spent, budget, and what is over or left", () => {
    const bars = teamSpendBars(teams);
    expect(teamTip(bars[0])).toEqual({ title: "Platform", rows: [
      { label: "Spent", value: "$4,320" }, { label: "Budget", value: "$4,000" }, { label: "Over", value: "+$320", tone: "red" },
    ] });
    expect(teamTip(bars[1]).rows).toEqual([
      { label: "Spent", value: "$1,000" }, { label: "Budget", value: "$2,500" }, { label: "Remaining", value: "$1,500", tone: "green" },
    ]);
    expect(teamTip(bars[2]).rows).toEqual([{ label: "Spent", value: "$700" }]);
    // flagged over by less than a dollar: red "<$1", never a green "Remaining $0"
    const [tiny] = teamSpendBars([team({ spend: { usd_micros: 1_000_000_300 } as Schemas["Money"], budget_pct: 1_000_000_300 / 1_000_000_000, over_budget: true })]);
    expect(tiny.deltaMicros).toBe(300);
    expect(teamTip(tiny).rows[2]).toEqual({ label: "Over", value: "<$1", tone: "red" });
    // exactly at budget and not flagged: nothing remaining, still not "over"
    const [on] = teamSpendBars([team({ spend: { usd_micros: 100 * M } as Schemas["Money"], budget_pct: 1 })]);
    expect(teamTip(on).rows[2]).toEqual({ label: "Remaining", value: "$0", tone: "green" });
    expect(teamBarsLabel(bars.slice(0, 3))).toBe(
      "Spend by team: Platform $4,320 of $4,000 budget, over budget; Data Science $1,000 of $2,500 budget; growth-web $700, no budget");
  });
  it("table rows: figures, budget-used bar and status", () => {
    const rows = teamTableRows(teams);
    expect(rows[0]).toMatchObject({
      initials: "PL", members: "8", spend: "$4,320", mergedPrs: "52", budgetUsed: "108%", barFraction: 1, tone: "red",
      status: { text: "$320 over budget", variant: "red" },
    });
    expect(rows[1]).toMatchObject({ initials: "DS", budgetUsed: "40%", barFraction: 0.4, tone: "green", status: { text: "On track", variant: "green" } });
    expect(rows[2]).toMatchObject({ budgetUsed: "—", barFraction: 0, tone: "muted", status: { text: "No budget", variant: "muted" } });
    expect(rows[3]).toMatchObject({ budgetUsed: "0%", barFraction: 0, status: { text: "On track" } });
  });
  it("over budget is the server's flag or a share above 100%, in status, tone and bar alike", () => {
    // flagged, but no budget share: red status, no bar
    const [flagged] = teamTableRows([team({ over_budget: true, budget_pct: null })]);
    expect(flagged).toMatchObject({ hasBar: false, budgetUsed: "—", barFraction: 0, tone: "red", status: { text: "Over budget", variant: "red" } });
    // not flagged, but 110% of budget: still over
    const [high] = teamTableRows([team({ over_budget: false, budget_pct: 1.1, spend: { usd_micros: 1_100 * M } as Schemas["Money"] })]);
    expect(high).toMatchObject({ hasBar: true, budgetUsed: "110%", barFraction: 1, tone: "red", status: { text: "$100 over budget", variant: "red" } });
    // no budget, not flagged
    expect(teamTableRows([team({ budget_pct: null })])[0]).toMatchObject({ hasBar: false, tone: "muted", status: { text: "No budget" } });
    // on budget exactly: not over
    expect(teamTableRows([team({ budget_pct: 1 })])[0]).toMatchObject({ hasBar: true, tone: "red", status: { text: "On track" } });
  });
  it("big counts get thousands separators; rows keep the API order", () => {
    const rows = teamTableRows([team({ team_id: "b", name: "B", members: 1_200, merged_prs: 3_450 }), team({ team_id: "a", name: "A", members: 0, merged_prs: 0 })]);
    expect(rows.map((r) => r.id)).toEqual(["b", "a"]);
    expect(rows[0]).toMatchObject({ members: "1,200", mergedPrs: "3,450" });
    expect(rows[1]).toMatchObject({ members: "0", mergedPrs: "0" });
  });
  it("an over-budget flag without a recoverable budget still says so", () => {
    const [r] = teamTableRows([team({ over_budget: true, budget_pct: 0, spend: { usd_micros: 0 } as Schemas["Money"] })]);
    expect(r.status).toEqual({ text: "Over budget", variant: "red" });
  });
  it("initials", () => {
    expect(initialsOf("Platform")).toBe("PL");
    expect(initialsOf("data science")).toBe("DS");
    expect(initialsOf("  ")).toBe("?");
    expect(initialsOf("X")).toBe("X");
    expect(initialsOf("😀rocket")).toBe("😀R");
    expect(initialsOf("Équipe Data")).toBe("ÉD");
  });
});

describe("team bar labels", () => {
  it("truncates without a dangling space and counts characters, not code units", () => {
    expect(truncateName("Platform", 10)).toBe("Platform");
    expect(truncateName("Data Science Team", 6)).toBe("Data…");
    expect(truncateName("Infrastructure", 10)).toBe("Infrastru…");
    expect(truncateName("😀😀😀😀😀😀", 4)).toBe("😀😀😀…");
    // a ZWJ family and a flag are one character each
    expect(truncateName("👨‍👩‍👧‍👦abcd", 3)).toBe("👨‍👩‍👧‍👦a…");
    expect(truncateName("🇮🇳🇮🇳🇮🇳🇮🇳", 3)).toBe("🇮🇳🇮🇳…");
    expect(truncateName("🇮🇳🇮🇳🇮🇳", 3)).toBe("🇮🇳🇮🇳🇮🇳");
    expect(truncateName("ab", 1)).toBe("a…");
  });
  it("plans labels from the room each bar has", () => {
    // 5 teams at 320: six characters flat, value labels kept
    expect(teamLabelPlan(262, 5)).toEqual({ chars: 6, rotate: false, valueLabels: true });
    // 5 teams at 375: flat, 7 characters
    expect(teamLabelPlan(287, 5)).toEqual({ chars: 7, rotate: false, valueLabels: true });
    // wide card, few teams: roomy
    expect(teamLabelPlan(450, 5)).toEqual({ chars: 12, rotate: false, valueLabels: true });
    // 10 teams on a narrow card: rotated and no value labels (tooltip and aria label carry them)
    expect(teamLabelPlan(300, 10)).toEqual({ chars: 12, rotate: true, valueLabels: false });
    expect(teamLabelPlan(330, 8)).toEqual({ chars: 5, rotate: false, valueLabels: true });
    // one team: all the room
    expect(teamLabelPlan(262, 1)).toEqual({ chars: 14, rotate: false, valueLabels: true });
    // boundaries: 32px a bar is five characters and keeps its value label; one pixel less rotates and drops them
    expect(teamLabelPlan(216, 5)).toEqual({ chars: 5, rotate: false, valueLabels: true });
    expect(teamLabelPlan(215, 5)).toEqual({ chars: 12, rotate: true, valueLabels: false });
    // unmeasured: assume room
    expect(teamLabelPlan(0, 5)).toEqual({ chars: 10, rotate: false, valueLabels: true });
  });
});

describe("daily chart", () => {
  const daily = [
    { day: "2026-10-01", spend_usd_micros: 1_000_000 }, { day: "2026-10-02", spend_usd_micros: 3_500_000 }, { day: "2026-10-03", spend_usd_micros: 0 },
  ];
  it("micros become dollars in the rows, once", () => {
    expect(dailySpendRows(daily)).toEqual([{ day: "2026-10-01", usd: 1 }, { day: "2026-10-02", usd: 3.5 }, { day: "2026-10-03", usd: 0 }]);
    expect(dailySpendRows([])).toEqual([]);
  });
  it("describes range, total and peak for assistive technology", () => {
    expect(dailySpendLabel(daily)).toBe("Daily spend over 3 days, Oct 1 to Oct 3: total $4.50, highest Oct 2 at $3.50");
    expect(dailySpendLabel(daily.slice(0, 1))).toBe("Spend on Oct 1: $1.00");
    expect(dailySpendLabel([])).toBe("No daily spend");
  });
  it("edge cases: tied peak, all-zero days, huge values, sub-dollar days", () => {
    const tied = [{ day: "2026-10-01", spend_usd_micros: 5 * M }, { day: "2026-10-02", spend_usd_micros: 5 * M }, { day: "2026-10-03", spend_usd_micros: 1 * M }];
    expect(dailySpendLabel(tied)).toContain("highest Oct 1 at $5.00");
    const zeros = [{ day: "2026-10-01", spend_usd_micros: 0 }, { day: "2026-10-02", spend_usd_micros: 0 }];
    expect(dailySpendLabel(zeros)).toBe("Daily spend over 2 days, Oct 1 to Oct 2: total $0, highest Oct 1 at $0");
    expect(dailyAxisDomain(dailySpendRows(zeros))).toEqual([0, 1]);
    expect(dailyAxisDomain(dailySpendRows(tied))).toEqual([0, "auto"]);
    expect(dailyAxisDomain([])).toEqual([0, 1]);
    const huge = [{ day: "2026-10-01", spend_usd_micros: 2_500_000 * M }, { day: "2026-10-02", spend_usd_micros: 1 * M }];
    expect(dailySpendLabel(huge)).toContain("total $2.5M, highest Oct 1 at $2.5M");
    expect(dailySpendLabel([{ day: "2026-10-01", spend_usd_micros: 50_000 }])).toBe("Spend on Oct 1: $0.05");
  });
  it("small amounts keep their cents, big ones do not", () => {
    expect([0, 0.05, 12.34, 99.99, 100.4, 410, 999.4, 1503, 2_500_000].map(fmtDailyUsd))
      .toEqual(["$0", "$0.05", "$12.34", "$99.99", "$100", "$410", "$999", "$1.5k", "$2.5M"]);
    expect(fmtDailyUsd(NaN)).toBe("—");
    expect([0, 10, 0.25, 0.5, 1.75, 400, 1200].map(fmtDailyAxis)).toEqual(["$0", "$10", "$0.25", "$0.5", "$1.75", "$400", "$1.2k"]);
  });
});

describe("daily aside", () => {
  it("counts days", () => {
    expect(dailySpendAside(14)).toBe("14 days · daily spend");
    expect(dailySpendAside(1)).toBe("1 day · daily spend");
  });
});

describe("task usage", () => {
  const mix: Schemas["AgentTaskMix"][] = [
    { agent: "claude_code", type: "IMPLEMENT", count: 30 },
    { agent: "claude_code", type: "BUG_FIX", count: 10 },
    { agent: "codex", type: "IMPLEMENT", count: 5 },
    { agent: "codex", type: "REVIEW", count: 5 },
  ];
  it("tabs: All, then agents by sessions, under display names", () => {
    expect(taskUsageTabs(mix)).toEqual([
      { id: "all", label: "All" }, { id: "Claude Code", label: "Claude Code" }, { id: "Codex", label: "Codex" },
    ]);
    expect(taskUsageTabs([])).toEqual([{ id: "all", label: "All" }]);
  });
  it("ids and display names are one agent", () => {
    const tabs = taskUsageTabs([{ agent: "claude_code", type: "REVIEW", count: 1 }, { agent: "Claude Code", type: "PLAN", count: 2 }]);
    expect(tabs).toHaveLength(2);
  });
  it("agents with no sessions get no tab; a missing agent id is Unknown", () => {
    const tabs = taskUsageTabs([
      { agent: "codex", type: "REVIEW", count: 0 }, { agent: "", type: "PLAN", count: 2 }, { agent: "cursor", type: "PLAN", count: 1 },
    ]);
    expect(tabs.map((t) => t.label)).toEqual(["All", "Unknown", "Cursor"]);
    const bars = taskUsageBars([{ agent: "", type: "PLAN", count: 2 }], ALL_AGENTS);
    expect(bars[0].segments.map((s) => s.agent)).toEqual(["Unknown"]);
    expect(taskUsageBars([{ agent: "", type: "PLAN", count: 2 }], "Unknown")[0].count).toBe(2);
  });
  it("All: bars by session count, split by agent", () => {
    const bars = taskUsageBars(mix, ALL_AGENTS);
    expect(bars.map((b) => [b.name, b.count])).toEqual([["Develop", 35], ["Fix", 10], ["Review", 5]]);
    expect(bars[0].share).toBeCloseTo(0.7, 10);
    expect(bars[0].length).toBe(1);
    expect(bars[1].length).toBeCloseTo(10 / 35, 10);
    expect(bars[0].segments.map((s) => [s.agent, s.count])).toEqual([["Claude Code", 30], ["Codex", 5]]);
    expect(bars[0].segments.map((s) => s.hue)).toEqual(["var(--primary)", "var(--green)"]);
    expect(bars[0].title).toBe("Develop: 35 sessions (Claude Code 30, Codex 5)");
    expect(bars.reduce((a, b) => a + b.share, 0)).toBeCloseTo(1, 10);
  });
  it("an agent's tab: its own types in the session-type colours", () => {
    const bars = taskUsageBars(mix, "Codex");
    expect(bars.map((b) => [b.name, b.count])).toEqual([["Develop", 5], ["Review", 5]]);
    expect(bars[0].share).toBe(0.5);
    expect(bars[0].segments).toEqual([{ agent: "Codex", count: 5, hue: "var(--primary)" }]);
    expect(bars[1].segments[0].hue).toBe("var(--green)");
    expect(bars[0].title).toBe("Develop: 5 sessions");
  });
  it("unknown tab and empty mix give no bars", () => {
    expect(taskUsageBars(mix, "Nobody")).toEqual([]);
    expect(taskUsageBars([], ALL_AGENTS)).toEqual([]);
  });
  it("on the mock, All per type equals Usage's session types", () => {
    const bars = taskUsageBars(mockAgentTaskMix(), ALL_AGENTS);
    const byType = Object.fromEntries(bars.map((b) => [b.type, b.count]));
    for (const t of sessionTypes) expect(byType[t.type]).toBe(t.count);
    expect(bars.reduce((a, b) => a + b.count, 0)).toBe(sessionTypes.reduce((a, t) => a + t.count, 0));
  });
});

describe("spend by agent", () => {
  const money = (m: number | null) => ({ usd_micros: m } as Schemas["Money"]);
  it("slices use display names, merge an id with its display name and sum to the total", () => {
    const api = agentSlices([
      { agent: "codex", spend: money(1_100 * M) }, { agent: "claude_code", spend: money(11_200 * M) }, { agent: "cursor", spend: money(540 * M) },
    ]);
    expect(api.map((s) => [s.name, s.value])).toEqual([["Claude Code", 11_200], ["Codex", 1_100], ["Cursor", 540]]);
    expect(api.reduce((a, s) => a + s.value, 0)).toBe(12_840);
    const merged = agentSlices([{ agent: "claude_code", spend: money(5 * M) }, { agent: "Claude Code", spend: money(7 * M) }, { agent: "x", spend: money(null) }]);
    expect(merged.map((s) => [s.name, s.value])).toEqual([["Claude Code", 12], ["X", 0]]);
    expect(agentSlices([])).toEqual([]);
  });
  it("a credit counts as zero spend, so the total never goes backwards", () => {
    const s = agentSlices([{ agent: "codex", spend: money(-50 * M) }, { agent: "claude_code", spend: money(100 * M) }]);
    expect(s.map((x) => [x.name, x.value])).toEqual([["Claude Code", 100], ["Codex", 0]]);
    expect(agentSlices([{ agent: "codex", spend: money(-5 * M) }]).reduce((a, x) => a + x.value, 0)).toBe(0);
    expect(agentSlices([{ agent: "codex", spend: money(0) }])[0].value).toBe(0);
  });
  it("a missing agent id is Unknown in the donut, the tabs and the bars alike", () => {
    expect(agentSlices([{ agent: "", spend: money(1 * M) }])[0].name).toBe("Unknown");
    expect(taskUsageTabs([{ agent: "", type: "PLAN", count: 1 }]).map((t) => t.label)).toEqual(["All", "Unknown"]);
    expect(taskUsageBars([{ agent: "  ", type: "PLAN", count: 1 }], ALL_AGENTS)[0].segments[0].agent).toBe("Unknown");
  });
  it("one colour map covers agents that are only in the spend list or only in the task mix", () => {
    const spend = [{ agent: "claude_code", spend: money(5 * M) }, { agent: "only_spend", spend: money(1 * M) }];
    const mix = [{ agent: "claude_code", type: "PLAN", count: 2 }, { agent: "only_mix", type: "PLAN", count: 3 }, { agent: "zero_rows", type: "PLAN", count: 0 }];
    const hues = agentHueMap(spend, mix);
    expect(Object.keys(hues).sort()).toEqual(["Claude Code", "Only Mix", "Only Spend"]);
    expect(new Set(Object.values(hues)).size).toBe(3);
    // donut and bars read the same map
    expect(agentSlices(spend, hues).find((x) => x.name === "Only Spend")!.fill).toBe(hues["Only Spend"]);
    expect(taskUsageBars(mix, ALL_AGENTS, hues)[0].segments.find((g) => g.agent === "Only Mix")!.hue).toBe(hues["Only Mix"]);
  });
  it("donut, list, tabs and bars agree on every agent's colour, from ids and from display names", () => {
    const names = [["claude_code", "Claude Code"], ["codex", "Codex"], ["cursor", "Cursor"], ["windsurf_ai", "Windsurf Ai"]];
    for (const ids of [names.map((n) => n[0]), names.map((n) => n[1])]) {
      const spend = ids.map((a, i) => ({ agent: a, spend: money((10 - i) * M) }));
      const mix = ids.map((a, i) => ({ agent: a, type: "IMPLEMENT", count: 1 + ((i * 5) % 4) }));
      const hues = agentHueMap(spend, mix);
      const slices = agentSlices(spend, hues);
      const segs = taskUsageBars(mix, ALL_AGENTS, hues)[0].segments;
      for (const [, display] of names) {
        const slice = slices.find((s) => s.name === display)!;
        expect(segs.find((g) => g.agent === display)!.hue, display).toBe(slice.fill);
        expect(hues[display]).toBe(slice.fill);
      }
      // without the shared map each side derives the same colours from the same agents
      expect(agentSlices(spend).map((s) => s.fill)).toEqual(slices.map((s) => s.fill));
    }
    // the pinned three keep their colours; an unknown agent never borrows one
    expect(categoryHue("agent", "claude_code")).toBe("var(--primary)");
    const pinned = new Set(["var(--primary)", "var(--green)", "var(--amber)", "var(--blue)"]);
    for (const n of ["windsurf", "Aider", "x", "Some New Agent"]) expect(pinned.has(categoryHue("agent", n))).toBe(false);
    expect(categoryHue("agent", "windsurf", 0)).toBe(categoryHue("agent", "Windsurf", 4));
  });
});

describe("PR cost envelope", () => {
  const env = { min_usd_micros: 5 * M, p25_usd_micros: 30 * M, median_usd_micros: 52 * M, p75_usd_micros: 80 * M, p90_usd_micros: 120 * M, p99_usd_micros: 300 * M, max_usd_micros: 486 * M };
  const cone = [
    { step: 0, median_usd_micros: 52 * M, band_lo_usd_micros: 40 * M, band_hi_usd_micros: 70 * M },
    { step: 1, median_usd_micros: 55 * M, band_lo_usd_micros: 35 * M, band_hi_usd_micros: 90 * M },
    { step: 2, median_usd_micros: 58 * M, band_lo_usd_micros: 30 * M, band_hi_usd_micros: 110 * M },
  ];
  it("rows: now, then the cone's later steps, in dollars", () => {
    expect(costEnvelopePoints(env, cone)).toEqual([
      { label: "now", bandLo: 30, band: 50, median: 52, p90: 120, p99: 300 },
      { label: "+1", forecast: true, fMedian: 55, fBandLo: 35, fBand: 55 },
      { label: "+2", forecast: true, fMedian: 58, fBandLo: 30, fBand: 80 },
    ]);
    expect(costEnvelopePoints(env, [])).toHaveLength(1);
  });
  it("describes it for assistive technology", () => {
    expect(costEnvelopeLabel(env, cone)).toBe(
      "Cost per PR: median $52, middle half $30 to $80, 90th percentile $120, 99th $300. Forecast: median $58 after 2 steps, likely $30 to $110");
    expect(costEnvelopeLabel(env, [])).toBe("Cost per PR: median $52, middle half $30 to $80, 90th percentile $120, 99th $300");
  });
});
