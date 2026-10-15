import React from "react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";

import type { Finding, Schemas } from "../data/source";
import { entityRoute } from "../present/routing";
import { assignAgentHues, categoryHue } from "../present/palette";
import { fmtPct } from "../present/format";
import { ALL_AGENTS, taskUsageBars, taskUsageTabs } from "../present/overview";
import type { AgentHues, BudgetTone, ModelSlice } from "../present/overview";
import { ChartTooltip } from "./chartTooltip";

// ── Chip / badge ────────────────────────────────────────────────
type ChipVariant = "green" | "amber" | "red" | "blue" | "muted";

export function Chip({ variant, children }: { variant: ChipVariant; children: React.ReactNode }) {
  // One line, ellipsised when the pill is narrower than its text; the full text stays in `title`.
  const parts = React.Children.toArray(children);
  const text = parts.every((c) => typeof c === "string" || typeof c === "number") ? parts.join("") : undefined;
  return (
    <span className={`chip chip-${variant}`} title={text}>
      <span className="min-w-0 truncate">{children}</span>
    </span>
  );
}

// ── Hero tile ───────────────────────────────────────────────────
export function HeroTile({
  label,
  value,
  sub,
  subVariant = "muted",
  large,
}: {
  label: string;
  value: string;
  sub?: string;
  subVariant?: ChipVariant;
  large?: boolean;
}) {
  // Figures stay on one line and shrink with the tile (cqi, see `.hero-tile`); a text value
  // ("Costly rework") may wrap between words instead of being cut off.
  const words = /[A-Za-z]{4,}/.test(value);
  return (
    <div className="hero-tile flex flex-col gap-2 min-w-0">
      <div className="text-[11px] font-medium uppercase tracking-wider leading-tight" style={{ color: "var(--muted-foreground)", minHeight: "2.5em" }}>
        {label}
      </div>
      <div
        className={`font-semibold tracking-tight ${words ? "break-words" : "whitespace-nowrap"}`}
        style={{
          fontSize: large ? "clamp(20px, 26cqi, 36px)" : words ? "clamp(16px, 16cqi, 22px)" : "clamp(18px, 22cqi, 28px)",
          color: "var(--foreground)", lineHeight: 1.15,
        }}
      >
        {value}
      </div>
      {sub && <Chip variant={subVariant}>{sub}</Chip>}
    </div>
  );
}

// ── Secondary stat strip ────────────────────────────────────────
export function StatStrip({ stats }: { stats: { label: string; value: string }[] }) {
  return (
    <div
      className="flex items-center gap-6 flex-wrap px-1"
      style={{ color: "var(--muted-foreground)", fontSize: 13 }}
    >
      {stats.map((s, i) => (
        <React.Fragment key={s.label}>
          {i > 0 && <span style={{ opacity: 0.4 }}>·</span>}
          <span>
            <span style={{ color: "var(--foreground)", fontWeight: 600 }}>{s.value}</span>
            {" "}
            <span>{s.label}</span>
          </span>
        </React.Fragment>
      ))}
    </div>
  );
}

// ── Overview v2 primitives (Track C) ─────────────────────────────────────────────────────
const TONE_COLOR: Record<BudgetTone, string> = {
  green: "var(--green)", amber: "var(--amber)", red: "var(--red)", muted: "var(--muted-foreground)",
};

// ── Card ─────────────────────────────────────────────────────────────────────────────────
/**
 * A compact grouped card: 10px uppercase title (and an optional right-hand aside) inside the card,
 * `p-3`, an optional footer pinned to the bottom. It is a labelled region, a size container (so
 * figures can scale with `cqi`, like `.hero-tile`) and deliberately NOT `overflow: hidden`, so a
 * chart tooltip can leave the card.
 */
export function Card({ title, aside, footer, children, className = "" }: {
  title?: React.ReactNode;
  aside?: React.ReactNode;
  footer?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  const id = React.useId();
  return (
    <section
      aria-labelledby={title ? id : undefined}
      className={`flex min-w-0 flex-col gap-2 rounded-lg p-3 ${className}`}
      // `color` is set here, not left to inherit: the page has no text colour of its own, so unstyled
      // text in a card would be black on the dark card.
      style={{ background: "var(--card)", color: "var(--foreground)", border: "1px solid var(--border)", containerType: "inline-size" }}
    >
      {(title || aside) && (
        <div className="flex items-baseline justify-between gap-2">
          {title && (
            <h2 id={id} className="min-w-0 text-[10px] font-medium uppercase tracking-wider leading-tight" style={{ color: "var(--muted-foreground)" }}>
              {title}
            </h2>
          )}
          {aside && (
            // Plain text asides are mono; a control (link, pill) keeps its own face.
            <span className={`shrink-0 text-[11px]${typeof aside === "string" ? " font-mono" : ""}`} style={{ color: "var(--muted-foreground)" }}>{aside}</span>
          )}
        </div>
      )}
      {children}
      {footer && (
        <div
          className="mt-auto flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 pt-2 text-xs"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          {footer}
        </div>
      )}
    </section>
  );
}

// ── Metric ───────────────────────────────────────────────────────────────────────────────
/** A labelled figure inside a card. `lg` is the headline number of a card; the value never wraps
 * and shrinks with the card (`cqi`), like `HeroTile`. */
export function Metric({ label, value, sub, size = "md" }: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  size?: "lg" | "md" | "sm";
}) {
  const fontSize = size === "lg" ? "clamp(24px, 15cqi, 34px)" : size === "md" ? "clamp(15px, 8cqi, 19px)" : "13px";
  return (
    <div className="min-w-0">
      <div className="text-[11px] leading-tight" style={{ color: "var(--muted-foreground)" }}>{label}</div>
      <div className="truncate whitespace-nowrap font-semibold tracking-tight" title={value} style={{ fontSize, lineHeight: 1.2, color: "var(--foreground)" }}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-[11px] leading-tight" style={{ color: "var(--muted-foreground)" }}>{sub}</div>}
    </div>
  );
}

// ── Pill ─────────────────────────────────────────────────────────────────────────────────
/** A status pill: the chip look with an optional leading dot. One line, ellipsised, full text in `title`. */
export function Pill({ variant, children, title, dot = false }: {
  variant: ChipVariant; children: React.ReactNode; title?: string; dot?: boolean;
}) {
  const text = title ?? (typeof children === "string" || typeof children === "number" ? String(children) : undefined);
  return (
    <span className={`chip chip-${variant}`} title={text}>
      {dot && <span aria-hidden className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: "currentColor" }} />}
      <span className="min-w-0 truncate">{children}</span>
    </span>
  );
}

// ── Spend gauge ──────────────────────────────────────────────────────────────────────────
/**
 * A semicircle gauge: `fraction` (0..1, already capped by the caller) of the arc is filled in the
 * tone's colour, `value` sits in the bowl, `caption` under it. It is one image for assistive
 * technology: `ariaLabel` must say everything the figure shows.
 */
export function SpendGauge({ fraction, tone, value, caption, ariaLabel }: {
  fraction: number; tone: BudgetTone; value: string; caption?: string; ariaLabel: string;
}) {
  const f = Math.max(0, Math.min(1, fraction));
  return (
    <div role="img" aria-label={ariaLabel} className="mx-auto w-full" style={{ maxWidth: 240 }}>
      <div className="relative">
        <svg viewBox="0 0 200 110" className="block w-full" aria-hidden>
          <path d="M20 100 A80 80 0 0 1 180 100" pathLength={100} fill="none" stroke="var(--muted)" strokeWidth={14} strokeLinecap="round" />
          {f > 0 && (
            <path
              d="M20 100 A80 80 0 0 1 180 100" pathLength={100} fill="none" stroke={TONE_COLOR[tone]} strokeWidth={14}
              strokeLinecap="round" strokeDasharray={`${f * 100} 100`}
            />
          )}
        </svg>
        <div className="absolute inset-x-0 bottom-0 flex justify-center">
          <span className="whitespace-nowrap font-semibold tracking-tight" style={{ fontSize: "clamp(18px, 12cqi, 28px)", lineHeight: 1.1, color: "var(--foreground)" }}>
            {value}
          </span>
        </div>
      </div>
      {caption && <div className="mt-1 text-center font-mono text-[11px]" style={{ color: "var(--muted-foreground)" }}>{caption}</div>}
    </div>
  );
}

// ── Model donut ──────────────────────────────────────────────────────────────────────────
/** The model-mix donut (total tokens in the middle) with its legend. The donut is one labelled
 * image; the legend is a list, and a rolled-up "+N other" row lists its models in its `title`. */
export function ModelDonut({ slices, total, totalLabel = "tokens", ariaLabel }: {
  slices: ModelSlice[]; total: string; totalLabel?: string; ariaLabel: string;
}) {
  return (
    <div className="flex min-w-0 flex-col items-center gap-2">
      <div role="img" aria-label={ariaLabel} className="relative shrink-0" style={{ width: 120, height: 120 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart accessibilityLayer={false}>
            <Pie rootTabIndex={-1} data={slices} dataKey="pct" nameKey="name" cx="50%" cy="50%" innerRadius={38} outerRadius={58} strokeWidth={0} paddingAngle={slices.length > 1 ? 2 : 0} isAnimationActive={false}>
              {slices.map((s) => <Cell key={s.key} fill={s.hue} />)}
            </Pie>
            <Tooltip
              isAnimationActive={false}
              content={<ChartTooltip describe={({ items }) => {
                const s = items[0]?.payload as ModelSlice | undefined;
                return s ? { title: s.name, rows: [{ label: "Share of tokens", value: fmtPct(s.pct), color: s.hue }, ...(s.title ? [{ label: "Includes", value: s.title }] : [])] } : null;
              }} />}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="whitespace-nowrap font-semibold" style={{ fontSize: total.length > 5 ? 14 : 17, color: "var(--foreground)" }}>{total}</span>
          <span className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>{totalLabel}</span>
        </div>
      </div>
      <ul className="m-0 flex w-full list-none flex-col gap-1 p-0">
        {slices.map((s) => (
          <li key={s.key} className="flex min-w-0 items-center gap-2 text-xs" title={s.title ?? `${s.name} ${fmtPct(s.pct)}`}>
            <span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: s.hue }} />
            <span className="min-w-0 truncate" style={{ color: "var(--foreground)" }}>{s.name}</span>
            <span className="ml-auto shrink-0 font-mono" style={{ color: "var(--muted-foreground)" }}>{fmtPct(s.pct)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Task usage ───────────────────────────────────────────────────────────────────────────
/**
 * "Task usage": tabs All + one per agent, one bar per session type, share by SESSION COUNT (the
 * same rule as Usage's breakdown, so "All" matches it for the same window). On "All" every bar is
 * split by agent (agent colours, small legend); on an agent's tab it is that type's own colour.
 * The tab is local state; the bars are `taskUsageBars` (pure).
 */
export function TaskUsageBars({ mix, agentHues }: {
  mix: Schemas["AgentTaskMix"][];
  /** The card-wide agent colours (`agentHueMap`), so these match the donut; derived from `mix` when omitted. */
  agentHues?: AgentHues;
}) {
  const uid = React.useId();
  const tabs = taskUsageTabs(mix);
  const [picked, setPicked] = React.useState(ALL_AGENTS);
  // An agent that is not in this window's mix falls back to "All" rather than an empty tab.
  const active = tabs.some((t) => t.id === picked) ? picked : ALL_AGENTS;
  const hues = agentHues ?? assignAgentHues(tabs.slice(1).map((t) => t.label));
  const bars = taskUsageBars(mix, active, hues);
  const refs = React.useRef<(HTMLButtonElement | null)[]>([]);
  const move = (to: number) => {
    const i = (to + tabs.length) % tabs.length;
    setPicked(tabs[i].id);
    refs.current[i]?.focus();
  };
  const onKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key === "ArrowRight") { e.preventDefault(); move(i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); move(i - 1); }
    else if (e.key === "Home") { e.preventDefault(); move(0); }
    else if (e.key === "End") { e.preventDefault(); move(tabs.length - 1); }
  };
  const agents = active === ALL_AGENTS
    ? tabs.slice(1).map((t) => ({ name: t.label, hue: hues[t.label] ?? categoryHue("agent", t.label) }))
    : [];
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div role="tablist" aria-label="Task usage by agent" className="flex flex-wrap gap-1">
        {tabs.map((t, i) => {
          const on = t.id === active;
          return (
            <button
              key={t.id} ref={(el) => { refs.current[i] = el; }} type="button" role="tab" id={`${uid}-tab-${i}`}
              aria-selected={on} aria-controls={`${uid}-panel`} tabIndex={on ? 0 : -1}
              onClick={() => setPicked(t.id)} onKeyDown={(e) => onKey(e, i)}
              className="rounded-md px-2 py-0.5 text-[11px] font-medium"
              style={{
                background: on ? "var(--primary)" : "var(--muted)",
                color: on ? "var(--primary-foreground)" : "var(--muted-foreground)",
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div id={`${uid}-panel`} role="tabpanel" tabIndex={0} aria-labelledby={`${uid}-tab-${Math.max(0, tabs.findIndex((t) => t.id === active))}`}>
        {bars.length === 0 ? (
          <div className="py-2 text-xs" style={{ color: "var(--muted-foreground)" }}>No classified sessions in this window.</div>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {bars.map((b) => (
              <li key={b.type} title={b.title}>
                {/* The counts, per agent on "All", are in the text for assistive technology (no hover needed). */}
                <span className="sr-only">{`${b.title}, ${fmtPct(b.share)} of sessions`}</span>
                <div aria-hidden className="flex items-baseline justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate" style={{ color: "var(--foreground)" }}>{b.name}</span>
                  <span className="shrink-0 font-mono" style={{ color: "var(--muted-foreground)" }}>{fmtPct(b.share)}</span>
                </div>
                <div aria-hidden className="mt-0.5 h-2 rounded-sm" style={{ background: "var(--muted)" }}>
                  <div className="flex h-full overflow-hidden rounded-sm" style={{ width: `${Math.max(2, b.length * 100)}%` }}>
                    {b.segments.map((g) => <div key={g.agent} style={{ flexGrow: g.count, flexBasis: 0, background: g.hue }} />)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        {agents.length > 1 && bars.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
            {agents.map((a) => (
              <span key={a.name} className="flex items-center gap-1.5">
                <span aria-hidden className="h-2 w-2 rounded-sm" style={{ background: a.hue }} />{a.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Link button ──────────────────────────────────────────────────────────────────────────
/** A small text link that navigates ("See forecast detail →"); the arrow is decoration. */
export function LinkButton({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="cursor-pointer self-start text-xs font-medium hover:underline" style={{ color: "var(--primary)" }}>
      {children}<span aria-hidden> →</span>
    </button>
  );
}

// ── Section heading ─────────────────────────────────────────────
export function SectionHeading({ title, sub }: { title: string; sub?: string }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-widest mb-1" style={{ color: "var(--muted-foreground)" }}>
        {sub}
      </div>
      <h2 className="font-semibold" style={{ fontSize: 20, color: "var(--foreground)" }}>
        {title}
      </h2>
    </div>
  );
}

// ── Worth a look list ────────────────────────────────────────────
// Findings carry a typed entity ref (plan section 1), never a screen/screenParam -- the
// client decides the route here, via U2's entityRoute (present/routing.ts).
export function WorthALook({
  items,
  onNavigate,
}: {
  items: Finding[];
  onNavigate: (screen: string, param?: string) => void;
}) {
  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ border: "1px solid var(--border)", background: "var(--card)" }}
    >
      {items.map((item, i) => {
        const severity = item.severity as "red" | "amber" | "green";
        return (
          <div
            key={item.id}
            className="flex items-center justify-between gap-4 px-5 py-3.5 row-hover"
            style={{ borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
          >
            <div className="flex items-center gap-3 min-w-0">
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ background: severity === "red" ? "var(--red)" : severity === "amber" ? "var(--amber)" : "var(--green)" }}
              />
              <span style={{ fontSize: 13.5, color: "var(--foreground)" }}>{item.text}</span>
            </div>
            <button
              onClick={() => {
                const route = entityRoute({ kind: item.entity.kind as never, id: item.entity.id ?? undefined });
                onNavigate(route.screen, route.param);
              }}
              className="shrink-0 text-xs font-medium px-3 py-1.5 rounded"
              style={{ background: "var(--muted)", color: "var(--foreground)" }}
            >
              {item.action} →
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ── Progress bar ────────────────────────────────────────────────
export function ProgressBar({ value, max, variant }: { value: number; max: number; variant?: ChipVariant }) {
  const pct = Math.min(100, (value / max) * 100);
  const color =
    variant === "red" ? "var(--red)" :
    variant === "amber" ? "var(--amber)" :
    variant === "green" ? "var(--green)" :
    "var(--primary)";
  return (
    <div className="progress-bar flex-1">
      <div className="progress-fill" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

// ── Model usage bars ────────────────────────────────────────────
// QW1 (mvp-gap-closure-plan): Session/PR's per-model token share (`ModelUsage[]`, F0-typed).
// `tier` is a category on the wire (schemas/README strip-rule 1); this is where it becomes a
// colour, same pattern as every other `categoryHue` call site.
export type ModelUsageEntry = { model: string; pct: number; tier: string };

export function ModelUsageBars({ entries }: { entries: ModelUsageEntry[] }) {
  return (
    <div className="rounded-lg p-5 flex flex-col gap-4" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
      {entries.map((m) => (
        <div key={m.model}>
          <div className="flex items-center justify-between mb-1 text-sm">
            <span style={{ color: "var(--foreground)" }}>{m.model}</span>
            <span className="font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>{fmtPct(m.pct)}</span>
          </div>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${m.pct * 100}%`, background: categoryHue("modelTier", m.tier) }} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Avatar ───────────────────────────────────────────────────────
export function Avatar({ initials, size = 32 }: { initials: string; size?: number }) {
  return (
    <div
      className="flex items-center justify-center rounded-full font-mono font-medium shrink-0"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.35,
        background: "var(--muted)",
        color: "var(--muted-foreground)",
        letterSpacing: "0.03em",
      }}
    >
      {initials}
    </div>
  );
}

// ── Back button ──────────────────────────────────────────────────
export function BackButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 text-sm font-medium mb-6"
      style={{ color: "var(--muted-foreground)" }}
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
        <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {label}
    </button>
  );
}

// ── Screen headline ──────────────────────────────────────────────
export function ScreenHeadline({ headline, cta, onCta }: { headline: string; cta?: string; onCta?: () => void }) {
  return (
    <div className="flex items-start justify-between gap-4 mb-8">
      <h1 className="font-serif" style={{ fontSize: 26, color: "var(--foreground)", lineHeight: 1.25, maxWidth: 680 }}>
        {headline}
      </h1>
      {cta && (
        <button
          onClick={onCta}
          className="shrink-0 px-4 py-2 rounded text-sm font-medium"
          style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
        >
          {cta}
        </button>
      )}
    </div>
  );
}

// ── Divider ──────────────────────────────────────────────────────
export function Divider() {
  return <div className="divider my-6" />;
}

// ── Empty ────────────────────────────────────────────────────────
export function Empty({ label }: { label: string }) {
  return (
    <div className="py-10 text-center text-sm" style={{ color: "var(--muted-foreground)" }}>
      {label}
    </div>
  );
}

// ── Format helpers ────────────────────────────────────────────────
// Canonical impls live in the presentation layer (U2); re-exported here so existing
// `import { fmtUSD } from "../components/ui"` call sites keep working.
export { fmtUSD, fmtPct, fmtTokens, fmtDuration, fmtRelativeTime, fmtDays, fmtDate, fmtCount, fmtMoneyMicros } from "../present/format";

// ── Work quality label ───────────────────────────────────────────
const qualityVariants: Record<string, ChipVariant> = {
  Clean: "green",
  "Heavy lift": "blue",
  "Quick & dirty": "amber",
  "Costly rework": "red",
};

export function QualityChip({ label }: { label: string }) {
  return <Chip variant={qualityVariants[label] ?? "muted"}>{label}</Chip>;
}

// ── Lifecycle timeline (milestone-anchored, reused by Session/PR/Issue) ──
//: Below this x-position gap (of 100), two milestone labels would visually overlap -- the
//: later one drops to a second row instead (real session/PR data clusters milestones far
//: more tightly than the mockup's evenly-spaced examples ever did).
const LABEL_COLLISION_GAP = 18;
const LABEL_ROW_HEIGHT = 20;

export function LifecycleTimeline({
  milestones,
}: {
  milestones: { name: string; ts: string | null | undefined; x: number; spanLabel?: string; spanKind?: "build" | "review" | "rework" }[];
}) {
  // Stagger a label onto a second row whenever it would sit too close to the previous
  // *rendered* label's own x (not just its immediate neighbor -- three milestones in a row
  // within the gap must still alternate, not all claim row 0 against their own predecessor).
  let lastLabelX = -Infinity;
  const rows = milestones.map((m) => {
    const row = m.x - lastLabelX < LABEL_COLLISION_GAP ? 1 : 0;
    lastLabelX = m.x;
    return row;
  });
  const height = 38 + (rows.some((r) => r === 1) ? 2 : 1) * LABEL_ROW_HEIGHT + 10;

  return (
    <div className="relative" style={{ height }}>
      {milestones.map((m, i) => {
        const next = milestones[i + 1];
        const reached = m.ts != null;
        const first = i === 0, last = i === milestones.length - 1;
        const spanColor = m.spanKind === "build" ? "var(--primary)" : m.spanKind === "rework" ? "var(--amber)" : "var(--muted)";
        const anchor = first ? { left: 0, alignItems: "flex-start" as const }
          : last ? { right: 0, alignItems: "flex-end" as const }
          : { left: `${m.x}%`, transform: "translateX(-50%)", alignItems: "center" as const };
        return (
          <div key={m.name}>
            {next && (
              <div className="absolute rounded-full" style={{ top: 22, left: `${m.x}%`, width: `${next.x - m.x}%`, height: 6, background: spanColor }} />
            )}
            {next && m.spanLabel && (
              <div className="absolute text-xs whitespace-nowrap" style={{ top: -4, left: `${(m.x + next.x) / 2}%`, transform: "translateX(-50%)", color: m.spanKind === "review" ? "var(--muted-foreground)" : "var(--foreground)" }}>{m.spanLabel}</div>
            )}
            <div className="absolute rounded-full" style={{ top: 19, left: `${m.x}%`, transform: "translateX(-50%)", width: 12, height: 12, background: reached ? "var(--foreground)" : "var(--card)", border: `2px solid ${reached ? "var(--foreground)" : "var(--muted-foreground)"}` }} />
            <div className="absolute flex flex-col" style={{ top: 38 + rows[i] * LABEL_ROW_HEIGHT, ...anchor }}>
              <span className="text-xs font-medium whitespace-nowrap" style={{ color: reached ? "var(--foreground)" : "var(--muted-foreground)" }}>{m.name}</span>
              <span className="font-mono whitespace-nowrap" style={{ fontSize: 10.5, color: "var(--muted-foreground)" }}>{m.ts ?? "not yet"}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Scope picker (Org / Team / Project) for unit economics ──────
export function ScopePicker({ value, onChange, scopes, label = "Scope" }: {
  value: string;
  onChange: (id: string) => void;
  /** The select's accessible name (it has no visible label of its own). */
  label?: string;
  scopes: { id: string; label: string; group: "" | "Teams" | "Projects" }[];
}) {
  const org = scopes.filter((s) => s.group === "");
  const teamOpts = scopes.filter((s) => s.group === "Teams");
  const projOpts = scopes.filter((s) => s.group === "Projects");
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="text-xs font-mono rounded px-2 py-1.5 cursor-pointer"
      style={{ background: "var(--muted)", color: "var(--foreground)", border: "1px solid var(--border)" }}
    >
      {org.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
      {teamOpts.length > 0 && <optgroup label="Teams">{teamOpts.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>}
      {projOpts.length > 0 && <optgroup label="Projects">{projOpts.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>}
    </select>
  );
}
