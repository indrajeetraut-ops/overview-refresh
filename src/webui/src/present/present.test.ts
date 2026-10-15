import { describe, expect, it } from "vitest";

import {
  assertLexicon,
  categoryHue,
  entityRoute,
  findingRoute,
  fmtDays,
  fmtSpanDays,
  percentileScale,
  assignLabelLanes,
  fmtDuration,
  fmtMoneyMicros,
  fmtPct,
  sessionTypeName,
  agentName,
  fmtRelativeTime,
  fmtTokens,
  fmtUSD,
  positionLifecycle,
  QUALITY_LABELS,
  seatChip,
  seatUsageChartData,
  scanLexicon,
} from "./index";
import { MockSource, mockAgentTaskMix, mockDailySpend } from "../data/mock-source";
import { ORG_SPENT, sessionTypes } from "../data/mock";
import { rollingSelection, timeframeOf } from "./timeframe";

describe("format", () => {
  it("durations", () => {
    expect(fmtDuration(200)).toBe("3h 20m");
    expect(fmtDuration(45)).toBe("45m");
    expect(fmtDuration(120)).toBe("2h");
    expect(fmtDuration(0)).toBe("0m");
    expect(fmtDuration(null)).toBe("—");
  });
  it("days", () => {
    expect(fmtDays(2.4)).toBe("2.4d");
    expect(fmtDays(5)).toBe("5d");
  });
  it("span in days picks a readable unit", () => {
    expect(fmtSpanDays(0.0001)).toBe("<1m");
    expect(fmtSpanDays(0.0124)).toBe("18m");
    expect(fmtSpanDays(0.33)).toBe("7.9h");
    expect(fmtSpanDays(3.29)).toBe("3.3d");
    expect(fmtSpanDays(12.4)).toBe("12d");
  });
  it("relative time", () => {
    const now = new Date("2026-09-10T12:00:00Z");
    expect(fmtRelativeTime("2026-09-10T10:00:00Z", now)).toMatch(/^2\s?h(r)?\s?ago$/);
    expect(fmtRelativeTime("2026-09-10T11:59:50Z", now)).toBe("just now");
    expect(fmtRelativeTime(null, now)).toBe("—");
  });
  it("money + tokens + pct", () => {
    expect(fmtUSD(12_840)).toBe("$12,840");
    expect(fmtMoneyMicros(1_250_000)).toBe("$1");
    expect(fmtTokens(2_400_000)).toBe("2.4M");
    expect(fmtTokens(320_000)).toBe("320K");
    expect(fmtPct(0.18)).toBe("18%");
    expect(fmtPct(3, 4)).toBe("75%");
  });
});

describe("palette", () => {
  it("pins the quality vocabulary", () => {
    expect(categoryHue("quality", "Clean")).toBe("var(--green)");
    expect(categoryHue("quality", "Costly rework")).toBe("var(--red)");
  });
  it("maps raw session-type labels to display names and pinned hues", () => {
    expect(sessionTypeName("IMPLEMENT")).toBe("Develop");
    expect(sessionTypeName("COMPREHEND")).toBe("Research");
    expect(sessionTypeName("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(categoryHue("sessionType", "BUG_FIX")).toBe("var(--amber)");
  });
  it("maps agent ids and display names to one display name", () => {
    expect(agentName("claude_code")).toBe("Claude Code");
    expect(agentName("codex")).toBe("Codex");
    expect(agentName("cursor")).toBe("Cursor");
    expect(agentName("Claude Code")).toBe("Claude Code");
    expect(agentName("claude-code")).toBe("Claude Code");
    expect(agentName("Codex")).toBe("Codex");
    expect(agentName("my_new_agent")).toBe("My New Agent");
    expect(agentName("windsurf")).toBe("Windsurf");
    expect(agentName("GPT_5")).toBe("GPT 5");
    expect(agentName("")).toBe("Unknown");
    expect(agentName(null)).toBe("Unknown");
    expect(agentName("_")).toBe("Unknown");
    expect(agentName("constructor")).toBe("Constructor");
  });
  it("colours an agent the same from its raw id or its display name", () => {
    expect(categoryHue("agent", "claude_code", 2)).toBe("var(--primary)");
    expect(categoryHue("agent", "codex", 0)).toBe("var(--green)");
    expect(categoryHue("agent", "Cursor", 4)).toBe(categoryHue("agent", "cursor", 0));
    expect(categoryHue("agent", "new_agent", 1)).toBe(categoryHue("agent", "New Agent", 1));
  });
  it("falls back to a stable ramp for unknown labels", () => {
    expect(categoryHue("agent", "Windsurf", 1)).toBe(categoryHue("agent", "Windsurf", 1));
    expect(categoryHue("agent", "Claude Code")).toBe("var(--primary)");
  });
});

describe("percentile layout", () => {
  it("switches to log scale for a skewed spread, linear otherwise", () => {
    const skewed = percentileScale(18, 538);
    expect(skewed.log).toBe(true);
    expect(skewed.pos(18)).toBe(0);
    expect(skewed.pos(538)).toBe(100);
    expect(skewed.pos(72)).toBeGreaterThan(35);
    expect(percentileScale(10, 50).log).toBe(false);
    expect(percentileScale(0, 500).log).toBe(false);
  });
  it("stacks colliding labels into lanes and clamps them inside the track", () => {
    const { placed, lanes } = assignLabelLanes(
      [{ center: 0, width: 40 }, { center: 5, width: 40 }, { center: 200, width: 40 }, { center: 400, width: 40 }], 400,
    );
    expect(placed[0]).toEqual({ left: 0, lane: 0 });
    expect(placed[1].lane).toBe(1);
    expect(placed[2].lane).toBe(0);
    expect(placed[3]).toEqual({ left: 360, lane: 0 });
    expect(lanes).toBe(2);
  });
});

describe("routing", () => {
  it("maps entity refs to routes", () => {
    expect(entityRoute({ kind: "team", id: "platform" })).toEqual({ screen: "team", param: "platform" });
    expect(entityRoute({ kind: "usage" })).toEqual({ screen: "usage" });
  });
  it("throws when an id-bearing kind has no id", () => {
    expect(() => entityRoute({ kind: "person" })).toThrow();
  });
  it("routes a finding via its entity ref", () => {
    expect(findingRoute({
      id: "w1", text: "Platform is over budget", action: "View Platform",
      severity: "red", entity: { kind: "team", id: "platform" },
    })).toEqual({ screen: "team", param: "platform" });
  });
});

describe("lexicon", () => {
  it("flags banned words", () => {
    expect(scanLexicon("this is recoverable spend")).toEqual([]);
    expect(scanLexicon("this is wasted money").some((h) => h.term === "wasted")).toBe(true);
    expect(() => assertLexicon("pure waste here")).toThrow();
    expect(assertLexicon("room to save $120")).toBe("room to save $120");
  });
  it("locks the quality vocabulary", () => {
    expect(QUALITY_LABELS).toEqual(["Clean", "Heavy lift", "Quick & dirty", "Costly rework"]);
  });
});

describe("lifecycle", () => {
  it("positions milestones proportionally by elapsed time", () => {
    const result = positionLifecycle([
      { name: "opened", ts: "2026-09-02T12:00:00Z", spanKind: "build" },
      { name: "reviewed", ts: "2026-09-02T18:00:00Z", spanKind: "review" },
      { name: "merged", ts: "2026-09-03T00:00:00Z" },
    ]);
    expect(result[0].x).toBe(0);
    expect(result[1].x).toBe(50);
    expect(result[2].x).toBe(100);
    expect(result[0].spanLabel).toBe("Build · 6h");
    expect(result[1].spanLabel).toBe("Review · 6h");
  });

  it("pins an unreached milestone (ts: null) at x=100", () => {
    const result = positionLifecycle([
      { name: "opened", ts: "2026-09-02T12:00:00Z" },
      { name: "merged", ts: null },
    ]);
    expect(result[1].x).toBe(100);
  });

  it("does not divide by zero with fewer than two reached timestamps", () => {
    const result = positionLifecycle([{ name: "opened", ts: "2026-09-02T12:00:00Z" }]);
    expect(result[0].x).toBe(0);
    expect(Number.isFinite(result[0].x)).toBe(true);
  });
});

describe("seats", () => {
  const base = { name: "Dana Lee", status: "on-track", low_use: false };

  it("leaves people with no seat allowance out of the chart instead of plotting 0%", () => {
    const points = seatUsageChartData([
      { ...base, usage_pct: 0.6 },
      { ...base, name: "No Budget", usage_pct: null },
      { ...base, name: "Idle Member", usage_pct: 0, low_use: true },
    ]);
    expect(points.map((p) => p.name)).toEqual(["Dana", "Idle"]);
    expect(points[1]).toMatchObject({ usage_pct: 0, fill: "var(--amber)" });
  });

  it("colours bars by the seat band, not by room to save", () => {
    const [over, room] = seatUsageChartData([
      { ...base, status: "over", usage_status: "over", usage_pct: 1.17 },
      { ...base, name: "Sam Roe", status: "room-to-save", usage_status: "healthy", usage_pct: 0.6 },
    ]);
    expect(over.fill).toBe("var(--red)");
    expect(room.fill).toBe("var(--primary)");
  });

  it("maps the headline status to a chip: room to save, high use, low use, new seat, on track", () => {
    expect(seatChip({ ...base, status: "room-to-save", low_use: true }).label).toBe("Room to save");
    expect(seatChip({ ...base, status: "over", usage_status: "over" })).toEqual({ label: "High use", variant: "red" });
    expect(seatChip({ ...base, status: "under", low_use: true }).label).toBe("Low use");
    expect(seatChip({ ...base, status: "new", in_grace: true })).toEqual({ label: "New seat", variant: "muted" });
    expect(seatChip(base).label).toBe("On track");
  });
});


describe("daily spend (Overview B1)", () => {
  it("the mock daily series covers the elapsed days and sums to the headline exactly", () => {
    const now = new Date("2026-10-14T09:00:00Z");
    const total = Math.round(ORG_SPENT * 1_000_000);
    const rolling = mockDailySpend(timeframeOf(rollingSelection(15, now)), total, now);
    // a rolling window starts mid-day, so N days span N+1 UTC days (the API does the same)
    expect(rolling).toHaveLength(16);
    expect(rolling[0].day).toBe("2026-09-29");
    expect(rolling[15].day).toBe("2026-10-14");
    expect(rolling.reduce((a, p) => a + p.spend_usd_micros, 0)).toBe(total);
    // an explicit window that is still running stops at today
    const running = mockDailySpend({ from: "2026-10-01T00:00:00Z", to: "2026-11-01T00:00:00Z" }, total, now);
    expect(running.map((p) => p.day)[0]).toBe("2026-10-01");
    expect(running).toHaveLength(14);
    expect(mockDailySpend({ from: "2026-11-01", to: "2026-12-01" }, total, now)).toEqual([]);
    // a finished budget period ends at the next period's midnight, which is not one of its days
    const sep = mockDailySpend({ from: "2026-09-01T00:00:00Z", to: "2026-10-01T00:00:00Z" }, total, now);
    expect(sep).toHaveLength(30);
    expect(sep[29].day).toBe("2026-09-30");
    expect(sep.reduce((a, p) => a + p.spend_usd_micros, 0)).toBe(total);
  });

  it("the mock overview carries daily_spend summing to its headline", async () => {
    const env = await new MockSource().orgOverview(timeframeOf(rollingSelection(30)));
    const data = env.data!;
    const sum = data.daily_spend.reduce((a, p) => a + p.spend_usd_micros, 0);
    expect(sum).toBe(data.headline.spend.usd_micros);
    expect("monthly_spend" in data).toBe(false);
  });
});

describe("agent task mix (Overview B2)", () => {
  it("the mock mix sums per type to the Usage session-type counts, one row per agent and type", () => {
    const mix = mockAgentTaskMix();
    const perType: Record<string, number> = {};
    for (const p of mix) perType[p.type] = (perType[p.type] ?? 0) + p.count;
    expect(perType).toEqual(Object.fromEntries(sessionTypes.map((t) => [t.type, t.count])));
    expect(new Set(mix.map((p) => `${p.agent}/${p.type}`)).size).toBe(mix.length);
    expect(mix.every((p) => p.count > 0)).toBe(true);
    // the agents are the mock's spend-by-agent agents
    expect([...new Set(mix.map((p) => p.agent))].sort()).toEqual(["Claude Code", "Codex", "Cursor"]);
  });

  it("the mock overview carries it", async () => {
    const env = await new MockSource().orgOverview(timeframeOf(rollingSelection(30)));
    expect(env.data!.agent_task_mix).toEqual(mockAgentTaskMix());
  });
});
