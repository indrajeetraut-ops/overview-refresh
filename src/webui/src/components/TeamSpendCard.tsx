/**
 * Overview "Spend by team" card: one bar per team with a tick at that team's own budget, a value
 * label, red when over budget, and an "N teams over budget" pill. The numbers come from
 * `present/overview.ts` (`teamSpendBars`, `teamTip`, `teamLabelPlan`); this lays them out.
 */
import { useLayoutEffect, useRef, useState } from "react";
import {
  CartesianGrid, Bar, Cell, ComposedChart, LabelList, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis,
} from "recharts";
import { Card, Empty, Pill } from "./ui";
import { ChartTooltip } from "./chartTooltip";
import { MONO_FONT } from "../present/fonts";
import { fmtCompactUsd } from "../present/forecast";
import { TEAM_CHART_INSET, teamBarsLabel, teamLabelPlan, teamTip, teamsOverPill, truncateName } from "../present/overview";
import type { TeamBar } from "../present/overview";

const BAR = 28;
const TICK = { fontSize: 10, fill: "var(--muted-foreground)", fontFamily: MONO_FONT } as const;

/** The budget mark: a short horizontal line across the bar's column, at that team's budget. */
function BudgetTick({ cx, cy, payload, w }: { cx?: number; cy?: number; payload?: { budget?: number }; w: number }) {
  if (cx == null || cy == null || payload?.budget == null) return null;
  return <line x1={cx - w / 2} x2={cx + w / 2} y1={cy} y2={cy} stroke="var(--foreground)" strokeWidth={2} strokeLinecap="round" />;
}

const dollars = (v: unknown) => fmtCompactUsd(Number(v) * 1_000_000);

export default function TeamSpendCard({ bars, teamsOverBudget }: { bars: TeamBar[]; teamsOverBudget: number }) {
  const pill = teamsOverPill(teamsOverBudget);
  const rows = bars.map((b) => ({ ...b, budget: b.budget ?? undefined }));
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  // Measured before paint, so the first frame is already laid out for its width.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, [bars.length]);
  const plan = teamLabelPlan(width, bars.length);
  // Never wider than the column a bar has, so neighbouring ticks and bars do not run into each other.
  const perBar = width > 0 ? (width - TEAM_CHART_INSET) / Math.max(1, bars.length) : Infinity;
  const barSize = Math.max(6, Math.min(BAR, perBar - 4));
  const tickW = Math.max(8, Math.min(BAR + 12, perBar - 2));

  /** The value label: above the bar AND above that team's budget tick, so the two never meet (the
   * tick's height is recovered from the bar's own pixels: height per dollar). */
  const valueLabel = (p: { x?: number; y?: number; width?: number; height?: number; index?: number }) => {
    const r = p.index != null ? rows[p.index] : undefined;
    if (!r || p.x == null || p.y == null || p.width == null || p.height == null) return null;
    const tickY = r.spent > 0 && r.budget != null ? p.y + p.height - r.budget * (p.height / r.spent) : p.y;
    return (
      <text
        x={p.x + p.width / 2} y={Math.min(p.y, tickY) - 5} textAnchor="middle"
        style={{ ...TICK, fill: "var(--foreground)", paintOrder: "stroke", stroke: "var(--card)", strokeWidth: 3, strokeLinejoin: "round" }}
      >
        {dollars(r.spent)}
      </text>
    );
  };

  const anyOver = bars.some((b) => b.over);
  const anyUnder = bars.some((b) => !b.over);
  const anyBudget = bars.some((b) => b.budget != null);
  return (
    <Card title="Spend by team" aside={pill ? <Pill variant="red">{pill}</Pill> : undefined}>
      {bars.length === 0 ? (
        <Empty label="No teams in this window." />
      ) : (
        <>
          <div ref={box} role="img" aria-label={teamBarsLabel(bars)}>
            <ResponsiveContainer width="100%" height={plan.rotate ? 250 : 220}>
              <ComposedChart data={rows} margin={{ top: 18, right: 8, bottom: 0, left: -6 }} accessibilityLayer={false}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" vertical={false} />
                <XAxis
                  dataKey="name" interval={0} tick={TICK} axisLine={false} tickLine={false}
                  tickFormatter={(n: string) => truncateName(n, plan.chars)}
                  {...(plan.rotate ? { angle: -35, textAnchor: "end", height: 56 } : {})}
                />
                <YAxis tick={{ ...TICK, fontSize: 11 }} axisLine={false} tickLine={false} width={48} tickFormatter={dollars} />
                <Tooltip
                  isAnimationActive={false}
                  cursor={{ fill: "var(--muted)", opacity: 0.5 }}
                  content={<ChartTooltip describe={({ items }) => {
                    const row = items.find((i) => i.payload)?.payload as TeamBar | undefined;
                    return row ? teamTip(row) : null;
                  }} />}
                />
                <Bar dataKey="spent" barSize={barSize} radius={[3, 3, 0, 0]} isAnimationActive={false}>
                  {rows.map((r) => <Cell key={r.id} fill={r.over ? "var(--red)" : "var(--primary)"} opacity={0.9} />)}
                  {plan.valueLabels && <LabelList dataKey="spent" content={valueLabel as never} />}
                </Bar>
                <Scatter dataKey="budget" shape={(p: unknown) => <BudgetTick {...(p as object)} w={tickW} />} isAnimationActive={false} legendType="none" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
            {anyUnder && <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--primary)" }} />Spent</span>}
            {anyOver && <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--red)" }} />Over budget</span>}
            {anyBudget && <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block w-3 border-t-2" style={{ borderColor: "var(--foreground)" }} />Team budget</span>}
          </div>
        </>
      )}
    </Card>
  );
}
