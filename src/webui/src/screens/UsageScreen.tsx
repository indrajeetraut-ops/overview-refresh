import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ReferenceLine, ResponsiveContainer, Cell
} from "recharts";
import {
  SectionHeading, HeroTile, Chip, ProgressBar, Avatar, fmtTokens, fmtPct, fmtDuration, fmtCount, Divider,
  fmtMoneyMicros
} from "../components/ui";
import { SessionTypeBar } from "../components/charts";
import { Block, UnavailableNote } from "../components/Block";
import AskBox from "../components/AskBox";
import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { useTimeframe } from "../shell/timeframe";
import { windowName } from "../present/cycle";
import { MONO_FONT } from "../present/fonts";
import { categoryHue, sessionTypeName } from "../present/palette";
import type { PinnedCard } from "./types";

type Props = {
  onNavigate: (screen: string, param?: string) => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

// Utilization here is spend over a dollar allowance -- a team's budget for the selected window
// (a whole budget period, or prorated for a rolling range), or a seat's share of it (cost-based seats plan, 2026-09-26). "new" is a seat inside
// its first 14 days: never shown as low use.
function statusVariant(status: string | null | undefined): "red" | "amber" | "green" | "muted" {
  if (status === "over") return "red";
  if (status === "under") return "amber";
  if (status === "new") return "muted";
  return "green";
}
function statusLabel(status: string | null | undefined): string {
  if (status === "over") return "High use";
  if (status === "under") return "Low use";
  if (status === "new") return "New seat";
  return "On track";
}
function initialsOf(name: string): string {
  return name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase();
}

export default function UsageScreen({ onNavigate, pinnedCards, onPin }: Props) {
  const { timeframe, selection } = useTimeframe();
  const usage = useData((src) => src.orgUsage(timeframe), [timeframe]);

  return (
    <Block state={usage} minHeight={420} isEmpty={(d) => d.teams.length === 0 && d.header.sessions === 0}>
      {(data: Schemas["OrgUsage"]) => {
        const highUse = data.teams.filter((t) => t.status === "over");
        const lowUse = data.teams.filter((t) => t.status === "under");

        const headline = highUse.length > 0
          ? `${highUse.map((t) => t.name).join(" and ")} ${highUse.length === 1 ? "is" : "are"} using the most of their budget; ${lowUse.length} team${lowUse.length !== 1 ? "s are" : " is"} well under theirs.`
          : `Spend is within budget across teams — ${lowUse.length} ${lowUse.length === 1 ? "team has" : "teams have"} unused capacity worth a look.`;

        const chartData = data.teams.map((t) => ({
          ...t,
          spend_usd_micros: t.spend.usd_micros ?? 0,
          fill: categoryHue("fit", t.status === "over" ? "under" : t.status === "under" ? "over" : "good"),
        }));

        return (
          <div className="flex flex-col gap-8">
            <div>
              <h1 className="font-serif mb-3" style={{ fontSize: 26, color: "var(--foreground)", lineHeight: 1.3 }}>
                {headline}
              </h1>
              {highUse.length > 0 && (
                <button
                  onClick={() => onNavigate("team", highUse[0].team_id)}
                  className="text-sm font-medium px-4 py-2 rounded"
                  style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
                >
                  Review {highUse[0].name} →
                </button>
              )}
            </div>

            {/* Usage header */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <HeroTile label="Active users" value={String(data.header.users)} sub={`${data.header.agents} agents`} subVariant="muted" />
              <HeroTile label="Sessions" value={fmtCount(data.header.sessions)} sub={data.header.avg_session_minutes != null ? `${fmtDuration(data.header.avg_session_minutes)} avg length` : undefined} subVariant="muted" />
              <HeroTile label="Hours spent" value={fmtCount(data.header.hours)} sub={selection.label} subVariant="muted" />
              <HeroTile label="Avg PRs / session" value={data.header.avg_prs_per_session != null ? data.header.avg_prs_per_session.toFixed(2) : "—"} sub="output rate" subVariant="muted" />
            </div>

            {/* Session mix by type */}
            <div>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
                <div className="min-w-0"><SectionHeading title="What sessions are actually doing" sub="Session mix by type" /></div>
                <div className="flex flex-wrap items-start gap-x-6 gap-y-2">
                  {data.sessions_no_output_pct != null && (
                    <div className="text-right shrink-0">
                      <span className="font-semibold" style={{ fontSize: 20, color: "var(--amber)" }}>{fmtPct(data.sessions_no_output_pct)}</span>
                      <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>produced no code</div>
                    </div>
                  )}
                  {data.reasoning_share != null && (
                    <div className="text-right shrink-0">
                      <span className="font-semibold" style={{ fontSize: 20, color: "var(--foreground)" }}>{fmtTokens(data.reasoning_share.reasoning_tokens)}</span>
                      <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>
                        reasoning tokens{data.reasoning_share.pct != null ? ` (${data.reasoning_share.pct}%)` : ""}
                      </div>
                    </div>
                  )}
                </div>
              </div>
              {data.session_types.length > 0 ? (
                <div className="mt-4 rounded-lg p-5" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                  <SessionTypeBar data={data.session_types.map((t, i) => ({ type: sessionTypeName(t.type), count: t.count, color: categoryHue("sessionType", t.type, i) }))} />
                  <p className="mt-4 text-xs" style={{ color: "var(--muted-foreground)", maxWidth: 620 }}>
                    No-output sessions are normal for Research and Plan work — worth a look only when Develop sessions
                    end with nothing shipped.
                  </p>
                </div>
              ) : (
                <div className="mt-4">
                  <UnavailableNote info={{ block: "session_types", requires: "sessions classified by type in this window" }} />
                </div>
              )}
            </div>

            {/* Spend vs each team's budget for the selected window */}
            <div>
              <SectionHeading title="How teams use their budget" sub={`Spend vs each team's budget for ${windowName(selection)}`} />
              <div className="mt-5 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={chartData} margin={{ top: 20, right: 24, bottom: 0, left: 10 }} barSize={32}>
                    <XAxis dataKey="name" tick={{ fontSize: 12, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtMoneyMicros(v)} width={56} />
                    <Tooltip formatter={(v: unknown) => [fmtMoneyMicros(v as number), "Spend"]} contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12, fontFamily: MONO_FONT }} labelStyle={{ color: "var(--muted-foreground)" }} />
                    <Bar dataKey="spend_usd_micros" radius={[3, 3, 0, 0]}>
                      {chartData.map((entry) => <Cell key={entry.team_id} fill={entry.fill} opacity={0.9} />)}
                    </Bar>
                    {chartData.filter((t) => t.allowance.usd_micros != null).map((t) => (
                      <ReferenceLine key={t.team_id} y={t.allowance.usd_micros!} stroke="var(--border)" strokeDasharray="3 3" />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
                <div className="px-5 py-3 flex items-center gap-6 text-xs font-mono" style={{ borderTop: "1px solid var(--border)", color: "var(--muted-foreground)" }}>
                  <span className="flex items-center gap-2"><span className="w-2 h-2 rounded-sm" style={{ background: "var(--red)" }} /> High use</span>
                  <span className="flex items-center gap-2"><span className="w-2 h-2 rounded-sm" style={{ background: "var(--amber)" }} /> Under-used</span>
                  <span className="flex items-center gap-2"><span className="w-2 h-2 rounded-sm" style={{ background: "var(--primary)" }} /> On track</span>
                </div>
              </div>
            </div>

            {/* Teams table */}
            <div>
              <SectionHeading title="Team-by-team seat utilisation" />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <div className="grid px-5 py-2.5" style={{ gridTemplateColumns: "1fr 80px 100px 80px 80px", fontSize: 11, color: "var(--muted-foreground)", fontFamily: MONO_FONT, textTransform: "uppercase", letterSpacing: "0.06em", borderBottom: "1px solid var(--border)" }}>
                  <span>Team</span><span className="text-right">Spent</span><span className="text-right">Utilisation</span><span className="text-right">Budget</span><span className="text-right">Status</span>
                </div>
                {data.teams.map((t, i) => {
                  const v = statusVariant(t.status);
                  return (
                    <button
                      key={t.team_id}
                      className="grid w-full px-5 py-3.5 row-hover text-left items-center"
                      style={{ gridTemplateColumns: "1fr 80px 100px 80px 80px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                      onClick={() => onNavigate("team", t.team_id)}
                    >
                      <span className="font-medium text-sm" style={{ color: "var(--foreground)" }}>{t.name}</span>
                      <span className="text-right font-mono text-xs" style={{ color: "var(--foreground)" }} title={`${fmtTokens(t.tokens_used)} tokens`}>{fmtMoneyMicros(t.spend.usd_micros)}</span>
                      <div className="flex items-center justify-end gap-2">
                        {t.allowance.usd_micros != null && <ProgressBar value={t.spend.usd_micros ?? 0} max={t.allowance.usd_micros} variant={v} />}
                        <span className="font-mono text-xs w-8 text-right" style={{ color: "var(--muted-foreground)" }}>{t.utilization_pct != null ? fmtPct(t.utilization_pct) : "—"}</span>
                      </div>
                      <span className="text-right font-mono text-xs" style={{ color: "var(--muted-foreground)" }} title={`budget share for ${windowName(selection)}`}>{fmtMoneyMicros(t.allowance.usd_micros)}</span>
                      <span className="flex justify-end"><Chip variant={v}>{statusLabel(t.status)}</Chip></span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Spotlight team's own roster — G3 (mvp-gap-closure-plan): dropped in Phase 1
                because A2 returned no per-person data at all (src/webui/AGENTS.md's own U5
                log); TeamScreen (A4) shows any team's full roster on its own drill-down, but
                that costs a click -- this surfaces the flagged team directly here. */}
            {data.spotlight_team && (
              <div>
                <SectionHeading title={`Who's using their seat in ${data.spotlight_team.team_name}`} sub="Person-level" />
                <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                  {data.spotlight_team.people.map((p, i) => {
                    const v = statusVariant(p.status);
                    return (
                      <button
                        key={p.user_id}
                        className="flex items-center gap-4 w-full px-5 py-3 row-hover text-left"
                        style={{ borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                        onClick={() => onNavigate("person", p.user_id)}
                      >
                        <Avatar initials={initialsOf(p.name)} size={30} />
                        <span className="font-medium text-sm w-32 shrink-0 truncate" style={{ color: "var(--foreground)" }}>{p.name}</span>
                        {p.allowance.usd_micros != null && <ProgressBar value={p.spend.usd_micros ?? 0} max={p.allowance.usd_micros} variant={v} />}
                        <span className="font-mono text-xs w-16 text-right shrink-0" style={{ color: "var(--muted-foreground)" }}>
                          {p.utilization_pct != null ? fmtPct(p.utilization_pct) : "—"}
                        </span>
                        <span className="w-20 text-right shrink-0"><Chip variant={v}>{statusLabel(p.status)}</Chip></span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

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
