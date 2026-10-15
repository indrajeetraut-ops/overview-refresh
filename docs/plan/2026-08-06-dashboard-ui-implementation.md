# Phase P3 — Dashboard UI: Implementation Plan

> **SUPERSEDED (2026-09-10).** The framework-free static SPA at `src/server/api/static/` was
> retired on branch `feat/mvp-implementation-#52` (MVP plan `2026-09-09-mvp-backend-implementation.md`
> Track U0). The served front-end is now the React app relocated to `src/webui/`. Kept for the
> data-binding history (BLUE §8) that the manager plan still builds against.

**Goal:** ship the v1 dashboard front-end — a framework-free static SPA served from `src/server/api/static/`, implementing the storytelling UX across all three datasource modes.

**Why it can be planned before P2 lands:** the endpoint contracts are specified (DASH §8 and §8.1), and the component-to-data bindings are specified (BLUE §8). P2 implements those contracts; it does not invent them. Where a response shape is genuinely still open, the task below says so.

**Spec set (read the cited section before starting a task — do not improvise against the spec):**
- Screens, zones, component inventory with its Modes column, five UI states: `docs/design/dashboard-ux-blueprint.md` (**BLUE**)
- Endpoint contracts, metrics, insight catalog: `docs/design/user-dashboard-design.md` (**DASH**) §8, §8.1
- Story anatomy, beats, shapes, **the tone lexicon**: `docs/design/session-storytelling.md` (**STORY**)
- Scorecard and practice insights: `docs/design/best-practice-insights.md` (**PRACT**)
- Mode capability matrix, the `unavailable` contract, coverage copy: `docs/design/dashboard-datasource-modes.md` (**MODES**)
- Visual contract: the published artifact **"Session Stories — Concept"** — palette, type roles, mark specs, receipt and burn-chart treatment
- Parent plan and ground rules: `docs/plan/2026-08-01-user-dashboard-implementation.md` (**PLAN**) §0

**Depends on:** P2 (the API). PM must land before P2. **Status:** planned 2026-08-06.

---

## 0. Ground rules specific to the front-end

PLAN §0 applies unchanged — especially §0.3, the tone lexicon. Six more:

1. **No framework, no build step, no external requests.** Vanilla ES modules, hand-rolled SVG, the mockup's CSS custom properties. No node toolchain enters this Python repo (BLUE §9.1). No CDN, no web font, no analytics call — the only network traffic the page may generate is `/v1/*`.
2. **One component idiom, chosen in U1 and never re-litigated.** Five screens written in five idioms is the predictable failure of a no-framework SPA. U1 fixes the pattern; every later task follows it.
3. **Five states are a primitive, not a per-component habit** (BLUE §6). Loading / empty / cold-start / **unavailable** / error. Each of ~14 components × 5 states hand-rolled is 70 chances to render the wrong sentence. U2 builds one wrapper; components declare, they do not re-implement.
4. **Mode drives composition, not just messaging.** A component renders in the modes BLUE §8 lists for it and ships `unavailable` for the rest. `otel_only` has no S4 route at all — that is routing, not a banner.
5. **Every number is `ui-monospace` + `tabular-nums`; cost is ink at all magnitudes.** Red is reserved for hard failures (`api_error`). A vendor-reported dollar and a priced-from-tokens dollar render identically and differ only in caption (BLUE §7).
6. **User-facing strings are reviewed against STORY §2 before merge, including the `unavailable` copy.** A violation is a bug, not a style nit. The `unavailable` sentences are the highest-stakes copy in the product — they are what a brand-new zero-install user reads first, and they are aimed at someone who has done nothing wrong.

---

## U0 — Design tokens from the mockup

**New:** `src/server/api/static/css/tokens.css`

- [ ] Lift the artifact's CSS custom-property system **verbatim**: the dataviz-validated palette, type roles, spacing scale, the quiet-band and hairline-grid values.
- [ ] Categorical slots 1–6 are **pinned to provenance origins globally** (conversation=blue, file reads=orange, tool output=aqua, MCP=yellow, subagents=magenta, setup=green). Same origin, same hue, on every screen. Write this as a comment in the file — it is a semantic binding, not a palette.
- [ ] Both theme scopes, and get the precedence right: `@media (prefers-color-scheme: dark)` as the default signal, `:root[data-theme="dark"]` / `[data-theme="light"]` overriding it **in both directions**. The common bug is a `data-theme="light"` toggle that loses to the media query on a dark-set OS.
- [ ] A token for the `unavailable` treatment (BLUE §7): reduced-contrast ink, no border, no alarm colour — visually distinct from both empty and error.

**Done when:** a scratch page renders every token in both themes and the toggle wins over the OS setting in both directions.

## U1 — Shell, router, and the component idiom

**New:** `index.html`, `js/app.mjs`, `js/router.mjs`, `js/api.mjs`, `js/dom.mjs`

- [ ] **Pick the component idiom and document it at the top of `dom.mjs`.** Recommendation: a small `h()`-style DOM builder plus plain functions returning elements — no template-literal HTML, because escaping user-supplied strings (session titles, repo names, file paths) by hand is how a no-framework SPA gets an injection bug. Whatever is chosen, every later task follows it.
- [ ] Hash router: `/`, `/sessions`, `/insights`, `/session/{id}`. Deep-linkable, no SSR, no auth (BLUE §9.3).
- [ ] `api.mjs`: one fetch wrapper stamping the global timeframe into every call, parsing the DASH §8.1 envelope, and surfacing `{unavailable: {block, requires}}` as a typed result rather than a null the caller has to guess about.
- [ ] Top bar: timeframe selector (7d/30d/90d/custom, global, persisted, stamped into every request), theme toggle writing `data-theme`, vendor filter.
- [ ] **Mode plumbing.** Mode is only known *after* the first response, so the shell cannot choose layout up front. The router resolves the route, fetches, then composes — and a `/session/{id}` request for an `otel_only` session resolves to a `SourceNote`, **not** a 404 page and not an error state. The session exists; its interiors do not.

**Done when:** all four routes resolve, the timeframe persists across them, and the theme toggle beats the OS preference.

## U2 — The five-state primitive

**New:** `js/components/state.mjs`, `js/copy.mjs`

- [ ] One wrapper: given a component's data result, render loading / empty / cold-start / unavailable / error, or hand off to the component's own render. Charts **reserve their final height** in loading so nothing jumps.
- [ ] **Cold start and unavailable must not be confusable in the API of this wrapper.** One is temporary and ours ("hasn't run yet"); the other is standing and the deployment's ("comes from the collector"). Make them separate named states with separate copy sources — a single `emptyMessage` parameter is how the wrong one ends up on screen.
- [ ] `copy.mjs`: every user-facing string in one module, so the STORY §2 lexicon review is one file rather than a grep across the tree. Includes the MODES §5 `unavailable` sentences and the §5.1 coverage copy.
- [ ] **The coverage copy uses the measured numbers, not a round one** (MODES §5.1): tokens are exact for observed requests; ~5.7% of spend is background calls JSONL cannot see, so the total is a floor; dollars are priced from a rate table. Never quote the provenance residual's ≤10% gate here — different measurement.
- [ ] `SourceNote` and `CostBasisCaption` (BLUE §8) live here.

**Done when:** a component can declare its five states in one call, and every string in the app is reachable from `copy.mjs`.

## U3 — The chart module

**New:** `js/chart.mjs`

- [ ] Hand-rolled SVG, no library (BLUE §9.4). Mark specs from the mockup: 2px lines, hairline grid, quiet band, hoverable markers, legend for ≥2 series. **One axis, always.**
- [ ] Primitives needed by the screens, and no more: line/area series, stacked bar (`OriginStack`), donut (`ModelDonut`), horizontal bars (`CostBars`), sparkline (`Glyph`).
- [ ] `Glyph` is the perf-sensitive one — up to ~40 per page on S2, each an SVG path from `info.story.curve` (≤64 points). Render as a single `<path>` with a precomputed `d` string; do not build per-point elements.
- [ ] Every chart takes an `aria-label` naming what it shows. Hit targets ≥ marker size. `prefers-reduced-motion` respected.

**Done when:** each primitive renders from fixture JSON in both themes, and 40 glyphs on one page stay smooth.

## U4 — S2 Sessions ("the shelf")

**New:** `js/screens/sessions.mjs`, `js/components/session-row.mjs`, `badges.mjs`

- [ ] `SessionRow`: glyph (74px sparkline + shape word), title, repo chip, started/duration, turns, tokens, cost, badges (💰⏱🗜🌿⚠❄).
- [ ] **The reduced row** (BLUE §3): an `otel_only` session has cost, model, time and vendor but no glyph, title, repo or badges — and **does not link to S4**. It states its source once. This is a distinct render path, not a row with empty cells.
- [ ] Controls: sort (recent | cost | tokens | duration | compactions | subagents | errors), badge filter chips, repo filter, vendor filter. All sorts map to scalar columns.
- [ ] Lag indicator where `jsonl_lag_s` is set (MODES §7). It is **not** a badge and does not participate in filtering — it describes our pipeline, not the session.
- [ ] Pagination or infinite scroll, implementer's choice, kept dumb.

**Done when:** a mixed-mode fixture list renders full rows, reduced rows and a lagging row correctly, and every sort round-trips.

## U5 — S4 Session Story (the centrepiece)

**New:** `js/screens/story.mjs`, `js/components/{burn-chart,narrator,acts,receipt}.mjs`

Implement STORY §3's anatomy in order; the mockup is the visual contract.

- [ ] Header: glyph + shape name, title, meta line, anchor chips (≤3 superlatives vs the user's own history).
- [ ] Narrator line, serif — the single storytelling gesture in the type system.
- [ ] `BurnChart`: cumulative consumption (area) + live context size (thin line) on one axis, the user's p25–p75 envelope behind both as a quiet band, hoverable beat markers, compaction ▼ and stall markers. Band hidden below `story.min_sessions_for_band` (`band: null` from the API).
- [ ] `ActsList`: 2–5 chapters with duration and per-act cost, `InferenceTag` on inferred beats, "mark as expected" → `POST /v1/sessions/{id}/feedback`.
- [ ] `Receipt`: Produced | Consumed, `OriginStack` mini, `PerUnitLine`. The dollar carries its basis as a caption; where `cost_complete` is false the receipt says the total is a floor **in words**, not as a colour. Background utility calls (provenance K) get their own line when OTel is present.
- [ ] `MetricsTabs`: [Metrics] [Tools & MCP] [Subagents] [Files] [Events]. [Events] is a virtualized list — it is debug-grade and can be long.
- [ ] **Lagging sessions render** (MODES §7): Consumed and cost settled, Produced and the acts still filling in, one quiet "still arriving" line. The chart stays JSONL-timed with OTel totals annotated — do not re-plot against request timestamps.
- [ ] Accessibility: beat markers focusable (`tabindex`), tooltips on focus as well as hover. A hover-only story is unreadable by keyboard.

**Done when:** a fixture session matches the mockup in both themes; markers are keyboard-reachable; a lagging fixture renders without a broken frame.

## U6 — S1 Overview ("the pulse")

**New:** `js/screens/overview.mjs`, `js/components/{stat-tile,top-list,scorecard}.mjs`

Zones in BLUE §2 order — facts before flags before anything that reads as advice.

- [ ] Z1 stat tiles. Cost tile captions its basis; active-time tile prefers OTel's exact counter.
- [ ] Z2 `ModelDonut` + `CostBars` (tokens/$ toggle), `OriginStack` with cold-start fallback, and `QuerySourceSplit` — the background-call widget, OTel modes only.
- [ ] Z3 four `TopList`s: tools, skills, MCP servers, repos.
- [ ] Z4 flagged rows, reusing `SessionRow`.
- [ ] Z5 `Scorecard` (PRACT §4.4) — hidden below `insights.min_sessions`, and **hidden entirely for non-`claude_code` vendors** (PRACT applicability gate). Hidden, not zeroed, not empty-stated.
- [ ] Z6 top-3 insight teasers → S3.
- [ ] **In `otel_only` the screen ends after Z2**, with one `SourceNote` below it — not six `unavailable` panels (MODES §5 rule 4).

**Done when:** all three modes render a coherent overview, and the `otel_only` version reads as a complete small screen rather than a broken large one.

## U7 — S3 Insights ("the opportunities") — *missing from PLAN P3; added here*

**New:** `js/screens/insights.mjs`, `js/components/insight-card.mjs`

The parent plan routes `/insights` and builds the Z6 teasers but never builds the screen they link to. BLUE §5 specifies it fully.

- [ ] Ranked cards, most-recoverable-$ first. Card anatomy: observation sentence (fact) → evidence (numbers, sessions as glyph links) → suggested action → "~$N potentially recoverable" → [dismiss] [mark as expected].
- [ ] Filter row: kind (cost | practice), status (open | dismissed).
- [ ] Practice-correlation cards show their personal-evidence line only past the min-sample gate; below it, the citation-only form (PRACT §4.3).
- [ ] Catalog is already mode-filtered by the API (DASH §8.1) — the screen renders what it is given and **never shows "0 found" for an insight uncomputable in this mode.** Absence of signal is not absence of the phenomenon.
- [ ] **Tone is the hard gate here.** Every card's copy against the STORY §2 lexicon: no "waste", no "inefficient", no grades, opportunity framing on every dollar. This screen is where the product is most likely to sound like it is grading someone.

**Done when:** cards render for all three kinds (cost, practice, OTel-only), dismiss round-trips, and every string passes the lexicon check.

## U8 — Responsive, accessibility, and the lexicon gate

- [ ] S1 zones stack single-column < 700px; S4's chart scrolls horizontally in its own container rather than shrinking below legibility; the receipt collapses to one column < 560px.
- [ ] Keyboard focus visible throughout; every chart `aria-label`led; `prefers-reduced-motion` respected; tooltips reachable by focus.
- [ ] **Lexicon review of `copy.mjs` as a merge gate**, plus a copy of the STORY §2 table at `src/server/api/static/js/lexicon.md` as the reviewer's checklist (PLAN §0.3).
- [ ] No console errors; no network requests beyond `/v1/*` — assert this by watching the network panel during the walkthrough, since a stray font or icon URL is exactly what slips in.

**Done when:** the walkthrough passes at mobile and desktop widths, in both themes, in all three modes.

---

## Sequencing

```
U0 ──► U1 ──► U2 ──► U3 ──┬──► U4 ──► U5
                          ├──► U6
                          └──► U7        U8 runs alongside, gates the last PR
```

- U0–U3 are the substrate and are worth doing carefully; every screen after them is assembly.
- U4 before U5 (S2's rows are how you reach a story) and before U6 (Z4 reuses `SessionRow`).
- U6 and U7 are independent of U5 and of each other.
- Suggested PRs: (U0+U1+U2), U3, U4, U5, (U6+U7), U8 folded into the last.
- **Build against a seeded fixture DB with all three modes present from the first screen.** A UI developed only against `both` grows mode-awareness as an afterthought, which is exactly the failure MODES exists to prevent.

## Out of scope, recorded

- **Org / multi-user views.** v1 is single-user (BLUE §9.5). `org_id` and `vendor` ride every response so the org view is a new screen, not a rewrite — but it is not this phase.
- **Provenance widgets go live in P4.3**, not here. U6 builds `OriginStack` with its cold-start fallback; the true origins arrive when the fold does.
- **Insight persistence** (dismiss/track surviving a reload) is P5. U7 wires the buttons to the endpoint pattern; whether the state persists is the API's problem.
- **Any node toolchain.** If something here seems to need a build step, it is the wrong solution — the mockup proves the visual system does not need one.
