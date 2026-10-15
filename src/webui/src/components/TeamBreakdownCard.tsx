/**
 * Overview "Team breakdown" card: one row per team, in the order the API sent them (no ranking).
 * Columns: Team (initials avatar and name), Developers (the team's members), Spend, Merged PRs,
 * Budget used (a bar coloured by how close to budget, with the percentage as text) and Status
 * (words, not colour alone). Every figure comes from `teamTableRows` (`present/overview.ts`).
 *
 * A row opens the team: the Team cell holds a real button (so Tab and Enter work and the row has an
 * accessible name), and the whole row is clickable with the mouse. The table scrolls sideways INSIDE
 * the card on a phone (a focusable, labelled region) with a fade at the edge while more is to the
 * right, so no column is ever clipped.
 */
import { useEffect, useRef, useState } from "react";
import { Avatar, Card, Empty, LinkButton, Pill, ProgressBar } from "./ui";
import { teamTableRows } from "../present/overview";
import type { TeamTableRow } from "../present/overview";
import type { Schemas } from "../data/source";

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]";
const TH = "px-2 py-1.5 text-[10px] font-medium uppercase tracking-wider whitespace-nowrap";

/** True while the element scrolls sideways and is not yet scrolled to its right end. */
function useMoreToTheRight(): [React.RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setMore(el.scrollWidth - el.clientWidth - el.scrollLeft > 2);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    el.addEventListener("scroll", update, { passive: true });
    return () => { ro.disconnect(); el.removeEventListener("scroll", update); };
  }, []);
  return [ref, more];
}

export default function TeamBreakdownCard({ teams, onNavigate }: {
  teams: Schemas["TeamSummary"][];
  onNavigate: (screen: string, param?: string) => void;
}) {
  const rows = teamTableRows(teams);
  const [scroller, more] = useMoreToTheRight();
  const open = (r: TeamTableRow) => onNavigate("team", r.id);
  return (
    <Card title="Team breakdown" aside={<LinkButton onClick={() => onNavigate("teams-overview")}>View all teams</LinkButton>}>
      {rows.length === 0 ? (
        <Empty label="No teams in this window." />
      ) : (
        <div className="relative">
          <div
            ref={scroller} role="region" tabIndex={0} aria-label="Team breakdown table (scrolls sideways on narrow screens)"
            className={`overflow-x-auto rounded-md ${FOCUS}`}
          >
            <table className="w-full min-w-[640px] border-collapse text-xs" style={{ color: "var(--foreground)" }}>
              <caption className="sr-only">Spend, merged pull requests and budget use for each team. Select a team to open it.</caption>
              <thead>
                <tr style={{ color: "var(--muted-foreground)" }}>
                  <th scope="col" className={`${TH} text-left`}>Team</th>
                  <th scope="col" className={`${TH} text-right`}>Developers</th>
                  <th scope="col" className={`${TH} text-right`}>Spend</th>
                  <th scope="col" className={`${TH} text-right`}>Merged PRs</th>
                  <th scope="col" className={`${TH} text-left`}>Budget used</th>
                  <th scope="col" className={`${TH} text-left`}>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} onClick={() => open(r)} className="row-hover" style={{ borderTop: "1px solid var(--border)" }}>
                    <th scope="row" className="px-2 py-2 text-left font-normal">
                      <button
                        type="button" aria-label={`${r.name}, open team`}
                        className={`flex max-w-[220px] cursor-pointer items-center gap-2 rounded text-left text-sm font-medium ${FOCUS}`}
                        style={{ color: "var(--foreground)" }}
                      >
                        <Avatar initials={r.initials} size={24} />
                        <span className="min-w-0 truncate" title={r.name}>{r.name}</span>
                      </button>
                    </th>
                    <td className="px-2 py-2 text-right font-mono whitespace-nowrap">{r.members}</td>
                    <td className="px-2 py-2 text-right font-mono whitespace-nowrap">{r.spend}</td>
                    <td className="px-2 py-2 text-right font-mono whitespace-nowrap">{r.mergedPrs}</td>
                    <td className="px-2 py-2">
                      {r.hasBar ? (
                        <div className="flex items-center gap-2">
                          <div aria-hidden className="flex w-24 shrink-0"><ProgressBar value={r.barFraction} max={1} variant={r.tone} /></div>
                          <span className="font-mono whitespace-nowrap">{r.budgetUsed}</span>
                        </div>
                      ) : (
                        <span className="font-mono" style={{ color: "var(--muted-foreground)" }}>{r.budgetUsed}</span>
                      )}
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap"><Pill variant={r.status.variant}>{r.status.text}</Pill></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {more && (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-y-0 right-0 w-8 rounded-r-md"
              style={{ background: "linear-gradient(to left, var(--card), transparent)" }}
            />
          )}
        </div>
      )}
    </Card>
  );
}
