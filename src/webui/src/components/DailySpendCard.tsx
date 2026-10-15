/**
 * Overview "Spend over time" card: a daily area chart over `daily_spend` (the window's elapsed UTC
 * days). Rows, labels and the aria summary come from `present/overview.ts`; micros become dollars
 * only there. One day is drawn as a dot, no days as an unavailable note.
 */
import { useId } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { UnavailableNote } from "./Block";
import { ChartTooltip } from "./chartTooltip";
import { Card } from "./ui";
import type { Schemas } from "../data/source";
import { MONO_FONT } from "../present/fonts";
import { fmtIsoDay } from "../present/forecast";
import { dailyAxisDomain, dailySpendAside, dailySpendLabel, dailySpendRows, fmtDailyAxis, fmtDailyUsd } from "../present/overview";

const TICK = { fontSize: 10, fill: "var(--muted-foreground)", fontFamily: MONO_FONT } as const;

export default function DailySpendCard({ daily }: { daily: Schemas["DailySpendPoint"][] }) {
  // `useId` gives ":r1:", which a url(#...) reference cannot use as is.
  const gradient = `spend-fill-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  if (daily.length === 0) {
    return (
      <Card title="Spend over time">
        <UnavailableNote minHeight={140} info={{ block: "daily_spend", requires: "session activity in range" }} />
      </Card>
    );
  }
  const rows = dailySpendRows(daily);
  const single = rows.length === 1;
  return (
    <Card title="Spend over time" aside={dailySpendAside(daily.length)}>
      <div role="img" aria-label={dailySpendLabel(daily)}>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={rows} margin={{ top: 8, right: 22, bottom: 0, left: -6 }} accessibilityLayer={false}>
            <defs>
              <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.32} />
                <stop offset="100%" stopColor="var(--primary)" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" vertical={false} />
            <XAxis dataKey="day" tick={TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={32} tickFormatter={fmtIsoDay} />
            <YAxis tick={{ ...TICK, fontSize: 11 }} axisLine={false} tickLine={false} width={48} domain={dailyAxisDomain(rows)} tickFormatter={(v) => fmtDailyAxis(Number(v))} />
            <Tooltip
              isAnimationActive={false}
              cursor={{ stroke: "var(--border)" }}
              content={<ChartTooltip describe={({ items }) => {
                const row = items.find((i) => i.payload)?.payload as { day: string; usd: number } | undefined;
                return row ? { title: fmtIsoDay(row.day), rows: [{ label: "Spend", value: fmtDailyUsd(row.usd), color: "var(--primary)" }] } : null;
              }} />}
            />
            <Area
              type="monotone" dataKey="usd" stroke="var(--primary)" strokeWidth={2} fill={`url(#${gradient})`}
              isAnimationActive={false}
              dot={single ? { r: 4, fill: "var(--primary)", stroke: "var(--card)", strokeWidth: 2 } : false}
              activeDot={{ r: 4, fill: "var(--primary)", stroke: "var(--card)", strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
