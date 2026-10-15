/**
 * The `mock` DataSource (MVP plan U1/U5) — serves fixtures shaped exactly like the generated
 * `Envelope_*_` schemas (`api-types.ts`), built from the prototype data in `mock.ts`. Wired up
 * in `main.tsx` when `VITE_DATA_SOURCE !== "api"`.
 *
 * Kept in the real contract shape on purpose: a screen must render identically from `mock`
 * and `api` (U1's own "done when"). Where the live API is honestly not computing something
 * yet (A6's `features[]`, gated on F4/Phase 2), this fixture says so too, rather than
 * showing richer fake data the real backend can't yet match.
 */
import type {
  AggregateMode, DataSource, EconomicsScope, Schemas, TableParams, Timeframe,
} from "./source";
import { ORG_SCOPE_ID } from "./source";
import { addDays, eachDay } from "./isoDay";
import * as fx from "./mock";
import { DEFAULT_MOCK_CYCLE, MOCK_CYCLE_LABELS, mockCycleError, mockPeriods, normaliseMockCycle } from "./mock-cycles";
import type { MockCycle } from "./mock-cycles";

type Money = Schemas["Money"];
type Finding = Schemas["Finding"];

const MODE: AggregateMode = { modes_present: ["both"], sessions_by_mode: { both: 1 } };
const settle = <T>(v: T): Promise<T> => Promise.resolve(v);

// QW1 (mvp-gap-closure-plan): the prototype's `models[]` (`{name, pct, tier}`, `pct` as a
// 0-100 number) reshaped into the real `ModelUsage` contract (`pct` a fraction, `model` not
// `name`). `totalTokens` distributes an approximate per-model token count for shape realism
// only -- the mock has no real per-request ledger to derive it from.
function toModelUsage(models: { name: string; pct: number; tier: string }[],
                      totalTokens: number): Schemas["ModelUsage"][] {
  return models.map((m) => ({
    model: m.name, tier: m.tier, pct: m.pct / 100,
    tokens: Math.round((m.pct / 100) * totalTokens),
    cost_usd_micros: null,
  }));
}

// Cost-based seats (docs/plan/2026-09-26-cost-based-seat-utilization.md), the same rules the API
// applies. The fixtures are period-agnostic -- their spend does not change with the selected
// window -- so the mock treats every figure as one period of whatever window is selected and uses
// the team's budget as the allowance (the API scales it to the window).
const SEAT_UNDER = 0.4;
const SEAT_OVER = 0.95;
function budgetMoney(usd: number | null): Money {
  return usd == null ? { usd_micros: null, cost_basis: null, cost_complete: null }
    : { usd_micros: Math.round(usd * 1_000_000), cost_basis: "budget", cost_complete: null };
}
function usageStatus(pct: number | null): string | null {
  if (pct == null) return null;
  return pct >= SEAT_OVER ? "over" : pct < SEAT_UNDER ? "under" : "healthy";
}
function isRoomToSave(recoverableUsd: number, spendUsd: number): boolean {
  return spendUsd > 0 && recoverableUsd / spendUsd >= 0.1 && recoverableUsd >= 10;
}
/** `aggregate.person_headline_status`: room to save, then the seat band, else on track. */
function headlineStatus(roomToSave: boolean, band: string | null): string {
  if (roomToSave) return "room-to-save";
  return band === "over" || band === "under" ? band : "on-track";
}

/** My-usage's seat: my team's budget / members (one fixture period, whatever the window), `null` with none. */
function mySeat(teamId: string, spendUsd: number) {
  const t = fx.teams.find((team) => team.id === teamId);
  const seat = t && t.budget && t.members ? t.budget / t.members : null;
  if (seat == null) return null;
  const pct = spendUsd / seat;
  return { team_id: teamId, seat_monthly: budgetMoney(seat), allowance: budgetMoney(seat),
           usage_pct: pct, status: usageStatus(pct), in_grace: false };
}

function money(usd: number): Money {
  return { usd_micros: Math.round(usd * 1_000_000), cost_basis: "estimated", cost_complete: true };
}

/** "2h ago" / "1d ago" -> an actual ISO timestamp, so fmtRelativeTime renders it the same
 * way regardless of data source. */
function agoToIso(label: string, now = new Date()): string {
  const m = /^(\d+)([hd])\s+ago$/.exec(label);
  if (!m) return now.toISOString();
  const n = Number(m[1]);
  const ms = m[2] === "h" ? n * 3_600_000 : n * 86_400_000;
  return new Date(now.getTime() - ms).toISOString();
}

function legacyEntity(w: (typeof fx.worthALook)[number]): Schemas["EntityRef"] {
  return { kind: w.screen, id: w.screenParam ?? undefined };
}

// G1 (mvp-gap-closure-plan): `mySessionHistory`'s `duration` is a pre-formatted string
// ("1h 12m", "24m") -- prototype-shaped, per strip-rule 2. Parsed back to minutes here so the
// mock can hand the real contract (`duration_minutes: number`) the same way every other mock
// reshape in this file does.
function parseDurationMinutes(label: string): number {
  const m = /^(?:(\d+)h)?\s*(?:(\d+)m)?$/.exec(label.trim());
  if (!m) return 0;
  return (Number(m[1] ?? 0) * 60) + Number(m[2] ?? 0);
}

/** Client-side sort/search/paginate over a fixture array, mirroring the real API's
 * aggregate.sort_desc_none_last / search_by_name / paginate (server does this for real). */
function sortSearchPage<T extends { name: string } & Record<string, unknown>>(
  items: T[], params: TableParams, moneyKey?: string,
): { items: T[]; total: number } {
  let rows = items;
  if (params.q) {
    const needle = params.q.toLowerCase();
    rows = rows.filter((r) => r.name.toLowerCase().includes(needle));
  }
  const total = rows.length;
  const sortKey = params.sort;
  if (sortKey) {
    const extract = (r: T): number | null => {
      const raw = moneyKey && sortKey === moneyKey ? (r[sortKey] as Money | undefined)?.usd_micros : (r[sortKey] as number | null | undefined);
      return raw ?? null;
    };
    rows = [...rows].sort((a, b) => {
      const av = extract(a), bv = extract(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av;
    });
  }
  const offset = params.offset ?? 0;
  const limit = params.limit ?? 50;
  return { items: rows.slice(offset, offset + limit), total };
}

/** The mock's `daily_spend`: the window's elapsed UTC days (`from`'s day through `to`'s day,
 * capped at today), with a fixed, deterministic weight per day. A `to` at exactly midnight is
 * exclusive, as the API treats it (a finished budget period ends at the next period's 00:00), so
 * it does not add a day. The integer split puts the rounding remainder on the last day so the
 * series sums to `totalMicros` exactly, the way the real series sums to the headline. */
export function mockDailySpend(tf: Timeframe, totalMicros: number, today = new Date()): Schemas["DailySpendPoint"][] {
  const todayIso = today.toISOString().slice(0, 10);
  const first = tf.from.slice(0, 10);
  const toDay = tf.to.slice(0, 10);
  const atMidnight = !tf.to.includes("T") || /T00:00(:00(\.0+)?)?(Z|\+00:00)?$/.test(tf.to);
  const lastIncl = atMidnight ? addDays(toDay, -1) : toDay;
  const last = lastIncl < todayIso ? lastIncl : todayIso;
  const days = eachDay(first, last);
  if (days.length === 0) return [];
  const weights = days.map((_, i) => 3 + ((i * 7) % 9));
  const sumW = weights.reduce((a, b) => a + b, 0);
  const shares = weights.map((w) => Math.floor((totalMicros * w) / sumW));
  shares[shares.length - 1] += totalMicros - shares.reduce((a, b) => a + b, 0);
  return days.map((day, i) => ({ day, spend_usd_micros: shares[i] }));
}

/** The mock's `agent_task_mix`: Usage's `sessionTypes` counts split across the mock agents of
 * `agentSpend` (about 9% Codex, 4% Cursor, the rest Claude Code), so summing `count` per type
 * gives exactly `session_types`, as the real API does. Same ordering as the API: agent asc,
 * count desc, type asc. */
export function mockAgentTaskMix(): Schemas["AgentTaskMix"][] {
  const codex = (n: number) => Math.round(n * 0.09);
  const cursor = (n: number) => Math.round(n * 0.04);
  const [claudeName, codexName, cursorName] = fx.agentSpend.map((a) => a.name);
  const out: Schemas["AgentTaskMix"][] = [];
  for (const t of fx.sessionTypes) {
    out.push({ agent: claudeName, type: t.type, count: t.count - codex(t.count) - cursor(t.count) });
    out.push({ agent: codexName, type: t.type, count: codex(t.count) });
    out.push({ agent: cursorName, type: t.type, count: cursor(t.count) });
  }
  return out
    .filter((p) => p.count > 0)
    .sort((a, b) => a.agent.localeCompare(b.agent) || b.count - a.count || a.type.localeCompare(b.type));
}

export class MockSource implements DataSource {
  orgOverview(tf: Timeframe) {
    const teamRows = fx.teams.map((t) => ({
      team_id: t.id, name: t.name, members: t.members, spend: money(t.spent),
      budget_pct: t.spent / t.budget, merged_prs: t.mergedPRs, cost_per_pr: money(t.avgCostPerPR),
      rework_rate: fx.teamMeta[t.id]?.reworkRate ?? null, over_budget: t.spent > t.budget,
    }));
    const teamsOverBudget = teamRows.filter((t) => t.over_budget).length;
    const findings: Finding[] = fx.worthALook.map((w) => ({
      id: w.id, text: w.text, action: w.action, severity: w.severity, entity: legacyEntity(w),
    }));
    return settle({
      data: {
        headline: {
          spend: money(fx.ORG_SPENT), budget_pct: fx.ORG_SPENT / fx.ORG_BUDGET,
          vs_last_pct: 0.06, could_save: money(fx.ORG_RECOVERABLE), dominant_cause: "resume",
        },
        highlights: {
          active_teams: fx.teams.length, active_devs: fx.people.length, tokens: 6_400_000,
          active_repos: fx.projects.length, merged_prs: fx.ORG_MERGED_PRS,
          cost_per_pr: money(fx.ORG_AVG_COST_PER_PR), teams_over_budget: teamsOverBudget,
          rework_rate: fx.REWORK_RATIO,
          // QW2/QW3 (mvp-gap-closure-plan)
          avg_time_to_pr_days: fx.prTimeStats.avg,
          model_mix: toModelUsage(fx.sessionDetail.models, 6_400_000),
          // F5: same illustrative-share reasoning as orgUsage()'s mock -- real orgs run well
          // under 1%, but a fixture that always shows "0%" is a worse UI test than a visible one.
          reasoning_share: { reasoning_tokens: 256_000, tokens_total: 6_400_000, pct: 4.0 },
        },
        teams: teamRows,
        agent_spend: fx.agentSpend.map((a) => ({ agent: a.name, spend: money(a.value) })),
        agent_task_mix: mockAgentTaskMix(),
        daily_spend: mockDailySpend(tf, Math.round(fx.ORG_SPENT * 1_000_000)),
        worth_a_look: findings,
      },
      mode: MODE, unavailable: null,
    });
  }

  orgUsage(_tf: Timeframe) {
    const teams = fx.teams.map((t) => {
      const pct = t.budget ? t.spent / t.budget : null;
      const seat = t.budget && t.members ? t.budget / t.members : null;
      return {
        team_id: t.id, name: t.name, members: t.members, spend: money(t.spent), tokens_used: t.tokensUsed,
        allowance: budgetMoney(t.budget || null), utilization_pct: pct, status: usageStatus(pct),
        seats_under_threshold: seat == null ? null
          : fx.people.filter((p) => p.teamId === t.id && p.spend / seat < SEAT_UNDER).length,
      };
    });
    // G3 (mvp-gap-closure-plan): the highest-utilization over-budget team, expanded to its own
    // roster -- same selection rule the real service uses (first "over" team, already sorted by
    // utilization_pct elsewhere; here just the max directly).
    const overTeams = teams.filter((t) => t.status === "over").sort((a, b) => (b.utilization_pct ?? 0) - (a.utilization_pct ?? 0));
    const spotlightTeam = overTeams[0]
      ? {
          team_id: overTeams[0].team_id, team_name: overTeams[0].name,
          people: fx.people.filter((p) => p.teamId === overTeams[0].team_id).map((p) => {
            const t = fx.teams.find((team) => team.id === p.teamId)!;
            const seat = t.budget && t.members ? t.budget / t.members : null;
            const pct = seat ? p.spend / seat : null;
            return {
              user_id: p.id, name: p.name, spend: money(p.spend), tokens_used: p.tokensUsed,
              allowance: budgetMoney(seat), utilization_pct: pct, status: usageStatus(pct), in_grace: false,
            };
          }).sort((a, b) => (b.utilization_pct ?? 0) - (a.utilization_pct ?? 0)),
        }
      : null;
    // F5: a modest, clearly-nonzero illustrative share -- real orgs run far lower (well under
    // 1%), but a mock fixture that always renders "0%"/hides the tile would be a worse test of
    // the UI than a made-up-but-visible number.
    const tokensTotal = teams.reduce((a, t) => a + t.tokens_used, 0);
    const reasoningTokens = Math.round(tokensTotal * 0.04);
    return settle({
      data: {
        header: {
          users: fx.usageHeader.users, agents: fx.usageHeader.agents, hours: fx.usageHeader.hours,
          sessions: fx.usageHeader.sessions, avg_session_minutes: 38,
          avg_prs_per_session: fx.usageHeader.avgPRPerSession,
        },
        session_types: fx.sessionTypes.map((t) => ({ type: t.type, count: t.count })),
        sessions_no_output_pct: fx.SESSIONS_NO_OUTPUT_PCT,
        reasoning_share: {
          reasoning_tokens: reasoningTokens, tokens_total: tokensTotal,
          pct: Math.round((reasoningTokens / tokensTotal) * 1000) / 10,
        },
        teams,
        spotlight_team: spotlightTeam,
      },
      mode: MODE, unavailable: null,
    });
  }

  teams(_tf: Timeframe, params: TableParams = {}) {
    const rows = fx.teams.map((t) => ({
      team_id: t.id, name: t.name, members: t.members, spend: money(t.spent),
      budget_pct: t.spent / t.budget, merged_prs: t.mergedPRs, cost_per_pr: money(t.avgCostPerPR),
      avg_time_to_pr_days: parseFloat(fx.teamMeta[t.id]?.avgTimeToPR ?? "") || null,
      rework_rate: fx.teamMeta[t.id]?.reworkRate ?? null,
      over_budget: t.spent > t.budget,
      last_active_at: fx.teamMeta[t.id] ? agoToIso(fx.teamMeta[t.id].lastActive) : null,
    }));
    return settle({ data: sortSearchPage(rows, { sort: "spend", ...params }, "spend"), mode: MODE, unavailable: null });
  }

  projects(_tf: Timeframe, params: TableParams = {}) {
    const rows = fx.projects.map((p) => ({
      repo_id: p.id, local_to_user: null, name: p.name, spend: money(p.spend),
      merged_prs: p.mergedPRs, contributors: p.contributors.length,
      avg_time_to_pr_days: parseFloat(fx.projectMeta[p.id]?.avgTimeToPR ?? "") || null,
      rework_rate: fx.projectMeta[p.id]?.reworkRate ?? null,
      last_active_at: fx.projectMeta[p.id] ? agoToIso(fx.projectMeta[p.id].lastActive) : null,
    }));
    return settle({ data: sortSearchPage(rows, { sort: "spend", ...params }, "spend"), mode: MODE, unavailable: null });
  }

  team(id: string, _tf: Timeframe) {
    const t = fx.teams.find((team) => team.id === id);
    if (!t) return settle({ data: null, mode: MODE, unavailable: { block: "team", requires: "directory" } });
    const teamPeople = fx.people.filter((p) => p.teamId === id);
    const seat = t.budget && t.members ? t.budget / t.members : null;
    const people = teamPeople.map((p) => {
      const recoverable = money(p.recoverableSpend);
      const usage = seat ? p.spend / seat : null;
      return {
        user_id: p.id, name: p.name, spend: money(p.spend), merged_prs: p.mergedPRs,
        cost_per_pr: money(p.avgCostPerPR), recoverable,
        recoverable_pct: p.spend > 0 ? p.recoverableSpend / p.spend : null,
        status: headlineStatus(isRoomToSave(p.recoverableSpend, p.spend), usageStatus(usage)),
        usage_status: usageStatus(usage),
        usage_pct: usage, low_use: usage != null && usage < SEAT_UNDER,
        seat_allowance: budgetMoney(seat), in_grace: false,
      };
    }).sort((a, b) => (b.recoverable.usd_micros ?? 0) - (a.recoverable.usd_micros ?? 0));
    const roomToSave = people.filter((p) => p.status === "room-to-save");
    const findings: Finding[] = roomToSave.slice(0, 3).map((p) => ({
      id: `room-to-save-${p.user_id}`, text: `${p.name} has recoverable spend this period.`,
      action: `View ${p.name}`, severity: "amber", entity: { kind: "person", id: p.user_id },
    }));
    return settle({
      data: {
        team: {
          team_id: t.id, name: t.name, members: t.members, spend: money(t.spent),
          budget_pct: t.spent / t.budget, merged_prs: t.mergedPRs, cost_per_pr: money(t.avgCostPerPR),
          rework_rate: fx.teamMeta[t.id]?.reworkRate ?? null, over_budget: t.spent > t.budget,
        },
        people,
        highlights: {
          members: t.members, room_to_save_count: roomToSave.length,
          low_use_count: people.filter((p) => p.low_use).length,
          merged_prs: t.mergedPRs, cost_per_pr: money(t.avgCostPerPR),
          rework_rate: fx.teamMeta[t.id]?.reworkRate ?? null,
          // QW2 (mvp-gap-closure-plan)
          avg_time_to_pr_days: parseFloat(fx.teamMeta[t.id]?.avgTimeToPR ?? "") || null,
        },
        worth_a_look: findings,
      },
      mode: MODE, unavailable: null,
    });
  }

  person(id: string, _tf: Timeframe) {
    const p = fx.people.find((person) => person.id === id);
    if (!p) return settle({ data: null, mode: MODE, unavailable: { block: "person", requires: "directory" } });
    const habits = p.habits.map((h) => ({
      category: h.category, label: h.label, recoverable_usd_micros: Math.round(h.recoverableAmount * 1_000_000),
    }));
    const velocity = [
      { week: "2026-08-18", merged_prs: 2 }, { week: "2026-08-25", merged_prs: 3 },
      { week: "2026-09-01", merged_prs: 1 }, { week: "2026-09-08", merged_prs: 2 },
    ];
    const recentPrs = fx.recentPRs.slice(0, 6).map((pr) => ({
      artifact_id: pr.id, repo_id: pr.repo, state: pr.status, title: pr.title,
      created_at: agoToIso("1d ago"), merged_at: pr.status === "merged" ? agoToIso("1d ago") : null,
      closed_at: pr.status !== "open" ? agoToIso("1d ago") : null,
    }));
    // G1 (mvp-gap-closure-plan): the prototype's `mySessionHistory` is one shared fixture (no
    // per-person id/session_id), reshaped into the real contract the same way `recentPrs`
    // above reuses `fx.recentPRs` org-wide.
    const recentSessions = fx.mySessionHistory.map((sess, i) => {
      // `mySessionHistory` (prototype fixture) carries no token figures at all -- approximated
      // from cost for the mock only, same "illustrative, not measured" posture as the
      // channel split below. F5: a modest, clearly-nonzero reasoning share (real orgs run
      // well under 1%; a fixture that always shows "—" is a worse UI test than a visible one).
      const tokensTotal = Math.round(sess.cost * 15_000);
      return {
        // `mySessionHistory` carries no title -- honestly null here, same as a real session
        // the vendor never auto-titled.
        session_id: `hist-${p.id}-${i}`, title: null, repo_id: sess.repo,
        started_at: agoToIso(`${i + 1}d ago`), duration_minutes: parseDurationMinutes(sess.duration),
        cost_usd_micros: Math.round(sess.cost * 1_000_000), merged_prs: sess.mergedPRs,
        quality: sess.label,
        reasoning_tokens: Math.round(tokensTotal * 0.04), tokens_total: tokensTotal,
      };
    });
    // G2 (mvp-gap-closure-plan): approximate channel split for the mock only -- the real
    // channel_totals rollup has no equivalent in the prototype's own per-person fixture.
    const channels = {
      output_usd_micros: Math.round(p.spend * 0.65 * 1_000_000),
      carry_usd_micros: Math.round(p.spend * 0.2 * 1_000_000),
      recache_usd_micros: Math.round(p.spend * 0.1 * 1_000_000),
      input_usd_micros: Math.round(p.spend * 0.05 * 1_000_000),
      complete: true,
    };
    return settle({
      data: {
        person: {
          user_id: p.id, name: p.name, team_id: p.teamId, spend: money(p.spend),
          merged_prs: p.mergedPRs, cost_per_pr: money(p.avgCostPerPR), recoverable: money(p.recoverableSpend),
          habits, work_quality: p.workQuality.map((w) => ({ label: w.label, count: w.count })),
          // A5-peer-task-mix: the fixture's `peerCompare` is just a bare user id -- reshaped
          // to the real $/KLOC-carrying contract, with an illustrative (not measured) rate.
          peer_compare: p.peerCompare
            ? (() => {
                const peer = fx.people.find((pp) => pp.id === p.peerCompare);
                return peer
                  ? { user_id: peer.id, name: peer.name, cost_per_kloc_usd_micros: money(peer.avgCostPerPR * 8).usd_micros ?? 0 }
                  : null;
              })()
            : null,
          channels,
        },
        recent_prs: recentPrs,
        recent_sessions: recentSessions,
        velocity,
      },
      mode: MODE, unavailable: null,
    });
  }

  project(id: string, _tf: Timeframe) {
    const p = fx.projects.find((project) => project.id === id);
    if (!p) return settle({ data: null, mode: MODE, unavailable: { block: "project", requires: "github" } });
    const contributors = p.contributors.map((userId) => {
      const person = fx.people.find((pp) => pp.id === userId);
      return {
        user_id: userId, name: person?.name ?? userId, spend: money(person?.spend ?? 0),
        merged_prs: person?.mergedPRs ?? 0,
      };
    }).sort((a, b) => (b.spend.usd_micros ?? 0) - (a.spend.usd_micros ?? 0));
    const linesAdded = p.features.reduce((s, ft) => s + ft.prs.reduce((s2) => s2 + 40, 0), 0);
    // F4 (Phase 2): features[] is live now -- one row per mock.ts Feature, mapped to the
    // real schema shape (issue_key/title/spend/merged_prs/status).
    const features = p.features.map((ft) => ({
      id: ft.id, issue_key: ft.id, title: ft.name, spend: money(ft.spend),
      merged_prs: ft.prs.filter((pr) => pr.status === "merged").length, status: ft.status,
    }));
    // G4 (mvp-gap-closure-plan): the flat "all pull requests" list -- every PR under every
    // feature, the same reshape `recentPrs`/`recentSessions` elsewhere in this file use for a
    // prototype fixture with no independent per-PR id scheme of its own.
    const prs = p.features.flatMap((ft) => ft.prs).map((pr) => ({
      artifact_id: pr.id, number: parseInt(pr.id.replace("#", ""), 10) || null, title: pr.title,
      author_name: pr.author, state: pr.status, cost_usd_micros: Math.round(pr.cost * 1_000_000),
      created_at: agoToIso("2d ago"),
      merged_at: pr.status === "merged" ? agoToIso("1d ago") : null,
      closed_at: pr.status !== "open" ? agoToIso("1d ago") : null,
    }));
    return settle({
      data: {
        project: {
          repo_id: p.id, name: p.name, spend: money(p.spend), merged_prs: p.mergedPRs,
          cost_per_pr: money(p.mergedPRs ? p.spend / p.mergedPRs : 0),
          rework_rate: fx.projectMeta[p.id]?.reworkRate ?? null,
        },
        contributors,
        highlights: { lines_added: linesAdded, lines_removed: Math.round(linesAdded * 0.3), reopened_prs: null },
        features,
        prs,
      },
      mode: MODE, unavailable: null,
    });
  }

  prDetail(id: string) {
    const p = fx.prsById[id] ?? fx.prDetail;
    const stateFor = (s: string) => (s === "changes" ? "CHANGES_REQUESTED" : s === "approved" ? "APPROVED" : "COMMENTED");
    const reviews = p.reviews.map((r) => ({
      reviewer_user_id: null, reviewer_name: r.reviewer, state: stateFor(r.state),
      submitted_at: agoToIso("1d ago"),
    }));
    const reviewRounds = reviews.filter((r) => r.state === "CHANGES_REQUESTED").length;
    // real X1/X2 flags only -- the mockup's own churn-based flag stays out, same rule D2's
    // service function follows: a churn flag needs a rework-ratio threshold (Landing 3), so the
    // api doesn't emit one yet and the mock mirrors that.
    const flags: Schemas["PRFlag"][] = [];
    if (reviewRounds >= 1) {
      flags.push({ label: "Multiple review rounds",
        detail: `${reviewRounds} round(s) of changes requested before approval.`, sev: "amber" });
    }
    if (p.quality === "Costly rework") {
      flags.push({ label: "Costly rework",
        detail: "High making-cost and multiple review rounds — EFF's costliest quadrant.", sev: "red" });
    }
    return settle({
      data: {
        pr: {
          artifact_id: p.id, number: parseInt(p.id, 10) || null, repo_id: p.repo, title: p.title,
          state: p.status, author_user_id: null, author_name: p.author, branch: p.head,
          base_branch: p.base, labels: p.labels, linked_issue: p.linkedIssue,
          created_at: agoToIso("3d ago"),
          merged_at: p.status === "merged" ? agoToIso("1d ago") : null,
          closed_at: p.status !== "open" ? agoToIso("1d ago") : null,
          lines_added: p.linesAdded, lines_removed: p.linesRemoved, files_changed: p.files,
          commits: p.commits,
        },
        cost: {
          spend: money(p.costUsd), pre_open: money(p.preOpenCost), post_open: money(p.postOpenCost),
          // approximate split for the mock chart only -- the real channel_totals split is
          // computed from priced sessions, not derivable from the prototype's totals alone.
          channels: {
            carry_usd_micros: Math.round(p.costUsd * 0.18 * 1_000_000),
            recache_usd_micros: Math.round(p.costUsd * 0.05 * 1_000_000),
            output_usd_micros: Math.round(p.costUsd * 0.70 * 1_000_000),
            input_usd_micros: Math.round(p.costUsd * 0.07 * 1_000_000),
            complete: true,
          },
        },
        review: {
          review_rounds: reviewRounds, time_to_first_review_hours: null, time_to_merge_days: null,
          comment_count: p.reviewComments, reviews,
        },
        quality: p.quality,
        // `reopens` and post-open churn (`churn_post_lines`/`rework_ratio`) are captured on the
        // api now (git_artifact_events + the git_commit_stats worker); the mock has no reopen
        // events or commit stats, so they stay null as honest sample data, not fabricated.
        // `churn_post_usd_micros` stays null everywhere -- churn has no lines->$ conversion.
        churn_post_lines: null, rework_ratio: null, churn_post_usd_micros: null, reopens: null,
        sessions: p.sessions.map((s) => ({
          session_id: s.id, title: s.title, user_id: null,
          cost_usd_micros: Math.round(s.costUsd * 1_000_000), role: s.role,
        })),
        flags,
        models: toModelUsage(p.models, p.tokens),
      },
      mode: MODE, unavailable: null,
    });
  }

  featureDetail(_id: string) {
    // Only one feature fixture exists in the prototype; the real feature-list ids (F4's
    // artifact-id scheme) don't correspond to it, so this ignores `id` -- the same fallback
    // FeatureScreen has always shown regardless of which feature card was clicked.
    const f = fx.featureDetail;
    const openPrs = f.prs.filter((pr) => pr.status === "open").length;
    return settle({
      data: {
        id: f.issueKey, issue_key: f.issueKey, title: f.title, status: f.status, repo_id: f.repo,
        spend: money(f.costUsd),
        channels: {
          carry_usd_micros: Math.round(f.costUsd * 0.19 * 1_000_000),
          recache_usd_micros: Math.round(f.costUsd * 0.04 * 1_000_000),
          output_usd_micros: Math.round(f.costUsd * 0.70 * 1_000_000),
          input_usd_micros: Math.round(f.costUsd * 0.07 * 1_000_000),
          complete: true,
        },
        member_prs: f.prs.map((pr) => ({
          artifact_id: pr.id, number: parseInt(pr.id, 10) || null, title: pr.title, state: pr.status,
          created_at: agoToIso("5d ago"),
          merged_at: pr.status === "merged" ? agoToIso("2d ago") : null,
          cost: money(pr.cost),
        })),
        contributors: f.contributors.map((c) => ({ user_id: c.name, name: c.name })),
        lifecycle: {
          first_session_started_at: agoToIso("8d ago"),
          first_pr_opened_at: agoToIso("8d ago"),
          last_merged_at: agoToIso("2d ago"),
        },
        cost_to_finish: f.costToFinish
          ? {
              open_prs: openPrs,
              low_usd_micros: money(f.costToFinish.low).usd_micros ?? 0,
              mid_usd_micros: money(f.costToFinish.mid).usd_micros ?? 0,
              high_usd_micros: money(f.costToFinish.high).usd_micros ?? 0,
              basis: f.costToFinish.basis,
            }
          : { open_prs: 0, low_usd_micros: 0, mid_usd_micros: 0, high_usd_micros: 0, basis: "No open PRs remain." },
        // D3-benchmark: a sample cross-feature cost distribution so the panel renders in mock
        // mode (the live one comes from aggregate.feature_benchmark over the repo's features).
        benchmark: {
          subject_cost_usd_micros: money(f.costUsd).usd_micros ?? 0,
          cost_percentile: 62,
          peer_count: 8,
          cost_dist: { avg: 41, min: 8, p50: 38, p70: 52, p95: 96, max: 120 },
        },
      },
      mode: MODE, unavailable: null,
    });
  }

  economics(scope: EconomicsScope, _tf: Timeframe) {
    const e = fx.econFor(scope.scopeId);
    // CostEnvelope's p25/p75/p90/p99 cut differs from Dist's p50/p70/p95 -- approximated here
    // from the same underlying distribution rather than duplicating X5's real percentile math.
    const p25 = Math.round(e.cost.p50 * 0.65);
    const p75 = Math.round(e.cost.p70 + (e.cost.p95 - e.cost.p70) * 0.3);
    const p90 = Math.round(e.cost.p95 * 0.85);
    const p99 = Math.round(e.cost.max * 0.9);
    const envelope = {
      min_usd_micros: money(e.cost.min).usd_micros ?? 0, p25_usd_micros: money(p25).usd_micros ?? 0,
      median_usd_micros: money(e.cost.p50).usd_micros ?? 0, p75_usd_micros: money(p75).usd_micros ?? 0,
      p90_usd_micros: money(p90).usd_micros ?? 0, p99_usd_micros: money(p99).usd_micros ?? 0,
      max_usd_micros: money(e.cost.max).usd_micros ?? 0,
    };
    // Mirrors forecast.forecast_cone's fixed-rate widening (X5) for the mock render path.
    const cone = Array.from({ length: 7 }, (_, step) => {
      const widen = 1 + 0.15 * step;
      return {
        step, median_usd_micros: envelope.median_usd_micros,
        band_lo_usd_micros: Math.round(envelope.median_usd_micros - (envelope.median_usd_micros - envelope.p25_usd_micros) * widen),
        band_hi_usd_micros: Math.round(envelope.median_usd_micros + (envelope.p75_usd_micros - envelope.median_usd_micros) * widen),
      };
    });
    const budgetBurn = fx.budgetBurn.map((pt) => ({
      day: parseInt(pt.day, 10),
      actual_usd_micros: pt.actual != null ? Math.round(pt.actual * 1_000_000) : undefined,
      projected_usd_micros: pt.projected != null ? Math.round(pt.projected * 1_000_000) : undefined,
    }));
    return settle({
      data: {
        scope_type: scope.scopeType, scope_id: scope.scopeId,
        cost_dist: e.cost, time_to_merge_dist: e.time,
        pr_cost_envelope: envelope, forecast_cone: cone,
        merged_prs: fx.ORG_MERGED_PRS, reverted_prs: Math.round(fx.ORG_MERGED_PRS * 0.05),
        merged_pr_cost: money(e.merged), reverted_pr_cost: money(e.reverted),
        rework_ratio: e.rework, budget_burn: budgetBurn,
      },
      mode: MODE, unavailable: null,
    });
  }

  forecast(scope: EconomicsScope) {
    return settle({
      data: {
        scope_type: scope.scopeType, scope_id: scope.scopeId,
        forecast: fx.forecastFixture(scope.scopeType, scope.scopeId),
      },
      mode: MODE, unavailable: null,
    });
  }

  forecastPlan() {
    return settle({ data: fx.planFixture(), mode: MODE, unavailable: null });
  }

  scopes() {
    return settle({
      teams: fx.teams.map((t) => ({ id: t.id, name: t.name })).sort((a, b) => a.name.localeCompare(b.name)),
      projects: fx.projects.map((p) => ({ id: p.id, name: p.name })).sort((a, b) => a.name.localeCompare(b.name)),
    });
  }

  myTips(_userId: string, _tf: Timeframe) {
    return settle({
      data: {
        tips: fx.myTips.map((t, i) => ({
          category: i === 0 ? "resume" : "cache_bust",
          text: t.tip,
          could_save: money(parseInt(t.saves.replace(/\D/g, ""), 10) || 0),
        })),
      },
      mode: MODE, unavailable: null,
    });
  }

  pricingModels() {
    return settle({ as_of: "2026-09-10", models: {} });
  }

  myOverview(_userId: string, _tf: Timeframe): Promise<Schemas["Overview"]> {
    const p = fx.myPerson;
    const modelSplit: Schemas["Overview"]["z2"]["model_split"] = {};
    for (const m of fx.sessionDetail.models) {
      modelSplit[m.name] = {
        tokens: Math.round((m.pct / 100) * 500_000),
        cost_usd_micros: Math.round((m.pct / 100) * p.spend * 1_000_000),
      };
    }
    return settle({
      modes_present: ["both"], sessions_by_mode: { both: p.sessionsRun },
      z0: {
        peak_hour: { value: 14, source: "otel_measures" },
        peak_hour_histogram: Array.from({ length: 24 }, (_, h) => (h === 14 ? 9 : h > 8 && h < 20 ? 2 : 0)),
        favorite_model: { model: fx.sessionDetail.models[0]?.name ?? "claude-sonnet-5",
                          tokens: 300_000, cost_usd_micros: Math.round(p.spend * 0.6 * 1_000_000) },
      },
      z1: {
        tokens: { value: p.tokensUsed, source: "otel_measures" },
        cost_usd_micros: { value: Math.round(p.spend * 1_000_000), source: "otel_vendor" },
        sessions: p.sessionsRun, active_sessions: 0,
        active_time_s: { value: p.sessionsRun * 1_800, source: "otel_measures" },
        merged_prs: p.mergedPRs,
        cost_per_pr: money(p.avgCostPerPR),
        subagents_spawned: Math.round(p.sessionsRun * 0.2),
        turns: p.sessionsRun * 12, long_turns: Math.round(p.sessionsRun * 0.3),
        files_touched: { created: 18, edited: 64, total: 82 },
        repos: fx.projects.filter((pr) => pr.contributors.includes(p.id)).map((pr) => `local:${pr.id}`),
      },
      z2: {
        model_split: modelSplit,
        by_query_source: { interactive: { requests: 120, cost_usd_micros: Math.round(p.spend * 1_000_000),
                                          cost_est_usd_micros: 0, cost_effective_usd_micros: Math.round(p.spend * 1_000_000) } },
        by_query_source_coverage: { sessions_with_otel: p.sessionsRun, sessions_total: p.sessionsRun },
        provenance_mix: null,
      },
      z3: {
        top_tools: [{ name: "Bash", count: 140, failed: 6 }, { name: "Edit", count: 95, failed: 1 }],
        top_skills: [], top_mcp_servers: [],
        top_repos: fx.projects.filter((pr) => pr.contributors.includes(p.id))
          .map((pr) => ({ repo_key: pr.name, sessions: Math.round(p.sessionsRun / 2) })),
      },
      z5_scorecard: null,
      recoverable: money(p.recoverableSpend),
      seat: mySeat(p.teamId, p.spend),
    });
  }

  myRepos(_userId: string, _tf: Timeframe) {
    const p = fx.myPerson;
    const repos = fx.projects.filter((pr) => pr.contributors.includes(p.id)).map((pr, i) => ({
      repo_id: `local:${pr.id}`, kind: "local_path", label: pr.name,
      sessions: Math.max(1, Math.round(p.sessionsRun / (i + 2))),
      cost_usd_micros: Math.round(pr.spend * 1_000_000 * 0.3), tokens_total: 120_000,
      files_created: 4, files_edited: 20, lines_added: 900, lines_removed: 210,
    }));
    return settle({ repos });
  }

  mySessions(_userId: string, _tf: Timeframe, limit = 6) {
    const sessions = fx.mySessionHistory.slice(0, limit).map((sess, i) => ({
      session_id: `hist-${fx.myPerson.id}-${i}`, datasource: "both", vendor: "claude_code",
      started_at: agoToIso(`${i + 1}d ago`), duration_s: parseDurationMinutes(sess.duration) * 60,
      tokens_total: Math.round(sess.cost * 15_000), cost_usd_micros: Math.round(sess.cost * 1_000_000),
      title: null, repo: sess.repo, badges: [] as string[],
    }));
    return settle({ sessions, has_more: false });
  }

  // TZ track: an in-memory stand-in for `users.timezone` so the detect-and-confirm banner has
  // something to compare against and persist to in mock mode -- no backend, so this resets on
  // reload, which is fine for a fixture.
  #timezone: string | null = null;

  getTimezone(_userId: string) {
    return settle({ user_id: _userId, timezone: this.#timezone });
  }

  setTimezone(userId: string, timezone: string) {
    this.#timezone = timezone;
    return settle({ user_id: userId, timezone });
  }

  // Overview refresh A3a: an in-memory stand-in for the org's `budget_cycle` row. Saving a
  // different kind on Configure changes it, and `budgetPeriods` follows, so every cycle kind can
  // be checked in the browser. Resets on reload.
  #cycle: MockCycle = { ...DEFAULT_MOCK_CYCLE };
  #cycleUpdatedAt: string | null = null;

  budgetCycle() {
    const c = this.#cycle;
    return settle({ ...c, period_label: MOCK_CYCLE_LABELS[c.kind], updated_at: this.#cycleUpdatedAt });
  }

  budgetPeriods(history = 6) {
    return settle(mockPeriods(this.#cycle, new Date(), history));
  }

  putBudgetCycle(body: Schemas["BudgetCycleWriteRequest"]) {
    // The real server answers a rule violation with a 400 and its reason; reject the same way.
    const reason = mockCycleError(body);
    if (reason) return Promise.reject(new Error(reason));
    const previous = this.#cycle;
    const next = normaliseMockCycle(body);
    const prevLabel = MOCK_CYCLE_LABELS[previous.kind];
    const label = MOCK_CYCLE_LABELS[next.kind];
    const labelChanged = prevLabel !== label;
    // `budget_cycles.put_cycle`'s three outcomes, in its words.
    const note = labelChanged
      ? `Budget amounts are not converted. Amounts saved for '${prevLabel}' periods are kept but not used; set budgets for '${label}' periods to see them.`
      : JSON.stringify(previous) !== JSON.stringify(next)
        ? "Existing budget amounts were kept and now apply per period of the changed cycle."
        : null;
    this.#cycle = next;
    this.#cycleUpdatedAt = new Date().toISOString();
    return this.budgetCycle().then((saved) => ({ ...saved, previous_period_label: prevLabel, label_changed: labelChanged, note }));
  }

  // F1: an in-memory stand-in for the `budgets` table, seeded from the same per-team `budget`
  // fixture field orgOverview/orgUsage already read (mock.ts has no per-project budget of its
  // own, so a repo scope starts unset, same as a real scope nobody has configured yet). Rows are
  // keyed by the cycle's label, like the real table: a kind change leaves the old amounts stored
  // but unread. Resets on reload -- fine for a fixture.
  #budgets = new Map<string, { amount_usd: string | null }>([
    // "default" mirrors `config/directory.yaml`'s `default_org_id` -- the scope_id
    // `org_overview()` actually reads the org budget back with (ConfigureScreen's own note).
    [`monthly:org:${ORG_SCOPE_ID}`, { amount_usd: String(fx.ORG_BUDGET) }],
    ...fx.teams.map((t): [string, { amount_usd: string }] => [`monthly:team:${t.id}`, { amount_usd: String(t.budget) }]),
  ]);

  getBudget(scopeType: string, scopeId: string) {
    const period = MOCK_CYCLE_LABELS[this.#cycle.kind];
    const row = this.#budgets.get(`${period}:${scopeType}:${scopeId}`);
    return settle({ scope_type: scopeType, scope_id: scopeId, period, amount_usd: row?.amount_usd ?? null });
  }

  putBudget(scopeType: string, scopeId: string, body: Schemas["BudgetWriteRequest"]) {
    const period = MOCK_CYCLE_LABELS[this.#cycle.kind];
    this.#budgets.set(`${period}:${scopeType}:${scopeId}`, { amount_usd: body.amount_usd });
    return settle({ scope_type: scopeType, scope_id: scopeId, period, ...body });
  }

  sessionDetail(id: string, view?: "economics") {
    const s = fx.sessionsById[id] ?? fx.sessionDetail;
    const findPractice = (label: string) => s.practices.find((p) => p.label === label)?.ok ?? false;
    const base = {
      session_id: s.id, datasource: "both", vendor: "claude_code", status: s.status,
      started_at: agoToIso("3d ago"), last_event_at: agoToIso("1d ago"), ended_at: agoToIso("1d ago"),
      title: s.title, repo: s.repo, tokens_total: s.tokens,
      cost: { cost_effective_usd_micros: Math.round(s.costUsd * 1_000_000) },
      metrics: { turn_count: s.segments.reduce((a, x) => a + Math.max(1, Math.round(x.minutes / 5)), 0) },
      // R7.1 + the per-session cost-by-repo split (segment-level-pricing plan): a plausible
      // second, sibling repo this session touched (real data comes from
      // `graph_fold.repo_info` + `aggregate.segment_cost_by_repo_key`), so the "Where the
      // cost landed" section has something to draw in mock mode.
      repos: [
        { repo_id: `local:${s.repo}`, repo_key: s.repo, display: s.repo, refs: 9, share: 0.75,
         primary: true, cost_usd_micros: Math.round(s.costUsd * 0.85 * 1_000_000) },
        { repo_id: "local:shared-utils", repo_key: "shared-utils", display: "shared-utils", refs: 3,
         share: 0.25, primary: false, cost_usd_micros: Math.round(s.costUsd * 0.15 * 1_000_000) },
      ],
    };
    if (view !== "economics") return settle(base);
    return settle({
      ...base,
      economics: {
        model_fit: {
          verdict: s.modelFit.verdict, note: s.modelFit.note,
          savings_usd_micros: Math.round(s.modelFit.savingsUsd * 1_000_000),
        },
        segments: s.segments.map((seg, i) => ({
          index: i, label: seg.task.toUpperCase(), confidence: 0.8,
          turns: Math.max(1, Math.round(seg.minutes / 5)), files: null,
          cost_usd_micros: Math.round(seg.costUsd * 1_000_000), carry_usd_micros: null, fit: seg.fit,
        })),
        channels: {
          carry: { value: { tokens: 0, usd_micros: Math.round(s.carryUsd * 1_000_000), share: null }, source: "carry_rollup" },
          recache: { value: { tokens: 0, usd_micros: Math.round(s.costUsd * 0.05 * 1_000_000), share: null }, source: "channel_cost_rollup" },
          output: { value: { tokens: 0, usd_micros: Math.round(s.costUsd * 0.6 * 1_000_000), share: null }, source: "channel_cost_rollup" },
          input: { value: { tokens: 0, usd_micros: Math.round(s.costUsd * 0.05 * 1_000_000), share: null }, source: "channel_cost_rollup" },
        },
        practices: {
          verified: findPractice("Verifier run"), verifier_runs: 1, verifier_pass_rate: 1,
          exitplan_count: findPractice("Plan mode") ? 1 : 0,
          subagent_research: s.subagents.length, review_subagent: false, interrupts: 0,
          manual_compactions: findPractice("Compaction") ? 1 : 0,
          auto_compactions: findPractice("Compaction") ? 0 : 2,
          fresh_session: findPractice("Fresh session"),
        },
        // D1-lifecycle: build->review->ship milestones off the session's linked PR (live comes
        // from _session_lifecycle joining session_git_links + PR timestamps).
        lifecycle: {
          session_started_at: agoToIso("3d ago"),
          first_pr_opened_at: agoToIso("2d ago"),
          first_review_at: agoToIso("1d ago"),
          reopened_at: [agoToIso("12h ago")],   // a rework loop before the final merge
          last_merged_at: agoToIso("6h ago"),
        },
        // A plausible rising context curve with one mid-session compaction, so the chart has
        // something to draw in mock mode (the live curve comes from graph_fold.context_curve_of).
        context_curve: Array.from({ length: 8 }, (_, i) => ({
          index: i + 1,
          ts: null,
          context_tokens: Math.round((28_000 + i * 22_000) * (i === 4 ? 0.4 : 1)),
          compaction: i === 4,
        })),
        models: toModelUsage(s.models, s.tokens),
      },
    });
  }
}
