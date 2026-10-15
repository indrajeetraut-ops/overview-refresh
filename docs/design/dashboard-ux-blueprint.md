# Dashboard UX Blueprint — screens, navigation, what goes where

*2026-08-01, revised 2026-08-06 for datasource modes. The single navigable UX spec for the v1 (single-user) dashboard. Detail lives in the source docs — this doc is the map: [`user-dashboard-design.md`](user-dashboard-design.md) (metrics, data mapping, APIs), [`session-storytelling.md`](session-storytelling.md) (story anatomy, beats, tone), [`best-practice-insights.md`](best-practice-insights.md) (practice insights, scorecard), [`dashboard-datasource-modes.md`](dashboard-datasource-modes.md) (which deployments can show what). Visual reference: the mockups in [`mockups/`](mockups/) — [`dashboard-ux-mockup.html`](mockups/dashboard-ux-mockup.html) (all four screens, working nav, datasource-mode switcher, the P3 reference) and [`session-stories-concept.html`](mockups/session-stories-concept.html) (the S4 treatment: validated palette, type roles, receipt/burn-chart). Open them directly in a browser — no server needed. The UX mockup's CSS token system is the starting stylesheet.*

---

## 0. Datasource mode governs the whole IA

**Read [`dashboard-datasource-modes.md`](dashboard-datasource-modes.md) §2 before building any screen.** An org deploys the Talium collector (JSONL), OTel telemetry, or both, and the choice is a standing deployment fact, not a warm-up period. The three modes do not differ in polish — they differ in which screens exist:

| Mode | What this blueprint delivers |
| :---- | :---- |
| **both** | Everything below. |
| **jsonl_only** | Everything below, minus the `query_source` split; dollars are priced rather than vendor-reported. |
| **otel_only** | **Z0, Z1 and Z2 only.** No Z3–Z6, no S2 rows beyond cost and model, and **no S4 at all** — the storytelling layer has no data to read. |

Two consequences that shape every screen:

1. **The absent modes are the product story, not an apology.** What a user cannot see is an accurate statement of what a second signal source would reveal — rendered in the `unavailable` state (§6), never as a nag. The modes doc §5 owns that copy and the tone rules it must pass.
2. **Mode is resolved per session and rolled up per screen.** Every response carries `datasource`; screen-level copy states the org-wide fact once rather than repeating it down forty rows.

## 1. Information architecture

Three screens, one modal layer. Flat navigation — no nesting deeper than overview → session.

```
┌──────────────────────────────────────────────────────────────┐
│  top bar:  Talium · [Overview] [Sessions] [Insights]         │
│            timeframe selector (7d/30d/90d/custom) · theme    │
└──────────────────────────────────────────────────────────────┘
   S1 Overview          S2 Sessions            S3 Insights
   (the pulse)          (the shelf)            (the opportunities)
        │                    │                       │
        └────────────┬───────┴───────────────────────┘
                     ▼
              S4 Session Story  (deep dive — routed page, not modal:
                                 /session/{id}, linkable/shareable)
```

- The **timeframe selector is global** (top bar), persists across S1–S3, and is stamped into every API call. S4 ignores it (a session is its own timeframe).
- Every session reference anywhere (S1 flagged list, S2 rows, S3 evidence links) is a **glyph + title link** to S4. The glyph is the unit of navigation — users learn to click shapes.
- Deep-linkable routes: `/`, `/sessions`, `/insights`, `/session/{id}`. Hash-router is fine for v1.

## 2. S1 — Overview ("the pulse")

Answers in one screen: *what did my agent work cost and produce lately, where does it go, and is anything worth a look?* Zones top-to-bottom (spec: dashboard doc §3; Z0: rhythm feasibility note, `docs/design/notes/2026-08-07-s1-rhythm-feasibility.md`, **FEAS**, and its implementation plan `docs/plan/2026-08-07-s1-rhythm-implementation.md`, R0–R8):

```
┌ Z0 rhythm strip + heatmap ────────────────────────────────────┐
│ active days · longest streak · peak hour(+micro-histogram) · favorite model │
│ 52-week activity heatmap, tokens/day, outlined to the selected timeframe    │
├ Z1 headline strip ───────────────────────────────────────────┐
│ tokens · est.$ · sessions(active) · active time                │
│ + one caption line: turns · files · repos · subagents          │
├ Z2 consumption (2-col) ──────────────────────────────────────┤
│ model pie + cost bars (tokens/$ toggle) │ provenance mix stack │
│ + spend by query_source (background calls) — OTel modes only  │
├ Z3 usage patterns (one tabbed card) ──────────────────────────┤
│ [Tools] [Skills] [MCP servers] [Repos]                          │
├ Z4 areas of interest ────────────────────────────────────────┤
│ flagged-session rows: GLYPH · title · repo · badges · totals  │
├ Z5 practice scorecard ───────────────────────────────────────┤
│ 10 practices · adherence % · trend · evidence line            │
├ Z6 insight teasers (top 3 by est. recoverable $) → S3 ───────┤
└──────────────────────────────────────────────────────────────┘
```

Ordering rationale: Z0 leads because it renders in **every** mode including `otel_only` (where the screen otherwise collapses to Z1+Z2 and looks abandoned) and because it answers a different question than everything below it — *your* working pattern, not a consumption total. Facts about consumption (Z1–Z3) come next, then flags (Z4), then anything that could read as advice (Z5–Z6) — the screen leads with observation, ends with opportunity, per the tone rules (storytelling doc §2, which governs **all** screens, not just S4).

Z0 is deliberately **not** range-scoped the way Z1–Z6 are: active days, the longest streak and the heatmap answer over a fixed 52-week window (`GET /v1/users/{id}/rhythm?weeks=`), lifetime facts a 7-day toggle cannot express (FEAS §5/§6). Peak hour and favorite model are the exception — both are scoped to the selected timeframe and ride on `/overview`'s own `z0` block, since "your peak hour right now" is a better product answer than a lifetime average that can never move. The heatmap's own timeframe awareness is visual only: the selected range is outlined into the grid, and the outline moves on a timeframe change without a second `/rhythm` fetch (the frontend caches that response by user, not by timeframe — FEAS §6, R6).

Z5 renders only when ≥ `insights.min_sessions` sessions exist in range; Z2's provenance stack and Z6 render only when their subsystems have data (see §6 states). In `otel_only`, Z3–Z6 do not render at all and the screen ends after Z2, with **Z0 still above it** — one `unavailable` statement below Z2 carries the reason, not six repeated ones.

Z1's cost tile shows `cost_effective_usd_micros` and captions its basis (vendor-reported vs priced); Z1's active-time tile prefers OTel's exact `active_time.total` counter and falls back to Σ turn durations. Both follow the configurable precedence chain (modes doc §3.3), and both label which source won. Z0's peak-hour tile follows the same pattern on a third chain (`turn_started` → `session_started`, precedence.py's `PEAK_HOUR_CHAIN`) — this is a precedence fallback, not the mode-gated `unavailable` shape, since it always resolves to a real number with a named source (modes doc §5 does not apply to it).

**Known limitation, deliberate:** every day boundary and hour bucket in Z0 is **UTC** — there is no user timezone anywhere in the system yet (no column, no config key, no query param; FEAS §5). Marked `TODO(rhythm-tz)` at each bucketing site (the `activity_by_day` query, the streak walk, the peak-hour bucketer); removed only once a user table exists to hang a timezone preference on. No user-facing caption for it this phase.

## 3. S2 — Sessions ("the shelf")

The full sortable/filterable list; Z4 is a preview of this screen.

- **Row anatomy:** `glyph(74px sparkline + shape word) · title · repo chip · started/duration · turns · tokens · est.$ · badges(💰⏱🗜🌿⚠❄)`.
- **Controls (one row above the list):** sort (recent | cost | tokens | duration | compactions | subagents | errors) · badge filter chips · repo filter · vendor filter. Sort keys map 1:1 to the scalar columns (dashboard doc §3.4) — no JSONB sorting, which is why cost is the promoted `cost_effective_usd_micros` column and not a reach into the `otel` block (modes doc §3.2).
- Infinite scroll or simple pagination (limit/offset) — implementer's choice, keep it dumb.
- Empty states per §6. Clicking anywhere on the row → S4.
- **Mixed-mode lists are normal.** An `otel_only` row has cost, model and time but no glyph, title, repo or badges; it renders as a quiet reduced row that states its own source once, and it does **not** link to S4. Rows mid-lag (`jsonl_lag_s` set) show cost as settled and structure as still arriving — the modes doc §7 treatment.

## 4. S4 — Session Story (deep dive)

Exactly the storytelling doc §3 anatomy, in order — the mockup is the visual contract:

```
┌ header: glyph+shape · title · meta line · anchor chips ──────┐
├ narrator line (serif voice) ─────────────────────────────────┤
├ burn chart: cumulative + live context + personal band        │
│             + hoverable beat markers                         │
├ acts list (i/ii/iii · what · cost) with inference tags       │
│   and "mark as expected"                                     │
├ receipt: Produced | Consumed (with provenance mini-stack)    │
├ per-unit line ($/file · tokens/line · re-read content)       │
├ tabs: [Metrics] [Tools & MCP] [Subagents] [Files] [Events]   │
└──────────────────────────────────────────────────────────────┘
```

The tabs hold the dense §4.2 metrics panel content (dashboard doc) so the story reads first and tables never compete with it. [Events] is the raw condensed timeline (debug-grade, virtualized list). Subagent lanes in the burn chart expand on click into the [Subagents] tab.

**S4 requires JSONL.** Every element above reads `signal_blob.events` or `session_info.info`; in `otel_only` there is neither, so the route does not resolve and nothing links to it (§3). This is the single largest capability difference between modes and the strongest reason a user has to add the collector — state it where they meet it, in the modes doc §5 voice, not as a broken page.

Where JSONL is merely **lagging** rather than absent, S4 does render: the receipt's Consumed column and the cost figures are settled from OTel, while Produced, the acts and the turn timeline are still filling in. The burn chart stays JSONL-timed with OTel totals annotated — a request ledger has no turn boundaries to plot against.

## 5. S3 — Insights ("the opportunities")

- Ranked cards, most-recoverable-$ first. Card anatomy: *observation sentence (fact) → evidence (numbers/sessions with glyph links) → suggested action → "~$N potentially recoverable" → [dismiss] [mark as expected]*.
- A filter row: kind (cost | practice), status (open | dismissed).
- Practice-correlation cards carry their personal-evidence line ("in your sessions: planned $1.10/file vs unplanned $2.30") only past the min-sample gate; below it they fall back to citation-only form (best-practice doc §4.3).
- Tone: the lexicon table (storytelling doc §2) is a **hard checklist for every card's copy** — treat a violation as a bug, not a style nit.

## 6. States (each component ships all five)

| State | Rule |
| :---- | :---- |
| Loading | skeleton blocks, no spinners-on-white; charts reserve their final height (no layout jump) |
| Empty (no data in range) | one quiet sentence + widen-timeframe hint; never a sad-face illustration |
| Cold start (subsystem not yet computing) | the honest version of empty: "Provenance decomposition hasn't run for these sessions yet." Personal band hidden below `story.min_sessions_for_band`; scorecard hidden below `insights.min_sessions` |
| **Unavailable (not derivable from this data source)** | **the mode state — full contract in modes doc §5.** Names the source, not the user; says concretely what that source would show, in this component's own terms; visually distinct from both empty and error; stated once per zone or screen, never per row. It never resolves on its own, so cold-start copy ("hasn't run yet") applied here is a lie |
| Error | inline, factual, retry affordance; never a toast that vanishes with the only explanation |

Cold start and unavailable are easy to confuse and must not be: one is *temporary and ours*, the other is *standing and the deployment's*. Choosing the wrong one either nags a user about something they cannot fix, or promises data that will never arrive.

## 7. Visual system

- **Tokens:** lift the mockup's CSS custom-property system verbatim (dataviz-validated palette, light+dark under both `prefers-color-scheme` and `data-theme` scopes). Categorical slots 1–6 are **pinned to provenance origins globally** (conversation=blue, file reads=orange, tool output=aqua, MCP=yellow, subagents=magenta, setup=green) — same origin, same hue, on every screen. Status red is failures only; cost is always neutral ink.
- **Type roles:** system sans (UI), `ui-monospace` + `tabular-nums` (every number), serif (narrator lines only — the single storytelling gesture).
- **Charts:** follow the mockup's mark specs — 2px lines, hairline grid, quiet band, hoverable markers, legend for ≥2 series. One axis, always.
- **Cost basis is a caption, never a colour.** A vendor-reported dollar and a priced-from-tokens dollar render in the same neutral ink at the same weight; which one it is goes in the caption or tooltip. Tinting estimates would make "we priced this ourselves" read as a warning, and STORY §2's cost-is-ink rule outranks the wish to differentiate. Same for `cost_complete: false` — the words "this total is a floor", not a red edge.
- **`unavailable` treatment:** the quiet band tone, not the error tone — reduced-contrast ink on the component's own background, no border, no icon. It should read as a caption about the deployment, which is what it is.
- **Responsive:** S1 zones stack single-column < 700px; S4 chart scrolls horizontally in its own container rather than shrinking below legibility; the receipt collapses to one column < 560px.

## 8. Component inventory (build-once list)

Mode column: which datasource modes the component renders in (modes doc §2). `otel` = `otel_only`, `jsonl` = `jsonl_only`, `both` = the hybrid deployment. A component that does not render in a mode ships the `unavailable` state for it.

| Component | Used on | Data | Modes | Spec |
| :---- | :---- | :---- | :---- | :---- |
| `StatTile` | S1 Z0, Z1 | overview + rhythm endpoints | **all** — Z0's four tiles render in every mode, unlike Z1's (subset of tiles in `otel`) | dashboard §3.1, FEAS §1 |
| `Heatmap` | S1 Z0 | rhythm endpoint (`days[]`) | **all** — the argument for the zone (FEAS §11) | FEAS §1/§8, R4 |
| `ModelDonut` + `CostBars` | S1 Z2 | overview | all | dashboard §3.2 |
| `QuerySourceSplit` | S1 Z2 | overview (`otel.by_query_source`) | `otel`, `both` | dashboard §3.2 |
| `OriginStack` (full + mini) | S1 Z2, S4 receipt | provenance | `jsonl`, `both` | dashboard §5, mockup |
| `Tabs` (generic shell) + `TopList` | S1 Z3 (one tabbed card) | overview | `jsonl`, `both` | dashboard §3.3, R7 |
| `SessionRow` + `Glyph` | S1 Z4, S2, S3 evidence | sessions endpoint (`info.story.curve`, shape, badges) | `jsonl`, `both`; reduced row in `otel` | storytelling §3.1/§5 |
| `Scorecard` | S1 Z5 | practices rollup | `jsonl`, `both` — **claude_code only** (§6 modes doc) | best-practice §4.4 |
| `InsightCard` | S1 Z6, S3 | insights endpoint | all (catalog filtered by mode — dashboard §6) | dashboard §6 + best-practice §3 + tone §2 |
| `BurnChart` (band, beats, lanes) | S4 | timeline endpoint | `jsonl`, `both` | storytelling §3.3–3.4, mockup |
| `NarratorLine`, `ActsList`, `Receipt`, `PerUnitLine` | S4 | timeline `story` block + session detail | `jsonl`, `both` | storytelling §3, mockup |
| `MetricsTabs` | S4 | session detail | `jsonl`, `both` | dashboard §4.2 |
| `SourceNote` (the `unavailable` statement + enrollment path) | S1 zones, S2, S3 | `datasource` on any response | wherever a mode is absent | modes doc §5 |
| `CostBasisCaption` | anywhere a dollar renders | `cost_basis`, `cost_complete` | all | modes doc §3.1, §7 |
| `TimeframePicker`, `Badges`, `AnchorChips`, `InferenceTag`, `VendorFilter` | global | — | all | mockup |

## 9. Decisions taken here (overridable, but the plan assumes them)

1. **v1 front-end = framework-free static SPA** (vanilla ES modules + the mockup's CSS), served by the new `src/server/api` FastAPI app from `src/server/api/static/`. No node toolchain in this Python repo; the mockup proves the visual system needs none. Revisit only if v2 interactivity demands it.
2. **S4 is a routed page, not a modal** — stories are meant to be linked and shared.
3. **Hash routing, no SSR, no auth in v1** (single-user, local/PoC posture; auth arrives with the org dashboard).
4. **One JS chart module, hand-rolled SVG** (mockup style) — no chart library; our charts are few and specific.
5. **v1 stays single-user** (2026-08-06). OTel intake is org-token authenticated and its enrollment story is fleet-shaped — one managed-settings block onboards a thousand developers — which puts real pressure on a single-user design. The decision stands anyway: build the single-user view, iterate on it, then design the org view deliberately. The one concession is structural: **`org_id` and `vendor` are carried through every query and response from day one**, even while filtered to one user, so the org view is a new screen rather than a rewrite of every endpoint.
6. **Mode support is a component-level contract, not a screen-level afterthought** (§8's Modes column). Every component knows which modes it renders in and ships the `unavailable` state for the rest. Deciding this per screen at build time is how a dashboard ends up lying to `otel_only` users in six places.
