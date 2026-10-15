/**
 * F1 budget config (issue #59) -- the admin form the `PUT /v1/budgets/{scope_type}/
 * {scope_id}` route had no front-end caller for, plus (Overview refresh A3c) the org's
 * budget cycle: what a "period" is (month, quarter, half-year, year, sprint or custom), which
 * every budget amount is per. Lead/admin only, reached via the top bar's Configure button
 * (App.tsx's own role gate keeps a
 * `dev` viewer from pushing this screen at all, mirroring `gate.require_manager` on the
 * backend). Not built on `<Block>`/`useData`'s `Envelope` machinery -- `BudgetResponse` is
 * explicitly not envelope-wrapped (same precedent as `scopes()`), so this reads `DataState`
 * status directly, the same way `EconomicsScreen`'s scope picker already does.
 */
import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useData, getDataSource, ORG_SCOPE_ID } from "../data/source";
import type { Schemas } from "../data/source";
import { BackButton, Divider, ScopePicker, SectionHeading } from "../components/ui";
import {
  CYCLE_KINDS, DEFAULT_ANCHOR_NUMBER, DEFAULT_SPRINT_WEEKS, MAX_CYCLE_DATE, MIN_CYCLE_DATE, SAME_KIND_NOTICE, amountLabel,
  buildCycleRequest, cycleChange, draftFromCycle, findCycleProblem, kindChangeWarning, offersOneTime, periodNoun,
} from "../present/cycle";
import type { CycleDraft, CycleDraftField, CycleKind } from "../present/cycle";
import { useScopes } from "../shell/scopes";
import { useTimeframe } from "../shell/timeframe";

type Props = { onBack: () => void };

// `ORG_SCOPE_ID` (the org's budget row and forecast scope id) lives in `data/source.ts`.

function parseScopeValue(v: string): { scopeType: string; scopeId: string } {
  if (v === "org") return { scopeType: "org", scopeId: ORG_SCOPE_ID };
  const [scopeType, ...rest] = v.split(":");
  return { scopeType, scopeId: rest.join(":") };
}

type SaveState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "saved" }
  | { status: "error"; message: string };

const inputStyle: CSSProperties = {
  border: "1px solid var(--border)", background: "var(--card)", color: "var(--foreground)",
  borderRadius: 6, padding: "8px 10px", fontSize: 14, width: "100%",
};

const labelStyle: CSSProperties = { color: "var(--muted-foreground)" };
const fieldLabelClass = "block text-xs font-medium uppercase tracking-widest mb-2";

type CycleSave =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "saved"; note: string | null }
  | { status: "error"; message: string };

/** The "Budget cycle" section: one cadence for the whole org. Lives in its own component so its
 * draft state is independent of the scope/amount form below. */
function BudgetCycleSection({ cycle, blocked, onSaved }: {
  cycle: Schemas["BudgetCycleResponse"];
  /** The saved cycle is being read again (or that failed): `cycle` may be stale, so Save and the
   * kind-change warning are not offered against it. */
  blocked: boolean;
  onSaved: () => void;
}) {
  const problemId = useId();
  const confirmRef = useRef<HTMLButtonElement>(null);
  const saveRef = useRef<HTMLButtonElement>(null);
  const statusRef = useRef<HTMLSpanElement>(null);
  const [draft, setDraft] = useState<CycleDraft>(() => draftFromCycle(cycle));
  const [confirming, setConfirming] = useState(false);
  const [save, setSave] = useState<CycleSave>({ status: "idle" });

  // A fresh saved cycle (first load, or the refetch after a save) resets the form to it.
  useEffect(() => {
    setDraft(draftFromCycle(cycle));
    setConfirming(false);
  }, [cycle]);

  const edit = (patch: Partial<CycleDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setConfirming(false);
    if (save.status === "saved" || save.status === "error") setSave({ status: "idle" });
  };
  const setKind = (kind: CycleKind) => edit({ kind, ...(offersOneTime(kind) ? {} : { recurrence: "recurring" as const }) });

  const found = findCycleProblem(draft);
  const problem = found?.message ?? null;
  const change = found ? "none" : cycleChange(cycle, draft);
  const saving = save.status === "saving";
  const canSave = found == null && change !== "none" && !saving && !blocked;
  const showWarning = change === "kind" && confirming && !blocked;
  // Attributes that tie a message to the input it is about.
  const invalid = (field: CycleDraftField) => ({
    "aria-invalid": found?.field === field ? true : undefined,
    "aria-describedby": found?.field === field ? problemId : undefined,
  });

  // Focus follows the warning: onto "Confirm change" when it opens, back to Save when it is
  // cancelled, and (below) onto the result line when a confirmed save closes it.
  useEffect(() => {
    if (showWarning) confirmRef.current?.focus();
  }, [showWarning]);
  useEffect(() => {
    if (save.status !== "saved" && save.status !== "error") return;
    const a = document.activeElement;
    // The Save button is disabled once the saved draft equals the saved cycle, which also
    // drops its focus, so a disabled focused control counts as lost focus.
    const lost = !a || a === document.body || (a as HTMLButtonElement).disabled === true;
    if (lost) statusRef.current?.focus();
  }, [save.status]);
  function cancelWarning() {
    setConfirming(false);
    saveRef.current?.focus();
  }

  async function doSave() {
    if (!canSave) return;
    setSave({ status: "saving" });
    setConfirming(false);
    try {
      const saved = await getDataSource().putBudgetCycle(buildCycleRequest(draft));
      setSave({ status: "saved", note: saved.note ?? null });
      onSaved();
    } catch (e) {
      // the server's own 400/422 message, as `errorMessage` extracted it
      setSave({ status: "error", message: (e as Error).message });
    }
  }
  function onSaveClick() {
    if (change === "kind" && !confirming) setConfirming(true);
    else void doSave();
  }

  return (
    <div className="flex flex-col gap-5">
      <div>
        <SectionHeading title="Budget cycle" sub="What a period is for every budget" />
        <p className="text-sm mt-2" style={labelStyle}>
          Budgets are amounts per period of this cycle, and the timeframe picker offers its periods.
        </p>
      </div>

      <div>
        <label htmlFor="cycle-kind" className={fieldLabelClass} style={labelStyle}>Cycle</label>
        <select id="cycle-kind" value={draft.kind} onChange={(e) => setKind(e.target.value as CycleKind)} style={inputStyle}>
          {CYCLE_KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
        </select>
      </div>

      {draft.kind === "sprint" && (
        <div className="grid items-end gap-4 sm:grid-cols-3">
          <div>
            <label htmlFor="cycle-weeks" className={fieldLabelClass} style={labelStyle}>Sprint length (weeks)</label>
            <input id="cycle-weeks" type="number" min="1" max="52" step="1" inputMode="numeric" placeholder={DEFAULT_SPRINT_WEEKS}
              value={draft.sprintWeeks} onChange={(e) => edit({ sprintWeeks: e.target.value })} style={inputStyle} {...invalid("sprintWeeks")} />
          </div>
          <div>
            <label htmlFor="cycle-number" className={fieldLabelClass} style={labelStyle}>Current sprint number</label>
            <input id="cycle-number" type="number" min="1" step="1" inputMode="numeric" placeholder={DEFAULT_ANCHOR_NUMBER}
              value={draft.anchorNumber} onChange={(e) => edit({ anchorNumber: e.target.value })} style={inputStyle} {...invalid("anchorNumber")} />
          </div>
          <div>
            <label htmlFor="cycle-anchor" className={fieldLabelClass} style={labelStyle}>Current sprint started</label>
            <input id="cycle-anchor" type="date" min={MIN_CYCLE_DATE} max={MAX_CYCLE_DATE}
              value={draft.anchorDate} onChange={(e) => edit({ anchorDate: e.target.value })} style={inputStyle} {...invalid("anchorDate")} />
          </div>
        </div>
      )}

      {draft.kind === "custom" && (
        <div className="grid items-end gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="cycle-start" className={fieldLabelClass} style={labelStyle}>First day</label>
            <input id="cycle-start" type="date" min={MIN_CYCLE_DATE} max={MAX_CYCLE_DATE}
              value={draft.anchorDate} onChange={(e) => edit({ anchorDate: e.target.value })} style={inputStyle} {...invalid("anchorDate")} />
          </div>
          <div>
            <label htmlFor="cycle-end" className={fieldLabelClass} style={labelStyle}>Last day</label>
            <input id="cycle-end" type="date" min={MIN_CYCLE_DATE} max={MAX_CYCLE_DATE}
              value={draft.customEnd} onChange={(e) => edit({ customEnd: e.target.value })} style={inputStyle} {...invalid("customEnd")} />
          </div>
        </div>
      )}

      {offersOneTime(draft.kind) && (
        <div>
          <label htmlFor="cycle-repeat" className={fieldLabelClass} style={labelStyle}>Repeats</label>
          <select id="cycle-repeat" value={draft.recurrence}
            onChange={(e) => edit({ recurrence: e.target.value as CycleDraft["recurrence"] })} style={inputStyle}>
            <option value="recurring">Recurring</option>
            <option value="one-time">One time</option>
          </select>
        </div>
      )}

      {problem && <div id={problemId} role="status" className="text-xs" style={{ color: "var(--red)" }}>{problem}</div>}

      {change === "edit" && !problem && !blocked && (
        <div role="status" className="text-sm" style={labelStyle}>{SAME_KIND_NOTICE}</div>
      )}

      {showWarning && (
        <div role="alert" className="rounded-md p-3 text-sm flex flex-col gap-3"
          onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); cancelWarning(); } }}
          style={{ border: "1px solid var(--amber)", background: "var(--card)", color: "var(--foreground)" }}>
          <span>{kindChangeWarning(cycle.period_label, draft.kind)}</span>
          <div className="flex items-center gap-3">
            <button ref={confirmRef} onClick={() => void doSave()} className="px-3 py-1.5 rounded text-sm font-medium"
              style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}>
              Confirm change
            </button>
            <button onClick={cancelWarning} className="px-3 py-1.5 rounded text-sm"
              style={{ border: "1px solid var(--border)", color: "var(--foreground)" }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          ref={saveRef}
          onClick={onSaveClick}
          disabled={!canSave}
          className="px-4 py-2 rounded text-sm font-medium"
          style={{
            background: "var(--primary)", color: "var(--primary-foreground)",
            opacity: canSave ? 1 : 0.5, cursor: canSave ? "pointer" : "not-allowed",
          }}
        >
          {saving ? "Saving…" : "Save cycle"}
        </button>
        {save.status === "saved" && (
          <span ref={statusRef} tabIndex={-1} role="status" className="text-sm outline-none" style={{ color: "var(--green)" }}>
            {save.note ?? "Budget cycle saved."}
          </span>
        )}
        {save.status === "error" && (
          <span ref={statusRef} tabIndex={-1} role="alert" className="text-sm outline-none" style={{ color: "var(--red)" }}>{save.message}</span>
        )}
      </div>
    </div>
  );
}

export default function ConfigureScreen({ onBack }: Props) {
  const { cycleLabel, reloadPeriods } = useTimeframe();
  const [scopeValue, setScopeValue] = useState("org");
  const scope = parseScopeValue(scopeValue);

  // The cycle is read here; a save bumps `cycleVersion`, which refetches both the cycle and the
  // budget amount (amounts are stored per cycle label, so a kind change shows an empty one).
  const [cycleVersion, setCycleVersion] = useState(0);
  const cycleState = useData((src) => src.budgetCycle(), [cycleVersion]);
  // The last cycle that loaded stays on screen while the next one is read (so the section keeps
  // its "saved" message and does not flash a skeleton after every save).
  const lastCycle = useRef<Schemas["BudgetCycleResponse"] | null>(null);
  if (cycleState.status === "ready") lastCycle.current = cycleState.data;
  const cycle = lastCycle.current;
  // What the amount is per: the saved cycle's label (never the unsaved draft's), else the one
  // the timeframe provider already knows.
  const periodLabel = cycle ? cycle.period_label : cycleLabel;
  const noun = periodNoun(periodLabel);

  // The Shell's one names-only, timeframe-independent `scopes()` result (`shell/scopes.tsx`).
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

  const budgetState = useData(
    (src) => src.getBudget(scope.scopeType, scope.scopeId),
    [scope.scopeType, scope.scopeId, cycleVersion],
  );
  // The amount form waits for BOTH reads: the label comes from the cycle and the stored amount
  // is per cycle label, so showing either alone could pair one cadence with the other's amount.
  const cycleReady = cycleState.status === "ready";
  const retryCycle = () => setCycleVersion((v) => v + 1);

  const [amountUsd, setAmountUsd] = useState("");
  const [save, setSave] = useState<SaveState>({ status: "idle" });

  // Prefill the form whenever a fresh budget row lands (scope switch or first load) -- never
  // while the user is mid-edit, since this only fires on a `budgetState` status transition.
  useEffect(() => {
    if (budgetState.status !== "ready") return;
    setAmountUsd(budgetState.data.amount_usd ?? "");
    setSave({ status: "idle" });
  }, [budgetState]);

  const parsedAmount = Number(amountUsd);
  const amountValid = amountUsd.trim() !== "" && Number.isFinite(parsedAmount) && parsedAmount >= 0;
  const canSave = amountValid && save.status !== "saving";

  async function handleSave() {
    if (!canSave) return;
    setSave({ status: "saving" });
    try {
      await getDataSource().putBudget(scope.scopeType, scope.scopeId, {
        amount_usd: amountUsd.trim(),
      });
      setSave({ status: "saved" });
    } catch (e) {
      setSave({ status: "error", message: (e as Error).message });
    }
  }

  // After the cycle is saved: the picker's periods and selection follow it, and the amount
  // below is read again under the new label.
  function onCycleSaved() {
    reloadPeriods();
    setCycleVersion((v) => v + 1);
  }

  return (
    <div className="flex flex-col gap-8" style={{ maxWidth: 560 }}>
      <BackButton label="Back" onClick={onBack} />

      <div>
        <h1 className="font-semibold mb-2" style={{ fontSize: 24, color: "var(--foreground)" }}>Configure budgets</h1>
        <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
          Set the budget per {noun} for the org, a team, or a project. A team's budget divided by its
          members is each seat's allowance.
        </p>
      </div>

      {!cycle && cycleState.status === "loading" && (
        <div className="rounded-lg animate-pulse" style={{ minHeight: 140, background: "var(--muted)", opacity: 0.6 }} aria-hidden />
      )}
      {cycleState.status === "error" && (
        <div role="alert" className="flex flex-wrap items-center gap-3 text-sm" style={{ color: "var(--red)" }}>
          <span>Couldn't load the budget cycle.</span>
          <button onClick={retryCycle} className="text-xs font-medium underline" style={{ color: "var(--foreground)" }}>Retry</button>
        </div>
      )}
      {cycle && <BudgetCycleSection cycle={cycle} blocked={!cycleReady} onSaved={onCycleSaved} />}

      <Divider />

      <div>
        <SectionHeading title="Scope" sub="Where this applies" />
        <div className="mt-3"><ScopePicker value={scopeValue} onChange={setScopeValue} scopes={scopes} /></div>
      </div>

      <Divider />

      {(cycleState.status === "loading" || (cycleReady && budgetState.status === "loading")) && (
        <div className="rounded-lg animate-pulse" style={{ minHeight: 140, background: "var(--muted)", opacity: 0.6 }} aria-hidden />
      )}

      {cycleReady && budgetState.status === "error" && (
        <div className="text-sm" style={{ color: "var(--red)" }}>Couldn't load the current budget for this scope.</div>
      )}

      {cycleReady && budgetState.status === "ready" && (
        <div className="flex flex-col gap-5">
          <div>
            <label htmlFor="budget-amount" className="block text-xs font-medium uppercase tracking-widest mb-2" style={{ color: "var(--muted-foreground)" }}>
              {amountLabel(periodLabel)}
            </label>
            <input
              id="budget-amount"
              type="number" min="0" step="0.01" inputMode="decimal" placeholder="e.g. 4000"
              value={amountUsd} onChange={(e) => setAmountUsd(e.target.value)} style={inputStyle}
              aria-invalid={!amountValid && amountUsd.trim() !== "" ? true : undefined}
              aria-describedby={!amountValid && amountUsd.trim() !== "" ? "budget-amount-problem" : undefined}
            />
            {!amountValid && amountUsd.trim() !== "" && (
              <div id="budget-amount-problem" role="status" className="text-xs mt-1.5" style={{ color: "var(--red)" }}>Enter a non-negative amount.</div>
            )}
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleSave}
              disabled={!canSave}
              className="px-4 py-2 rounded text-sm font-medium"
              style={{
                background: "var(--primary)", color: "var(--primary-foreground)",
                opacity: canSave ? 1 : 0.5, cursor: canSave ? "pointer" : "not-allowed",
              }}
            >
              {save.status === "saving" ? "Saving…" : "Save budget"}
            </button>
            {save.status === "saved" && <span className="text-sm" style={{ color: "var(--green)" }}>Saved.</span>}
            {save.status === "error" && <span className="text-sm" style={{ color: "var(--red)" }}>{save.message}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
