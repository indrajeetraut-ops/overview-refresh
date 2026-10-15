# Session Storytelling — narrative layer for the session deep dive

*2026-08-01, revised 2026-08-06 (§3.6, §6, §8 — datasource modes). Extends `user-dashboard-design.md` §4. Goal: a viewer should read a session the way they read a face — costly-and-turbulent vs quick-and-clean should be **felt in under five seconds**, before a single number is parsed. Constraint (explicit, from product): never be judgmental. "Waste" is an inference; inferences can be wrong; developers who feel graded will kill adoption (see review doc's dev-trust posture and Onro's "never a manager spy tool" positioning — this is the terrain where tools die).*

> **Implementation note (2026-09-01).** The story curve/shape and the session **segments/beats** are now
> computed graph-natively by the extraction worker: `server/worker/graph_fold.py` (`story_of`) and the
> vendored beat-detection + Tier-0 classification library `server/worker/segments/`, driven by the
> causal-graph turn model. Both are behavior-preserving ports of the retired event-fold; because they run
> over graph turns, segment boundaries/labels are **graph-native** (accepted as such, not byte-matched to
> the old fold — see the graph-integration §4.B port). This doc owns the *what/why*; the code owns the *how*.

---

## 1. The mechanism: show, compare, never grade

Three rules produce the intuition without the judgment:

1. **Juxtaposition does the telling.** A story card places *what came out* (files, tests green, PR) directly against *what went in* (time, tokens, dollars). "Efficient" is the feeling of a short receipt next to a finished thing; "costly" is the feeling of a long receipt next to a small thing. The UI never says either word — the layout says it.
2. **The only baseline is the user's own history.** "3.4× your median session" is a fact the dev can't argue with and no one else can see. Never compare devs to each other on this surface, never to an org norm. Percentile bands come from the user's own trailing sessions.
3. **Facts are asserted; inferences are offered.** `foo.py read 14 times` is a fact — state it. "Looks like a revision loop" is an inference — label it as one, attach confidence, and make it correctable ("mark as expected"). A dismissed inference is training data for us and respect signaled to the dev.

## 2. Language system

Voice: a neutral narrator observing the session — never a coach mid-story, never a verdict. (Suggestions live in the insights section, phrased as opportunities, only after the story has shown the evidence.)

| Never in UI | Use instead | Why |
| :---- | :---- | :---- |
| waste, wasted, wasteful | *re-read content · carried context · unreturned exploration · retried calls* | names the phenomenon, not a verdict |
| inefficient / efficient (as labels) | *(nothing — the juxtaposition carries it)* | efficiency is the reader's conclusion |
| overkill, overuse | *"frontier model on N short turns"* | fact form |
| you should have… | *"sessions like this one often… — see insight"* | offer, don't scold |
| wasted $N | *"~$N potentially recoverable"* | opportunity framing |
| error-prone, bad session | *"turbulent stretch: 3 rate-limit retries"* | describes the stretch, not the person |
| grade/score (A–F, 0–100) | shape name + anchors vs own median | scores are verdicts wearing numbers |

Internal metric codes (F2 "re-read waste" etc.) never surface; the presentation layer owns the vocabulary. One more rule: **cost is ink, not alarm** — dollars and tokens render in neutral ink at all magnitudes. Red is reserved for hard failures (api_error). A big number in red says "you screwed up"; a big number in ink says "here's what it took".

## 3. Anatomy of a session story

Top to bottom on the deep-dive screen (mockup: see artifact "Session Stories — Concept"):

1. **Signature glyph** — a sparkline of the burn curve, rendered everywhere the session appears (lists, links). Shape literacy develops in a day: a flat low arc reads calm, a cliff reads expensive, a sawtooth reads compaction-cycling — before any label.
2. **Narrator line** — one deterministic sentence from the beat sequence (§5): *"Began as a survey of the payments module; became a long build with two context squeezes and a rate-limit stall; ended with tests green."* Template-generated from beats — no LLM required, no content shipped.
3. **The burn chart** — elapsed time × tokens, two series on one axis: cumulative consumption (area; the money story — its slope is burn rate) and live context size (thin line; the health story — sawtooth = compaction breathing). Behind both, a **quiet gray band: the user's own p25–p75 envelope** at that elapsed time. Inside the band nothing is said; a curve leaving the band needs no caption.
4. **Beat markers on the curve** (§4) — hoverable, each opening a plain-fact tooltip.
5. **Acts** — beats grouped into 2–5 chapters with durations and per-act cost; the readable table-of-contents of the session.
6. **The receipt** — two columns. *Produced:* files ±lines, checks run and final status, PRs/commits, subagent findings returned. *Consumed:* wall time, turns, tokens by origin (provenance mini-stack), model mix, $. Per-unit line at the bottom (`$/file changed`, `tokens/line`) in muted ink — facts, no adjectives.

   The dollar is `cost_effective_usd_micros` and carries its basis as a caption — *vendor-reported* or *priced from tokens* — in the same neutral ink at the same weight, never tinted (tinting an estimate makes "we priced this ourselves" read as a warning, and §2's cost-is-ink rule outranks the wish to differentiate). Where `cost_complete` is false the receipt says the total is a floor, in words. The Consumed column also carries background utility calls (provenance category K) as their own line when OTel is present — they are part of what the session cost, and hiding them in a residual would be the kind of quiet rounding this whole doc exists to avoid.
7. **Anchors** — up to three one-liners against personal history: *"3.4× your median session · longest since July 12 · first session with 5 subagents."* Superlatives are engaging and self-referential — never normative.

## 4. Beat taxonomy

Detected from the existing timeline events (dashboard design §8); every beat = `{kind, t0, t1, evidence, confidence}`. Names are deliberately physical/neutral — no success/failure connotation.

| Beat | Detection | Fact shown | Kind |
| :---- | :---- | :---- | :---- |
| **Setup** | turn-1 context load (provenance A+B+C) | "context loaded: 38k tokens (memory 9k, MCP 11k)" | fact |
| **Survey** | ≥N read-type calls, no file_change | "read 14 files, 210k tokens" | fact |
| **Build** | file_change cluster | "edited 4 files, +212 −80" | fact |
| **Check** | verifier Bash run (+ exit) | "pytest — 2 failed, then green" | fact |
| **Revision loop** | ≥3 same-file/overlapping-hunk edit cycles | "auth.py revised 5 times in 18 min" | **inference** (correctable) |
| **Delegation** | sub_agent_spawn → fold-back | "3 subagents, 840k tokens explored, 4k returned" | fact |
| **Context squeeze** | compaction event | "compacted: 148k → 62k" | fact |
| **Stall** | api_error retries / rate-limit cluster | "6 retries over 9 min (429)" | fact |
| **Pause** | inter-turn gap > threshold | "idle 41 min" (+ cache TTL note if decayed) | fact |
| **Interruption** | interrupt sentinel | "stopped mid-turn" | fact |
| **Wrap-up** | commit/PR tool activity near end | "committed; PR opened" | fact |

Computation is **read-path** (from `signal_blob.events` at `GET /sessions/{id}/timeline` time) so heuristics iterate without refolds; only what Level-1 lists need is rolled up (§6).

## 5. Shape archetypes (the glyph vocabulary)

Assigned from curve geometry; names are geometry, not quality:

| Shape | Geometry | What a reader learns at a glance |
| :---- | :---- | :---- |
| **Short spark** | brief, low, done | quick targeted session |
| **Steady climb** | near-linear slope | continuous work, even burn |
| **Staircase** | bursts + flats | work/pause rhythm (or batched turns) |
| **Sawtooth** | context line repeatedly drops | compaction cycling — long-haul session |
| **Cliff** | slope inflects sharply upward | something began consuming fast (fan-out, loop, big reads) |
| **Long tail** | active start, thin drawn-out end | lingering finish, idle gaps |

Cliff and sawtooth are the shapes that *invite* the click — which is exactly the storytelling goal: curiosity, not accusation.

## 6. Data additions

- `info.story = {curve: [[t, cum, ctx] × ≤64 downsampled], shape, anchors: {median_ratio, superlatives[]}}` — fold rollup; powers list glyphs + anchors without blob reads. (Personal median/p25/p75 envelope computed at query time from the user's `session_info` history; not stored per session.)
- Beats/acts/narrator line: read-path service over the timeline endpoint (§4) — new response block `story: {narrator, acts[], beats[]}` on `GET /sessions/{id}/timeline`.
- "Mark as expected" feedback: tiny table `{session_id, beat_ref, user_id, verdict, ts}` — the first labeled-data loop we own (feeds threshold tuning and, later, M-04 classification).

### 6.1 This entire layer requires JSONL (2026-08-06)

Every mechanism in this doc — the glyph, the narrator line, the burn chart, the beats, the acts, the receipt's Produced column, the personal envelope — reads `signal_blob.events` or `info.story`. An **`otel_only`** deployment has neither, so the storytelling layer **does not render at all** there: no glyphs in lists, no shape names, no S4 route. See [`dashboard-datasource-modes.md`](dashboard-datasource-modes.md) §2.

This is worth stating in the doc that owns the product's centre of gravity, because it is the sharpest single argument for adding the collector — and because the argument must be made in this doc's own voice. "Session interiors come from the collector" is a fact about a signal source. Anything that shades toward "you are missing out" is the tone failure §2 exists to prevent, aimed at a user who has done nothing wrong.

### 6.2 Two clocks

OTel spend truth lands ~1–2 min after the fact; JSONL is 10–30 s live, or days behind in batch-upload mode. So a story routinely renders while its cost is settled and its structure is still arriving (`jsonl_lag_s`, modes doc §7).

The burn chart stays **JSONL-timed**: a request ledger has no turn boundaries, and re-plotting the story against request timestamps would break every beat's alignment to earn a slightly earlier total. OTel totals annotate the chart rather than driving its axis. A lagging session shows its Consumed column settled and its Produced column filling in — stated once, quietly, as a fact about our pipeline rather than about the session.

## 7. Open questions

1. **Narrator line in lists?** Glyph + shape name may be enough at Level 1; the sentence might belong only to the deep dive. Decide from the mockup.
2. **Envelope cold-start.** New users have no median band — hide the band below N sessions rather than borrowing an org norm (rule 2 of §1 outranks completeness).
3. **How much positivity?** A "clean run" callout on efficient sessions is tempting (celebration, not judgment) — but praise implies its absence is criticism. Current stance: superlative anchors only ("fastest green checks this month"), no praise labels. Revisit after user testing.
4. **Do anchors survive a mixed-mode history?** (2026-08-06) Anchors compare a session against the user's own trailing sessions. If some of those are `otel_only` — no curve, no turns, no shape — the comparison set is silently uneven, and "3.4× your median session" would be computed against a median that skipped the sessions with no structure. Cheapest honest fix is to compute anchors only over sessions that have the dimension being compared, and to let the band's `min_sessions_for_band` gate count only those. Decide when the envelope query is written.
5. **Does a lagging session get a story at all, or a held frame?** Rendering a half-arrived timeline and letting it grow under the reader is one option; waiting until `jsonl_lag_s` clears is the other. The first is more honest, the second is less unsettling. Leaning toward the first with an explicit "still arriving" line, but this wants a look at a real batch-upload session before it is fixed.
