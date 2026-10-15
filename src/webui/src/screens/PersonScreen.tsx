import {
  HeroTile, StatStrip, SectionHeading, Avatar, Chip, QualityChip,
  fmtPct, fmtMoneyMicros, fmtRelativeTime, fmtDate, fmtDuration, BackButton, Divider
} from "../components/ui";
import { Block, UnavailableNote } from "../components/Block";
import { AgentDonut } from "../components/charts";
import { channelTotalsToSeries } from "../present/palette";
import { MONO_FONT } from "../present/fonts";
import AskBox from "../components/AskBox";
import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { useTimeframe } from "../shell/timeframe";
import type { PinnedCard } from "./types";
import { useCrumbLabel } from "../shell/crumbs";

type Props = {
  personId: string;
  onNavigate: (screen: string, param?: string) => void;
  onBack: () => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

function initialsOf(name: string): string {
  return name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase();
}

function prStatusVariant(state: string): "green" | "red" | "blue" {
  if (state === "merged") return "green";
  if (state === "reopened") return "red";
  return "blue";
}

export default function PersonScreen({ personId, onNavigate, onBack, pinnedCards, onPin }: Props) {
  const { timeframe, selection } = useTimeframe();
  const detail = useData((src) => src.person(personId, timeframe), [personId, timeframe]);
  useCrumbLabel("person", personId, detail.status === "ready" ? detail.data.data?.person.name : undefined);

  return (
    <Block state={detail} minHeight={420}>
      {(data: Schemas["PersonDetail"]) => {
        const { person, recent_prs, recent_sessions, velocity } = data;

        return (
          <div className="flex flex-col gap-8">
            <BackButton label="Team" onClick={onBack} />

            {/* Header */}
            <div className="flex items-center gap-4">
              <Avatar initials={initialsOf(person.name)} size={48} />
              <div>
                <div className="text-xs font-mono uppercase tracking-widest mb-1" style={{ color: "var(--muted-foreground)" }}>Person</div>
                <h1 className="font-semibold" style={{ fontSize: 24, color: "var(--foreground)" }}>{person.name}</h1>
              </div>
            </div>

            {/* Hero tiles */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <HeroTile label={`Spent, ${selection.label}`} value={fmtMoneyMicros(person.spend.usd_micros)} sub="total spend" subVariant="muted" />
              <HeroTile label="Merged PRs" value={String(person.merged_prs)} sub={`${fmtMoneyMicros(person.cost_per_pr.usd_micros)} avg / PR`} subVariant="muted" />
              <HeroTile label="Recoverable" value={fmtMoneyMicros(person.recoverable.usd_micros)} sub={(person.recoverable.usd_micros ?? 0) > 0 ? "could save" : "nothing flagged"} subVariant={(person.recoverable.usd_micros ?? 0) > 0 ? "amber" : "green"} />
              <HeroTile label="Velocity" value={String(velocity.reduce((s, v) => s + v.merged_prs, 0))} sub="merged PRs, trailing weeks" subVariant="muted" />
            </div>

            <StatStrip stats={[
              { label: "PRs merged", value: String(person.merged_prs) },
              { label: "avg cost/PR", value: fmtMoneyMicros(person.cost_per_pr.usd_micros) },
            ]} />

            <Divider />

            {/* Session history — G1 (mvp-gap-closure-plan): dropped in Phase 1 pending
                session-level (D1) data, which shipped in Phase 3; restored now that
                person_detail can join a session to the PR it fed. */}
            <div>
              <SectionHeading title="What each session produced" sub="Recent sessions" />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <div className="grid px-5 py-2.5" style={{ gridTemplateColumns: "90px 1fr 80px 80px 80px 60px 120px", fontSize: 11, color: "var(--muted-foreground)", fontFamily: MONO_FONT, textTransform: "uppercase", letterSpacing: "0.06em", borderBottom: "1px solid var(--border)" }}>
                  <span>Date</span>
                  <span>Repo</span>
                  <span className="text-right">Duration</span>
                  <span className="text-right">Cost</span>
                  <span className="text-right">Reasoning</span>
                  <span className="text-right">PRs</span>
                  <span className="text-right">Quality</span>
                </div>
                {recent_sessions.length === 0 && (
                  <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No sessions in range.</div>
                )}
                {recent_sessions.map((sess, i) => (
                  <button
                    key={sess.session_id}
                    onClick={() => onNavigate("session", sess.session_id)}
                    className="grid px-5 py-3 items-center row-hover w-full text-left"
                    style={{ gridTemplateColumns: "90px 1fr 80px 80px 80px 60px 120px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                  >
                    <span className="font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>{fmtDate(sess.started_at)}</span>
                    <span className="font-mono text-xs" style={{ color: "var(--foreground)" }}>{sess.repo_id ?? "—"}</span>
                    <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>{fmtDuration(sess.duration_minutes)}</span>
                    <span className="font-mono text-xs text-right" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(sess.cost_usd_micros)}</span>
                    <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>
                      {sess.reasoning_tokens != null && sess.tokens_total ? fmtPct(sess.reasoning_tokens, sess.tokens_total) : "—"}
                    </span>
                    <span className="font-mono text-xs text-right" style={{ color: sess.merged_prs > 0 ? "var(--green)" : "var(--muted-foreground)" }}>
                      {sess.merged_prs > 0 ? sess.merged_prs : "—"}
                    </span>
                    <span className="flex justify-end">
                      {sess.quality
                        ? <QualityChip label={sess.quality} />
                        : <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>—</span>}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <Divider />

            {/* Recent PRs */}
            <div>
              <SectionHeading title="Recent pull requests" sub="Output" />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                {recent_prs.length === 0 && <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No pull requests in range.</div>}
                {recent_prs.map((pr, i) => (
                  <button
                    key={pr.artifact_id + i}
                    onClick={() => onNavigate("pr", pr.artifact_id)}
                    className="grid items-center w-full px-5 py-3 row-hover text-left"
                    style={{ gridTemplateColumns: "1fr 130px 90px 100px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                  >
                    <span className="text-sm truncate" style={{ color: "var(--foreground)" }}>{pr.title ?? pr.artifact_id}</span>
                    <span className="font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>{pr.repo_id}</span>
                    <span className="flex justify-end"><Chip variant={prStatusVariant(pr.state)}>{pr.state}</Chip></span>
                    <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>{fmtRelativeTime(pr.merged_at ?? pr.closed_at ?? pr.created_at)}</span>
                  </button>
                ))}
              </div>
            </div>

            <Divider />

            {/* Where the spend went — G2 (mvp-gap-closure-plan): dropped in Phase 1 pending
                session-level (D1) data, which shipped in Phase 3; restored via the same
                channel-cost rollup D2's PR channels already use, just over this person's own
                sessions. Complements Habits below rather than replacing it — this answers
                "where did the hours go", Habits answers "what patterns cost money". */}
            <div>
              <SectionHeading title="Where the spend went" sub="Breakdown" />
              <div className="mt-4">
                {person.channels
                  ? <AgentDonut data={channelTotalsToSeries(person.channels)} />
                  : <UnavailableNote minHeight={200} info={{ block: "channels", requires: "priced sessions" }} />}
              </div>
            </div>

            <Divider />

            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
              {/* Work quality — gated on X2 (Phase 2) */}
              <div>
                <SectionHeading title="Quality breakdown" sub="Work quality" />
                <div className="mt-4">
                  {person.work_quality
                    ? (
                      <div className="rounded-lg p-5 flex flex-col gap-3" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                        {person.work_quality.filter((w) => w.count > 0).map((w) => (
                          <div key={w.label} className="flex items-center gap-3">
                            <QualityChip label={w.label} />
                            <span className="font-mono text-xs ml-auto" style={{ color: "var(--muted-foreground)" }}>{w.count} PR{w.count !== 1 ? "s" : ""}</span>
                          </div>
                        ))}
                      </div>
                    )
                    : <UnavailableNote minHeight={120} info={{ block: "work_quality", requires: "PR-lifecycle classification" }} />}
                </div>
              </div>

              {/* Peer compare -- A5-peer-task-mix: cheapest teammate by $/KLOC (spend per
                 1,000 lines changed), not $/PR (a PR is not a unit of volume). */}
              <div>
                <SectionHeading title="Peer benchmark" sub="Cheapest teammate by $/KLOC" />
                <div className="mt-4">
                  {person.peer_compare
                    ? (
                      <button
                        onClick={() => onNavigate("person", person.peer_compare!.user_id)}
                        className="rounded-lg p-5 w-full text-left row-hover flex items-center justify-between gap-4"
                        style={{ border: "1px solid var(--border)", background: "var(--card)" }}
                      >
                        <div>
                          <div className="text-sm" style={{ color: "var(--foreground)" }}>{person.peer_compare.name}</div>
                          <div className="text-xs mt-1" style={{ color: "var(--muted-foreground)" }}>View profile →</div>
                        </div>
                        <div className="font-mono text-sm" style={{ color: "var(--foreground)" }}>
                          {fmtMoneyMicros(person.peer_compare.cost_per_kloc_usd_micros)}/KLOC
                        </div>
                      </button>
                    )
                    : <UnavailableNote minHeight={120} info={{ block: "peer_compare", requires: "a priced, comparable-volume teammate" }} />}
                </div>
              </div>
            </div>

            {/* Habits */}
            {person.habits.length > 0 && (
              <>
                <Divider />
                <div>
                  <SectionHeading title="A few patterns worth trying" sub="Coachable habits" />
                  <div className="mt-4 flex flex-col gap-3">
                    {person.habits.map((h, i) => (
                      <div key={i} className="rounded-lg px-5 py-4 flex items-start justify-between gap-4" style={{ background: "var(--amber-bg)", border: "1px solid color-mix(in srgb, var(--amber) 20%, transparent)" }}>
                        <div className="font-medium text-sm" style={{ color: "var(--foreground)" }}>{h.label}</div>
                        <div className="shrink-0 text-right">
                          <div className="font-mono text-xs mb-1" style={{ color: "var(--amber)" }}>could save</div>
                          <div className="font-semibold" style={{ fontSize: 18, color: "var(--amber)" }}>{fmtMoneyMicros(h.recoverable_usd_micros)}</div>
                        </div>
                      </div>
                    ))}
                  </div>
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
