/**
 * The Economics screen's budget forecast (issue #85, T7): preset buttons, the headline and
 * runway sentences, a period toggle, the burn-up chart with its likely range, the daily chart
 * and a footnote. Every number, label and sentence comes from `present/forecast.ts`; this file
 * lays them out.
 *
 * The data is fetched by the screen (`useData`, scope only -- never the timeframe -- so a
 * period change does not refetch) and arrives as `state`; switching a preset or a month is
 * local state and makes no request, because every preset is in the one response.
 *
 * `forecast: null` (no run loaded, or this scope is not in it) renders `fallback` -- the
 * flat-rate section the screen had before -- unchanged.
 */
import { useState } from "react";
import type { ReactNode } from "react";

import { Block, resolveBlockState } from "./Block";
import { SectionHeading } from "./ui";
import { DailyFanChart } from "./charts";
import ForecastBurnUp from "./ForecastBurnUp";
import type { DataState, Envelope, Schemas } from "../data/source";
import {
  bandLabel, dailyRows, effectivePresetId, forecastFootnote, groupPresets,
  headlineSentences, incompleteNote, isIncomplete, monthName, periodLabels, runwaySentence,
} from "../present/forecast";

type View = Schemas["ForecastView"];

const card = { border: "1px solid var(--border)", background: "var(--card)" } as const;

function toggleStyle(on: boolean, small = false) {
  return {
    border: `1px solid ${on ? "var(--primary)" : "var(--border)"}`,
    background: on ? "var(--primary)" : "var(--card)",
    color: on ? "var(--primary-foreground)" : "var(--foreground)",
    padding: small ? "4px 10px" : "6px 12px",
    fontSize: small ? 12 : 13,
    borderRadius: 6,
    cursor: "pointer",
    textAlign: "left" as const,
  };
}

export default function ForecastPanel({ state, onRetry, presetId, onPresetChange, fallback, cycleNote }: {
  state: DataState<Envelope<Schemas["BudgetForecast"]>>;
  /** `forecastCycleNote`: shown above the footnote when the org's cycle is not the calendar month. */
  cycleNote?: string | null;
  onRetry: () => void;
  presetId: string;
  onPresetChange: (id: string) => void;
  fallback: ReactNode;
}) {
  // Not computing (yet) is the same as "not in the run": keep the flat-rate section.
  const resolved = resolveBlockState(state);
  if (resolved.kind === "unavailable" || resolved.kind === "cold-start" || resolved.kind === "empty") return <>{fallback}</>;
  return (
    <Block state={state} minHeight={520} onRetry={onRetry}>
      {(data) => (data.forecast
        ? <ForecastBody view={data.forecast} presetId={presetId} onPresetChange={onPresetChange} cycleNote={cycleNote} />
        : <>{fallback}</>)}
    </Block>
  );
}

/** Its own component so the period toggle's state is not a hook inside `Block`'s render prop. */
function ForecastBody({ view, presetId, onPresetChange, cycleNote }: {
  view: View; presetId: string; onPresetChange: (id: string) => void; cycleNote?: string | null;
}) {
  const [periodChoice, setPeriodChoice] = useState(0);
  const id = effectivePresetId(view.presets, presetId);
  const periods = (view.series.find((s) => s.periods.length > 0) ?? view.series[0])?.periods ?? [];
  const periodIndex = Math.min(periodChoice, Math.max(0, periods.length - 1));
  const labels = periodLabels(periods);
  const incomplete = isIncomplete(view, id);

  const sentences = headlineSentences(view, id, periodIndex);
  const runway = runwaySentence(view, id, periodIndex);
  const band = bandLabel(view.band_low, view.band_high);

  return (
    <div>
      <SectionHeading title="Where the window lands" sub="Budget forecast" />
      <div className="mt-4 rounded-lg overflow-hidden" style={card}>
        <div className="flex flex-col gap-5 p-4 sm:p-5">
          <div className="flex flex-col gap-3">
            {groupPresets(view.presets).map((g) => (
              <div key={g.group ?? "ungrouped"} role="group" aria-label={g.group ?? "Current trajectory"}
                className="flex flex-wrap items-center gap-2">
                {g.group && (
                  <span className="text-xs font-medium uppercase tracking-widest" style={{ color: "var(--muted-foreground)", minWidth: 72 }}>
                    {g.group}
                  </span>
                )}
                {g.presets.map((p) => (
                  <button key={p.id} type="button" aria-pressed={p.id === id} aria-label={p.label} title={p.description || undefined}
                    onClick={() => onPresetChange(p.id)} style={toggleStyle(p.id === id)}>
                    {p.label}
                  </button>
                ))}
              </div>
            ))}
          </div>

          {incomplete ? (
            <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>{incompleteNote(id)}</p>
          ) : (
            <>
              <div className="flex flex-col gap-1.5">
                <p className="font-serif" aria-live="polite" aria-atomic="true"
                  style={{ fontSize: 20, lineHeight: 1.35, color: "var(--foreground)" }}>
                  {sentences.join(" ")}
                </p>
                {runway && <p className="text-sm" style={{ color: "var(--foreground)" }}>{runway}</p>}
              </div>

              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-medium uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>
                    Cumulative cost, {monthName(periods[periodIndex].start)}
                  </span>
                  <div role="group" aria-label="Forecast month" className="flex gap-2">
                    {labels.map((label, i) => (
                      <button key={periods[i].start} type="button" aria-pressed={i === periodIndex}
                        onClick={() => setPeriodChoice(i)} style={toggleStyle(i === periodIndex, true)}>
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                <ForecastBurnUp view={view} presetId={id} periodIndex={periodIndex} />
              </div>

              <div className="flex flex-col gap-2">
                <span className="text-xs font-medium uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>
                  Daily cost: the last 28 days, then the forecast
                </span>
                <DailyFanChart rows={dailyRows(view, id)} band={band} />
              </div>
            </>
          )}

          {cycleNote && <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>{cycleNote}</p>}
          <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>{forecastFootnote(view)}</p>
        </div>
      </div>
    </div>
  );
}
