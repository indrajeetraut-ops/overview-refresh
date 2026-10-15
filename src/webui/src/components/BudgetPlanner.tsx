/**
 * The budget planner (issue #85, T7), org scope only: one row per team and project of the
 * forecast run with its current budget, what the period is expected to cost, the figure to plan
 * for (the cost at `planning_confidence`, actual spend included) and the gap between that and
 * the budget. Follows the preset selected in the forecast panel. A row expands to the forecast
 * tokens per model.
 *
 * It fetches `GET /v1/forecast/plan` itself, so it is fetched again when the screen's timeframe
 * changes (the Economics screen remounts its body then); the plan does not depend on the
 * timeframe, only the refetch does. No run loaded (`unavailable`) renders nothing -- no empty box.
 * Sorting by gap, the month and the open rows are local state.
 */
import { useState } from "react";

import { Block, resolveBlockState } from "./Block";
import { Chip, SectionHeading } from "./ui";
import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { fmtMoneyMicros } from "../present/format";
import {
  defaultPlanPeriod, effectivePresetId, fmtCompactUsd, fmtPlanTokens, gapSortLabel, groupPlanRows, nextSortDir, periodLabels,
  planForHeader, planGapLabel, planRows, sortPlanRows, tokensFootnote,
} from "../present/forecast";
import type { PlanTableRow, SortDir } from "../present/forecast";

type Plan = Schemas["BudgetPlan"];

const muted = { color: "var(--muted-foreground)" } as const;
const GRID = "sm:grid sm:grid-cols-[minmax(0,1.6fr)_repeat(3,minmax(0,1fr))_minmax(0,1.2fr)] sm:items-center sm:gap-3";

export default function BudgetPlanner({ presetId, cycleNote }: { presetId: string; cycleNote?: string | null }) {
  const [attempt, setAttempt] = useState(0);
  const state = useData((src) => src.forecastPlan(), [attempt]);
  const resolved = resolveBlockState(state);
  // No forecast run: say nothing rather than draw an empty box.
  if (resolved.kind === "unavailable" || resolved.kind === "cold-start") return null;
  if (resolved.kind === "ready" && resolved.data.rows.length === 0) return null;
  return (
    <Block state={state} minHeight={240} onRetry={() => setAttempt((a) => a + 1)}>
      {(plan) => <PlannerBody plan={plan} presetId={presetId} cycleNote={cycleNote} />}
    </Block>
  );
}

function SortButton({ dir, onClick }: { dir: SortDir; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={gapSortLabel(dir)}
      className="text-xs font-medium uppercase tracking-widest inline-flex items-center gap-1"
      style={{ color: "var(--foreground)", cursor: "pointer" }}
      title="Sort by gap: largest first, smallest first, then the original order">
      Gap<span aria-hidden>{dir === "desc" ? " ▼" : dir === "asc" ? " ▲" : " ↕"}</span>
    </button>
  );
}

function GapCell({ gap }: { gap: number | null }) {
  const label = planGapLabel(gap);
  if (label.tone === "none") return <span style={muted}>{label.text}</span>;
  return <Chip variant={label.tone === "over" ? "amber" : label.tone === "room" ? "green" : "muted"}>{label.text}</Chip>;
}

function PlannerBody({ plan, presetId, cycleNote }: { plan: Plan; presetId: string; cycleNote?: string | null }) {
  const [periodChoice, setPeriodChoice] = useState<number | null>(null);
  const [dir, setDir] = useState<SortDir>(null);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());

  const id = effectivePresetId(plan.presets, presetId);
  const periodIndex = Math.min(periodChoice ?? defaultPlanPeriod(plan.periods), Math.max(0, plan.periods.length - 1));
  const period = plan.periods[periodIndex];
  const labels = periodLabels(plan.periods);
  const presetName = plan.presets.find((p) => p.id === id)?.label ?? id;
  const groups = groupPlanRows(sortPlanRows(planRows(plan, id, periodIndex), dir));
  const toggle = (key: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (!next.delete(key)) next.add(key);
    return next;
  });

  return (
    <div>
      <SectionHeading title="What each team and project needs" sub="Budget planner" />
      <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
        <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:px-5">
          <span className="text-sm" style={muted}>Scenario: {presetName}</span>
          <div className="flex flex-wrap items-center gap-3">
            <span className="sm:hidden"><SortButton dir={dir} onClick={() => setDir(nextSortDir(dir))} /></span>
            <div role="group" aria-label="Planner month" className="flex gap-2">
              {labels.map((label, i) => (
                <button key={plan.periods[i].start} type="button" aria-pressed={i === periodIndex} onClick={() => setPeriodChoice(i)}
                  style={{
                    border: `1px solid ${i === periodIndex ? "var(--primary)" : "var(--border)"}`,
                    background: i === periodIndex ? "var(--primary)" : "var(--card)",
                    color: i === periodIndex ? "var(--primary-foreground)" : "var(--foreground)",
                    padding: "4px 10px", fontSize: 12, borderRadius: 6, cursor: "pointer",
                  }}>
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className={`hidden px-5 py-2 text-xs font-medium uppercase tracking-widest ${GRID}`}
          style={{ ...muted, borderTop: "1px solid var(--border)", borderBottom: "1px solid var(--border)" }}>
          <span>Name</span>
          <span className="text-right">Budget</span>
          <span className="text-right">Expected</span>
          <span className="text-right normal-case tracking-normal">{planForHeader(plan.planning_confidence)}</span>
          <span className="text-right">
            <SortButton dir={dir} onClick={() => setDir(nextSortDir(dir))} />
          </span>
        </div>

        {groups.map((g) => (
          <div key={g.group}>
            <div className="px-4 sm:px-5 py-2 text-xs font-medium uppercase tracking-widest"
              style={{ ...muted, background: "var(--muted)" }}>{g.group}</div>
            {g.rows.map((row) => (
              <PlanRowView key={row.key} row={row} confidence={plan.planning_confidence}
                open={open.has(row.key)} onToggle={() => toggle(row.key)}
                footnote={period ? tokensFootnote(period) : null} />
            ))}
          </div>
        ))}
      </div>
      {cycleNote && <p className="mt-2 text-xs" style={muted}>{cycleNote}</p>}
    </div>
  );
}

function PlanRowView({ row, confidence, open, onToggle, footnote }: {
  row: PlanTableRow; confidence: number; open: boolean; onToggle: () => void; footnote: string | null;
}) {
  const dollars = (v: number | null) => fmtCompactUsd(v);
  const detailId = `plan-${row.key}`;
  return (
    <div style={{ borderTop: "1px solid var(--border)" }}>
      <div className={`px-4 sm:px-5 py-3 ${GRID}`}>
        <div className="flex items-center justify-between gap-2 sm:justify-start">
          <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={detailId}
            className="text-left text-sm font-medium" style={{ color: "var(--foreground)", cursor: "pointer" }}>
            <span aria-hidden style={muted}>{open ? "▾ " : "▸ "}</span>{row.name}
          </button>
          <span className="sm:hidden"><GapCell gap={row.gap} /></span>
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2 text-sm sm:mt-0 sm:contents">
          <Figure label="Budget" className="sm:text-right">{fmtMoneyMicros(row.budget)}</Figure>
          <Figure label="Expected" className="sm:text-right">{dollars(row.expected)}</Figure>
          <Figure label={planForHeader(confidence)} className="sm:text-right">{dollars(row.planFor)}</Figure>
        </div>
        <div className="hidden sm:block sm:text-right"><GapCell gap={row.gap} /></div>
      </div>
      {open && (
        <div id={detailId} className="px-4 sm:px-5 pb-4 text-sm">
          {row.tokens.length === 0 ? (
            <p style={muted}>No token figures for this scenario.</p>
          ) : (
            <table className="w-full max-w-md text-sm">
              <thead>
                <tr className="text-xs" style={muted}>
                  <th scope="col" className="whitespace-nowrap text-left font-medium py-1 pr-3">Model</th>
                  <th scope="col" className="whitespace-nowrap text-right font-medium py-1 px-3">Expected tokens</th>
                  <th scope="col" className="whitespace-nowrap text-right font-medium py-1 pl-3">p90 tokens</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {row.tokens.map((t) => (
                  <tr key={t.model}>
                    <th scope="row" className="py-0.5 pr-3 text-left font-normal">{t.model}</th>
                    <td className="py-0.5 px-3 text-right whitespace-nowrap">{fmtPlanTokens(t.expected)}</td>
                    <td className="py-0.5 pl-3 text-right whitespace-nowrap">{fmtPlanTokens(t.p90)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {footnote && <p className="text-xs mt-2" style={muted}>{footnote}</p>}
        </div>
      )}
    </div>
  );
}

/** One figure: a labelled stat on a phone, a bare table cell from `sm` up. */
function Figure({ label, className = "", children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <div className={className}>
      <div className="text-xs sm:hidden" style={muted}>{label}</div>
      <div className="font-mono">{children}</div>
    </div>
  );
}
