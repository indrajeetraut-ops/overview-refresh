/**
 * App-level timeframe (Overview refresh A3a). One `selection` for the whole app, resolved ONCE
 * here to a `{from, to}` window that every `useData` call threads through as request params.
 * Entity pages (Session / PR / Feature) ignore it.
 *
 * A selection is a budget period of the org's cycle (`GET /v1/budget-cycle/periods`, fetched once
 * through the data seam) or a rolling "Last N days" range. The app opens on the CURRENT budget
 * period; if there is none, or the request fails, on rolling "Last 30 days" and the picker says
 * so. The pure rules (selection -> window, default, grouping) live in `present/timeframe.ts`.
 *
 * **Identity.** `timeframe` is memoised per selection: it is a new object only when the
 * selection changes, because every `useData` keys on it and a new object would refetch every
 * widget. A rolling selection is therefore made in the click handler (its `to` is "now" at that
 * moment), never during render.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { getDataSource } from "../data/source";
import type { Timeframe } from "../data/source";
import {
  FALLBACK_ROLLING_DAYS, defaultSelection, rollingSelection, timeframeOf,
} from "../present/timeframe";
import type { BudgetPeriod, TimeframeSelection } from "../present/timeframe";

/** How many past periods the picker offers (the server's default; it allows up to 24). */
const PERIOD_HISTORY = 6;

export type PeriodsStatus = "loading" | "ready" | "unavailable";

type TimeframeCtx = {
  selection: TimeframeSelection;
  setSelection: (s: TimeframeSelection) => void;
  /** The resolved window: pass straight to a `DataSource` method. Stable until the selection changes. */
  timeframe: Timeframe;
  /** The budget periods on offer (oldest first, the current one last); empty while unavailable. */
  periods: BudgetPeriod[];
  /** What budget amounts are stored under (`monthly`, `sprint`, ...); `null` while unavailable. */
  cycleLabel: string | null;
  /** `loading` only until the first answer; a reload keeps showing the previous periods. */
  status: PeriodsStatus;
  /** Refetch the periods and go back to the current one (after the cycle is saved on Configure). */
  reloadPeriods: () => void;
};

const Ctx = createContext<TimeframeCtx | null>(null);

type PeriodsState = { status: PeriodsStatus; periods: BudgetPeriod[]; cycleLabel: string | null };

export function TimeframeProvider({ children }: { children: React.ReactNode }) {
  // Until the periods settle the gate keeps screens unmounted, so this placeholder is never fetched on.
  const [selection, setSelectionState] = useState<TimeframeSelection>(() => rollingSelection(FALLBACK_ROLLING_DAYS));
  const [state, setState] = useState<PeriodsState>({ status: "loading", periods: [], cycleLabel: null });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    getDataSource()
      .budgetPeriods(PERIOD_HISTORY)
      .then((res) => {
        if (!live) return;
        setState({ status: "ready", periods: res.periods, cycleLabel: res.period_label });
        setSelectionState(defaultSelection(res.periods, new Date()));
      })
      .catch(() => {
        if (!live) return;
        setState({ status: "unavailable", periods: [], cycleLabel: null });
        setSelectionState(rollingSelection(FALLBACK_ROLLING_DAYS));
      });
    return () => {
      live = false;
    };
  }, [attempt]);

  // Picking what is already selected is a no-op, so identity (and every fetch) stays put.
  const setSelection = useCallback((next: TimeframeSelection) => {
    setSelectionState((prev) => (prev.key === next.key ? prev : next));
  }, []);
  // A reload deliberately REPLACES the selection object (back to the default) even when it is
  // the same period, so every `useData` refetches once with the fresh bounds; a reload that
  // fails falls back to rolling "Last 30 days" like the first load does.
  const reloadPeriods = useCallback(() => setAttempt((a) => a + 1), []);

  const timeframe = useMemo(() => timeframeOf(selection), [selection]);
  const value = useMemo<TimeframeCtx>(
    () => ({ selection, setSelection, timeframe, periods: state.periods, cycleLabel: state.cycleLabel, status: state.status, reloadPeriods }),
    [selection, setSelection, timeframe, state, reloadPeriods],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTimeframe(): TimeframeCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTimeframe outside <TimeframeProvider>");
  return v;
}

/** Holds back what is inside until the budget periods have answered the first time, so no
 * rolling window is fetched (and flashed) before the current period is known. Wrap the filter bar
 * and the screens only, not the shell (the top bar sits outside it): its navigation state must survive a reload of the periods. */
export function TimeframeGate({ children }: { children: React.ReactNode }) {
  const { status } = useTimeframe();
  // TODO(timeframe-gate): no retry/timeout on a periods request that never settles
  if (status === "loading") {
    return (
      <div role="status" aria-busy="true" aria-label="Loading budget periods" className="p-4 sm:p-9 md:p-12">
        <div className="mx-auto w-full max-w-[1440px] rounded-lg animate-pulse" style={{ minHeight: 320, background: "var(--muted)", opacity: 0.6 }} />
      </div>
    );
  }
  return <>{children}</>;
}
