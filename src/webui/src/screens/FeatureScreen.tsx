import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { Block, UnavailableNote } from "../components/Block";
import {
  HeroTile, SectionHeading, Chip, Avatar, BackButton, Divider, LifecycleTimeline, QualityChip,
} from "../components/ui";
import { AgentDonut, PercentileBar } from "../components/charts";
import { fmtMoneyMicros, fmtDate } from "../present/format";
import { channelTotalsToSeries } from "../present/palette";
import { positionLifecycle } from "../present/lifecycle";
import AskBox from "../components/AskBox";
import type { PinnedCard } from "./types";
import { useCrumbLabel } from "../shell/crumbs";

type Props = {
  featureId: string;
  onNavigate: (screen: string, param?: string) => void;
  onBack: () => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

const PR_VARIANT: Record<string, "green" | "red" | "blue"> = { merged: "green", closed: "red", open: "blue" };

export default function FeatureScreen({ featureId, onNavigate, onBack, pinnedCards, onPin }: Props) {
  const detail = useData((src) => src.featureDetail(featureId), [featureId]);
  useCrumbLabel("feature", featureId, detail.status === "ready" ? detail.data.data?.title : undefined);

  return (
    <Block state={detail} minHeight={420}>
      {(data: Schemas["FeatureDetail"]) => {
        const { id, issue_key, title, status, repo_id, spend, channels, member_prs, contributors,
          lifecycle: rawLifecycle, cost_to_finish, benchmark } = data;
        void id;
        const merged = member_prs.filter((p) => p.state === "merged").length;

        const lifecycle = positionLifecycle([
          { name: "first work", ts: rawLifecycle.first_session_started_at, spanKind: "build" },
          { name: "first PR opened", ts: rawLifecycle.first_pr_opened_at, spanKind: "build" },
          { name: rawLifecycle.last_merged_at ? "last merged" : "in progress", ts: rawLifecycle.last_merged_at },
        ]);

        return (
          <div className="flex flex-col gap-8">
            <BackButton label="Back" onClick={onBack} />

            {/* Header */}
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-xs font-mono uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Issue {issue_key}</span>
                <Chip variant={status === "shipped" ? "green" : "blue"}>{status}</Chip>
              </div>
              <h1 className="font-semibold" style={{ fontSize: 24, color: "var(--foreground)", lineHeight: 1.25 }}>{title}</h1>
              {repo_id && (
                <div className="flex items-center gap-2 flex-wrap text-xs font-mono mt-2" style={{ color: "var(--muted-foreground)" }}>
                  <span>{repo_id}</span>
                </div>
              )}
            </div>

            {/* Hero */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <HeroTile label="Total cost" value={fmtMoneyMicros(spend.usd_micros)} sub={`${member_prs.length} PR(s) · ${merged} merged`} subVariant="muted" />
              <HeroTile label="Contributors" value={String(contributors.length)} sub="this feature" subVariant="muted" />
              <HeroTile label="First work" value={fmtDate(rawLifecycle.first_session_started_at)} sub="started" subVariant="muted" />
              <HeroTile label="Last merged" value={fmtDate(rawLifecycle.last_merged_at)} sub={rawLifecycle.last_merged_at ? "shipped" : "in progress"} subVariant="muted" />
            </div>

            <Divider />

            {/* Lifecycle */}
            <div>
              <SectionHeading title="Delivery timeline" sub="Lifecycle" />
              <div className="mt-4 rounded-lg p-6 pt-9" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <LifecycleTimeline milestones={lifecycle} />
              </div>
            </div>

            {/* Cost to finish */}
            <div>
              {cost_to_finish ? (
                cost_to_finish.open_prs > 0 ? (
                  <div className="rounded-lg p-5" style={{ border: "1px solid var(--primary)", background: "color-mix(in srgb, var(--primary) 5%, var(--card))" }}>
                    <div className="flex items-baseline justify-between gap-4 flex-wrap">
                      <div>
                        <div className="text-xs font-mono uppercase tracking-widest mb-1" style={{ color: "var(--muted-foreground)" }}>Cost to finish</div>
                        <div className="text-sm" style={{ color: "var(--foreground)" }}>
                          About <span className="font-semibold" style={{ fontSize: 20 }}>{fmtMoneyMicros(cost_to_finish.mid_usd_micros)}</span> more to close the {cost_to_finish.open_prs} open PR(s)
                          <span style={{ color: "var(--muted-foreground)" }}> (range {fmtMoneyMicros(cost_to_finish.low_usd_micros)}–{fmtMoneyMicros(cost_to_finish.high_usd_micros)})</span>
                        </div>
                        <div className="text-xs mt-1" style={{ color: "var(--muted-foreground)" }}>{cost_to_finish.basis}</div>
                      </div>
                      <div className="text-right">
                        <div className="text-xs font-mono uppercase tracking-widest mb-1" style={{ color: "var(--muted-foreground)" }}>Projected total</div>
                        <div className="font-semibold" style={{ fontSize: 24, color: "var(--foreground)" }}>
                          {fmtMoneyMicros((spend.usd_micros ?? 0) + cost_to_finish.mid_usd_micros)}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <Frame minHeight={60} text="No open PRs remain." />
                )
              ) : (
                <UnavailableNote minHeight={80} info={{ block: "cost_to_finish", requires: "merged-PR cost history" }} />
              )}
            </div>

            <Divider />

            {/* Cost decomposition + contributors */}
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <SectionHeading title="Where the money went" sub="By channel" />
                <div className="mt-4">
                  {channels ? (
                    <AgentDonut data={channelTotalsToSeries(channels)} />
                  ) : (
                    <UnavailableNote minHeight={200} info={{ block: "channels", requires: "priced sessions" }} />
                  )}
                </div>
              </div>
              <div>
                <SectionHeading title="Who built it" sub="Contributors" />
                <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                  {contributors.length === 0 && (
                    <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No contributors in range.</div>
                  )}
                  {contributors.map((c, i) => (
                    <button key={c.user_id} onClick={() => onNavigate("person", c.user_id)}
                      className="flex items-center gap-3 w-full px-5 py-3.5 row-hover text-left"
                      style={{ borderTop: i > 0 ? "1px solid var(--border)" : undefined }}>
                      <Avatar initials={c.name.slice(0, 2).toUpperCase()} size={28} />
                      <span className="text-sm" style={{ color: "var(--foreground)" }}>{c.name}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <Divider />

            {/* PRs */}
            <div>
              <SectionHeading title="The pull requests" sub={`${member_prs.length} under this issue`} />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                {member_prs.length === 0 && (
                  <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No PRs linked yet.</div>
                )}
                {member_prs.map((pr, i) => (
                  <button key={pr.artifact_id} onClick={() => onNavigate("pr", pr.artifact_id)}
                    className="grid items-center w-full px-5 py-3.5 row-hover text-left"
                    style={{ gridTemplateColumns: "56px 1fr 90px 90px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}>
                    <span className="font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>{pr.number != null ? `#${pr.number}` : "—"}</span>
                    <span className="text-sm truncate" style={{ color: "var(--foreground)" }}>{pr.title ?? "Untitled PR"}</span>
                    <span className="font-mono text-xs text-right" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(pr.cost.usd_micros)}</span>
                    <span className="flex justify-end"><Chip variant={PR_VARIANT[pr.state] ?? "blue"}>{pr.state}</Chip></span>
                  </button>
                ))}
              </div>
            </div>

            {/* Benchmark — D3-benchmark: this feature's cost percentile vs its repo peers */}
            <div>
              <SectionHeading title="vs similar features" sub="Benchmark" />
              <div className="mt-4">
                {benchmark ? (
                  <div className="rounded-xl border p-5" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                    <div className="text-sm mb-4" style={{ color: "var(--muted-foreground)" }}>
                      This feature cost <span className="font-mono" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(benchmark.subject_cost_usd_micros)}</span>
                      {" — "}more than <span style={{ color: "var(--foreground)" }}>{benchmark.cost_percentile}%</span> of {benchmark.peer_count} other features in this repo.
                    </div>
                    <PercentileBar label="Feature cost across the repo" unit="$" d={benchmark.cost_dist} />
                  </div>
                ) : (
                  <UnavailableNote minHeight={96} info={{ block: "benchmark", requires: "a cross-feature cost distribution" }} />
                )}
              </div>
            </div>

            <Divider />

            {/* Ask */}
            <div>
              <SectionHeading title="Ask this issue" sub="Questions" />
              <div className="mt-4"><AskBox pinnedCards={pinnedCards} onPin={onPin} /></div>
            </div>
          </div>
        );
      }}
    </Block>
  );
}

function Frame({ minHeight, text }: { minHeight: number; text: string }) {
  return (
    <div className="rounded-lg flex items-center px-5" style={{ minHeight, border: "1px solid var(--border)", background: "var(--card)", color: "var(--muted-foreground)" }}>
      <span className="text-sm">{text}</span>
    </div>
  );
}
