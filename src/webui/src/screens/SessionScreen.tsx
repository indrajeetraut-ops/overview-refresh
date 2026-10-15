import { useData } from "../data/source";
import {
  HeroTile, SectionHeading, Chip, BackButton, Divider, LifecycleTimeline, ModelUsageBars,
  type ModelUsageEntry,
} from "../components/ui";
import { Frame, Skeleton, UnavailableNote } from "../components/Block";
import { AgentDonut, ContextCurve } from "../components/charts";
import { fmtMoneyMicros, fmtTokens, fmtCount, fmtDate, fmtPct } from "../present/format";
import { channelTotalsToSeries, sessionTypeName } from "../present/palette";
import { positionLifecycle } from "../present/lifecycle";
import AskBox from "../components/AskBox";
import type { PinnedCard } from "./types";
import { useCrumbLabel } from "../shell/crumbs";

type Props = {
  sessionId: string;
  onNavigate: (screen: string, param?: string) => void;
  onBack: () => void;
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

// ── local types for the still-unported single-user session_detail dict (schemas/README.md's
// own porting table: "port with its screen" — the base payload stays a bare dict; only the
// `economics` block (D1, this screen's own reason for existing) is worth hand-typing here,
// mirroring the same tradeoff `myOverview`'s `Record<string, unknown>` already accepted). ──
type Unavailable = { unavailable: { block: string; requires: string } };
function isUnavailable(x: unknown): x is Unavailable {
  return !!x && typeof x === "object" && "unavailable" in x;
}

type Sourced<T> = { value: T; source: string };
type ChannelValue = { tokens: number; usd_micros: number | null; share: number | null };
type SessionChannels = Record<"carry" | "recache" | "output" | "input", Sourced<ChannelValue>>;

type RepoCostSplit = {
  repo_id: string; repo_key: string; display: string; refs: number;
  share: number | null; primary: boolean; cost_usd_micros: number | null;
};
type ModelFit = { verdict: "good" | "over" | "under"; note: string; savings_usd_micros: number | null };
type SessionSegment = {
  index: number; label: string | null; confidence: number | null; turns: number | null;
  files: number | null; cost_usd_micros: number | null; carry_usd_micros: number | null;
  fit: "good" | "over" | "under";
};
type Practices = {
  verified: boolean; verifier_runs: number; verifier_pass_rate: number | null;
  exitplan_count: number; subagent_research: number; review_subagent: boolean;
  interrupts: number; manual_compactions: number; auto_compactions: number; fresh_session: boolean;
};
type ContextPoint = { index: number | null; ts: string | null; context_tokens: number; compaction: boolean };
type SessionLifecycle = {
  session_started_at: string | null; first_pr_opened_at: string | null;
  first_review_at: string | null; reopened_at: string[]; last_merged_at: string | null;
};
type SessionEconomics = {
  model_fit: ModelFit | Unavailable | null;
  segments: SessionSegment[] | Unavailable | null;
  channels: SessionChannels | Unavailable | null;
  practices: Practices | Unavailable | null;
  lifecycle: SessionLifecycle | Unavailable | null;
  context_curve: ContextPoint[] | Unavailable | null;
  models: ModelUsageEntry[] | Unavailable | null;
};
type SessionDetail = {
  session_id: string; datasource: string | null; vendor: string; status: string;
  started_at: string | null; last_event_at: string | null; ended_at: string | null;
  title: string | null; repo: string | null; tokens_total: number | null;
  cost: { cost_effective_usd_micros: number | null };
  metrics: { turn_count: number | Unavailable };
  repos: RepoCostSplit[] | Unavailable | null;
  economics?: SessionEconomics;
};

const FIT_VARIANT: Record<string, "green" | "amber" | "red"> = { good: "green", over: "amber", under: "red" };
const FIT_LABEL: Record<string, string> = { good: "right-sized", over: "over-modeled", under: "under-modeled" };

export default function SessionScreen({ sessionId, onBack, pinnedCards, onPin }: Props) {
  const state = useData(
    (src) => src.sessionDetail(sessionId, "economics") as Promise<SessionDetail>,
    [sessionId],
  );
  useCrumbLabel("session", sessionId, state.status === "ready" ? state.data.title : undefined);

  if (state.status === "loading") return <Skeleton minHeight={420} />;
  if (state.status === "error") {
    return <Frame minHeight={420} tone="red"><span style={{ color: "var(--red)" }}>Couldn't load this session.</span></Frame>;
  }
  const s = state.data;
  const econ = s.economics;

  const modelFit = econ && !isUnavailable(econ.model_fit) ? econ.model_fit : null;
  const segments = econ && !isUnavailable(econ.segments) ? econ.segments : null;
  const channels = econ && !isUnavailable(econ.channels) ? econ.channels : null;
  const practices = econ && !isUnavailable(econ.practices) ? econ.practices : null;
  const models = econ && !isUnavailable(econ.models) ? econ.models : null;
  const contextCurve = econ && !isUnavailable(econ.context_curve) ? econ.context_curve : null;
  // ContextCurve wants ctx in thousands (its axis/tooltip label is "…k tokens") and a short x
  // label; the turn index is the natural "over the session" x-axis.
  const contextSeries = contextCurve?.map((p) => ({
    t: p.index != null ? `T${p.index}` : "",
    ctx: Math.round(p.context_tokens / 1000),
    compaction: p.compaction,
  })) ?? null;
  const turnCount = typeof s.metrics.turn_count === "number" ? s.metrics.turn_count : null;
  const segTotal = segments?.reduce((a, x) => a + (x.cost_usd_micros ?? 0), 0) ?? 0;

  // Per-session cost-by-repo split (segment-level-pricing plan): only worth a section when a
  // session genuinely touched more than one repo -- a single-repo session's "split" is just
  // its header repo, already shown above.
  const repos = !isUnavailable(s.repos) ? s.repos : null;
  const multiRepo = repos && repos.length > 1 ? repos : null;
  const repoCostTotal = multiRepo?.reduce((a, r) => a + (r.cost_usd_micros ?? 0), 0) ?? 0;

  // D1-lifecycle: build->review->ship milestones off the session's linked PR when the correlator
  // has tied this session to one; otherwise the session-only start->last-activity fallback.
  const lc = econ && !isUnavailable(econ.lifecycle) ? econ.lifecycle : null;
  const lifecycle = lc
    ? positionLifecycle([
        { name: "session start", ts: lc.session_started_at ?? s.started_at, spanKind: "build" },
        { name: "PR opened", ts: lc.first_pr_opened_at, spanKind: "build" },
        { name: "first review", ts: lc.first_review_at, spanKind: "review" },
        // each reopen (from git_artifact_events) opens a rework span up to the next milestone
        ...(lc.reopened_at ?? []).map((ts) => ({ name: "reopened", ts, spanKind: "rework" as const })),
        { name: lc.last_merged_at ? "merged" : "in progress", ts: lc.last_merged_at },
      ])
    : positionLifecycle([
        { name: "session start", ts: s.started_at, spanKind: "build" },
        { name: "last activity", ts: s.ended_at ?? s.last_event_at },
      ]);

  return (
    <div className="flex flex-col gap-8">
      <BackButton label="Back" onClick={onBack} />

      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-mono uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Session</span>
          {s.datasource && <Chip variant="blue">{s.datasource}</Chip>}
        </div>
        <h1 className="font-semibold" style={{ fontSize: 24, color: "var(--foreground)", lineHeight: 1.25 }}>{s.title ?? "Untitled session"}</h1>
        <div className="text-xs font-mono mt-1.5" style={{ color: "var(--muted-foreground)" }}>
          {s.repo ?? "no repo"} · {s.vendor} · started {fmtDate(s.started_at)}
        </div>
      </div>

      {/* Hero */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <HeroTile label="Cost" value={fmtMoneyMicros(s.cost.cost_effective_usd_micros)} sub="this session" subVariant="muted" />
        <HeroTile label="Tokens" value={fmtTokens(s.tokens_total)} sub="total" subVariant="muted" />
        <HeroTile label="Turns" value={turnCount != null ? fmtCount(turnCount) : "—"} sub="agent turns" subVariant="muted" />
        <HeroTile label="Status" value={s.status} sub={fmtDate(s.ended_at ?? s.last_event_at)} subVariant="muted" />
      </div>

      {/* Model fit */}
      <div>
        <SectionHeading title="Was the right model used?" sub="Model fit" />
        <div className="mt-4">
          {modelFit ? (
            <div className="rounded-lg p-5" style={{ border: `1px solid ${modelFit.verdict === "good" ? "var(--border)" : modelFit.verdict === "over" ? "var(--amber)" : "var(--red)"}`, background: "var(--card)" }}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Verdict</span>
                <Chip variant={FIT_VARIANT[modelFit.verdict]}>{FIT_LABEL[modelFit.verdict]}</Chip>
              </div>
              {/* Model fit is a coaching signal with no dollar figure (efficiency note §2, decision
                  2026-09-16): `savings_usd_micros` is a counterfactual judgment, not measured spend. */}
              <p className="text-sm leading-relaxed" style={{ color: "var(--foreground)" }}>{modelFit.note}</p>
            </div>
          ) : econ && isUnavailable(econ.model_fit) ? (
            <UnavailableNote info={econ.model_fit.unavailable} />
          ) : (
            <Frame minHeight={96} tone="muted">Still gathering data for this — check back shortly.</Frame>
          )}
        </div>
      </div>

      <Divider />

      {/* Lifecycle — session start -> last activity only; PR-anchored spans need D2's own join */}
      <div>
        <SectionHeading title="Where the time went" sub="Lifecycle" />
        <div className="mt-4 rounded-lg p-6 pt-9" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
          <LifecycleTimeline milestones={lifecycle} />
        </div>
      </div>

      {/* Segments */}
      <div>
        <SectionHeading title="How the session unfolded" sub="Phases" />
        <div className="mt-4">
          {segments ? (
            segments.length === 0 ? (
              <Frame minHeight={96} tone="muted">No phases in range.</Frame>
            ) : (
              <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                <div className="flex px-5 pt-5" style={{ height: 8 }}>
                  <div className="flex w-full rounded-full overflow-hidden">
                    {segments.map((seg) => (
                      <div key={seg.index}
                        style={{ width: `${segTotal ? ((seg.cost_usd_micros ?? 0) / segTotal) * 100 : 100 / segments.length}%`,
                                background: seg.fit === "good" ? "var(--green)" : seg.fit === "over" ? "var(--amber)" : "var(--red)" }}
                        title={`${seg.label ? sessionTypeName(seg.label) : "segment"}: ${fmtMoneyMicros(seg.cost_usd_micros)}`} />
                    ))}
                  </div>
                </div>
                <div className="px-5 pb-2 pt-3">
                  {segments.map((seg, i) => (
                    <div key={seg.index} className="grid items-center py-2.5" style={{ gridTemplateColumns: "1fr 90px 70px 110px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}>
                      <div className="text-sm" style={{ color: "var(--foreground)" }}>{sessionTypeName(seg.label)}</div>
                      <span className="font-mono text-xs text-right" style={{ color: "var(--foreground)" }}>{fmtMoneyMicros(seg.cost_usd_micros)}</span>
                      <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>{seg.turns ?? 0} turns</span>
                      <span className="flex items-center justify-end gap-1.5">
                        <span className="w-2 h-2 rounded-full" style={{ background: seg.fit === "good" ? "var(--green)" : seg.fit === "over" ? "var(--amber)" : "var(--red)" }} />
                        <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>{FIT_LABEL[seg.fit]}</span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )
          ) : econ && isUnavailable(econ.segments) ? (
            <UnavailableNote info={econ.segments.unavailable} />
          ) : (
            <Frame minHeight={96} tone="muted">Still gathering data for this — check back shortly.</Frame>
          )}
        </div>
      </div>

      {/* Repo split — only when this session genuinely touched more than one repo */}
      {multiRepo && (
        <div>
          <SectionHeading title="Where the cost landed" sub="By repo" />
          <div className="mt-4 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
            <div className="flex px-5 pt-5" style={{ height: 8 }}>
              <div className="flex w-full rounded-full overflow-hidden" style={{ background: "var(--border)" }}>
                {multiRepo.map((r) => (
                  <div key={r.repo_id}
                    style={{ width: `${repoCostTotal ? ((r.cost_usd_micros ?? 0) / repoCostTotal) * 100 : 100 / multiRepo.length}%`,
                            background: r.primary ? "var(--blue)" : "var(--muted-foreground)" }}
                    title={`${r.display}: ${r.cost_usd_micros != null ? fmtMoneyMicros(r.cost_usd_micros) : "not priced"}`} />
                ))}
              </div>
            </div>
            <div className="px-5 pb-2 pt-3">
              {multiRepo.map((r, i) => (
                <div key={r.repo_id} className="grid items-center py-2.5" style={{ gridTemplateColumns: "1fr 90px 70px", borderTop: i > 0 ? "1px solid var(--border)" : undefined }}>
                  <div className="text-sm flex items-center gap-2" style={{ color: "var(--foreground)" }}>
                    {r.display}
                    {r.primary && <Chip variant="blue">primary</Chip>}
                  </div>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--foreground)" }}>
                    {r.cost_usd_micros != null ? fmtMoneyMicros(r.cost_usd_micros) : "—"}
                  </span>
                  <span className="font-mono text-xs text-right" style={{ color: "var(--muted-foreground)" }}>
                    {r.cost_usd_micros != null && repoCostTotal ? fmtPct(r.cost_usd_micros / repoCostTotal) : "not priced"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <Divider />

      {/* Where the cost went */}
      <div>
        <SectionHeading title="Where the cost went" sub="By channel" />
        <div className="mt-4">
          {channels ? (
            <AgentDonut data={channelTotalsToSeries({
              carry_usd_micros: channels.carry.value.usd_micros ?? 0,
              recache_usd_micros: channels.recache.value.usd_micros ?? 0,
              output_usd_micros: channels.output.value.usd_micros ?? 0,
              input_usd_micros: channels.input.value.usd_micros ?? 0,
            })} />
          ) : econ && isUnavailable(econ.channels) ? (
            <UnavailableNote info={econ.channels.unavailable} />
          ) : (
            <Frame minHeight={200} tone="muted">Still gathering data for this — check back shortly.</Frame>
          )}
        </div>
      </div>

      {/* Models used — QW1 (mvp-gap-closure-plan): session_info.models was already folded and
          stored; this was a wiring gap, not missing data. */}
      <div>
        <SectionHeading title="Models used" sub="Token share" />
        <div className="mt-4">
          {models ? (
            <ModelUsageBars entries={models} />
          ) : econ && isUnavailable(econ.models) ? (
            <UnavailableNote info={econ.models.unavailable} />
          ) : (
            <Frame minHeight={96} tone="muted">Still gathering data for this — check back shortly.</Frame>
          )}
        </div>
      </div>

      {/* Context curve — D1-context-curve: per-turn context-window size, folded into info */}
      <div>
        <SectionHeading title="Context over the session" sub="Cache" />
        <div className="mt-4">
          {contextSeries && contextSeries.length > 0 ? (
            <ContextCurve data={contextSeries} />
          ) : econ && isUnavailable(econ.context_curve) ? (
            <UnavailableNote info={econ.context_curve.unavailable} />
          ) : (
            <UnavailableNote info={{ block: "context_curve", requires: "a persisted per-turn context series" }} />
          )}
        </div>
      </div>

      <Divider />

      {/* Practices */}
      <div>
        <SectionHeading title="Habits worth a look" sub="Practices" />
        <div className="mt-4">
          {practices ? (
            <div className="flex flex-col gap-2">
              {[
                { label: "Plan mode", ok: practices.exitplan_count > 0, detail: practices.exitplan_count > 0 ? "Used plan mode before building." : "Went straight to code." },
                { label: "Verifier run", ok: practices.verified, detail: practices.verified ? `${practices.verifier_runs} verifier run(s) this session.` : "No tests/verifier run." },
                { label: "Fresh session", ok: practices.fresh_session, detail: practices.fresh_session ? "Started clean." : "A long-running session — consider a fresh one sooner." },
                { label: "Delegation", ok: practices.subagent_research > 0, detail: practices.subagent_research > 0 ? `${practices.subagent_research} sub-agent(s) for research.` : "No sub-agents used." },
                { label: "Compaction", ok: practices.auto_compactions === 0, detail: `${practices.manual_compactions} manual · ${practices.auto_compactions} auto compaction(s).` },
              ].map((p) => (
                <div key={p.label} className="flex items-center gap-3 rounded-lg px-4 py-2.5" style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: p.ok ? "var(--green)" : "var(--amber)" }} />
                  <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>{p.label}</span>
                  <span className="text-sm ml-2" style={{ color: "var(--muted-foreground)" }}>{p.detail}</span>
                </div>
              ))}
            </div>
          ) : econ && isUnavailable(econ.practices) ? (
            <UnavailableNote info={econ.practices.unavailable} />
          ) : (
            <Frame minHeight={96} tone="muted">Still gathering data for this — check back shortly.</Frame>
          )}
        </div>
      </div>

      <Divider />

      {/* Ask */}
      <div>
        <SectionHeading title="Ask this session" sub="Questions" />
        <div className="mt-4"><AskBox pinnedCards={pinnedCards} onPin={onPin} /></div>
      </div>
    </div>
  );
}
