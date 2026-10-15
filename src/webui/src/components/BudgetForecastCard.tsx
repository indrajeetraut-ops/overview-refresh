/**
 * Overview "Budget forecast" card: the baseline burn-up of the period under way (else the first),
 * drawn through the same `ForecastBurnUp` as Economics. The data is fetched by the screen, OUTSIDE
 * its page `<Block>` and keyed on the scope only (a timeframe switch must not refetch it), and
 * arrives as `state`.
 *
 * Footer, left: the runway pill and sentence (or, with no runway to report, the window's own
 * "Budget status" from `budget_pct`) and "See forecast detail". Right: the Recoverable line and
 * "See how to save". Every state goes through `<Block>` / `<UnavailableNote>`, never dropped.
 */
import { Block, UnavailableNote, resolveBlockState } from "./Block";
import ForecastBurnUp from "./ForecastBurnUp";
import { Card, LinkButton, Pill } from "./ui";
import type { DataState, Envelope, Schemas } from "../data/source";
import { BASELINE_ID, headlineSentences, incompleteNote, isIncomplete } from "../present/forecast";
import { budgetStatus, forecastCardTitle, forecastPeriodIndex, forecastRunway, recoverableLine } from "../present/overview";

const HEIGHT = 230;

export default function BudgetForecastCard({
  state, onRetry, budgetPct, spendMicros, couldSaveMicros, cause, cycleNote, onNavigate,
}: {
  state: DataState<Envelope<Schemas["BudgetForecast"]>>;
  onRetry: () => void;
  /** The window's own budget share: the footer's fallback when there is no runway. */
  budgetPct: number | null | undefined;
  spendMicros: number | null | undefined;
  couldSaveMicros: number | null | undefined;
  cause: string | null | undefined;
  /** `forecastCycleNote`: "Forecast is by calendar month" for a known non-monthly cycle. */
  cycleNote: string | null;
  onNavigate: (screen: string, param?: string) => void;
}) {
  const resolved = resolveBlockState(state);
  const view = resolved.kind === "ready" ? resolved.data.forecast ?? null : null;
  const idx = view ? forecastPeriodIndex(view) : 0;
  const incomplete = view ? isIncomplete(view, BASELINE_ID) : false;
  const runway = view && !incomplete ? forecastRunway(view, idx) : null;
  const status = budgetStatus(budgetPct);

  const left = (
    <div className="flex min-w-0 flex-1 basis-[220px] flex-col gap-1.5">
      {runway ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Pill variant={runway.pill.variant}>{runway.pill.text}</Pill>
          <span style={{ color: "var(--foreground)" }}>{runway.sentence}</span>
        </div>
      ) : resolved.kind !== "loading" ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span style={{ color: "var(--muted-foreground)" }}>{status.label}</span>
          <Pill variant={status.variant}>{status.text}</Pill>
        </div>
      ) : null}
      <LinkButton onClick={() => onNavigate("economics")}>See forecast detail</LinkButton>
    </div>
  );
  const right = (
    <div className="flex min-w-0 flex-1 basis-[220px] flex-col gap-1.5">
      <span style={{ color: "var(--muted-foreground)" }}>{recoverableLine(couldSaveMicros, spendMicros, cause)}</span>
      <LinkButton onClick={() => onNavigate("usage")}>See how to save</LinkButton>
    </div>
  );

  return (
    <Card title={forecastCardTitle(view)} footer={<>{left}{right}</>}>
      <Block state={state} minHeight={HEIGHT + 40} onRetry={onRetry}>
        {(data) => {
          if (!data.forecast) {
            return <UnavailableNote minHeight={HEIGHT + 40} info={{ block: "budget_forecast", requires: "budget forecast" }} />;
          }
          const v = data.forecast;
          if (isIncomplete(v, BASELINE_ID)) {
            return <p className="py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>{incompleteNote(BASELINE_ID)}</p>;
          }
          const i = forecastPeriodIndex(v);
          return (
            <div role="group" aria-label={`Budget forecast chart. ${headlineSentences(v, BASELINE_ID, i).join(" ")}`}>
              <ForecastBurnUp view={v} presetId={BASELINE_ID} periodIndex={i} height={HEIGHT} />
            </div>
          );
        }}
      </Block>
      {/* Under every state, not just the chart: the footer's budget is still by calendar month. */}
      {cycleNote && resolved.kind !== "loading" && <p className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{cycleNote}</p>}
    </Card>
  );
}
