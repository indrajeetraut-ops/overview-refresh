import { useState } from "react";
import { forecastScope, useData } from "../data/source";
import type { EconomicsScope, Schemas } from "../data/source";
import { Block, UnavailableNote } from "../components/Block";
import { HeroTile, SectionHeading, Chip, ScopePicker, Divider, fmtUSD } from "../components/ui";
import { PercentileBar, CostEnvelopeChart, BudgetBurn } from "../components/charts";
import ForecastPanel from "../components/ForecastPanel";
import BudgetPlanner from "../components/BudgetPlanner";
import { fmtMoneyMicros, fmtPct } from "../present/format";
import { useScopes } from "../shell/scopes";
import { useTimeframe } from "../shell/timeframe";
import { forecastCycleNote } from "../present/cycle";
import AskBox from "../components/AskBox";
import type { PinnedCard } from "./types";

type Props = {
  onNavigate: (screen: string, param?: string) => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

/** `"org"` | `"team:<id>"` | `"repo:<id>"` -> the two-part scope E1 actually takes. */
function parseScopeValue(v: string): EconomicsScope {
  if (v === "org") return { scopeType: "org", scopeId: "org" };
  const [scopeType, ...rest] = v.split(":");
  return { scopeType: scopeType as "team" | "repo", scopeId: rest.join(":") };
}

export default function EconomicsScreen({ pinnedCards, onPin }: Props) {
  const { timeframe, cycleLabel } = useTimeframe();
  // The forecast is by calendar month whatever the org's cycle: say so beside it (wording only).
  const cycleNote = forecastCycleNote(cycleLabel);
  const [scopeValue, setScopeValue] = useState("org");
  const scope = parseScopeValue(scopeValue);
  // Forecast T7: the selected preset lives here so the planner follows the forecast panel; it
  // goes back to the baseline whenever the scope changes. The forecast is fetched here, not
  // inside the body Block below (which remounts when the timeframe changes), keyed on the scope
  // only: the run is fixed, so a timeframe change must not refetch it and a preset change never does.
  const [presetId, setPresetId] = useState("baseline");
  const [forecastAttempt, setForecastAttempt] = useState(0);
  const onScopeChange = (v: string) => { setScopeValue(v); setPresetId("baseline"); };
  const fScope = forecastScope(scope);
  const forecast = useData((src) => src.forecast(fScope), [fScope.scopeType, fScope.scopeId, forecastAttempt]);

  // U7-econ-picker: a dedicated names-only endpoint, all scopes (no `limit`), timeframe-
  // independent (fetched once by the Shell, `shell/scopes.tsx`, never on timeframe change) -- replaces fetching the
  // full Teams/Projects table pages (which silently truncated at 50 rows) just for a <select>.
  const scopesState = useScopes();
  const scopes = [
    { id: "org", label: "Entire org", group: "" as const },
    ...(scopesState.data?.teams ?? []).map((t) => (
      { id: `team:${t.id}`, label: t.name, group: "Teams" as const }
    )),
    ...(scopesState.data?.projects ?? []).map((p) => (
      { id: `repo:${p.id}`, label: p.name, group: "Projects" as const }
    )),
  ];

  const detail = useData((src) => src.economics(scope, timeframe), [scope.scopeType, scope.scopeId, timeframe]);

  return (
    <Block state={detail} minHeight={420}>
      {(data: Schemas["Economics"]) => {
        const { cost_dist, time_to_merge_dist, pr_cost_envelope, forecast_cone, merged_prs,
          reverted_prs, merged_pr_cost, reverted_pr_cost, rework_ratio, budget_burn } = data;

        const envelopePoints = pr_cost_envelope
          ? [
              {
                label: "now",
                bandLo: pr_cost_envelope.p25_usd_micros / 1_000_000,
                band: (pr_cost_envelope.p75_usd_micros - pr_cost_envelope.p25_usd_micros) / 1_000_000,
                median: pr_cost_envelope.median_usd_micros / 1_000_000,
                p90: pr_cost_envelope.p90_usd_micros / 1_000_000,
                p99: pr_cost_envelope.p99_usd_micros / 1_000_000,
              },
              ...forecast_cone.slice(1).map((c) => ({
                label: `+${c.step}`,
                forecast: true,
                fMedian: c.median_usd_micros / 1_000_000,
                fBandLo: c.band_lo_usd_micros / 1_000_000,
                fBand: (c.band_hi_usd_micros - c.band_lo_usd_micros) / 1_000_000,
              })),
            ]
          : [];

        const burnPoints = (budget_burn ?? []).map((p) => ({
          day: String(p.day),
          actual: p.actual_usd_micros != null ? p.actual_usd_micros / 1_000_000 : undefined,
          projected: p.projected_usd_micros != null ? p.projected_usd_micros / 1_000_000 : undefined,
        }));
        const lastProjected = [...(budget_burn ?? [])].reverse().find((p) => p.projected_usd_micros != null);

        // The pre-forecast section, verbatim: used when there is no forecast run for the scope.
        const flatRateForecast = (
          <div>
            <div className="flex items-start justify-between gap-4">
              <SectionHeading title="Where the window lands" sub="Budget forecast" />
              {lastProjected?.projected_usd_micros != null && (
                <span className="font-semibold" style={{ fontSize: 24, color: "var(--foreground)" }}>{fmtMoneyMicros(lastProjected.projected_usd_micros)}</span>
              )}
            </div>
            <div className="mt-4">
              {budget_burn ? (
                <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                  <div className="px-3 py-4"><BudgetBurn data={burnPoints} height={220} /></div>
                </div>
              ) : (
                <UnavailableNote minHeight={140} info={{ block: "budget_burn", requires: "session activity in this window" }} />
              )}
              {cycleNote && <p className="mt-2 text-xs" style={{ color: "var(--muted-foreground)" }}>{cycleNote}</p>}
            </div>
          </div>
        );

        return (
          <div className="flex flex-col gap-8">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h1 className="font-serif mb-3" style={{ fontSize: 26, color: "var(--foreground)", lineHeight: 1.3 }}>
                  {cost_dist ? `A PR costs ${fmtUSD(cost_dist.p50)} at the median — but the tail runs to ${fmtUSD(cost_dist.p95)}.`
                    : "Unit economics for this scope"}
                </h1>
                <p className="text-sm" style={{ color: "var(--muted-foreground)", maxWidth: 620 }}>
                  Unit economics of the work your agents produce, and a simple forecast of where cost is heading.
                </p>
              </div>
              <div className="shrink-0"><ScopePicker value={scopeValue} onChange={onScopeChange} scopes={scopes} /></div>
            </div>

            {/* Headline economics */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <HeroTile label="Median PR cost" value={cost_dist ? fmtUSD(cost_dist.p50) : "—"} sub={cost_dist ? `p95 ${fmtUSD(cost_dist.p95)}` : "no data"} subVariant="amber" />
              <HeroTile label="Merged PR" value={fmtMoneyMicros(merged_pr_cost.usd_micros)} sub={`${merged_prs} merged`} subVariant="green" />
              <HeroTile label="Reverted PR" value={fmtMoneyMicros(reverted_pr_cost.usd_micros)} sub={`${reverted_prs} reverted`} subVariant="red" />
              <HeroTile label="Rework ratio" value={rework_ratio != null ? fmtPct(rework_ratio) : "—"} sub="of merged PRs" subVariant="amber" />
            </div>

            <Divider />

            {/* Distributions */}
            <div>
              <SectionHeading title="How much a PR costs — and how long it takes" sub="Distributions" />
              <div className="mt-5">
                {cost_dist && time_to_merge_dist ? (
                  <div className="rounded-lg p-6 grid grid-cols-1 gap-8 sm:grid-cols-2" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                    <PercentileBar label="PR cost" unit="$" d={cost_dist} />
                    <PercentileBar label="Time to merge" unit="d" d={time_to_merge_dist} />
                  </div>
                ) : (
                  <UnavailableNote minHeight={140} info={{ block: "distributions", requires: "merged PRs in this scope" }} />
                )}
              </div>
            </div>

            {/* Envelope + forecast cone */}
            <div>
              <SectionHeading title="The cost-per-PR envelope" sub="Variability & forecast" />
              <div className="mt-4">
                {pr_cost_envelope ? (
                  <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                    <div className="px-5 pt-4 pb-1 text-xs" style={{ color: "var(--muted-foreground)" }}>
                      median · p25–p75 band · p90/p99 tail · forecast cone
                    </div>
                    <div className="px-3 pb-4"><CostEnvelopeChart data={envelopePoints} height={260} /></div>
                  </div>
                ) : (
                  <UnavailableNote minHeight={200} info={{ block: "pr_cost_envelope", requires: "merged PRs in this scope" }} />
                )}
              </div>
            </div>

            <Divider />

            {/* Budget forecast (T7). The flat-rate burn-up (`flatRateForecast`) is the fallback when the scope has
                no forecast run; `forecast: null` renders it exactly as it was. */}
            <ForecastPanel state={forecast} onRetry={() => setForecastAttempt((a) => a + 1)}
              presetId={presetId} onPresetChange={setPresetId} fallback={flatRateForecast} cycleNote={cycleNote} />
            {scope.scopeType === "org" && <BudgetPlanner presetId={presetId} cycleNote={cycleNote} />}

            <Divider />

            {/* Ask */}
            <div>
              <SectionHeading title="Ask" sub="Questions" />
              <div className="mt-4"><AskBox pinnedCards={pinnedCards} onPin={onPin} /></div>
            </div>
          </div>
        );
      }}
    </Block>
  );
}
