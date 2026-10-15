/**
 * Tone lexicon gate (MVP plan §1 / U2 / U8; `AGENTS.md` "binding conventions").
 *
 * Surfaced strings say **recoverable / could save / room to save**, never "waste", and never
 * rank a person. Work-quality labels are exactly the four in `QUALITY_LABELS`.
 *
 * `scanLexicon` is the unit-testable core; U8 wires it into a CI gate over all front-end
 * strings (and the backend runs its own over API-produced strings).
 */
export const QUALITY_LABELS = ["Clean", "Heavy lift", "Quick & dirty", "Costly rework"] as const;
export type QualityLabel = (typeof QUALITY_LABELS)[number];

/** Words that must never appear in a user-facing string. */
export const BANNED = [
  "waste", "wasted", "wasteful", "wasting",
  "burn rate", "burning money",
  "lazy", "sloppy", "incompetent",
] as const;

/** Phrases that read as a personal stack-rank (efficiency note §5.5) — flagged, not hard-banned. */
export const RANKING_SMELL = [
  "worst performer", "bottom of the", "ranked last", "least productive",
] as const;

export type LexiconHit = { term: string; kind: "banned" | "ranking"; index: number };

export function scanLexicon(text: string): LexiconHit[] {
  const hay = text.toLowerCase();
  const hits: LexiconHit[] = [];
  for (const term of BANNED) {
    const i = hay.indexOf(term);
    if (i !== -1) hits.push({ term, kind: "banned", index: i });
  }
  for (const term of RANKING_SMELL) {
    const i = hay.indexOf(term);
    if (i !== -1) hits.push({ term, kind: "ranking", index: i });
  }
  return hits;
}

/** Throws on any banned term — use in dev/test around formatter output. */
export function assertLexicon(text: string): string {
  const banned = scanLexicon(text).filter((h) => h.kind === "banned");
  if (banned.length) {
    throw new Error(`lexicon violation: "${banned.map((h) => h.term).join('", "')}" in ${JSON.stringify(text)}`);
  }
  return text;
}

export function isQualityLabel(s: string): s is QualityLabel {
  return (QUALITY_LABELS as readonly string[]).includes(s);
}
