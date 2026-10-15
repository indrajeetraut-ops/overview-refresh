/**
 * FIXTURE DATA (MVP plan §1 / U1). This was the prototype's data contract; it is now demoted
 * to a fixture consumed only through `mock-source.ts` + the `useData` seam. The real contract
 * is backend-owned (`server/api/schemas/` → `api-types.ts`).
 *
 * As each screen is cut over (U5–U7), its slice here is **rewritten to the generated envelope
 * shape** — dropping the prototype-isms flagged in `server/api/schemas/README.md`:
 *   - colours in data (`color`/`fill`, `"var(--…)"`, `"#hex"`) — the client owns colour
 *   - pre-formatted strings (`buildTime:"4 days"`, `lastActive:"2h ago"`) — the client formats
 *   - routing hints (`worthALook[].screen`/`screenParam`) — replaced by typed `{kind,id}` refs
 *   - generated fakes (`genEnvelope`/`genBurn`/`econFor`, hardcoded multipliers) — see Track X5
 */
import { ORG_SCOPE_ID } from "./source";
import type { Schemas } from "./source";
import { addDays, eachDay } from "./isoDay";

export type Team = {
  id: string;
  name: string;
  budget: number;
  spent: number;
  tokensUsed: number;
  members: number;
  mergedPRs: number;
  recoverableSpend: number;
  avgCostPerPR: number;
};

export type Person = {
  id: string;
  name: string;
  initials: string;
  teamId: string;
  spend: number;
  tokensUsed: number;
  sessionsRun: number;
  mergedPRs: number;
  avgCostPerPR: number;
  recoverableSpend: number;
  habits: Habit[];
  workQuality: WorkQuality[];
  peerCompare?: string;
};

export type Habit = {
  label: string;
  description: string;
  recoverableAmount: number;
  category: "context" | "rework" | "model";
};

export type WorkQuality = {
  label: "Clean" | "Heavy lift" | "Quick & dirty" | "Costly rework";
  count: number;
  color: string;
};

export type Project = {
  id: string;
  name: string;
  teamId: string;
  spend: number;
  mergedPRs: number;
  contributors: string[];
  features: Feature[];
};

export type Feature = {
  id: string;
  projectId: string;
  name: string;
  spend: number;
  buildTime: string;
  buildDays: number;
  buildSpend: number;
  reworkSpend: number;
  readingSpend: number;
  contributors: string[];
  prs: PR[];
  status: "shipped" | "in-progress";
};

export type PR = {
  id: string;
  title: string;
  status: "merged" | "open" | "closed";
  author: string;
  cost: number;
};

export const ORG_BUDGET = 15_000;
export const ORG_SPENT = 12_840;
export const ORG_RECOVERABLE = 2_180;
export const ORG_MERGED_PRS = 187;
export const ORG_AVG_COST_PER_PR = Math.round(ORG_SPENT / ORG_MERGED_PRS);

export const teams: Team[] = [
  {
    id: "platform",
    name: "Platform",
    budget: 4_000,
    spent: 4_320,
    tokensUsed: 2_190_000,
    members: 8,
    mergedPRs: 52,
    recoverableSpend: 820,
    avgCostPerPR: 83,
  },
  {
    id: "growth",
    name: "Growth",
    budget: 3_000,
    spent: 2_140,
    tokensUsed: 1_070_000,
    members: 6,
    mergedPRs: 41,
    recoverableSpend: 310,
    avgCostPerPR: 52,
  },
  {
    id: "data",
    name: "Data",
    budget: 2_500,
    spent: 1_890,
    tokensUsed: 945_000,
    members: 5,
    mergedPRs: 29,
    recoverableSpend: 440,
    avgCostPerPR: 65,
  },
  {
    id: "infra",
    name: "Infrastructure",
    budget: 3_500,
    spent: 2_930,
    tokensUsed: 1_620_000,
    members: 7,
    mergedPRs: 48,
    recoverableSpend: 470,
    avgCostPerPR: 61,
  },
  {
    id: "mobile",
    name: "Mobile",
    budget: 2_000,
    spent: 1_560,
    tokensUsed: 560_000,
    members: 4,
    mergedPRs: 17,
    recoverableSpend: 140,
    avgCostPerPR: 92,
  },
];

export const people: Person[] = [
  {
    id: "alex-kim",
    name: "Alex Kim",
    initials: "AK",
    teamId: "platform",
    spend: 890,
    tokensUsed: 448_000,
    sessionsRun: 47,
    mergedPRs: 12,
    avgCostPerPR: 74,
    recoverableSpend: 210,
    peerCompare: "priya-nair",
    habits: [
      {
        label: "Re-reading old context",
        description: "Long sessions that re-summarize previous work instead of starting fresh.",
        recoverableAmount: 120,
        category: "context",
      },
      {
        label: "Oversized model for small tasks",
        description: "Using Opus-class models for single-file edits under 50 lines.",
        recoverableAmount: 90,
        category: "model",
      },
    ],
    workQuality: [
      { label: "Clean", count: 7, color: "#1A7A4A" },
      { label: "Heavy lift", count: 3, color: "#2B4FD8" },
      { label: "Quick & dirty", count: 1, color: "#A05C00" },
      { label: "Costly rework", count: 1, color: "#C5333A" },
    ],
  },
  {
    id: "priya-nair",
    name: "Priya Nair",
    initials: "PN",
    teamId: "platform",
    spend: 412,
    tokensUsed: 206_000,
    sessionsRun: 31,
    mergedPRs: 11,
    avgCostPerPR: 37,
    recoverableSpend: 30,
    habits: [
      {
        label: "Occasional large context loads",
        description: "Some sessions start by loading the entire repo rather than targeted files.",
        recoverableAmount: 30,
        category: "context",
      },
    ],
    workQuality: [
      { label: "Clean", count: 9, color: "#1A7A4A" },
      { label: "Heavy lift", count: 2, color: "#2B4FD8" },
      { label: "Quick & dirty", count: 0, color: "#A05C00" },
      { label: "Costly rework", count: 0, color: "#C5333A" },
    ],
  },
  {
    id: "marcus-chen",
    name: "Marcus Chen",
    initials: "MC",
    teamId: "platform",
    spend: 680,
    tokensUsed: 340_000,
    sessionsRun: 38,
    mergedPRs: 9,
    avgCostPerPR: 76,
    recoverableSpend: 195,
    peerCompare: "priya-nair",
    habits: [
      {
        label: "Rework loops",
        description: "Several sessions involved fixing bugs introduced in the same session.",
        recoverableAmount: 145,
        category: "rework",
      },
      {
        label: "Re-reading old context",
        description: "Average session context is 40% re-summarization.",
        recoverableAmount: 50,
        category: "context",
      },
    ],
    workQuality: [
      { label: "Clean", count: 4, color: "#1A7A4A" },
      { label: "Heavy lift", count: 3, color: "#2B4FD8" },
      { label: "Quick & dirty", count: 2, color: "#A05C00" },
      { label: "Costly rework", count: 2, color: "#C5333A" },
    ],
  },
  {
    id: "sarah-jones",
    name: "Sarah Jones",
    initials: "SJ",
    teamId: "platform",
    spend: 290,
    tokensUsed: 145_000,
    sessionsRun: 18,
    mergedPRs: 8,
    avgCostPerPR: 36,
    recoverableSpend: 15,
    habits: [],
    workQuality: [
      { label: "Clean", count: 8, color: "#1A7A4A" },
      { label: "Heavy lift", count: 0, color: "#2B4FD8" },
      { label: "Quick & dirty", count: 0, color: "#A05C00" },
      { label: "Costly rework", count: 0, color: "#C5333A" },
    ],
  },
  {
    id: "tom-wright",
    name: "Tom Wright",
    initials: "TW",
    teamId: "platform",
    spend: 780,
    tokensUsed: 390_000,
    sessionsRun: 52,
    mergedPRs: 7,
    avgCostPerPR: 111,
    recoverableSpend: 280,
    peerCompare: "sarah-jones",
    habits: [
      {
        label: "Oversized model for small tasks",
        description: "70% of sessions use Opus-class on tasks under 100 lines.",
        recoverableAmount: 180,
        category: "model",
      },
      {
        label: "Rework loops",
        description: "Average of 3.2 rework iterations per PR.",
        recoverableAmount: 100,
        category: "rework",
      },
    ],
    workQuality: [
      { label: "Clean", count: 2, color: "#1A7A4A" },
      { label: "Heavy lift", count: 2, color: "#2B4FD8" },
      { label: "Quick & dirty", count: 3, color: "#A05C00" },
      { label: "Costly rework", count: 3, color: "#C5333A" },
    ],
  },
  {
    id: "lena-foster",
    name: "Lena Foster",
    initials: "LF",
    teamId: "platform",
    spend: 148,
    tokensUsed: 74_000,
    sessionsRun: 9,
    mergedPRs: 3,
    avgCostPerPR: 49,
    recoverableSpend: 0,
    habits: [],
    workQuality: [
      { label: "Clean", count: 3, color: "#1A7A4A" },
      { label: "Heavy lift", count: 0, color: "#2B4FD8" },
      { label: "Quick & dirty", count: 0, color: "#A05C00" },
      { label: "Costly rework", count: 0, color: "#C5333A" },
    ],
  },
  {
    id: "dan-huang",
    name: "Dan Huang",
    initials: "DH",
    teamId: "platform",
    spend: 82,
    tokensUsed: 41_000,
    sessionsRun: 5,
    mergedPRs: 1,
    avgCostPerPR: 82,
    recoverableSpend: 0,
    habits: [],
    workQuality: [
      { label: "Clean", count: 1, color: "#1A7A4A" },
      { label: "Heavy lift", count: 0, color: "#2B4FD8" },
      { label: "Quick & dirty", count: 0, color: "#A05C00" },
      { label: "Costly rework", count: 0, color: "#C5333A" },
    ],
  },
  {
    id: "nina-patel",
    name: "Nina Patel",
    initials: "NP",
    teamId: "platform",
    spend: 48,
    tokensUsed: 24_000,
    sessionsRun: 3,
    mergedPRs: 2,
    avgCostPerPR: 24,
    recoverableSpend: 0,
    habits: [],
    workQuality: [
      { label: "Clean", count: 2, color: "#1A7A4A" },
      { label: "Heavy lift", count: 0, color: "#2B4FD8" },
      { label: "Quick & dirty", count: 0, color: "#A05C00" },
      { label: "Costly rework", count: 0, color: "#C5333A" },
    ],
  },
];

export const projects: Project[] = [
  {
    id: "api-gateway",
    name: "api-gateway",
    teamId: "platform",
    spend: 1_240,
    mergedPRs: 18,
    contributors: ["alex-kim", "priya-nair", "marcus-chen"],
    features: [
      {
        id: "rate-limiting",
        projectId: "api-gateway",
        name: "Rate limiting v2",
        spend: 420,
        buildTime: "4 days",
        buildDays: 4,
        buildSpend: 260,
        reworkSpend: 110,
        readingSpend: 50,
        contributors: ["alex-kim", "priya-nair"],
        status: "shipped",
        prs: [
          { id: "pr-142", title: "feat: add token bucket algorithm", status: "merged", author: "Alex Kim", cost: 180 },
          { id: "pr-144", title: "fix: edge case on burst resets", status: "merged", author: "Priya Nair", cost: 95 },
          { id: "pr-148", title: "chore: add tests for burst window", status: "merged", author: "Alex Kim", cost: 145 },
        ],
      },
      {
        id: "auth-refresh",
        projectId: "api-gateway",
        name: "Auth token refresh",
        spend: 310,
        buildTime: "3 days",
        buildDays: 3,
        buildSpend: 210,
        reworkSpend: 60,
        readingSpend: 40,
        contributors: ["marcus-chen"],
        status: "shipped",
        prs: [
          { id: "pr-151", title: "feat: silent token refresh flow", status: "merged", author: "Marcus Chen", cost: 195 },
          { id: "pr-155", title: "fix: race condition on concurrent refresh", status: "merged", author: "Marcus Chen", cost: 115 },
        ],
      },
      {
        id: "request-signing",
        projectId: "api-gateway",
        name: "Request signing",
        spend: 195,
        buildTime: "2 days",
        buildDays: 2,
        buildSpend: 160,
        reworkSpend: 20,
        readingSpend: 15,
        contributors: ["priya-nair"],
        status: "shipped",
        prs: [
          { id: "pr-160", title: "feat: HMAC request signing", status: "merged", author: "Priya Nair", cost: 195 },
        ],
      },
      {
        id: "retry-policy",
        projectId: "api-gateway",
        name: "Retry policy engine",
        spend: 315,
        buildTime: "5 days",
        buildDays: 5,
        buildSpend: 180,
        reworkSpend: 90,
        readingSpend: 45,
        contributors: ["alex-kim", "marcus-chen"],
        status: "in-progress",
        prs: [
          { id: "pr-171", title: "feat: configurable retry strategies", status: "open", author: "Alex Kim", cost: 140 },
          { id: "pr-173", title: "fix: exponential backoff overflow", status: "open", author: "Marcus Chen", cost: 175 },
        ],
      },
    ],
  },
  {
    id: "auth-service",
    name: "auth-service",
    teamId: "platform",
    spend: 860,
    mergedPRs: 12,
    contributors: ["sarah-jones", "tom-wright"],
    features: [],
  },
  {
    id: "growth-web",
    name: "growth-web",
    teamId: "growth",
    spend: 720,
    mergedPRs: 21,
    contributors: [],
    features: [],
  },
];

export const worthALook = [
  {
    id: "w1",
    text: "Platform is $320 over its $4,000 budget.",
    action: "View Platform",
    screen: "team",
    screenParam: "platform",
    severity: "red" as const,
  },
  {
    id: "w2",
    text: "Mobile has 4 seats using under 60% of their allowance — $880 in unused capacity.",
    action: "View Usage",
    screen: "usage",
    screenParam: null,
    severity: "amber" as const,
  },
  {
    id: "w3",
    text: "Tom Wright (Platform) has $280 in recoverable spend this month.",
    action: "View Tom",
    screen: "person",
    screenParam: "tom-wright",
    severity: "amber" as const,
  },
  {
    id: "w4",
    text: "Growth merged 41 PRs at $52 each — the best cost-per-PR of any team.",
    action: "View Growth",
    screen: "team",
    screenParam: "growth",
    severity: "green" as const,
  },
];

export const askSuggestions = [
  "Which team gets the best value?",
  "Where is money recoverable?",
  "Who's barely using their seat?",
  "What did the rate-limiting feature cost?",
];

export const askResponses: Record<string, { answer: string; receipt: string; sessions: number; people: number; followups: string[] }> = {
  "Which team gets the best value?": {
    answer: "Growth shipped 41 PRs at an average of $52 each — the lowest cost per merged PR of any team, at 71% budget utilization.",
    receipt: "Based on 41 merged PRs · $2,140 spend · 6 people",
    sessions: 89,
    people: 6,
    followups: ["Show Growth's spend breakdown", "Compare Growth vs Platform", "Who drives Growth's efficiency?"],
  },
  "Where is money recoverable?": {
    answer: "About $2,180 is recoverable this month — mostly from re-reading old context ($890), oversized model choices ($720), and rework loops ($570).",
    receipt: "Based on 34 sessions with recoverable patterns · 8 people",
    sessions: 34,
    people: 8,
    followups: ["Which people have the most recoverable spend?", "What causes rework loops?", "Show context-reading breakdown"],
  },
  "Who's barely using their seat?": {
    answer: "Dan Huang and Nina Patel (Platform) are each using under 12% of their monthly seat allowance. Mobile has 3 engineers under 60% utilization.",
    receipt: "Based on token usage vs monthly limits · all teams",
    sessions: 0,
    people: 5,
    followups: ["Show full seat utilization", "Which seats could be reallocated?"],
  },
  "What did the rate-limiting feature cost?": {
    answer: "Rate limiting v2 cost $420 over 4 days. $260 was building, $110 was rework, and $50 was reading context. It shipped 3 PRs.",
    receipt: "Based on 12 sessions · 2 people · api-gateway",
    sessions: 12,
    people: 2,
    followups: ["Compare to other features", "Who worked on this?", "Show the PRs"],
  },
};

export const MY_ID = "alex-kim";
export const myPerson = people.find((p) => p.id === MY_ID)!;

export const mySessionHistory = [
  { date: "Sep 3", repo: "api-gateway", duration: "1h 12m", cost: 38, mergedPRs: 1, label: "Heavy lift" as const },
  { date: "Sep 3", repo: "auth-service", duration: "24m", cost: 9, mergedPRs: 0, label: "Quick & dirty" as const },
  { date: "Sep 2", repo: "api-gateway", duration: "2h 05m", cost: 67, mergedPRs: 1, label: "Clean" as const },
  { date: "Sep 1", repo: "api-gateway", duration: "48m", cost: 22, mergedPRs: 0, label: "Clean" as const },
  { date: "Aug 31", repo: "auth-service", duration: "3h 18m", cost: 94, mergedPRs: 2, label: "Costly rework" as const },
  { date: "Aug 30", repo: "api-gateway", duration: "55m", cost: 28, mergedPRs: 1, label: "Clean" as const },
];

export const myTips = [
  {
    tip: "Your long sessions on api-gateway re-read context heavily. Try starting a fresh session when picking up work the next day.",
    saves: "$120 / month",
  },
  {
    tip: "You use Opus-class models for most tasks. Switching to Sonnet for single-file edits could cut model costs.",
    saves: "$90 / month",
  },
];

// ─────────────────────────────────────────────────────────────────
// Overview additions (2026-09-06)
// ─────────────────────────────────────────────────────────────────

// Spend by agent. Today it is mostly claude-code; the small codex/cursor
// slices are illustrative so the donut reads as multi-agent-ready.
export const agentSpend = [
  { name: "Claude Code", value: 11_200, fill: "var(--primary)" },
  { name: "Codex", value: 1_100, fill: "var(--green)" },
  { name: "Cursor", value: 540, fill: "var(--amber)" },
];

export const CACHE_EFFICIENCY = 0.78; // hit ratio
export const CACHE_LOST_USD = 1_180; // spent on re-caching context

// ── Unit economics of a PR ───────────────────────────────────────
export type Dist = { avg: number; min: number; p50: number; p70: number; p95: number; max: number };
export const prCostStats: Dist = { avg: 69, min: 6, p50: 52, p70: 78, p95: 210, max: 486 };
export const prTimeStats: Dist = { avg: 2.6, min: 0.2, p50: 1.8, p70: 3.1, p95: 7.4, max: 14.0 }; // days
export const MERGED_PR_COST = 61;
export const REVERTED_PR_COST = 138; // sunk — produced nothing that stayed
export const REWORK_RATIO = 0.21;

// Cost-per-PR envelope over time: median, p25–p75 band, p90/p99 lines,
// sparse outliers, then a widening forecast cone. Deterministic (no random).
export type EnvPoint = {
  label: string;
  bandLo?: number; band?: number; median?: number; p90?: number; p99?: number; outlier?: number | null;
  forecast?: boolean; fBandLo?: number; fBand?: number; fMedian?: number;
};
function genEnvelope(): EnvPoint[] {
  const TODAY = new Date(2026, 8, 6);
  const ago = (days: number) => {
    const d = new Date(TODAY);
    d.setDate(d.getDate() - days);
    return `${d.toLocaleString("en-US", { month: "short" })} ${d.getDate()}`;
  };
  const pts: EnvPoint[] = [];
  let base = 8.5;
  let lastMedian = base;
  for (let i = 0; i < 18; i++) {
    base += Math.sin(i / 2.4) * 0.5 + (i > 10 ? 0.22 : 0.02);
    const median = +base.toFixed(1);
    lastMedian = median;
    const p25 = +(median - 2.2).toFixed(1);
    const p75 = +(median + 3.2).toFixed(1);
    pts.push({
      label: ago(18 - i),
      bandLo: p25,
      band: +(p75 - p25).toFixed(1),
      median,
      p90: +(median + 6.5).toFixed(1),
      p99: +(median + 13 + (i % 4 === 0 ? 9 : 0)).toFixed(1),
      outlier: i % 5 === 2 ? +(median + 17 + i).toFixed(1) : null,
    });
  }
  // bridge the boundary so the median and forecast lines meet
  pts.push({ label: "now", median: lastMedian, fMedian: lastMedian, fBandLo: lastMedian, fBand: 0, forecast: true });
  for (let j = 1; j <= 6; j++) {
    const m = +(lastMedian + j * 0.35).toFixed(1);
    const lo = +(m - (1.5 + j * 0.5)).toFixed(1);
    const hi = +(m + (2.5 + j * 0.9)).toFixed(1);
    pts.push({ label: `+${j}d`, forecast: true, fMedian: m, fBandLo: lo, fBand: +(hi - lo).toFixed(1) });
  }
  return pts;
}
export const prCostEnvelope = genEnvelope();

// Month-end budget forecast (burn-up): actual so far, projected to month end.
export type BurnPoint = { day: string; actual?: number; projected?: number };
function genBurn(): BurnPoint[] {
  const pts: BurnPoint[] = [];
  const perDay = ORG_SPENT / 21;
  for (let d = 1; d <= 30; d++) {
    const label = `${d}`;
    if (d <= 21) pts.push({ day: label, actual: Math.round(perDay * d) });
    else if (d === 21) pts.push({ day: label, actual: ORG_SPENT, projected: ORG_SPENT });
    else pts.push({ day: label, projected: Math.round(ORG_SPENT + perDay * 1.08 * (d - 21)) });
  }
  // ensure continuity at the handoff
  pts[20].projected = ORG_SPENT;
  return pts;
}
export const budgetBurn = genBurn();
export const PROJECTED_MONTH_END = budgetBurn[budgetBurn.length - 1].projected!;

// ─────────────────────────────────────────────────────────────────
// Usage tab additions
// ─────────────────────────────────────────────────────────────────
export const usageHeader = {
  users: 42,
  agents: 3,
  hours: 1_284,
  sessions: 936,
  avgSessionLen: "38m",
  avgPRPerSession: 0.6,
};

export const sessionTypes = [
  { type: "IMPLEMENT", count: 388 },
  { type: "BUG_FIX", count: 214 },
  { type: "REVIEW", count: 156 },
  { type: "COMPREHEND", count: 98 },
  { type: "PLAN", count: 52 },
  { type: "MISC", count: 28 },
];
export const SESSIONS_NO_OUTPUT_PCT = 0.18;

// ─────────────────────────────────────────────────────────────────
// Session deep-dive (2026-09-07)
// ─────────────────────────────────────────────────────────────────
export type SessionSegment = {
  label: string; task: string; model: string;
  costUsd: number; tokens: number; minutes: number;
  fit: "good" | "over" | "under";
};
export type SessionDetail = {
  id: string; title: string; taskType: string;
  user: string; initials: string; repo: string; branch: string; date: string;
  startedAt: string; prOpenedAt: string | null; mergedAt: string | null;
  agent: string; status: string;
  costUsd: number; tokens: number; reasoningTokens: number;
  activeMin: number;            // real agent activity
  activeLabel: string;
  buildElapsed: string;         // T0 -> T1 (session start -> PR opened)
  reviewElapsed: string;        // T1 -> T2 (PR opened -> merged/closed)
  totalElapsed: string;         // T0 -> T2
  lifecycle: { name: string; ts: string | null; x: number; spanLabel?: string; spanKind?: "build" | "review" | "rework" }[];
  cacheHitRatio: number; carryUsd: number;
  modelFit: { verdict: "good" | "over" | "under"; note: string; savingsUsd: number };
  models: { name: string; pct: number; tier: string }[];
  channels: { label: string; usd: number; color: string }[];
  segments: SessionSegment[];
  context: { t: string; ctx: number; compaction?: boolean }[]; // context size (k tokens) over the session
  tools: { name: string; count: number; failPct?: number }[];
  files: { path: string; added: number; removed: number }[];
  subagents: { purpose: string; model: string; tokens: string; minutes: number }[];
  practices: { label: string; ok: boolean; detail: string }[];
  prs: { n: string; status: string }[];
};

export const sessionDetail: SessionDetail = {
  id: "s1",
  title: "Add token-bucket rate limiter to the gateway",
  taskType: "Debug + Develop",
  user: "Riya Patel", initials: "RP", repo: "api-gateway", branch: "feat/rate-limit",
  date: "Sep 2, 2026 · 2:14 PM",
  startedAt: "Sep 2 · 2:14 PM", prOpenedAt: "Sep 2 · 4:12 PM", mergedAt: "Sep 5 · 8:00 PM",
  agent: "Claude Code", status: "ended",
  costUsd: 67, tokens: 2_400_000, reasoningTokens: 624_000,
  activeMin: 74, activeLabel: "1h 14m",
  buildElapsed: "1h 58m", reviewElapsed: "3d 4h", totalElapsed: "3d 6h",
  lifecycle: [
    { name: "session start", ts: "Sep 2 · 2:14 PM", x: 0, spanLabel: "Build · 1h 58m", spanKind: "build" },
    { name: "PR opened", ts: "Sep 2 · 4:12 PM", x: 40, spanLabel: "Review · 3d 4h", spanKind: "review" },
    { name: "merged", ts: "Sep 5 · 8:00 PM", x: 100 },
  ],
  cacheHitRatio: 0.71, carryUsd: 22,
  modelFit: {
    verdict: "over",
    note: "Opus ran the straightforward implement and fix phases; Sonnet would have handled them at similar quality.",
    savingsUsd: 14,
  },
  models: [
    { name: "opus-4.8", pct: 68, tier: "high" },
    { name: "sonnet-5", pct: 22, tier: "mid" },
    { name: "haiku-4.5", pct: 10, tier: "low" },
  ],
  channels: [
    { label: "Building output", usd: 26, color: "var(--primary)" },
    { label: "Re-reading context", usd: 22, color: "var(--amber)" },
    { label: "Reasoning (thinking)", usd: 9, color: "var(--green)" },
    { label: "Re-cache", usd: 6, color: "var(--blue)" },
    { label: "Input", usd: 4, color: "var(--muted-foreground)" },
  ],
  segments: [
    { label: "Read the gateway + limiter libs", task: "Research", model: "sonnet-5", costUsd: 6, tokens: 320_000, minutes: 12, fit: "good" },
    { label: "Debug why the old limiter drops bursts", task: "Debug", model: "opus-4.8", costUsd: 24, tokens: 780_000, minutes: 28, fit: "good" },
    { label: "Implement the token bucket", task: "Develop", model: "opus-4.8", costUsd: 21, tokens: 690_000, minutes: 22, fit: "over" },
    { label: "Fix the failing burst test", task: "Fix", model: "opus-4.8", costUsd: 11, tokens: 410_000, minutes: 9, fit: "over" },
    { label: "Verify + open PR", task: "Check", model: "haiku-4.5", costUsd: 5, tokens: 200_000, minutes: 3, fit: "good" },
  ],
  context: [
    { t: "0m", ctx: 18 }, { t: "10m", ctx: 62 }, { t: "20m", ctx: 104 },
    { t: "28m", ctx: 168, compaction: true }, { t: "34m", ctx: 46 },
    { t: "44m", ctx: 92 }, { t: "54m", ctx: 148 },
    { t: "60m", ctx: 182, compaction: true }, { t: "66m", ctx: 58 }, { t: "74m", ctx: 96 },
  ],
  tools: [
    { name: "Read", count: 42 }, { name: "Edit", count: 28 },
    { name: "Bash", count: 19, failPct: 5 }, { name: "Grep", count: 14 }, { name: "Write", count: 6 },
  ],
  files: [
    { path: "src/gateway/ratelimit.ts", added: 180, removed: 24 },
    { path: "src/gateway/middleware.ts", added: 36, removed: 12 },
    { path: "tests/ratelimit.test.ts", added: 94, removed: 0 },
  ],
  subagents: [
    { purpose: "Compare rate-limit library options", model: "sonnet-5", tokens: "240K", minutes: 6 },
  ],
  practices: [
    { label: "Plan mode", ok: false, detail: "Went straight to code — likely drove the fix loop" },
    { label: "Compaction", ok: false, detail: "Context hit 182k before clearing" },
    { label: "Verifier run", ok: true, detail: "Tests run before the PR" },
    { label: "Fresh session", ok: true, detail: "Started clean" },
    { label: "Delegation", ok: true, detail: "1 sub-agent for research" },
  ],
  prs: [{ n: "#418", status: "merged" }],
};

// A rework-heavy session — Sonnet thrashing on a hard refactor.
export const sessionReworkDetail: SessionDetail = {
  id: "s2",
  title: "Make the auth middleware async",
  taskType: "Refactor + Fix",
  user: "Marcus Chen", initials: "MC", repo: "auth-service", branch: "refactor/async-mw",
  date: "Sep 4, 2026 · 10:02 AM",
  startedAt: "Sep 4 · 10:02 AM", prOpenedAt: "Sep 4 · 1:22 PM", mergedAt: "Sep 6 · 11:15 AM",
  agent: "Claude Code", status: "ended",
  costUsd: 148, tokens: 4_100_000, reasoningTokens: 1_190_000,
  activeMin: 200, activeLabel: "3h 20m",
  buildElapsed: "3h 20m", reviewElapsed: "1d 22h", totalElapsed: "2d 1h",
  cacheHitRatio: 0.54, carryUsd: 41,
  modelFit: {
    verdict: "under",
    note: "Sonnet kept missing an async edge case and looped four times before it went green. Opus would likely have solved it on the first pass — the cheaper model cost more here.",
    savingsUsd: 0,
  },
  models: [
    { name: "sonnet-5", pct: 68, tier: "mid" },
    { name: "opus-4.8", pct: 20, tier: "high" },
    { name: "haiku-4.5", pct: 12, tier: "low" },
  ],
  channels: [
    { label: "Building output", usd: 34, color: "var(--primary)" },
    { label: "Rework loops", usd: 46, color: "var(--red)" },
    { label: "Re-reading context", usd: 30, color: "var(--amber)" },
    { label: "Reasoning (thinking)", usd: 22, color: "var(--green)" },
    { label: "Re-cache + input", usd: 16, color: "var(--muted-foreground)" },
  ],
  segments: [
    { label: "Read + plan the refactor", task: "Research", model: "sonnet-5", costUsd: 12, tokens: 380_000, minutes: 22, fit: "good" },
    { label: "First async rewrite", task: "Refactor", model: "sonnet-5", costUsd: 28, tokens: 720_000, minutes: 34, fit: "under" },
    { label: "Fix failing tests — attempt 1", task: "Fix", model: "sonnet-5", costUsd: 22, tokens: 610_000, minutes: 26, fit: "under" },
    { label: "Revert & retry the edge case", task: "Fix", model: "sonnet-5", costUsd: 26, tokens: 680_000, minutes: 30, fit: "under" },
    { label: "Fix — attempt 3, finally green", task: "Fix", model: "opus-4.8", costUsd: 18, tokens: 520_000, minutes: 20, fit: "good" },
    { label: "Open PR + address review", task: "Check", model: "sonnet-5", costUsd: 42, tokens: 1_190_000, minutes: 48, fit: "under" },
  ],
  context: [
    { t: "0m", ctx: 22 }, { t: "20m", ctx: 88 }, { t: "40m", ctx: 154 },
    { t: "56m", ctx: 210, compaction: true }, { t: "64m", ctx: 60 },
    { t: "90m", ctx: 132 }, { t: "120m", ctx: 196 },
    { t: "140m", ctx: 214, compaction: true }, { t: "150m", ctx: 72 },
    { t: "180m", ctx: 148 }, { t: "200m", ctx: 190 },
  ],
  tools: [
    { name: "Read", count: 61 }, { name: "Edit", count: 44 },
    { name: "Bash", count: 38, failPct: 18 }, { name: "Grep", count: 20 }, { name: "Write", count: 9 },
  ],
  files: [
    { path: "src/auth/middleware.ts", added: 240, removed: 182 },
    { path: "src/auth/session.ts", added: 64, removed: 40 },
    { path: "tests/auth.test.ts", added: 120, removed: 58 },
  ],
  subagents: [],
  practices: [
    { label: "Plan mode", ok: false, detail: "No plan — went straight into the rewrite, which drove the fix loop" },
    { label: "Compaction", ok: false, detail: "Context hit 214k twice before clearing" },
    { label: "Verifier run", ok: true, detail: "Tests run each round — but 4 rounds failed" },
    { label: "Fresh session", ok: false, detail: "Resumed an older session; carried stale context" },
    { label: "Delegation", ok: false, detail: "No sub-agents — did the exploration inline on the pricey path" },
  ],
  prs: [{ n: "#502", status: "reopened" }],
  lifecycle: [
    { name: "session start", ts: "Sep 4 · 10:02 AM", x: 0, spanLabel: "Build · 3h 20m", spanKind: "build" },
    { name: "PR opened", ts: "Sep 4 · 1:22 PM", x: 26, spanLabel: "Review · 1d 2h", spanKind: "review" },
    { name: "changes requested", ts: "Sep 5 · 3:40 PM", x: 52, spanLabel: "Rework · 2h 40m", spanKind: "rework" },
    { name: "merged", ts: "Sep 6 · 11:15 AM", x: 100 },
  ],
};

export const sessionsById: Record<string, SessionDetail> = { s1: sessionDetail, s2: sessionReworkDetail };

// ─────────────────────────────────────────────────────────────────
// PR deep-dive (2026-09-07)
// ─────────────────────────────────────────────────────────────────
export type PRSessionRef = { id: string; title: string; who: string; initials: string; role: "build" | "rework"; costUsd: number; active: string; when: string };
export type PRReview = { reviewer: string; initials: string; state: "approved" | "changes" | "commented"; ts: string; comments: number };
export type PRDetail = {
  id: string; title: string; repo: string; author: string; initials: string;
  head: string; base: string; linkedIssue: string; labels: string[]; status: string;
  costUsd: number; tokens: number; reasoningTokens: number;
  preOpenCost: number; postOpenCost: number;
  linesAdded: number; linesRemoved: number; files: number; commits: number;
  activeLabel: string; totalElapsed: string; timeToFirstReview: string; timeToMerge: string;
  lifecycle: { name: string; ts: string | null; x: number; spanLabel?: string; spanKind?: "build" | "review" | "rework" }[];
  reviews: PRReview[];
  reviewRounds: number; reviewComments: number; unresolved: number; reopens: number; ci: string;
  models: { name: string; pct: number; tier: string }[];
  cacheHitRatio: number;
  channels: { label: string; usd: number; color: string }[];
  sessions: PRSessionRef[];
  quality: "Clean" | "Heavy lift" | "Quick & dirty" | "Costly rework";
  reworkRatio: number; churnPost: number;
  flags: { label: string; detail: string; sev: "amber" | "red" }[];
};

export const prDetail: PRDetail = {
  id: "502", title: "Make the auth middleware async", repo: "auth-service",
  author: "Marcus Chen", initials: "MC", head: "refactor/async-mw", base: "main",
  linkedIssue: "PLAT-214", labels: ["refactor", "auth"], status: "merged",
  costUsd: 214, tokens: 6_200_000, reasoningTokens: 1_612_000,
  preOpenCost: 148, postOpenCost: 66,
  linesAdded: 424, linesRemoved: 280, files: 6, commits: 9,
  activeLabel: "5h 5m", totalElapsed: "2d 1h", timeToFirstReview: "18h", timeToMerge: "2d 1h",
  lifecycle: [
    { name: "first work", ts: "Sep 4 · 10:02 AM", x: 0, spanLabel: "Build · 3h 20m", spanKind: "build" },
    { name: "PR opened", ts: "Sep 4 · 1:22 PM", x: 22, spanLabel: "Review · 18h", spanKind: "review" },
    { name: "changes requested", ts: "Sep 5 · 7:40 AM", x: 46, spanLabel: "Rework · 1h 45m", spanKind: "rework" },
    { name: "re-review", ts: "Sep 5 · 3:10 PM", x: 64, spanLabel: "Review · 20h", spanKind: "review" },
    { name: "merged", ts: "Sep 6 · 11:15 AM", x: 100 },
  ],
  reviews: [
    { reviewer: "Priya Nair", initials: "PN", state: "changes", ts: "Sep 5 · 7:40 AM", comments: 8 },
    { reviewer: "Sarah Jones", initials: "SJ", state: "commented", ts: "Sep 5 · 9:15 AM", comments: 3 },
    { reviewer: "Priya Nair", initials: "PN", state: "approved", ts: "Sep 6 · 10:50 AM", comments: 0 },
  ],
  reviewRounds: 2, reviewComments: 11, unresolved: 0, reopens: 0, ci: "passing",
  models: [
    { name: "sonnet-5", pct: 62, tier: "mid" },
    { name: "opus-4.8", pct: 26, tier: "high" },
    { name: "haiku-4.5", pct: 12, tier: "low" },
  ],
  cacheHitRatio: 0.58,
  channels: [
    { label: "Building output", usd: 82, color: "var(--primary)" },
    { label: "Rework after review", usd: 66, color: "var(--red)" },
    { label: "Re-reading context", usd: 40, color: "var(--amber)" },
    { label: "Reasoning (thinking)", usd: 20, color: "var(--green)" },
    { label: "Re-cache + input", usd: 6, color: "var(--muted-foreground)" },
  ],
  sessions: [
    { id: "s2", title: "Make the auth middleware async", who: "Marcus Chen", initials: "MC", role: "build", costUsd: 148, active: "3h 20m", when: "Sep 4" },
    { id: "s2", title: "Address review — async edge cases", who: "Marcus Chen", initials: "MC", role: "rework", costUsd: 66, active: "1h 45m", when: "Sep 5" },
  ],
  quality: "Costly rework", reworkRatio: 0.31, churnPost: 96,
  flags: [
    { label: "Two review rounds", detail: "8 comments and a changes-requested before approval", sev: "amber" },
    { label: "Heavy post-open churn", detail: "96 lines changed after the PR opened — 31% of the diff was rework", sev: "red" },
    { label: "Under-modeled build", detail: "Sonnet looped on an async edge case; Opus would have been cheaper overall", sev: "amber" },
  ],
};

export const prsById: Record<string, PRDetail> = { "502": prDetail };

// Recent PRs shown on a person's page (shared list for the mockup).
export const recentPRs = [
  { id: "502", n: "#502", title: "Make the auth middleware async", repo: "auth-service", status: "merged", cost: 214, date: "Sep 6" },
  { id: "418", n: "#418", title: "Add token-bucket rate limiter", repo: "api-gateway", status: "merged", cost: 67, date: "Sep 5" },
  { id: "495", n: "#495", title: "Fix session cookie expiry edge case", repo: "auth-service", status: "merged", cost: 42, date: "Sep 3" },
  { id: "487", n: "#487", title: "Move the token store to Redis", repo: "auth-service", status: "reopened", cost: 128, date: "Sep 1" },
  { id: "480", n: "#480", title: "Add rate-limit metrics + dashboard", repo: "api-gateway", status: "open", cost: 31, date: "Aug 30" },
  { id: "472", n: "#472", title: "Document the gateway config", repo: "docs-site", status: "merged", cost: 12, date: "Aug 28" },
  { id: "465", n: "#465", title: "Retry transient upstream failures", repo: "api-gateway", status: "merged", cost: 58, date: "Aug 26" },
];

// ─────────────────────────────────────────────────────────────────
// Issue / feature deep-dive (2026-09-07) — the cumulative view
// ─────────────────────────────────────────────────────────────────
export type FeatureDetail = {
  issueKey: string; title: string; epic: string; repo: string; labels: string[]; status: "in-progress" | "shipped";
  costUsd: number; tokens: number; reasoningTokens: number; activeLabel: string; calendarSpan: string;
  sessions: number; reviewRounds: number; reopens: number; reverts: number; reworkRatio: number;
  quality: "Clean" | "Heavy lift" | "Quick & dirty" | "Costly rework";
  channels: { label: string; usd: number; color: string }[];
  prs: { id: string; n: string; title: string; status: string; cost: number; when: string }[];
  contributors: { name: string; initials: string; cost: number; prs: number }[];
  lifecycle: { name: string; ts: string | null; x: number; spanLabel?: string; spanKind?: "build" | "review" | "rework" }[];
  costToFinish: { openPRs: number; low: number; mid: number; high: number; basis: string } | null;
  benchmark: { costPct: number; timePct: number };
};

export const featureDetail: FeatureDetail = {
  issueKey: "PLAT-214", title: "Async auth middleware overhaul",
  epic: "PLAT-200 · Auth performance", repo: "auth-service", labels: ["refactor", "auth", "perf"],
  status: "in-progress",
  costUsd: 512, tokens: 14_800_000, reasoningTokens: 3_620_000, activeLabel: "11h 40m", calendarSpan: "8 days",
  sessions: 18, reviewRounds: 6, reopens: 1, reverts: 0, reworkRatio: 0.29, quality: "Costly rework",
  channels: [
    { label: "Building output", usd: 214, color: "var(--primary)" },
    { label: "Rework after review", usd: 158, color: "var(--red)" },
    { label: "Re-reading context", usd: 96, color: "var(--amber)" },
    { label: "Reasoning + other", usd: 44, color: "var(--muted-foreground)" },
  ],
  prs: [
    { id: "512", n: "#512", title: "Connection pool tuning", status: "open", cost: 97, when: "in progress" },
    { id: "502", n: "#502", title: "Make the auth middleware async", status: "merged", cost: 214, when: "Sep 6" },
    { id: "495", n: "#495", title: "Fix session cookie expiry edge case", status: "merged", cost: 42, when: "Sep 3" },
    { id: "487", n: "#487", title: "Move the token store to Redis", status: "reopened", cost: 128, when: "Sep 1" },
    { id: "480", n: "#480", title: "Add rate-limit metrics", status: "open", cost: 31, when: "Aug 30" },
  ],
  contributors: [
    { name: "Marcus Chen", initials: "MC", cost: 288, prs: 3 },
    { name: "Priya Nair", initials: "PN", cost: 142, prs: 1 },
    { name: "Tom Wright", initials: "TW", cost: 82, prs: 1 },
  ],
  lifecycle: [
    { name: "first work", ts: "Aug 30", x: 0, spanKind: "build" },
    { name: "#487 merged", ts: "Sep 1", x: 24, spanKind: "build" },
    { name: "#495 merged", ts: "Sep 3", x: 44, spanKind: "build" },
    { name: "#502 merged", ts: "Sep 6", x: 74, spanKind: "rework" },
    { name: "2 PRs open", ts: null, x: 100 },
  ],
  costToFinish: { openPRs: 2, low: 120, mid: 180, high: 260, basis: "2 open PRs × the recent $/PR envelope (median $61, p75 $92)" },
  benchmark: { costPct: 78, timePct: 64 },
};

// ─────────────────────────────────────────────────────────────────
// Timeframe + overview tables (2026-09-08)
// ─────────────────────────────────────────────────────────────────
// Per-team extras the overview table needs (avg time-to-produce, rework, last active).
export const teamMeta: Record<string, { avgTimeToPR: string; reworkRate: number; lastActive: string }> = {
  platform: { avgTimeToPR: "2.4d", reworkRate: 0.24, lastActive: "2h ago" },
  growth: { avgTimeToPR: "1.6d", reworkRate: 0.12, lastActive: "5h ago" },
  data: { avgTimeToPR: "3.1d", reworkRate: 0.22, lastActive: "1d ago" },
  infra: { avgTimeToPR: "2.0d", reworkRate: 0.18, lastActive: "3h ago" },
  mobile: { avgTimeToPR: "4.2d", reworkRate: 0.31, lastActive: "2d ago" },
};

export const projectMeta: Record<string, { avgTimeToPR: string; reworkRate: number; lastActive: string; repo: string }> = {
  "api-gateway": { avgTimeToPR: "2.2d", reworkRate: 0.19, lastActive: "2h ago", repo: "api-gateway" },
  "auth-service": { avgTimeToPR: "3.4d", reworkRate: 0.29, lastActive: "4h ago", repo: "auth-service" },
  "growth-web": { avgTimeToPR: "1.8d", reworkRate: 0.14, lastActive: "1d ago", repo: "growth-web" },
};

// ─────────────────────────────────────────────────────────────────
// Unit-economics scope (2026-09-09) — Org / Team / Project
// ─────────────────────────────────────────────────────────────────
export type EconScopeOpt = { id: string; label: string; group: "" | "Teams" | "Projects" };
export const econScopes: EconScopeOpt[] = [
  { id: "org", label: "Entire org", group: "" },
  ...teams.map((t) => ({ id: t.id, label: t.name, group: "Teams" as const })),
  ...projects.map((p) => ({ id: p.id, label: p.name, group: "Projects" as const })),
];

const ECON_MULT: Record<string, number> = {
  org: 1, platform: 1.35, growth: 0.78, data: 1.05, infra: 0.95, mobile: 1.5,
  "api-gateway": 0.9, "auth-service": 1.45, "growth-web": 0.7,
};

export function econFor(scopeId: string) {
  const m = ECON_MULT[scopeId] ?? 1;
  const tm = 1 + (m - 1) * 0.5; // time skews less than cost
  const sdInt = (d: Dist, k: number): Dist => ({ avg: Math.round(d.avg * k), min: Math.round(d.min * k), p50: Math.round(d.p50 * k), p70: Math.round(d.p70 * k), p95: Math.round(d.p95 * k), max: Math.round(d.max * k) });
  const sd1 = (d: Dist, k: number): Dist => ({ avg: +(d.avg * k).toFixed(1), min: +(d.min * k).toFixed(1), p50: +(d.p50 * k).toFixed(1), p70: +(d.p70 * k).toFixed(1), p95: +(d.p95 * k).toFixed(1), max: +(d.max * k).toFixed(1) });
  const cost = sdInt(prCostStats, m);
  const time = sd1(prTimeStats, tm);
  const envelope = prCostEnvelope.map((p) => {
    const src = p as Record<string, number | string | boolean | null | undefined>;
    const o: Record<string, number | string | boolean | undefined> = { label: p.label };
    for (const key of ["bandLo", "band", "median", "p90", "p99", "outlier", "fBandLo", "fBand", "fMedian"]) {
      const v = src[key];
      if (typeof v === "number") o[key] = +(v * m).toFixed(1);
    }
    if (p.forecast) o.forecast = true;
    return o;
  });
  return {
    cost, time,
    merged: Math.round(MERGED_PR_COST * m),
    reverted: Math.round(REVERTED_PR_COST * m),
    rework: Math.min(0.6, +(REWORK_RATIO * (m > 1 ? 1.15 : 0.85)).toFixed(2)),
    envelope,
    avgTimeLabel: `${time.avg}d`,
  };
}

// ─────────────────────────────────────────────────────────────────────────────────────────
// Forecast fixture (issue #85, T7): what `GET /v1/forecast` and `GET /v1/forecast/plan` return,
// shaped exactly like the generated `ForecastView` / `BudgetPlan` (it is typed against them, so
// `npm run typecheck` is the conformance check). A small deterministic generator: no clock, no
// randomness, so every number is reproducible. The run is fixed in time (data through
// 2026-10-08, origin 2026-09-30, October and November), like the real batch run.
//
// Mock-only simplifications: budgets are static (a Configure edit does not move the mock
// forecast, unlike the API, which reads the budget per request); the quantile bands are a fixed
// widening of the cumulative mean, not a model. The org and the five mock teams have a run; two
// projects too, so the planner lists them; `growth-web` is deliberately NOT in the run, so the
// Economics screen exercises the "no forecast for this scope" fallback in mock mode as well.
// ─────────────────────────────────────────────────────────────────────────────────────────

type FcPreset = Schemas["ForecastPreset"];
type FcPoint = Schemas["ForecastDailyPoint"];
type FcPeriod = Schemas["ForecastPeriod"];

const FC_ORIGIN = "2026-09-30";
const FC_DATA_THROUGH = "2026-10-08";
const FC_PERIODS = [
  { start: "2026-10-01", end: "2026-10-31", from: "2026-10-09" },
  { start: "2026-11-01", end: "2026-11-30", from: "2026-11-01" },
] as const;
const FC_GRID_LOW = 10;
const FC_GRID_HIGH = 90;
const FC_PLANNING_CONFIDENCE = 0.8;
const FC_PRESETS: (FcPreset & { factor: number })[] = [
  { id: "baseline", label: "Current trajectory", group: null, factor: 1,
    description: "No change: the recent pattern continues.", assumptions: [] },
  { id: "opus-to-sonnet-30", label: "Move 30% of Opus work to Sonnet", group: "Model mix", factor: 0.9,
    description: "30% of the Opus work runs on Sonnet from the day after the data ends.", assumptions: ["model_transfer"] },
  { id: "opus-to-sonnet-60", label: "Move 60% of Opus work to Sonnet", group: "Model mix", factor: 0.8,
    description: "60% of the Opus work runs on Sonnet from the day after the data ends.", assumptions: ["model_transfer"] },
  { id: "sonnet-to-haiku-30", label: "Move 30% of Sonnet work to Haiku", group: "Model mix", factor: 0.97,
    description: "30% of the Sonnet work runs on Haiku from the day after the data ends.", assumptions: ["model_transfer"] },
  { id: "usage-up-20", label: "Usage grows 20%", group: "Usage", factor: 1.2,
    description: "Token volume is 20% higher from the day after the data ends.", assumptions: ["growth"] },
  { id: "usage-down-15", label: "Usage drops 15%", group: "Usage", factor: 0.85,
    description: "Token volume is 15% lower from the day after the data ends.", assumptions: ["growth"] },
  { id: "usage-up-20-opus-to-sonnet-50", label: "Grow 20%, move half of Opus to Sonnet", group: "Combined", factor: 1.08,
    description: "Token volume grows 20% and half of the Opus work runs on Sonnet.", assumptions: ["growth", "model_transfer"] },
];

/** The scopes with a run: `level` is USD per weekday; a null budget is a scope without one.
 * Chosen so the sample covers every runway state: platform runs out, growth lasts, data and
 * infra are at risk, mobile has already used its budget up, the projects have no budget. */
const FC_SCOPES: Record<string, { type: "org" | "team" | "repo"; name: string; level: number; budgetUsd: number | null }> = {
  [`org:${ORG_SCOPE_ID}`]: { type: "org", name: "Entire org", level: 855, budgetUsd: ORG_BUDGET },
  "team:platform": { type: "team", name: "Platform", level: 190, budgetUsd: 4_000 },
  "team:growth": { type: "team", name: "Growth", level: 105, budgetUsd: 3_000 },
  "team:data": { type: "team", name: "Data", level: 100, budgetUsd: 2_500 },
  "team:infra": { type: "team", name: "Infrastructure", level: 150, budgetUsd: 3_500 },
  "team:mobile": { type: "team", name: "Mobile", level: 310, budgetUsd: 2_000 },
  "repo:api-gateway": { type: "repo", name: "api-gateway", level: 60, budgetUsd: null },
  "repo:auth-service": { type: "repo", name: "auth-service", level: 45, budgetUsd: null },
};
/** One cost run the fixture marks `incomplete` (no numbers): exercises the panel's note. */
const FC_INCOMPLETE = new Set(["team:platform|sonnet-to-haiku-30"]);

/** Weekdays carry the level, weekends 30% of it. Date parts only, no local-time parsing. */
function fcDayFactor(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return dow === 0 || dow === 6 ? 0.3 : 1;
}

/** Piecewise-linear CDF through (value, probability) knots. */
function fcCdf(knots: [number, number][], x: number): number {
  if (x <= knots[0][0]) return 0;
  for (let i = 1; i < knots.length; i++) {
    const [x1, p1] = knots[i];
    const [x0, p0] = knots[i - 1];
    if (x <= x1) return p0 + ((p1 - p0) * (x - x0)) / (x1 - x0);
  }
  return 1;
}

/** The period total's quantile knots (10th / 50th / 90th, stretched to 0 and 1). */
function fcKnots(p: { actual: number; expected: number; low: number; high: number }): [number, number][] {
  const p50 = p.actual + Math.round((p.expected - p.actual) * 0.97);
  return [[p.low - (p50 - p.low) / 2, 0], [p.low, 0.1], [p50, 0.5], [p.high, 0.9], [p.high + (p.high - p50) / 2, 1]];
}

function fcActualDaily(level: number): Schemas["ActualDailyPoint"][] {
  const first = addDays(FC_PERIODS[0].start, -28);
  return eachDay(first, FC_DATA_THROUGH).map((date, i) => ({
    date,
    usd_micros: Math.round(level * 1_000_000 * 1.04 * fcDayFactor(date) * (1 + (((i * 7) % 5) - 2) * 0.03)),
  }));
}

function fcPeriod(
  scopeLevel: number, budget: number | null, factor: number, pi: number,
  actualDaily: Schemas["ActualDailyPoint"][], baselineExpected: number | null,
): FcPeriod {
  const per = FC_PERIODS[pi];
  const current = per.from > per.start;
  const actual = current
    ? actualDaily.filter((a) => a.date >= per.start && a.date < per.from).reduce((s, a) => s + a.usd_micros, 0)
    : 0;
  let cum = 0;
  const projected: FcPoint[] = eachDay(per.from, per.end).map((date, k) => {
    cum += Math.round(scopeLevel * 1_000_000 * factor * fcDayFactor(date));
    const h = 0.2 * (1 - 1 / (k + 2));
    return {
      date, expected_usd_micros: actual + cum,
      low_usd_micros: actual + Math.round(cum * (1 - h)), high_usd_micros: actual + Math.round(cum * (1 + h)),
    };
  });
  const last = projected[projected.length - 1];
  const rowKnots = (r: FcPoint) => fcKnots({ actual, expected: r.expected_usd_micros, low: r.low_usd_micros, high: r.high_usd_micros });
  const landingKnots = rowKnots(last);
  const landing = {
    expected_usd_micros: last.expected_usd_micros, p10_usd_micros: last.low_usd_micros,
    p50_usd_micros: Math.round(landingKnots[2][0]), p90_usd_micros: last.high_usd_micros,
  };
  const within = budget == null ? null : budget - actual <= 0 ? 0 : fcCdf(landingKnots, budget);

  let runway: FcPeriod["runway"] = null;
  if (budget != null && current) {
    if (actual >= budget) {
      let running = 0;
      const crossed = actualDaily.filter((a) => a.date >= per.start).find((a) => (running += a.usd_micros) >= budget)?.date ?? per.start;
      runway = {
        status: "exhausted", runs_out_probability: 1,
        runs_out_on: { p10: crossed, p50: crossed, p90: crossed }, days_short: eachDay(crossed, per.end).length - 1,
      };
    } else {
      const pOut = 1 - (within ?? 0);
      const outBy = projected.map((r) => 1 - fcCdf(rowKnots(r), budget));
      const dateAt = (q: number) => projected.find((_, k) => outBy[k] >= q * pOut)?.date ?? last.date;
      const on = pOut >= 0.05
        ? { p10: dateAt(0.1), p50: dateAt(0.5), p90: dateAt(0.9) }
        : { p10: null, p50: null, p90: null };
      runway = {
        status: pOut < 0.2 ? "lasts" : pOut > 0.8 ? "runs_out" : "at_risk",
        runs_out_probability: pOut, runs_out_on: on,
        days_short: on.p50 ? eachDay(on.p50, per.end).length - 1 : null,
      };
    }
  }
  return {
    start: per.start, end: per.end, forecast_from: per.from, actual_to_date_usd_micros: actual,
    projected, landing, within_budget_probability: within,
    delta_vs_baseline_usd_micros: baselineExpected == null ? 0 : landing.expected_usd_micros - baselineExpected,
    runway,
  };
}

function fcSeries(key: string, level: number, budget: number | null, actualDaily: Schemas["ActualDailyPoint"][]): Schemas["ForecastSeries"][] {
  const runDates = eachDay(FC_PERIODS[0].start, FC_PERIODS[FC_PERIODS.length - 1].end);
  const baseline = FC_PRESETS[0];
  const baseLandings = FC_PERIODS.map((_, pi) =>
    fcPeriod(level, budget, baseline.factor, pi, actualDaily, null).landing.expected_usd_micros);
  return FC_PRESETS.map((preset) => {
    if (FC_INCOMPLETE.has(`${key}|${preset.id}`)) return { preset_id: preset.id, status: "incomplete", daily: [], periods: [] };
    return {
      preset_id: preset.id, status: "complete",
      daily: runDates.map((date) => {
        const expected = Math.round(level * 1_000_000 * preset.factor * fcDayFactor(date));
        return { date, expected_usd_micros: expected, low_usd_micros: Math.round(expected * 0.7), high_usd_micros: Math.round(expected * 1.3) };
      }),
      periods: FC_PERIODS.map((_, pi) =>
        fcPeriod(level, budget, preset.factor, pi, actualDaily, preset.id === baseline.id ? null : baseLandings[pi])),
    };
  });
}

const fcPresetView = (): FcPreset[] =>
  FC_PRESETS.map(({ id, label, group, description, assumptions }) => ({ id, label, group, description, assumptions }));

/** `GET /v1/forecast` for a scope, or `null` when the scope is not in the mock run. */
export function forecastFixture(scopeType: string, scopeId: string): Schemas["ForecastView"] | null {
  const key = `${scopeType}:${scopeId}`;
  const sc = FC_SCOPES[key];
  if (!sc) return null;
  const budget = sc.budgetUsd == null ? null : sc.budgetUsd * 1_000_000;
  const actualDaily = fcActualDaily(sc.level);
  return {
    origin: FC_ORIGIN, data_through: FC_DATA_THROUGH, timezone: "UTC",
    band_low: FC_GRID_LOW, band_high: FC_GRID_HIGH, budget_usd_micros: budget,
    presets: fcPresetView(), actual_daily: actualDaily, series: fcSeries(key, sc.level, budget, actualDaily),
  };
}

const FC_MODELS = [
  { model: "opus-4.8", share: 0.62, usdPerMillion: 18 },
  { model: "sonnet-5", share: 0.28, usdPerMillion: 5 },
  { model: "haiku-4.5", share: 0.1, usdPerMillion: 1.2 },
];

/** `GET /v1/forecast/plan`: every team then project of the mock run, with the planning figure
 * at the 80th percentile. Tokens cover the forecast days only (as in the API). */
export function planFixture(): Schemas["BudgetPlan"] {
  const rows = Object.entries(FC_SCOPES)
    .filter(([, sc]) => sc.type !== "org")
    .map(([key, sc]): Schemas["PlanRow"] => {
      const budget = sc.budgetUsd == null ? null : sc.budgetUsd * 1_000_000;
      const actualDaily = fcActualDaily(sc.level);
      return {
        scope_type: sc.type, scope_id: key.slice(key.indexOf(":") + 1), name: sc.name, budget_usd_micros: budget,
        by_preset: fcSeries(key, sc.level, budget, actualDaily).map((s) => ({
          preset_id: s.preset_id,
          periods: s.periods.map((p): Schemas["PlanPeriodFigures"] => {
            const forecastCost = p.landing.expected_usd_micros - p.actual_to_date_usd_micros;
            const p80 = Math.round(p.landing.p50_usd_micros + 0.75 * (p.landing.p90_usd_micros - p.landing.p50_usd_micros));
            return {
              needed: {
                expected_usd_micros: p.landing.expected_usd_micros, p50_usd_micros: p.landing.p50_usd_micros,
                p80_usd_micros: p80, p90_usd_micros: p.landing.p90_usd_micros,
              },
              plan_for_usd_micros: p80,
              gap_usd_micros: budget == null ? null : p80 - budget,
              tokens_by_model: FC_MODELS.map((m) => {
                const expected = Math.round((forecastCost * m.share) / m.usdPerMillion);
                return { model: m.model, expected, p90: Math.round(expected * 1.25) };
              }),
            };
          }),
        })),
      };
    });
  return {
    planning_confidence: FC_PLANNING_CONFIDENCE,
    periods: FC_PERIODS.map((p) => ({ start: p.start, end: p.end, forecast_from: p.from, current: p.from > p.start })),
    presets: fcPresetView(), rows,
  };
}
