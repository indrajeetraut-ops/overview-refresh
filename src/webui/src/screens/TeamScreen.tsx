import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ReferenceLine, ResponsiveContainer, Cell
} from "recharts";
import {
  HeroTile, StatStrip, SectionHeading, Avatar, Chip, ProgressBar, WorthALook,
  fmtPct, fmtMoneyMicros, fmtDays, BackButton, Divider
} from "../components/ui";
import { Block } from "../components/Block";
import AskBox from "../components/AskBox";
import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { useTimeframe } from "../shell/timeframe";
import { inWindow } from "../present/cycle";
import { MONO_FONT } from "../present/fonts";
import { seatChip, seatUsageChartData } from "../present/seats";
import type { PinnedCard } from "./types";
import { useCrumbLabel } from "../shell/crumbs";

type Props = {
  teamId: string;
  onNavigate: (screen: string, param?: string) => void;
  onBack: () => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};


export default function TeamScreen({ teamId, onNavigate, onBack, pinnedCards, onPin }: Props) {
  const { timeframe, selection } = useTimeframe();
  const detail = useData((src) => src.team(teamId, timeframe), [teamId, timeframe]);
  useCrumbLabel("team", teamId, detail.status === "ready" ? detail.data.data?.team.name : undefined);

  return (
    <Block state={detail} minHeight={420}>
      {(data: Schemas["TeamDetail"]) => {
        const { team, people, highlights, worth_a_look } = data;
        const overBudget = team.over_budget;

        const headline = team.budget_pct != null
          ? overBudget
            ? `${team.name} is over its budget ${inWindow(selection)} — ${fmtPct(team.budget_pct)} spent.`
            : `${team.name} has spent ${fmtPct(team.budget_pct)} of its budget ${inWindow(selection)}.`
          : `${team.name} spent ${fmtMoneyMicros(team.spend.usd_micros)} ${inWindow(selection)} — no budget set.`;

        // Seats with no allowance (no team budget) are left out, not drawn at 0%.
        const usageChartData = seatUsageChartData(people);

        return (
          <div className="flex flex-col gap-8">
            <BackButton label="All teams" onClick={onBack} />

            <div>
              <div className="text-xs font-mono uppercase tracking-widest mb-2" style={{ color: "var(--muted-foreground)" }}>Team</div>
              <h1 className="font-serif mb-3" style={{ fontSize: 26, color: "var(--foreground)", lineHeight: 1.3 }}>{headline}</h1>
              {overBudget && <Chip variant="red">Over budget</Chip>}
            </div>

            {/* Hero tiles */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <HeroTile label="Spent" value={fmtMoneyMicros(team.spend.usd_micros)} sub={team.budget_pct != null ? `${fmtPct(team.budget_pct)} of budget` : undefined} subVariant={overBudget ? "red" : "muted"} />
              <HeroTile label="Merged PRs" value={String(team.merged_prs)} sub={`${fmtMoneyMicros(team.cost_per_pr.usd_micros)} avg / PR`} subVariant="muted" />
              <HeroTile label="Room to save" value={String(highlights.room_to_save_count)} sub={`of ${highlights.members} people`} subVariant={highlights.room_to_save_count > 0 ? "amber" : "green"} />
              <HeroTile label="Low seat use" value={String(highlights.low_use_count)} sub={`of ${highlights.members} seats`} subVariant={highlights.low_use_count > 0 ? "amber" : "green"} />
            </div>

            <StatStrip stats={[
              { label: "people", value: String(highlights.members) },
              { label: "PRs merged", value: String(highlights.merged_prs) },
              ...(highlights.avg_time_to_pr_days != null
                ? [{ label: "avg time to PR", value: fmtDays(highlights.avg_time_to_pr_days) }]
                : []),
              ...(highlights.rework_rate != null ? [{ label: "rework rate", value: fmtPct(highlights.rework_rate) }] : []),
            ]} />

            <Divider />

            {/* People ranked by recoverable spend */}
            <div>
              <SectionHeading title="People with room to save" sub="Ranked by recoverable spend" />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <div className="grid px-5 py-2.5" style={{ gridTemplateColumns: "1fr 80px 110px 100px 90px", fontSize: 11, color: "var(--muted-foreground)", fontFamily: MONO_FONT, textTransform: "uppercase", letterSpacing: "0.06em", borderBottom: "1px solid var(--border)" }}>
                  <span>Person</span><span className="text-right">Spent</span><span className="text-right">seat usage</span><span className="text-right">Recoverable</span><span className="text-right">Status</span>
                </div>
                {people.map((p, i) => {
                  const chip = seatChip(p);
                  const v = chip.variant;
                  return (
                    <button key={p.user_id} className="grid w-full px-5 py-3.5 row-hover text-left items-center"
                      style={{ gridTemplateColumns: "1fr 80px 110px 100px 90px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                      onClick={() => onNavigate("person", p.user_id)}>
                      <span className="flex items-center gap-3">
                        <Avatar initials={p.name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase()} size={28} />
                        <span className="font-medium text-sm" style={{ color: "var(--foreground)" }}>{p.name}</span>
                      </span>
                      <span className="text-right font-mono text-sm" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(p.spend.usd_micros)}</span>
                      <div className="flex items-center gap-2 justify-end"
                        title={p.usage_pct != null ? `${fmtMoneyMicros(p.spend.usd_micros)} of ${fmtMoneyMicros(p.seat_allowance.usd_micros)} seat allowance, ${selection.label}` : "no team budget, so no seat allowance"}>
                        {p.usage_pct != null && <ProgressBar value={p.usage_pct} max={1} variant={v} />}
                        <span className="font-mono text-xs w-8 text-right shrink-0" style={{ color: "var(--muted-foreground)" }}>{p.usage_pct != null ? fmtPct(p.usage_pct) : "—"}</span>
                      </div>
                      <span className="text-right font-mono text-xs" style={{ color: (p.recoverable.usd_micros ?? 0) > 0 ? "var(--amber)" : "var(--muted-foreground)" }}>
                        {(p.recoverable.usd_micros ?? 0) > 0 ? fmtMoneyMicros(p.recoverable.usd_micros) : "—"}
                        {p.recoverable_pct != null && (p.recoverable.usd_micros ?? 0) > 0 && (
                          <span className="block" style={{ color: "var(--muted-foreground)" }}>{fmtPct(p.recoverable_pct)} of spend</span>
                        )}
                      </span>
                      <span className="flex justify-end"><Chip variant={v}>{chip.label}</Chip></span>
                    </button>
                  );
                })}
              </div>
            </div>

            <Divider />

            {/* Seat usage chart */}
            <div>
              <SectionHeading title="Seat utilisation per person" sub="Spend vs seat allowance" />
              <div className="mt-5 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={usageChartData} margin={{ top: 16, right: 24, bottom: 0, left: 10 }} barSize={24}>
                    <XAxis dataKey="name" tick={{ fontSize: 12, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)", fontFamily: MONO_FONT }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtPct(v)} width={44} />
                    <Tooltip formatter={(v: unknown) => [fmtPct(v as number), "Usage"]} contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12, fontFamily: MONO_FONT }} labelStyle={{ color: "var(--muted-foreground)" }} />
                    <ReferenceLine y={1} stroke="var(--border)" strokeDasharray="3 3" label={{ value: "seat allowance", fill: "var(--muted-foreground)", fontSize: 10, fontFamily: MONO_FONT }} />
                    <Bar dataKey="usage_pct" radius={[3, 3, 0, 0]}>
                      {usageChartData.map((entry, i) => <Cell key={i} fill={entry.fill} opacity={0.85} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {worth_a_look.length > 0 && (
              <>
                <Divider />
                <div>
                  <SectionHeading title="Worth a look" />
                  <div className="mt-4"><WorthALook items={worth_a_look} onNavigate={onNavigate} /></div>
                </div>
              </>
            )}

            {/* Ask — out of scope (Track K); slot present, unwired */}
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
