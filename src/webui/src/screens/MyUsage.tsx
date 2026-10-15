import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import {
  HeroTile, StatStrip, SectionHeading, ProgressBar,
  fmtUSD, fmtMoneyMicros, Divider
} from "../components/ui";
import { Block, Skeleton, Frame, UnavailableNote } from "../components/Block";
import AskBox from "../components/AskBox";
import { useData, MY_USER_ID } from "../data/source";
import type { Schemas } from "../data/source";
import { useTimeframe } from "../shell/timeframe";
import { inWindow, periodNoun } from "../present/cycle";
import { MONO_FONT } from "../present/fonts";
import { fmtDate, fmtDuration, fmtTokens } from "../present/format";
import type { PinnedCard } from "./types";

type Props = {
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

const SPEND_BREAKDOWN = [
  { name: "Building", value: 52, color: "var(--primary)" },
  { name: "Rework", value: 28, color: "var(--red)" },
  { name: "Reading context", value: 20, color: "var(--amber)" },
];

export default function MyUsage({ pinnedCards, onPin }: Props) {
  const { timeframe, selection, cycleLabel } = useTimeframe();
  const overview = useData((src) => src.myOverview(MY_USER_ID, timeframe), [timeframe]);
  const repos = useData((src) => src.myRepos(MY_USER_ID, timeframe), [timeframe]);
  const sessions = useData((src) => src.mySessions(MY_USER_ID, timeframe, 6), [timeframe]);
  const tips = useData((src) => src.myTips(MY_USER_ID, timeframe), [timeframe]);

  if (overview.status === "loading") return <Skeleton minHeight={480} />;
  if (overview.status === "error") {
    return <Frame minHeight={480} tone="red"><span style={{ color: "var(--red)" }}>Couldn't load your usage.</span></Frame>;
  }

  // `seat` (cost-based seats plan §3.5): your share of your team's per-period budget (budget /
  // members), for the selected window and prorated from when your seat started. `null`
  // with no team or no team budget -- then there is simply no limit to show.
  const { z1, seat, recoverable } = overview.data;
  const spendUsd = (z1.cost_usd_micros.value ?? 0) / 1_000_000;
  const limitUsd = seat?.allowance.usd_micros != null ? seat.allowance.usd_micros / 1_000_000 : null;
  // (the API names the full-period seat budget `seat_monthly`; it is per period of the org's cycle)
  const seatPeriodUsd = seat?.seat_monthly.usd_micros != null ? seat.seat_monthly.usd_micros / 1_000_000 : null;
  const overLimit = limitUsd != null && spendUsd > limitUsd;
  const pct = limitUsd ? Math.round((spendUsd / limitUsd) * 100) : null;

  return (
    <div className="flex flex-col gap-8">
      <div>
        <div className="text-xs font-mono uppercase tracking-widest mb-2" style={{ color: "var(--muted-foreground)" }}>My usage</div>
        <h1 className="font-serif mb-3" style={{ fontSize: 26, color: "var(--foreground)", lineHeight: 1.3 }}>
          You've spent {fmtUSD(spendUsd)} {inWindow(selection)}
          {limitUsd != null && (overLimit ? " — over your seat allowance" : ` — ${fmtUSD(limitUsd - spendUsd)} left in your seat allowance`)}.
        </h1>
        {limitUsd != null && pct != null && (
          <div className="flex items-center gap-3">
            <ProgressBar value={spendUsd} max={limitUsd} variant={overLimit ? "red" : pct > 80 ? "amber" : "green"} />
            <span className="font-mono text-xs shrink-0" style={{ color: "var(--muted-foreground)" }}>{pct}%</span>
          </div>
        )}
      </div>

      {/* Hero tiles */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <HeroTile label="Spent" value={fmtUSD(spendUsd)}
                 sub={pct != null ? `${pct}% of seat allowance` : selection.label} subVariant={overLimit ? "red" : "muted"} />
        <HeroTile label="Merged PRs" value={String(z1.merged_prs)}
                 sub={z1.cost_per_pr.usd_micros != null ? `${fmtMoneyMicros(z1.cost_per_pr.usd_micros)} avg / PR` : "no merged PRs yet"} subVariant="muted" />
        <HeroTile label="Sessions run" value={String(z1.sessions)} sub={selection.label} subVariant="muted" />
        <HeroTile label="Recoverable" value={fmtMoneyMicros(recoverable.usd_micros)} sub="could save" subVariant="amber" />
      </div>

      <StatStrip stats={[
        ...(limitUsd != null ? [{ label: `seat allowance (${selection.label})`, value: fmtUSD(limitUsd) }] : []),
        ...(seatPeriodUsd != null ? [{ label: `seat per ${periodNoun(cycleLabel)}`, value: fmtUSD(seatPeriodUsd) }] : []),
        { label: "tokens used", value: fmtTokens(z1.tokens.value) },
        { label: "avg cost/PR", value: fmtMoneyMicros(z1.cost_per_pr.usd_micros) },
      ]} />

      <Divider />

      {/* By repo -- U5-myusage: GET /v1/users/{id}/repos, mode-gated same as every other
         jsonl-only capability (dashboard plan §3). No per-repo PR count on this endpoint --
         the mock's "PRs" column dropped, same honest-simplification precedent as Person's
         U5 session-history drop (AGENTS.md). */}
      <div>
        {repos.status === "ready" && Array.isArray(repos.data.repos) && repos.data.repos.length > 0 ? (
          <SectionHeading title={`Most of your spend is in ${repos.data.repos[0].label}`} sub="By repo" />
        ) : (
          <SectionHeading title="Where your spend went by repo" sub="By repo" />
        )}
        <div className="mt-4">
          {repos.status === "loading" && <Skeleton minHeight={140} />}
          {repos.status === "error" && (
            <Frame minHeight={140} tone="red"><span style={{ color: "var(--red)" }}>Couldn't load this.</span></Frame>
          )}
          {repos.status === "ready" && !Array.isArray(repos.data.repos) && (
            <UnavailableNote info={repos.data.repos.unavailable} minHeight={140} />
          )}
          {repos.status === "ready" && Array.isArray(repos.data.repos) && (
            repos.data.repos.length === 0 ? (
              <Frame minHeight={140} tone="muted">Nothing in this range.</Frame>
            ) : (
              <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                {repos.data.repos.map((r, i) => (
                  <div
                    key={r.repo_id}
                    className="flex items-center justify-between gap-4 px-5 py-3.5"
                    style={{ borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                  >
                    <div>
                      <div className="font-mono text-sm" style={{ color: "var(--foreground)" }}>{r.label}</div>
                      <div className="font-mono text-xs mt-0.5" style={{ color: "var(--muted-foreground)" }}>{r.sessions} sessions</div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div style={{ width: 120 }}>
                        <ProgressBar value={r.cost_usd_micros} max={z1.cost_usd_micros.value || 1} variant="blue" />
                      </div>
                      <span className="font-mono text-sm min-w-14 text-right" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(r.cost_usd_micros)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )
          )}
        </div>
      </div>

      <Divider />

      {/* Recent sessions -- U5-myusage: GET /v1/users/{id}/sessions. That row has no merged-PR
         count or work-quality label (single-user, per-session, not per-scope like A5's), so
         those two mock columns are dropped rather than fabricated. */}
      <div>
        <SectionHeading title="Your recent sessions" sub="Session history" />
        <div className="mt-4">
          {sessions.status === "loading" && <Skeleton minHeight={200} />}
          {sessions.status === "error" && (
            <Frame minHeight={200} tone="red"><span style={{ color: "var(--red)" }}>Couldn't load this.</span></Frame>
          )}
          {sessions.status === "ready" && sessions.data.sessions.length === 0 && (
            <Frame minHeight={200} tone="muted">Nothing in this range.</Frame>
          )}
          {sessions.status === "ready" && sessions.data.sessions.length > 0 && (
            <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
              <div
                className="grid px-5 py-2.5"
                style={{ gridTemplateColumns: "80px 1fr 80px 80px", fontSize: 11, color: "var(--muted-foreground)", fontFamily: MONO_FONT, textTransform: "uppercase", letterSpacing: "0.06em", borderBottom: "1px solid var(--border)" }}
              >
                <span>Date</span>
                <span>Repo</span>
                <span className="text-right">Duration</span>
                <span className="text-right">Cost</span>
              </div>
              {sessions.data.sessions.map((s, i) => (
                <div
                  key={s.session_id}
                  className="grid px-5 py-3 items-center"
                  style={{ gridTemplateColumns: "80px 1fr 80px 80px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                >
                  <span className="font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>{fmtDate(s.started_at)}</span>
                  <span className="font-mono text-xs" style={{ color: "var(--foreground)" }}>{s.repo ?? "—"}</span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>
                    {s.duration_s != null ? fmtDuration(s.duration_s / 60) : "—"}
                  </span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(s.cost_usd_micros)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <Divider />

      {/* Where spend went + tips (tips are live, Track A7) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        <div>
          <SectionHeading title="Where your spend went" sub="Breakdown" />
          <div className="mt-4 rounded-lg p-5" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
            <div className="flex items-center gap-4">
              <ResponsiveContainer width={110} height={110}>
                <PieChart>
                  <Pie data={SPEND_BREAKDOWN} cx="50%" cy="50%" innerRadius={30} outerRadius={50} dataKey="value" strokeWidth={0}>
                    {SPEND_BREAKDOWN.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v: unknown) => [`${v as number}%`, ""]} contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex flex-col gap-2.5">
                {SPEND_BREAKDOWN.map((s) => (
                  <div key={s.name} className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.color }} />
                    <span className="text-sm" style={{ color: "var(--foreground)" }}>{s.name}</span>
                    <span className="font-mono text-xs ml-2" style={{ color: "var(--muted-foreground)" }}>{s.value}%</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Tips — A7, live */}
        <div>
          <SectionHeading title="A few things to try" sub="Tips" />
          <div className="mt-4">
            <Block state={tips} minHeight={100} isEmpty={(d) => d.tips.length === 0} emptyLabel={`Nothing to flag ${inWindow(selection)}.`}>
              {(data: Schemas["MyTips"]) => (
                <div className="flex flex-col gap-3">
                  {data.tips.map((t, i) => (
                    <div key={i} className="rounded-lg px-4 py-3.5" style={{ background: "var(--green-bg)", border: "1px solid color-mix(in srgb, var(--green) 20%, transparent)" }}>
                      <div className="text-sm leading-relaxed mb-2" style={{ color: "var(--foreground)" }}>{t.text}</div>
                      <div className="font-mono text-xs" style={{ color: "var(--green)" }}>Could save {fmtMoneyMicros(t.could_save.usd_micros)}</div>
                    </div>
                  ))}
                </div>
              )}
            </Block>
          </div>
        </div>
      </div>

      {/* Ask — out of scope (Track K); slot present, unwired */}
      <div>
        <SectionHeading title="Ask about your usage" sub="Questions" />
        <div className="mt-4">
          <AskBox pinnedCards={pinnedCards} onPin={onPin} />
        </div>
      </div>
    </div>
  );
}
