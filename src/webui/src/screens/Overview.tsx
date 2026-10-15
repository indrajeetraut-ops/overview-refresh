import { useState } from "react";
import {
  Card, Metric, ModelDonut, Pill, SectionHeading, SpendGauge, WorthALook,
  fmtPct, fmtTokens, fmtCount, fmtMoneyMicros, fmtDays,
} from "../components/ui";
import { Block } from "../components/Block";
import AgentSpendCard from "../components/AgentSpendCard";
import AskBox from "../components/AskBox";
import BudgetForecastCard from "../components/BudgetForecastCard";
import DailySpendCard from "../components/DailySpendCard";
import TeamBreakdownCard from "../components/TeamBreakdownCard";
import TeamSpendCard from "../components/TeamSpendCard";
import UnitEconomicsCard from "../components/UnitEconomicsCard";
import { forecastScope, useData } from "../data/source";
import type { Schemas } from "../data/source";
import { useTimeframe } from "../shell/timeframe";
import {
  modelMixLabel, modelSlices, mergedPerDay, reasoningText, reposText,
  spendGauge, teamSpendBars, teamsBanner,
} from "../present/overview";
import { forecastCycleNote, inWindow } from "../present/cycle";
import type { PinnedCard } from "./types";

type Props = {
  onNavigate: (screen: string, param?: string) => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

export default function Overview({ onNavigate, pinnedCards, onPin }: Props) {
  const { timeframe, selection, cycleLabel } = useTimeframe();
  const overview = useData((src) => src.orgOverview(timeframe), [timeframe]);
  // The forecast is fetched here, OUTSIDE the page Block below (which remounts when the timeframe
  // changes) and keyed on the scope only: the run is fixed, so a timeframe switch never refetches it.
  // Same pattern as EconomicsScreen.
  const [forecastAttempt, setForecastAttempt] = useState(0);
  const fScope = forecastScope({ scopeType: "org", scopeId: "org" });
  const forecast = useData((src) => src.forecast(fScope), [fScope.scopeType, fScope.scopeId, forecastAttempt]);
  // The forecast is by calendar month whatever the org's cycle: say so beside it.
  const cycleNote = forecastCycleNote(cycleLabel);

  return (
    <Block state={overview} minHeight={420} isEmpty={(d) => d.teams.length === 0 && d.headline.spend.usd_micros == null}>
      {(data: Schemas["OrgOverview"]) => {
        const spendMicros = data.headline.spend.usd_micros;
        const budgetPct = data.headline.budget_pct;
        const overBudgetTeam = data.teams.find((t) => t.over_budget);

        const headline = overBudgetTeam
          ? `You've spent ${fmtMoneyMicros(spendMicros)} ${inWindow(selection)}. ${overBudgetTeam.name} is over budget — worth a look.`
          : `You've spent ${fmtMoneyMicros(spendMicros)} ${inWindow(selection)}${budgetPct != null ? ` — ${fmtPct(budgetPct)} of budget` : ""}.`;

        const gauge = spendGauge(spendMicros, budgetPct);
        const banner = teamsBanner(data.highlights.teams_over_budget, data.teams.length);
        const models = modelSlices(data.highlights.model_mix);
        const teamBars = teamSpendBars(data.teams);

        return (
          <div className="flex flex-col gap-8">
            <div>
              <h1 className="font-serif mb-3" style={{ fontSize: 26, color: "var(--foreground)", lineHeight: 1.3 }}>
                {headline}
              </h1>
              {overBudgetTeam && (
                <button
                  onClick={() => onNavigate("team", overBudgetTeam.team_id)}
                  className="text-sm font-medium px-4 py-2 rounded"
                  style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
                >
                  Review {overBudgetTeam.name} →
                </button>
              )}
            </div>

            {/* KPI row and Row 2: grouped cards (every former hero tile and stat lives in one) */}
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                <Card title="Spend & budget" aside={selection.label}>
                  {gauge ? (
                    <>
                      <SpendGauge fraction={gauge.fraction} tone={gauge.tone} value={fmtMoneyMicros(spendMicros)} caption={gauge.caption} ariaLabel={gauge.ariaLabel} />
                      {gauge.pill && <div className="flex justify-center"><Pill variant={gauge.pill.variant} dot>{gauge.pill.text}</Pill></div>}
                    </>
                  ) : (
                    <Metric label="Spent" value={fmtMoneyMicros(spendMicros)} size="lg" />
                  )}
                </Card>

                <Card title="Teams & developers">
                  <button
                    type="button"
                    onClick={() => onNavigate("teams-overview")}
                    aria-label={`${banner.text}, open Teams`}
                    className={`chip chip-${banner.variant} w-full cursor-pointer justify-between hover:brightness-95 hover:underline`}
                  >
                    <span className="min-w-0 truncate">{banner.text}</span>
                    <span aria-hidden>→</span>
                  </button>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                    <Metric label="Active teams" value={fmtCount(data.highlights.active_teams)} size="lg" />
                    <Metric label="Active developers" value={fmtCount(data.highlights.active_devs)} size="lg" />
                  </div>
                  <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{reposText(data.highlights.active_repos)}</div>
                </Card>

                <Card title="PR performance">
                  <Metric label="Merged PRs" value={fmtCount(data.highlights.merged_prs)} size="lg" sub={mergedPerDay(data.highlights.merged_prs, selection.daysElapsed)} />
                  <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                    <Metric label="Cost / PR" value={fmtMoneyMicros(data.highlights.cost_per_pr.usd_micros)} size="md" />
                    {data.highlights.avg_time_to_pr_days != null && (
                      <Metric label="Time to PR" value={fmtDays(data.highlights.avg_time_to_pr_days)} size="md" />
                    )}
                    {data.highlights.rework_rate != null && (
                      <Metric label="Rework rate" value={fmtPct(data.highlights.rework_rate)} size="md" />
                    )}
                  </div>
                </Card>

                <Card title="Tokens & models">
                  {models.length > 0 ? (
                    <ModelDonut slices={models} total={fmtTokens(data.highlights.tokens)} ariaLabel={modelMixLabel(fmtTokens(data.highlights.tokens), models)} />
                  ) : (
                    <Metric label="Tokens used" value={fmtTokens(data.highlights.tokens)} size="lg" />
                  )}
                  {data.highlights.reasoning_share != null && (
                    <Metric label="Reasoning tokens" value={reasoningText(data.highlights.reasoning_share)} size="md" />
                  )}
                </Card>
              </div>

              {/* Row 2: Spend by team | Budget forecast (two columns from lg, equal height) */}
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                <TeamSpendCard bars={teamBars} teamsOverBudget={data.highlights.teams_over_budget} />
                <BudgetForecastCard
                  state={forecast}
                  onRetry={() => setForecastAttempt((a) => a + 1)}
                  budgetPct={budgetPct}
                  spendMicros={spendMicros}
                  couldSaveMicros={data.headline.could_save.usd_micros}
                  cause={data.headline.dominant_cause}
                  cycleNote={cycleNote}
                  onNavigate={onNavigate}
                />
              </div>

              {/* Row 3: Spend over time (full width) */}
              <DailySpendCard daily={data.daily_spend} />

              {/* Row 4: Spend by agent (+ Task usage) | PR costs & forecast (two columns from lg, equal height) */}
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                <AgentSpendCard agentSpend={data.agent_spend} taskMix={data.agent_task_mix} />
                <UnitEconomicsCard onNavigate={onNavigate} />
              </div>

              {/* Row 5: Team breakdown (full width) */}
              <TeamBreakdownCard teams={data.teams} onNavigate={onNavigate} />
            </div>

            {/* Worth a look */}
            <div>
              <SectionHeading title="Worth a look" />
              <div className="mt-4">
                <WorthALook items={data.worth_a_look} onNavigate={onNavigate} />
              </div>
            </div>

            {/* Ask — out of scope (Track K); slot present, unwired */}
            <div>
              <SectionHeading title="Ask" sub="Questions" />
              <div className="mt-4">
                <AskBox pinnedCards={pinnedCards} onPin={onPin} />
              </div>
            </div>

            <footer className="text-xs" style={{ color: "var(--muted-foreground)" }}>All amounts in USD</footer>
          </div>
        );
      }}
    </Block>
  );
}
