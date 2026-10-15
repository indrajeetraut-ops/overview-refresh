/**
 * Category → colour mapping (MVP plan U2 / BLUE §7). The API sends a **category or label**,
 * never a `#hex` or `var(--…)`. The client owns the mapping to a hue, pinned here so every
 * screen renders the same category the same colour.
 *
 * Values are CSS custom-property names defined in `index.css` (light + dark). Use via
 * `style={{ color: hue }}` / `fill={hue}` — Recharts accepts `var(--x)` in `fill`/`stroke`.
 */
export type Hue = `var(--${string})`;

const H = {
  primary: "var(--primary)" as Hue,
  green: "var(--green)" as Hue,
  amber: "var(--amber)" as Hue,
  red: "var(--red)" as Hue,
  blue: "var(--blue)" as Hue,
  muted: "var(--muted-foreground)" as Hue,
};

/** Work quality 2×2 — the locked vocabulary (never re-map these strings). */
export const QUALITY_HUE: Record<string, Hue> = {
  Clean: H.green,
  "Heavy lift": H.blue,
  "Quick & dirty": H.amber,
  "Costly rework": H.red,
};

/** Cost channels on the Session/PR/Feature deep-dives. Keyed by the API's channel `category`. */
export const CHANNEL_HUE: Record<string, Hue> = {
  output: H.primary,          // "Building output"
  rework: H.red,              // "Rework loops" / "Rework after review"
  context: H.amber,           // "Re-reading context"
  reasoning: H.green,         // "Reasoning (thinking)"
  recache: H.blue,            // "Re-cache"
  input: H.muted,             // "Input" / "Re-cache + input"
};

/** Session-type distribution (Usage). Keyed by the API's raw segment label
 * (`worker/segments/session_type.py`); `sessionTypeName` gives the display name. */
export const SESSION_TYPE_HUE: Record<string, Hue> = {
  IMPLEMENT: H.primary,
  BUG_FIX: H.amber,
  REVIEW: H.green,
  COMPREHEND: "var(--teal)",
  PLAN: "var(--violet)",
  DESIGN: "var(--rose)",
  OPS: "var(--slate)",
  MISC: H.muted,
};

const SESSION_TYPE_NAME: Record<string, string> = {
  IMPLEMENT: "Develop",
  BUG_FIX: "Fix",
  REVIEW: "Review",
  COMPREHEND: "Research",
  PLAN: "Plan",
  DESIGN: "Design",
  OPS: "Ops",
  MISC: "Misc",
};

/** Raw segment/session label (`IMPLEMENT`) → display name (`Develop`); unknown labels pass through. */
export function sessionTypeName(label: string | null | undefined): string {
  if (!label) return "—";
  return SESSION_TYPE_NAME[label] ?? label;
}

/** Raw agent ids as the API sends them (`agent_spend[].agent`, `agent_task_mix[].agent`), normalised
 * to lower snake case, to the agent's display name. */
const AGENT_NAME = new Map<string, string>([
  ["claude_code", "Claude Code"],
  ["codex", "Codex"],
  ["cursor", "Cursor"],
]);

/**
 * An agent id or display name -> its display name. The API sends raw ids (`claude_code`), the mock
 * sends display names ("Claude Code"); both land on the same name, so the donut, the list, the
 * task-usage tabs and the colours agree on either source. A display name passes through
 * unchanged; an unknown id is title-cased word by word on `_`, `-` and spaces (existing capitals
 * are kept: `GPT_5` -> "GPT 5"). Nothing or blank -> "Unknown".
 */
export function agentName(idOrName: string | null | undefined): string {
  const raw = (idOrName ?? "").trim();
  if (!raw) return "Unknown";
  const known = AGENT_NAME.get(raw.toLowerCase().replace(/[\s-]+/g, "_"));
  if (known) return known;
  const words = raw.split(/[_\-\s]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1));
  return words.length > 0 ? words.join(" ") : "Unknown";
}

/** Spend-by-agent colours, keyed by agent DISPLAY name (`agentName`). Any other agent gets one of
 * `UNKNOWN_AGENT_RAMP` (see `assignAgentHues`). */
export const AGENT_HUE: Record<string, Hue> = {
  "Claude Code": H.primary,
  Codex: H.green,
  Cursor: H.amber,
};

/** The colours an agent with no pinned one can take. All are categorical hues that differ from
 * the pinned agents' (primary, green, amber) and from red (a status colour), in light and dark
 * (`palette.test.ts` checks the CSS). `--blue` is deliberately absent: it IS `--primary`. */
export const UNKNOWN_AGENT_RAMP: Hue[] = [
  "var(--teal)", "var(--violet)", "var(--rose)", "var(--slate)", "var(--olive)", "var(--brown)",
];

function nameHash(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + (ch.codePointAt(0) ?? 0)) >>> 0;
  return h;
}

/** An unknown agent's colour from its name alone: stable, but two names can collide. */
function hashedAgentHue(name: string): Hue {
  return UNKNOWN_AGENT_RAMP[nameHash(name) % UNKNOWN_AGENT_RAMP.length];
}

/**
 * The colour of every agent in one displayed set (the donut, its list, the task-usage tabs and
 * bars share one set: pass every name they show). Pinned agents keep theirs. The others start at
 * their name's hash slot, in sorted order, and probe forward to the next free slot, so no two
 * agents in the set share a colour while the set has no more unknown agents than the ramp has
 * colours (beyond that the extra ones reuse hash slots). The result depends only on the SET of
 * names, never on the order they are listed in.
 */
export function assignAgentHues(names: readonly string[]): Record<string, Hue> {
  const out: Record<string, Hue> = {};
  const used = new Set<Hue>();
  const unknown: string[] = [];
  for (const raw of new Set(names.map(agentName))) {
    if (AGENT_HUE[raw]) { out[raw] = AGENT_HUE[raw]; used.add(AGENT_HUE[raw]); } else unknown.push(raw);
  }
  unknown.sort();
  const n = UNKNOWN_AGENT_RAMP.length;
  for (const name of unknown) {
    const start = nameHash(name) % n;
    let pick = hashedAgentHue(name);
    for (let k = 0; k < n; k++) {
      const hue = UNKNOWN_AGENT_RAMP[(start + k) % n];
      if (!used.has(hue)) { pick = hue; break; }
    }
    out[name] = pick;
    used.add(pick);
  }
  return out;
}

/** Recoverable-spend habit category (efficiency note §2: `resume` | `cache_bust` | `interrupted`). */
export const HABIT_HUE: Record<string, Hue> = {
  resume: H.amber,
  cache_bust: H.blue,
  interrupted: H.primary,
};

/** A recoverable-spend bucket key (`dominant_cause`, habit `category`) as a short lowercase phrase for
 * inline copy like "mostly …". The API sends the key; wording is the presentation layer's (U2). */
export const RECOVERABLE_CAUSE_PHRASE: Record<string, string> = {
  resume: "resuming after pauses",
  cache_bust: "cache resets",
  interrupted: "interrupted replies",
};

/** Model-fit verdict / segment fit. */
export const FIT_HUE: Record<string, Hue> = {
  good: H.green,
  over: H.amber,
  under: H.red,
};

/** Severity of a worth-a-look finding / flag. */
export const SEVERITY_HUE: Record<string, Hue> = {
  green: H.green,
  amber: H.amber,
  red: H.red,
};

/** Model tier (`aggregate.model_tier`'s `high`/`mid`/`low`) — the "Models used" token-share
 * bars on Session/PR (QW1, mvp-gap-closure-plan). The API sends the tier name, never a hue,
 * per strip-rule 1 — same treatment every other category table on this page already gets. */
export const MODEL_TIER_HUE: Record<string, Hue> = {
  high: H.primary,
  mid: H.green,
  low: H.muted,
};

/** `ChannelTotals` (D1/D2/D3's raw `{carry,recache,output,input}_usd_micros`, no label/color
 * on the wire per schemas/README's strip-rule) -> a chart-ready, non-zero-only series. `carry`
 * displays as "Re-reading context" and takes the `context` hue -- the same bucket X3's own
 * recoverable-spend waterfall names it. */
export function channelTotalsToSeries(c: {
  carry_usd_micros: number; recache_usd_micros: number; output_usd_micros: number; input_usd_micros: number;
}): { name: string; value: number; fill: Hue }[] {
  const entries: { hueKey: string; label: string; usdMicros: number }[] = [
    { hueKey: "output", label: "Building output", usdMicros: c.output_usd_micros },
    { hueKey: "context", label: "Re-reading context", usdMicros: c.carry_usd_micros },
    { hueKey: "recache", label: "Re-cache", usdMicros: c.recache_usd_micros },
    { hueKey: "input", label: "Input", usdMicros: c.input_usd_micros },
  ];
  return entries
    .filter((e) => e.usdMicros > 0)
    .map((e) => ({ name: e.label, value: e.usdMicros / 1_000_000, fill: categoryHue("channel", e.hueKey) }));
}

const NEUTRAL_RAMP: Hue[] = [H.primary, H.green, H.amber, H.blue, H.red, H.muted];

export type HueKind =
  | "quality" | "channel" | "sessionType" | "agent" | "habit" | "fit" | "severity" | "modelTier";

const TABLE: Record<HueKind, Record<string, Hue>> = {
  quality: QUALITY_HUE,
  channel: CHANNEL_HUE,
  sessionType: SESSION_TYPE_HUE,
  agent: AGENT_HUE,
  habit: HABIT_HUE,
  fit: FIT_HUE,
  severity: SEVERITY_HUE,
  modelTier: MODEL_TIER_HUE,
};

/**
 * Resolve a category to its hue. `index` (position in the series) seeds the neutral ramp for
 * labels with no pinned colour, so an unknown agent/channel still gets a stable, distinct hue.
 */
export function categoryHue(kind: HueKind, label: string, index = 0): Hue {
  if (kind === "agent") {
    const name = agentName(label);
    return AGENT_HUE[name] ?? hashedAgentHue(name);
  }
  return TABLE[kind][label] ?? NEUTRAL_RAMP[index % NEUTRAL_RAMP.length];
}
