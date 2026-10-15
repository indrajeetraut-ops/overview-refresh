import { MONO_FONT } from "../present/fonts";
import { useState } from "react";
import { HeroTile, fmtMoneyMicros, fmtDays, fmtPct, fmtRelativeTime } from "../components/ui";
import { Block } from "../components/Block";
import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { useTimeframe } from "../shell/timeframe";

type Props = { onNavigate: (screen: string, param?: string) => void };
type SortKey = "spend" | "merged_prs" | "contributors" | "rework_rate";

export default function ProjectsOverview({ onNavigate }: Props) {
  const { timeframe, selection } = useTimeframe();
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("spend");
  const [dir, setDir] = useState<-1 | 1>(-1);

  const page = useData(
    (src) => src.projects(timeframe, { sort: sortKey, q: q || undefined, limit: 200 }),
    [timeframe, sortKey, q],
  );

  const toggleSort = (k: SortKey) => { if (sortKey === k) setDir((d) => (d === -1 ? 1 : -1)); else { setSortKey(k); setDir(-1); } };
  const th = (label: string, k?: SortKey, right?: boolean) => (
    <span onClick={k ? () => toggleSort(k) : undefined} className={right ? "text-right" : ""}
      style={{ cursor: k ? "pointer" : "default", color: k === sortKey ? "var(--foreground)" : "var(--muted-foreground)" }}>
      {label}{k === sortKey ? (dir === -1 ? " ↓" : " ↑") : ""}
    </span>
  );

  const cols = "1fr 96px 56px 96px 80px 80px 68px 80px";

  return (
    <Block state={page} minHeight={420} isEmpty={(d) => d.items.length === 0 && !q}>
      {(data: Schemas["ProjectsPage"]) => {
        const rows = dir === -1 ? data.items : [...data.items].reverse();
        const totalSpend = rows.reduce((s, r) => s + (r.spend.usd_micros ?? 0), 0);
        const totalPRs = rows.reduce((s, r) => s + r.merged_prs, 0);
        const blendedCostPer = totalPRs ? Math.round(totalSpend / totalPRs) : null;
        const timeVals = rows.map((r) => r.avg_time_to_pr_days).filter((n): n is number => n != null);
        const avgTime = timeVals.length ? timeVals.reduce((s, n) => s + n, 0) / timeVals.length : null;

        return (
          <div className="flex flex-col gap-6">
            <div>
              <h1 className="font-semibold" style={{ fontSize: 24, color: "var(--foreground)" }}>Projects</h1>
              <p className="text-sm mt-1" style={{ color: "var(--muted-foreground)" }}>
                {data.total} repositories · {fmtMoneyMicros(totalSpend)} spent · {selection.label}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <HeroTile label="Fleet spend" value={fmtMoneyMicros(totalSpend)} sub={`across ${rows.length} repos`} subVariant="muted" />
              <HeroTile label="PRs merged" value={String(totalPRs)} sub={selection.label} subVariant="green" />
              <HeroTile label="Blended $/PR" value={fmtMoneyMicros(blendedCostPer)} sub="across repos" subVariant="muted" />
              <HeroTile label="Avg time to PR" value={avgTime != null ? fmtDays(avgTime) : "—"} sub="fleet average" subVariant="muted" />
            </div>

            <div className="flex items-center gap-2 rounded-lg px-3 py-2" style={{ border: "1px solid var(--border)", background: "var(--card)", maxWidth: 320 }}>
              <svg width="15" height="15" viewBox="0 0 15 15" fill="none" style={{ color: "var(--muted-foreground)" }}><circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" strokeWidth="1.3" /><path d="M10 10l3 3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search projects…" className="bg-transparent border-0 outline-none text-sm w-full" style={{ color: "var(--foreground)" }} />
            </div>

            <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
              <div className="grid px-5 py-2.5" style={{ gridTemplateColumns: cols, fontSize: 11, fontFamily: MONO_FONT, textTransform: "uppercase", letterSpacing: "0.05em", borderBottom: "1px solid var(--border)", gap: 10 }}>
                {th("Project")}{th("Spend", "spend", true)}{th("PRs", "merged_prs", true)}{th("$/PR")}{th("people", "contributors", true)}{th("avg time", undefined, true)}{th("rework", "rework_rate", true)}{th("active", undefined, true)}
              </div>
              {rows.map((r, i) => (
                <button key={r.repo_id} onClick={() => onNavigate("project", r.repo_id)}
                  className="grid items-center w-full px-5 py-3.5 row-hover text-left"
                  style={{ gridTemplateColumns: cols, gap: 10, borderTop: i > 0 ? "1px solid var(--border)" : undefined }}>
                  <span className="flex items-center gap-2">
                    <svg width="13" height="13" viewBox="0 0 14 14" fill="none" style={{ color: "var(--muted-foreground)" }}><path d="M2 3h5l1.5 1.5H12v7H2V3z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
                    <span className="font-medium text-sm" style={{ color: "var(--foreground)" }}>{r.name}</span>
                  </span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(r.spend.usd_micros)}</span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>{r.merged_prs}</span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--foreground)" }}>{r.merged_prs ? fmtMoneyMicros(Math.round((r.spend.usd_micros ?? 0) / r.merged_prs)) : "—"}</span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>{r.contributors}</span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>{r.avg_time_to_pr_days != null ? fmtDays(r.avg_time_to_pr_days) : "—"}</span>
                  <span className="font-mono text-xs text-right" style={{ color: (r.rework_rate ?? 0) > 0.25 ? "var(--amber)" : "var(--muted-foreground)" }}>{r.rework_rate != null ? fmtPct(r.rework_rate) : "—"}</span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>{fmtRelativeTime(r.last_active_at)}</span>
                </button>
              ))}
            </div>
          </div>
        );
      }}
    </Block>
  );
}
