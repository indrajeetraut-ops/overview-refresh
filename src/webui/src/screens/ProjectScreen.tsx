import { MONO_FONT } from "../present/fonts";
import {
  HeroTile, StatStrip, SectionHeading, Chip, fmtMoneyMicros, fmtPct, fmtCount, fmtRelativeTime,
  BackButton, Divider
} from "../components/ui";
import { Block, UnavailableNote } from "../components/Block";
import AskBox from "../components/AskBox";
import { useData } from "../data/source";
import type { Schemas } from "../data/source";
import { useTimeframe } from "../shell/timeframe";
import type { PinnedCard } from "./types";
import { useCrumbLabel } from "../shell/crumbs";

type Props = {
  projectId: string;
  onNavigate: (screen: string, param?: string) => void;
  onBack: () => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

function prStatusVariant(state: string): "green" | "red" | "blue" {
  if (state === "merged") return "green";
  if (state === "reopened" || state === "closed") return "red";
  return "blue";
}

export default function ProjectScreen({ projectId, onNavigate, onBack, pinnedCards, onPin }: Props) {
  const { timeframe, selection } = useTimeframe();
  const detail = useData((src) => src.project(projectId, timeframe), [projectId, timeframe]);
  useCrumbLabel("project", projectId, detail.status === "ready" ? detail.data.data?.project.name : undefined);

  return (
    <Block state={detail} minHeight={420}>
      {(data: Schemas["ProjectDetail"]) => {
        const { project, contributors, highlights, features, prs } = data;
        const shippedCount = features?.filter((f) => f.status === "shipped").length ?? null;

        return (
          <div className="flex flex-col gap-8">
            <BackButton label="Overview" onClick={onBack} />

            <div>
              <div className="text-xs font-mono uppercase tracking-widest mb-2" style={{ color: "var(--muted-foreground)" }}>Project</div>
              <h1 className="font-serif mb-2" style={{ fontSize: 26, color: "var(--foreground)" }}>
                Is {project.name} worth it?
              </h1>
              <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
                {fmtMoneyMicros(project.spend.usd_micros)} spent · {project.merged_prs} PRs merged · {contributors.length} contributors
              </p>
            </div>

            {/* Hero tiles */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <HeroTile label="Total spend" value={fmtMoneyMicros(project.spend.usd_micros)} sub={`${project.merged_prs} PRs merged`} subVariant="green" />
              <HeroTile label="Avg cost per PR" value={fmtMoneyMicros(project.cost_per_pr.usd_micros)} sub="this project" subVariant="muted" />
              <HeroTile label="Features shipped" value={shippedCount != null ? String(shippedCount) : "—"} sub={features ? `of ${features.length} tracked` : "not connected"} subVariant="muted" />
              <HeroTile label="Contributors" value={String(contributors.length)} sub={selection.label} subVariant="muted" />
            </div>

            <StatStrip stats={[
              { label: "repo", value: project.name },
              ...(project.rework_rate != null ? [{ label: "rework rate", value: fmtPct(project.rework_rate) }] : []),
              { label: "lines added", value: fmtCount(highlights.lines_added) },
              { label: "lines removed", value: fmtCount(highlights.lines_removed) },
            ]} />

            <Divider />

            {/* Feature cost table — F4 */}
            <div>
              <SectionHeading title="What each feature cost to build" sub="Features" />
              <div className="mt-4">
                {features ? (
                  <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                    <div
                      className="grid px-5 py-2.5"
                      style={{ gridTemplateColumns: "1fr 90px 70px 100px", fontSize: 11, color: "var(--muted-foreground)", fontFamily: MONO_FONT, textTransform: "uppercase", letterSpacing: "0.06em", borderBottom: "1px solid var(--border)" }}
                    >
                      <span>Feature</span>
                      <span className="text-right">Cost</span>
                      <span className="text-right">PRs</span>
                      <span className="text-right">Status</span>
                    </div>
                    {features.length === 0 && (
                      <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No features tracked yet.</div>
                    )}
                    {features.map((f, i) => (
                      <button
                        key={f.issue_key}
                        className="grid w-full px-5 py-3.5 row-hover text-left items-center"
                        style={{ gridTemplateColumns: "1fr 90px 70px 100px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                        onClick={() => onNavigate("feature", f.id)}
                      >
                        <div>
                          <div className="font-medium text-sm" style={{ color: "var(--foreground)" }}>{f.title}</div>
                          <div className="text-xs mt-0.5 font-mono" style={{ color: "var(--muted-foreground)" }}>{f.issue_key}</div>
                        </div>
                        <span className="text-right font-mono text-sm" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(f.spend.usd_micros)}</span>
                        <span className="text-right font-mono text-xs" style={{ color: "var(--foreground)" }}>{f.merged_prs}</span>
                        <span className="flex justify-end">
                          <Chip variant={f.status === "shipped" ? "green" : "blue"}>
                            {f.status === "shipped" ? "Shipped" : "In progress"}
                          </Chip>
                        </span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <UnavailableNote minHeight={140} info={{ block: "features", requires: "a connected GitHub App" }} />
                )}
              </div>
            </div>

            <Divider />

            {/* Contributors */}
            <div>
              <SectionHeading title="Who's contributing" sub="Contributors" />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                {contributors.length === 0 && (
                  <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No contributors in range.</div>
                )}
                {contributors.map((c, i) => (
                  <button
                    key={c.user_id}
                    className="flex items-center justify-between gap-4 w-full px-5 py-3 row-hover text-left"
                    style={{ borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                    onClick={() => onNavigate("person", c.user_id)}
                  >
                    <span className="text-sm" style={{ color: "var(--foreground)" }}>{c.name}</span>
                    <span className="flex items-center gap-4 font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>
                      <span>{c.merged_prs} PRs</span>
                      <span style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(c.spend.usd_micros)}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <Divider />

            {/* All pull requests — G4 (mvp-gap-closure-plan): dropped when F4 shipped Phase 2
                ("Dropped from Project detail ... the 'all pull requests' list", src/webui/
                AGENTS.md's own U6 log said D2/D3 would bring it back; D2/D3 shipped in Phase 3
                and it was never revisited). Rebuilt directly off git_artifacts, independent of
                F4's feature grouping above -- every PR in range, any state. */}
            <div>
              <SectionHeading title="All pull requests" sub="PRs" />
              <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                {prs.length === 0 && (
                  <div className="px-5 py-6 text-sm" style={{ color: "var(--muted-foreground)" }}>No pull requests in range.</div>
                )}
                {prs.map((pr, i) => (
                  <button
                    key={pr.artifact_id}
                    onClick={() => onNavigate("pr", pr.artifact_id)}
                    className="flex items-center justify-between gap-4 w-full px-5 py-3 row-hover text-left"
                    style={{ borderTop: i > 0 ? "1px solid var(--border)" : undefined }}
                  >
                    <div className="min-w-0">
                      <div className="font-mono text-xs mb-0.5" style={{ color: "var(--muted-foreground)" }}>
                        {pr.number != null ? `#${pr.number}` : pr.artifact_id}
                      </div>
                      <div className="text-sm truncate" style={{ color: "var(--foreground)" }}>{pr.title ?? pr.artifact_id}</div>
                    </div>
                    <div className="flex items-center gap-4 shrink-0">
                      {pr.author_name && <span className="font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>{pr.author_name}</span>}
                      <span className="font-mono text-xs" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(pr.cost_usd_micros)}</span>
                      <Chip variant={prStatusVariant(pr.state)}>{pr.state}</Chip>
                      <span className="font-mono text-xs" style={{ color: "var(--muted-foreground)" }}>
                        {fmtRelativeTime(pr.merged_at ?? pr.closed_at ?? pr.created_at)}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* Ask — out of scope (Track K); slot present, unwired */}
            <div>
              <SectionHeading title="Ask" sub="Questions" />
              <div className="mt-4">
                <AskBox pinnedCards={pinnedCards} onPin={onPin} />
              </div>
            </div>
          </div>
        );
      }}
    </Block>
  );
}
