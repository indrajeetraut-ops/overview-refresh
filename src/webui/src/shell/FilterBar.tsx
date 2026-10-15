/**
 * The filter bar (Overview refresh D3). It replaces the old timeframe strip and sticks right under
 * the top bar (`top-12`, see `TopBar`). Left: the date range and "day N of M" (the day count
 * drops out below `sm` to keep the bar compact; it stays in the tooltip and for screen readers),
 * and for managers the team filter. Right: the timeframe picker and Export CSV (managers).
 *
 * The team filter and Export CSV are inert until their backends exist: disabled, "Coming soon".
 *
 * LAYOUT CONTRACT with `TimeframePicker` (it must hold for whatever hosts the picker):
 * - Below `sm` the picker's listbox is `absolute left-4 right-4 top-full`, anchored to its nearest
 *   positioned ancestor. That is this bar: `sticky` (positioned), the full width of the content
 *   pane, NOT `overflow: hidden`/`auto` (it would clip the listbox), with the 16px gutter (`px-4`).
 *   No element between the bar and the picker may be positioned or the listbox anchors to it.
 * - From `sm` the picker's root is `relative` and the listbox hangs right-aligned under the
 *   button, 280px wide, so the picker needs 280px of room to its LEFT. The right cluster sits at
 *   the bar's right edge (`ml-auto`) and wraps as a unit, so with Export CSV beside it the picker's
 *   right edge is still more than 360px in from the bar's left at `sm` (bar content 568px wide).
 * - The listbox's max height leaves room for the top bar and this bar above it.
 * Break any of these and the listbox leaves the viewport.
 */
import { progressText, rangeText, stripText } from "../present/timeframe";
import TimeframePicker from "./TimeframePicker";
import { useTimeframe } from "./timeframe";

type Props = {
  /** Teams for the (inert) filter; `null` for a dev viewer, who gets neither the filter nor Export. */
  teams: { id: string; name: string }[] | null;
};

// h-[26px] = the picker button's height (12px mono text is 16px line + py-1 + 1px borders), so every
// control in a row is the same whole-pixel height; the select's intrinsic height otherwise differs.
const ctl = "flex h-[26px] shrink-0 items-center gap-1.5 rounded-md px-3 py-0 text-xs font-mono";
const ctlStyle = { background: "var(--muted)", color: "var(--foreground)", border: "1px solid var(--border)" } as const;

export default function FilterBar({ teams }: Props) {
  const { selection, setSelection, periods, status } = useTimeframe();
  const range = rangeText(selection);
  const progress = progressText(selection);
  const manager = teams !== null;

  return (
    <div
      className="sticky top-12 z-10 px-4 sm:px-9 md:px-12"
      style={{ paddingTop: 10, paddingBottom: 10, background: "var(--background)", borderBottom: "1px solid var(--border)" }}
    >
      {/* same content width as App.tsx's screen container, so the controls line up with the page */}
      <div className="mx-auto flex w-full max-w-[1440px] flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <span
            className="min-w-0 text-xs font-mono"
            style={{ color: "var(--muted-foreground)" }}
            title={stripText(selection)}
          >
            {range}
            {progress && <span className="sr-only sm:not-sr-only"> · {progress}</span>}
          </span>
          {manager && (
            // TODO(team filter): team-scoped org endpoints
            <select
              disabled
              value="all"
              aria-label="Team filter"
              title="Coming soon"
              onChange={() => {}}
              className={`${ctl} w-[9rem] truncate cursor-not-allowed opacity-60`}
              style={ctlStyle}
            >
              <option value="all">All teams</option>
              {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          )}
        </div>

        {/* Not positioned: below `sm` the picker's listbox anchors to this bar (see the contract). */}
        <div className="ml-auto flex min-w-0 items-center gap-2">
          <TimeframePicker selection={selection} periods={periods} unavailable={status === "unavailable"} onSelect={setSelection} />
          {manager && (
            // TODO(export): wire Export CSV
            <button
              type="button"
              disabled
              aria-label="Export CSV"
              title="Coming soon"
              className={`${ctl} cursor-not-allowed opacity-60`}
              style={ctlStyle}
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden>
                <path d="M7 1.5v7M4 6l3 3 3-3M2 11.5h10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="hidden sm:inline">Export CSV</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
