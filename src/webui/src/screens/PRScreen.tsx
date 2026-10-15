import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { Block, UnavailableNote } from "../components/Block";
import {
  HeroTile, SectionHeading, Chip, Avatar, BackButton, Divider, LifecycleTimeline, QualityChip,
  ModelUsageBars,
} from "../components/ui";
import { AgentDonut } from "../components/charts";
import { fmtMoneyMicros, fmtCount, fmtDate, fmtDays } from "../present/format";
import { channelTotalsToSeries } from "../present/palette";
import { positionLifecycle } from "../present/lifecycle";
import AskBox from "../components/AskBox";
import type { PinnedCard } from "./types";
import { useCrumbLabel } from "../shell/crumbs";

type Props = {
  prId: string;
  onNavigate: (screen: string, param?: string) => void;
  onBack: () => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

const REVIEW_VARIANT: Record<string, "green" | "red" | "muted"> = {
  APPROVED: "green", CHANGES_REQUESTED: "red", COMMENTED: "muted",
};
const REVIEW_TEXT: Record<string, string> = {
  APPROVED: "approved", CHANGES_REQUESTED: "changes requested", COMMENTED: "commented",
};

export default function PRScreen({ prId, onNavigate, onBack, pinnedCards, onPin }: Props) {
  const detail = useData((src) => src.prDetail(prId), [prId]);
  useCrumbLabel("pr", prId, detail.status === "ready" ? detail.data.data?.pr.title : undefined);

  return (
    <Block state={detail} minHeight={420}>
      {(data: Schemas["PRDetail"]) => {
        const { pr, cost, review, sessions, flags, quality, models } = data;
        const statusVariant = pr.state === "merged" ? "green" : pr.state === "closed" ? "red" : "blue";
        const postOpenPct = cost.spend.usd_micros && cost.post_open?.usd_micros
          ? Math.round((cost.post_open.usd_micros / cost.spend.usd_micros) * 100) : null;

        const lifecycle = positionLifecycle([
          { name: "opened", ts: pr.created_at, spanKind: "build" },
          ...(review.reviews[0] ? [{ name: "first review", ts: review.reviews[0].submitted_at, spanKind: "review" as const }] : []),
          { name: pr.merged_at ? "merged" : pr.closed_at ? "closed" : "open", ts: pr.merged_at ?? pr.closed_at },
        ]);

        return (
          <div className="flex flex-col gap-8">
            <BackButton label="Back" onClick={onBack} />

            {/* Header */}
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-xs font-mono uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>
                  Pull request {pr.number != null ? `#${pr.number}` : pr.artifact_id}
                </span>
                <Chip variant={statusVariant}>{pr.state}</Chip>
              </div>
              <h1 className="font-semibold" style={{ fontSize: 24, color: "var(--foreground)", lineHeight: 1.25 }}>{pr.title ?? "Untitled PR"}</h1>
              <div className="flex items-center gap-2 flex-wrap text-xs font-mono mt-2" style={{ color: "var(--muted-foreground)" }}>
                {pr.branch && pr.base_branch && <span>{pr.branch} → {pr.base_branch}</span>}
                {pr.author_name && <><span style={{ opacity: 0.5 }}>·</span><span>{pr.author_name}</span></>}
                {pr.linked_issue && (
                  <button className="ml-1" onClick={() => onNavigate("feature", pr.linked_issue!)}>
                    <Chip variant="blue">{pr.linked_issue}</Chip>
                  </button>
                )}
                {pr.labels.map((l) => <Chip key={l} variant="muted">{l}</Chip>)}
              </div>
            </div>

            {/* Hero */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <HeroTile label="Cost to produce" value={fmtMoneyMicros(cost.spend.usd_micros)} sub={`${sessions.length} session(s)`} subVariant="muted" />
              <HeroTile label="Review rounds" value={String(review.review_rounds)} sub={`${review.comment_count ?? 0} comments`} subVariant="muted" />
              <HeroTile label="Time to merge" value={fmtDays(review.time_to_merge_days)} sub="open → merge" subVariant="muted" />
              <HeroTile label="Quality" value={quality ?? "—"} sub="EFF quadrant" subVariant="muted" />
            </div>

            {/* Pre-open vs post-open cost split */}
            {cost.pre_open && cost.post_open ? (
              <div className="rounded-lg p-5" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <div className="flex items-baseline justify-between mb-3">
                  <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
                    Built for {fmtMoneyMicros(cost.pre_open.usd_micros)}, then {fmtMoneyMicros(cost.post_open.usd_micros)} more to survive review
                  </span>
                  {postOpenPct != null && <Chip variant="red">{postOpenPct}% after open</Chip>}
                </div>
                <div className="flex rounded-md overflow-hidden" style={{ height: 22 }}>
                  <div className="flex items-center px-3" style={{ flex: cost.pre_open.usd_micros ?? 1, background: "color-mix(in srgb, var(--primary) 22%, transparent)" }}>
                    <span className="text-xs" style={{ color: "var(--foreground)" }}>build {fmtMoneyMicros(cost.pre_open.usd_micros)}</span>
                  </div>
                  <div className="flex items-center px-3" style={{ flex: cost.post_open.usd_micros ?? 1, background: "color-mix(in srgb, var(--red) 20%, transparent)" }}>
                    <span className="text-xs" style={{ color: "var(--foreground)" }}>review/rework {fmtMoneyMicros(cost.post_open.usd_micros)}</span>
                  </div>
                </div>
              </div>
            ) : (
              <UnavailableNote minHeight={80} info={{ block: "cost split", requires: "linked sessions" }} />
            )}

            <Divider />

            {/* Lifecycle */}
            <div>
              <SectionHeading title="From open to merge" sub="Lifecycle" />
              <div className="mt-4 rounded-lg p-6 pt-9" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <LifecycleTimeline milestones={lifecycle} />
              </div>
            </div>

            {/* Reviews */}
            <div>
              <SectionHeading title={`${review.review_rounds} round(s) of review`} sub="Reviews" />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                {review.reviews.length === 0 && (
                  <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No reviews yet.</div>
                )}
                {review.reviews.map((r, i) => (
                  <div key={i} className="flex items-center gap-3 px-5 py-3" style={{ borderTop: i > 0 ? "1px solid var(--border)" : undefined }}>
                    <Avatar initials={(r.reviewer_name ?? "?").slice(0, 2).toUpperCase()} size={28} />
                    <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>{r.reviewer_name ?? r.reviewer_user_id ?? "unknown"}</span>
                    <Chip variant={REVIEW_VARIANT[r.state] ?? "muted"}>{REVIEW_TEXT[r.state] ?? r.state.toLowerCase()}</Chip>
                    <span className="text-xs font-mono ml-auto" style={{ color: "var(--muted-foreground)" }}>{fmtDate(r.submitted_at)}</span>
                  </div>
                ))}
              </div>
            </div>

            <Divider />

            {/* Where the cost went */}
            <div>
              <SectionHeading title="Where the cost went" sub="By channel" />
              <div className="mt-4">
                {cost.channels ? (
                  <AgentDonut data={channelTotalsToSeries(cost.channels)} />
                ) : (
                  <UnavailableNote minHeight={200} info={{ block: "channels", requires: "priced sessions" }} />
                )}
              </div>
            </div>

            {/* Models used — QW1 (mvp-gap-closure-plan) */}
            <div>
              <SectionHeading title="Models used" sub="Token share" />
              <div className="mt-4">
                {models && models.length > 0 ? (
                  <ModelUsageBars entries={models} />
                ) : (
                  <UnavailableNote minHeight={96} info={{ block: "models", requires: "priced sessions" }} />
                )}
              </div>
            </div>

            {/* Contributing sessions */}
            <div>
              <SectionHeading title="The sessions that built and reworked it" sub="Contributing sessions" />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                {sessions.length === 0 && (
                  <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No linked sessions.</div>
                )}
                {sessions.map((sess, i) => (
                  <button key={`${sess.session_id}-${i}`} onClick={() => onNavigate("session", sess.session_id)}
                    className="flex items-center gap-4 w-full px-5 py-3.5 row-hover text-left"
                    style={{ borderTop: i > 0 ? "1px solid var(--border)" : undefined }}>
                    <Chip variant={sess.role === "build" ? "blue" : "amber"}>{sess.role}</Chip>
                    <span className="text-sm truncate" style={{ color: "var(--foreground)" }}>{sess.title ?? sess.session_id}</span>
                    <span className="text-xs font-mono ml-auto" style={{ color: "var(--muted-foreground)" }}>{fmtMoneyMicros(sess.cost_usd_micros)} →</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Output & quality */}
            <div>
              <SectionHeading title="What shipped, and how clean it was" sub="Output & quality" />
              <div className="mt-4 rounded-lg p-5 flex items-center gap-8 flex-wrap" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <div>
                  <div className="text-xs font-mono uppercase tracking-widest mb-1" style={{ color: "var(--muted-foreground)" }}>Diff</div>
                  <div className="font-mono text-sm">
                    <span style={{ color: "var(--green)" }}>+{fmtCount(pr.lines_added)}</span>{" "}
                    <span style={{ color: "var(--red)" }}>−{fmtCount(pr.lines_removed)}</span>{" "}
                    <span style={{ color: "var(--muted-foreground)" }}>· {pr.files_changed ?? "—"} files · {pr.commits ?? "—"} commits</span>
                  </div>
                </div>
                <div>
                  <div className="text-xs font-mono uppercase tracking-widest mb-1.5" style={{ color: "var(--muted-foreground)" }}>Quality</div>
                  {quality ? <QualityChip label={quality} /> : <span className="text-sm" style={{ color: "var(--muted-foreground)" }}>not yet classified</span>}
                </div>
              </div>
            </div>

            {/* Flags */}
            {flags.length > 0 && (
              <div>
                <SectionHeading title="Worth a look" sub="Flags" />
                <div className="mt-4 flex flex-col gap-2">
                  {flags.map((f) => (
                    <div key={f.label} className="flex items-start gap-3 rounded-lg px-4 py-3" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                      <span className="w-2 h-2 rounded-full shrink-0 mt-1.5" style={{ background: f.sev === "red" ? "var(--red)" : "var(--amber)" }} />
                      <div>
                        <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>{f.label}</div>
                        <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>{f.detail}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <Divider />

            {/* Ask */}
            <div>
              <SectionHeading title="Ask this PR" sub="Questions" />
              <div className="mt-4"><AskBox pinnedCards={pinnedCards} onPin={onPin} /></div>
            </div>
          </div>
        );
      }}
    </Block>
  );
}
