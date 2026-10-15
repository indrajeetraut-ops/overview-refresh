import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  ComposedChart, Area, Scatter, ReferenceLine, ReferenceArea,
} from "recharts";
import { fmtUSD, Chip } from "./ui";
import type { Dist } from "../data/mock";
import { fmtSpanDays } from "../present/format";
import { MONO_FONT } from "../present/fonts";
import { assignLabelLanes, percentileScale } from "../present/percentile";
import { burnUpLegend, dailyLegend, fmtCompactUsd, fmtIsoDay } from "../present/forecast";
import type { BurnUpMarkers, BurnUpRow, DailyRow } from "../present/forecast";

// The shared tooltip (Overview refresh Track C) lives in its own file; see `chartTooltip.tsx`.
export { ChartTooltip, ChartTooltipBox } from "./chartTooltip";
export type { TipContent, TipItem, TipRow } from "./chartTooltip";

const tooltipStyle = {
  background: "var(--card)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  fontSize: 12,
  fontFamily: MONO_FONT,
} as const;

// ── Spend by agent (donut) ───────────────────────────────────────
export function AgentDonut({ data, bare = false }: {
  data: { name: string; value: number; fill: string }[];
  /** Inside a card that already has its own border and padding: no box of its own; the list drops
   * under the donut as soon as it would have less than ~236px beside it (room for a 14-character
   * name, its dollars and its share). */
  bare?: boolean;
}) {
  const total = data.reduce((s, d) => s + d.value, 0);
  // Nothing to split (no spend, or only credits): a muted placeholder ring and a dash, no tooltip.
  const empty = !(total > 0);
  return (
    <div
      className={bare ? "flex flex-wrap items-center gap-4" : "rounded-lg p-5 flex items-center gap-5"}
      style={bare ? undefined : { border: "1px solid var(--border)", background: "var(--card)", height: 200 }}
    >
      <div className="relative shrink-0" style={{ width: 130, height: 130 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart accessibilityLayer={false}>
            <Pie
              rootTabIndex={-1} data={empty ? [{ name: "", value: 1, fill: "var(--muted)" }] : data} cx="50%" cy="50%"
              innerRadius={40} outerRadius={62} dataKey="value" strokeWidth={0} paddingAngle={empty ? 0 : 2}
            >
              {(empty ? [{ name: "", fill: "var(--muted)" }] : data).map((d) => <Cell key={d.name} fill={d.fill} />)}
            </Pie>
            {!empty && <Tooltip formatter={(v: unknown, name: unknown) => [fmtUSD(v as number), String(name)]} contentStyle={tooltipStyle} separator=": " />}
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="font-semibold whitespace-nowrap" style={{ fontSize: empty ? 18 : fmtUSD(total).length >= 10 ? 11 : fmtUSD(total).length >= 7 ? 14 : 18, color: empty ? "var(--muted-foreground)" : "var(--foreground)" }}>{empty ? "—" : fmtUSD(total)}</span>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>total</span>
        </div>
      </div>
      <div className={`flex flex-col gap-2 min-w-0${bare ? " flex-1 basis-[236px]" : ""}`}>
        {data.map((d) => (
          <div key={d.name} className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: d.fill }} />
            <span className="text-sm min-w-0 truncate" title={d.name} style={{ color: "var(--foreground)" }}>{d.name}</span>
            <span className="font-mono text-xs ml-auto shrink-0" style={{ color: "var(--muted-foreground)" }}>
              {fmtUSD(d.value)} · {total > 0 ? `${Math.round((d.value / total) * 100)}%` : "—"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Percentile number-line (min · p50 · p70 · p95 · max) ─────────
// Every mark sits at its true value; labels are pinned under their marks,
// so the right-skew (long tail pulling max far right) is visible and honest.
const PB_TRACK_H = 24;        // tick row: baseline at 12, ticks 4..20
const PB_LANE_H = 30;         // name + value, two lines
const PB_CHAR_W = 6.4;        // JetBrains Mono advance is 0.6em (6.3px @ 10.5px); 6.4 keeps a hair of slack

export function PercentileBar({ label, unit, d }: { label: string; unit: "$" | "d"; d: Dist }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { pos, log } = percentileScale(d.min, d.max);
  const fmt = (v: number) => (unit === "$" ? `$${Math.round(v).toLocaleString()}` : fmtSpanDays(v));
  const marks: { name: string; v: number; kind: "end" | "median" | "p" | "tail" }[] = [
    { name: "min", v: d.min, kind: "end" },
    { name: "p50", v: d.p50, kind: "median" },
    { name: "p70", v: d.p70, kind: "p" },
    { name: "p95", v: d.p95, kind: "tail" },
    { name: "max", v: d.max, kind: "end" },
  ];
  const { placed, lanes } = assignLabelLanes(
    marks.map((m) => ({
      center: (pos(m.v) / 100) * width,
      width: Math.max(m.name.length, fmt(m.v).length) * PB_CHAR_W,
    })),
    width,
  );
  const tickColor = (k: string) => (k === "median" ? "var(--foreground)" : k === "tail" ? "var(--amber)" : "var(--muted-foreground)");

  return (
    <div>
      <div className="flex items-center justify-between mb-3 gap-2">
        <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
          {label}
          {log && <span className="font-mono ml-2" style={{ fontSize: 10, color: "var(--muted-foreground)" }}>log scale</span>}
        </span>
        <Chip variant="muted">avg {fmt(d.avg)}</Chip>
      </div>
      <div ref={ref} className="relative" style={{ height: PB_TRACK_H + lanes * PB_LANE_H }}>
        {/* baseline */}
        <div className="absolute" style={{ top: 11, left: 0, right: 0, height: 2, background: "var(--border)", borderRadius: 2 }} />
        {/* shaded core p50 → p70 */}
        <div className="absolute" style={{ top: 6, height: 12, left: `${pos(d.p50)}%`, width: `${pos(d.p70) - pos(d.p50)}%`, background: "color-mix(in srgb, var(--primary) 22%, transparent)", borderRadius: 2 }} />
        {/* avg diamond */}
        <div className="absolute" style={{ top: 8, left: `${pos(d.avg)}%`, width: 8, height: 8, background: "var(--muted-foreground)", transform: "translate(-50%,0) rotate(45deg)" }} title={`avg ${fmt(d.avg)}`} />
        {width > 0 && marks.map((m, i) => {
          const x = (pos(m.v) / 100) * width;
          const { left, lane } = placed[i];
          const labelTop = PB_TRACK_H + lane * PB_LANE_H;
          const w = Math.max(m.name.length, fmt(m.v).length) * PB_CHAR_W;
          const align = x <= left + 1 ? "left" : x >= left + w - 1 ? "right" : "center";
          return (
            <div key={m.name}>
              {/* tick */}
              <span className="absolute" style={{ top: 4, left: x, width: m.kind === "median" ? 2 : 1.5, height: 16, background: tickColor(m.kind), borderRadius: 1, transform: "translateX(-50%)" }} />
              {/* leader from the tick down to a stacked label */}
              {lane > 0 && (
                <span className="absolute" style={{ top: 20, left: x, width: 1, height: labelTop - 20, background: "var(--border)", transform: "translateX(-50%)" }} />
              )}
              <div className="absolute flex flex-col font-mono" title={`${m.name} ${fmt(m.v)}`}
                style={{ top: labelTop + 2, left, width: w, textAlign: align, lineHeight: 1.25, background: lane > 0 ? "var(--card)" : undefined }}>
                <span style={{ fontSize: 10, color: "var(--muted-foreground)" }}>{m.name}</span>
                <span style={{ fontSize: 10.5, color: "var(--foreground)", whiteSpace: "nowrap" }}>{fmt(m.v)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Cost-per-PR envelope with forecast cone ──────────────────────
type EnvPoint = Record<string, number | string | boolean | null | undefined>;
function EnvelopeLegend({ compact = false }: { compact?: boolean }) {
  const line = (color: string, dash: boolean) => (
    <span style={{ width: 14, height: 0, borderTop: `2px ${dash ? "dashed" : "solid"} ${color}`, display: "inline-block" }} />
  );
  const item = (sw: ReactNode, t: string) => (
    <span className="flex items-center gap-1.5">{sw}<span>{t}</span></span>
  );
  if (compact) {
    // The four things the Overview card's old caption named, as a legend.
    return (
      <div className="flex items-center gap-x-4 gap-y-1 flex-wrap pt-2 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
        {item(line("var(--primary)", false), "median")}
        {item(<span style={{ width: 14, height: 9, borderRadius: 2, background: "color-mix(in srgb, var(--primary) 16%, transparent)", display: "inline-block" }} />, "p25–p75 band")}
        {item(line("var(--muted-foreground)", true), "p90/p99 tail")}
        {item(line("var(--amber)", true), "forecast cone")}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-x-5 gap-y-1 flex-wrap px-3 pt-2 text-xs" style={{ color: "var(--muted-foreground)" }}>
      {item(line("var(--primary)", false), "median")}
      {item(<span style={{ width: 14, height: 9, borderRadius: 2, background: "color-mix(in srgb, var(--primary) 16%, transparent)", display: "inline-block" }} />, "p25–p75")}
      {item(line("var(--muted-foreground)", true), "p90 / p99")}
      {item(<span style={{ width: 8, height: 8, borderRadius: 99, background: "var(--red)", display: "inline-block" }} />, "outlier PR")}
      {item(line("var(--amber)", true), "forecast")}
    </div>
  );
}

export function CostEnvelopeChart({ data, height = 200, compact = false }: { data: EnvPoint[]; height?: number; compact?: boolean }) {
  return (
    <div>
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data as any[]} margin={{ top: 8, right: 16, bottom: 0, left: -6 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} interval={2} />
        <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} width={compact ? 46 : 52} tickFormatter={(v) => `$${v}`} />
        <Tooltip contentStyle={tooltipStyle} />
        {/* historical p25–p75 band (stacked: transparent floor + colored band) */}
        <Area dataKey="bandLo" stackId="hist" stroke="none" fill="transparent" isAnimationActive={false} />
        <Area dataKey="band" stackId="hist" stroke="none" fill="color-mix(in srgb, var(--primary) 14%, transparent)" isAnimationActive={false} />
        {/* forecast cone */}
        <Area dataKey="fBandLo" stackId="fc" stroke="none" fill="transparent" isAnimationActive={false} />
        <Area dataKey="fBand" stackId="fc" stroke="none" fill="color-mix(in srgb, var(--amber) 18%, transparent)" isAnimationActive={false} />
        {/* p90 / p99 tail lines */}
        <Line dataKey="p99" stroke="var(--muted-foreground)" strokeWidth={1} strokeDasharray="2 3" dot={false} opacity={0.5} />
        <Line dataKey="p90" stroke="var(--muted-foreground)" strokeWidth={1} strokeDasharray="4 3" dot={false} opacity={0.7} />
        {/* median + forecast median */}
        <Line dataKey="median" stroke="var(--primary)" strokeWidth={2} dot={false} />
        <Line dataKey="fMedian" stroke="var(--amber)" strokeWidth={2} strokeDasharray="5 4" dot={false} />
        {/* outliers */}
        <Scatter dataKey="outlier" fill="var(--red)" />
      </ComposedChart>
    </ResponsiveContainer>
    <EnvelopeLegend compact={compact} />
    </div>
  );
}

// ── Month-end budget burn-up ─────────────────────────────────────
export function BudgetBurn({ data, budget, height = 180 }: { data: { day: string; actual?: number; projected?: number }[]; budget?: number; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -6 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" vertical={false} />
        <XAxis dataKey="day" tick={{ fontSize: 10, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} interval={4} />
        <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} width={44} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => [fmtUSD(v as number), ""]} />
        {budget != null && (
          <ReferenceLine y={budget} stroke="var(--red)" strokeDasharray="4 3" label={{ value: "budget", position: "insideTopRight", fontSize: 10, fill: "var(--red)", fontFamily: MONO_FONT }} />
        )}
        <Line dataKey="actual" stroke="var(--primary)" strokeWidth={2} dot={false} />
        <Line dataKey="projected" stroke="var(--amber)" strokeWidth={2} strokeDasharray="5 4" dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Session type distribution (stacked bar + legend) ─────────────
export function SessionTypeBar({ data }: { data: { type: string; count: number; color: string }[] }) {
  const total = data.reduce((s, d) => s + d.count, 0);
  return (
    <div>
      <div className="flex rounded-md overflow-hidden" style={{ height: 22 }}>
        {data.map((d) => (
          <div key={d.type} style={{ width: `${(d.count / total) * 100}%`, background: d.color }} title={`${d.type}: ${d.count}`} />
        ))}
      </div>
      <div className="flex items-center gap-x-5 gap-y-2 flex-wrap mt-3">
        {data.map((d) => (
          <div key={d.type} className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: d.color }} />
            <span className="text-sm" style={{ color: "var(--foreground)" }}>{d.type}</span>
            <span className="font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>{Math.round((d.count / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Context size over the session (with compaction resets) ───────
export function ContextCurve({ data, height = 180 }: { data: { t: string; ctx: number; compaction?: boolean }[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 16, bottom: 0, left: -6 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" vertical={false} />
        <XAxis dataKey="t" tick={{ fontSize: 10.5, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} width={44} tickFormatter={(v) => `${v}k`} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => [`${v as number}k tokens`, "context"]} />
        {data.filter((d) => d.compaction).map((d, i) => (
          <ReferenceLine key={i} x={d.t} stroke="var(--green)" strokeDasharray="3 3"
            label={{ value: "compaction", fontSize: 9, fill: "var(--green)", position: "top", fontFamily: MONO_FONT }} />
        ))}
        <Line dataKey="ctx" stroke="var(--amber)" strokeWidth={2} dot={{ r: 2, fill: "var(--amber)" }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Forecast: burn-up with the likely range, and the daily fan ───────────────────────────
// Rows come from `present/forecast.ts` (micro-USD, one per day); the charts only draw them.
// The band is two stacked areas (a transparent floor at `low`, then `high - low`), so the
// tooltip reads the row itself rather than the helper series.
type Band = { legend: string; tooltip: string };
const FC_TICK = { fontSize: 10, fill: "var(--muted-foreground)", fontFamily: MONO_FONT } as const;
// Strong enough to read on the dark card as well as the light one.
const FC_BAND_FILL = "color-mix(in srgb, var(--amber) 28%, transparent)";

type TipProps = { active?: boolean; label?: unknown; payload?: readonly { payload?: Record<string, number | string | undefined> }[] };

// TODO(tooltip): move ForecastTip (and the default tooltips of the other charts here) onto the shared ChartTooltip.
// It changes how Economics' tooltips look, so it is a deliberate follow-up, not part of the Overview layout.
function ForecastTip({ active, label, payload, lines }: TipProps & {
  lines: { key: string; name: string }[];
}) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const num = (k: string) => (typeof row[k] === "number" ? (row[k] as number) : null);
  const low = num("low"), high = num("high");
  return (
    <div style={{ ...tooltipStyle, padding: "8px 10px" }}>
      <div style={{ color: "var(--muted-foreground)", marginBottom: 4 }}>{fmtIsoDay(String(label))}</div>
      {lines.map(({ key, name }) => num(key) != null && (
        <div key={key}>{name}: <strong>{fmtCompactUsd(num(key))}</strong></div>
      ))}
      {low != null && high != null && low !== high && (
        <div>Likely range: <strong>{fmtCompactUsd(low)}–{fmtCompactUsd(high)}</strong></div>
      )}
    </div>
  );
}

function ForecastLegend({ items }: { items: { swatch: ReactNode; text: string; title?: string }[] }) {
  return (
    <div className="flex items-center gap-x-5 gap-y-1 flex-wrap px-3 pt-2 text-xs" style={{ color: "var(--muted-foreground)" }}>
      {items.map((i) => (
        <span key={i.text} className="flex items-center gap-1.5" title={i.title}>{i.swatch}<span>{i.text}</span></span>
      ))}
    </div>
  );
}

const fcLine = (color: string, dash: boolean, opacity = 1) => (
  <span style={{ width: 14, height: 0, borderTop: `2px ${dash ? "dashed" : "solid"} ${color}`, display: "inline-block", opacity }} />
);
const fcBox = (color: string) => (
  <span style={{ width: 14, height: 9, borderRadius: 2, background: color, display: "inline-block" }} />
);

export function BurnUpChart({ rows, markers, band, showBaseline, height = 260 }: {
  rows: BurnUpRow[]; markers: BurnUpMarkers; band: Band; showBaseline: boolean; height?: number;
}) {
  const lines = [
    { key: "actual", name: "Spent so far" }, { key: "expected", name: "Expected" },
    ...(showBaseline ? [{ key: "baselineExpected", name: "Current trajectory" }] : []),
  ];
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: -6 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" vertical={false} />
          <XAxis dataKey="date" tick={FC_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={28} tickFormatter={fmtIsoDay} />
          <YAxis tick={{ ...FC_TICK, fontSize: 11 }} axisLine={false} tickLine={false} width={52} tickFormatter={fmtCompactUsd} />
          <Tooltip content={(p) => <ForecastTip {...(p as TipProps)} lines={lines} />} />
          {markers.range && (
            <ReferenceArea x1={markers.range.from} x2={markers.range.to} fill="var(--red)" fillOpacity={0.1} stroke="none" />
          )}
          {markers.runsOutOn && (
            <ReferenceLine x={markers.runsOutOn} stroke="var(--red)" strokeWidth={1.5}
              label={{ value: "runs out", position: "insideTopLeft", fontSize: 10, fill: "var(--red)", fontFamily: MONO_FONT }} />
          )}
          {markers.budget != null && (
            <ReferenceLine y={markers.budget} stroke="var(--red)" strokeDasharray="4 3" ifOverflow="extendDomain"
              label={{ value: "budget", position: "insideTopRight", fontSize: 10, fill: "var(--red)", fontFamily: MONO_FONT }} />
          )}
          <Area dataKey="bandBase" stackId="band" stroke="none" fill="transparent" tooltipType="none" isAnimationActive={false} />
          <Area dataKey="bandSpan" stackId="band" stroke="none" fill={FC_BAND_FILL} tooltipType="none" isAnimationActive={false} />
          {showBaseline && (
            <Line dataKey="baselineExpected" stroke="var(--muted-foreground)" strokeWidth={1.5} strokeDasharray="3 3" strokeOpacity={0.5} dot={false} isAnimationActive={false} />
          )}
          <Line dataKey="actual" stroke="var(--primary)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="expected" stroke="var(--amber)" strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <ForecastLegend items={burnUpLegend(rows, markers, showBaseline).map((k) => ({
        actual: { swatch: fcLine("var(--primary)", false), text: "Spent so far" },
        expected: { swatch: fcLine("var(--amber)", true), text: "Expected" },
        band: { swatch: fcBox(FC_BAND_FILL), text: band.legend, title: band.tooltip },
        baseline: { swatch: fcLine("var(--muted-foreground)", true, 0.5), text: "Current trajectory" },
        budget: { swatch: fcLine("var(--red)", true), text: "Budget" },
        runway: { swatch: fcBox("color-mix(in srgb, var(--red) 12%, transparent)"), text: "When it could run out", title: "Range of dates the budget could run out, given that it does" },
      }[k]))} />
    </div>
  );
}

export function DailyFanChart({ rows, band, height = 220 }: { rows: DailyRow[]; band: Band; height?: number }) {
  const lines = [{ key: "actual", name: "Actual" }, { key: "expected", name: "Expected" }];
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: -6 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" vertical={false} />
          <XAxis dataKey="date" tick={FC_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={28} tickFormatter={fmtIsoDay} />
          <YAxis tick={{ ...FC_TICK, fontSize: 11 }} axisLine={false} tickLine={false} width={52} tickFormatter={fmtCompactUsd} />
          <Tooltip content={(p) => <ForecastTip {...(p as TipProps)} lines={lines} />} />
          <Area dataKey="bandBase" stackId="band" stroke="none" fill="transparent" tooltipType="none" isAnimationActive={false} />
          <Area dataKey="bandSpan" stackId="band" stroke="none" fill={FC_BAND_FILL} tooltipType="none" isAnimationActive={false} />
          <Line dataKey="actual" stroke="var(--primary)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="expected" stroke="var(--amber)" strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <ForecastLegend items={dailyLegend(rows).map((k) => ({
        actual: { swatch: fcLine("var(--primary)", false), text: "Actual" },
        expected: { swatch: fcLine("var(--amber)", true), text: "Expected" },
        band: { swatch: fcBox(FC_BAND_FILL), text: band.legend, title: band.tooltip },
      }[k]))} />
    </div>
  );
}
