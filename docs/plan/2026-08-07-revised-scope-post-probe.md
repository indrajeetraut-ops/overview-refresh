# Revised scope — storytelling and provenance, after the feasibility probes

**Date:** 2026-08-07
**Supersedes:** the storytelling half of [`2026-08-06-dashboard-api-implementation.md`](2026-08-06-dashboard-api-implementation.md) §A6, all of [`2026-08-06-dashboard-ui-implementation.md`](2026-08-06-dashboard-ui-implementation.md) §U5, and phases P4.2/P4.3 of [`2026-08-01-user-dashboard-implementation.md`](2026-08-01-user-dashboard-implementation.md).
**Evidence:** [`docs/superpowers/reports/2026-08-07-provenance-storytelling-probe.md`](../superpowers/reports/2026-08-07-provenance-storytelling-probe.md), and the corpus measurements repeated inline below.
**Status:** proposed 2026-08-07.

---

## 0. Why this document exists

Two phases were deferred as "real, undesigned computation": storytelling (A6/U5) and provenance (P4). Before building either, both were probed offline against 44 real folded sessions. The probes changed the answer materially in both cases — not "build it" or "don't", but **build a different thing.**

The three findings that drive everything below:

1. **60% of all spend is `cache_read`** — re-reading context that is already in place. The provenance design measures *contribution* (what added to context); the money is in *carry* (how long it stays there being re-read). The design itself calls carry "the actionable one" (DASH §5.3); the data says it is also four times larger than everything contribution can explain.
2. **Beats are real; the rest of storytelling is not.** A median session fires 6 of 11 beat kinds. The personal band, on its specified axis, has p75 = 15.4× p25. Shape archetypes classify 87% of sessions as `cliff`.
3. **Session-type classification is cheap and works.** A six-line rule over read/edit counts, file extensions and web-tool usage separates the corpus into four coherent groups. It needs no LLM and no message content.

---

## 1. Cut, with reasons

| Cut | Measured | Was |
| :--- | :--- | :--- |
| **Narrator line** | not directly measurable; inherits the failure of the chart and shape word it sits between | STORY §3.2, A6, U5 |
| **Personal band (p25/p75 envelope)** | p75 = 15.4× p25 on the absolute-token axis — contains essentially every session | STORY §3, A6, U5 |
| **Shape archetypes** | 87% `cliff`; 3 of 8 archetypes ever fire | STORY §5, F3 (already built in the fold) |
| **Provenance by origin (P4.2/P4.3)** | delta attribution scores 70% residual against a ≤10% gate; the estimator itself is fine (16.3% on genuine appends) but nothing attributes a cache re-write back to what it re-sends | DASH §5, P4 |
| **Insight #1 — file re-read waste** | F2 is **0.5%** of appended tokens | DASH §6, the catalog's flagship |
| **Failed tool calls as a waste category** | Bash fails 4% of calls; ~70KB wasted across the whole corpus | candidate, never specced |

Two notes on the cuts.

**The band is a spec problem, not a dead idea.** Re-plotted on share-of-own-total, the same curves give p75/p25 = **3.03×**. If a band is ever wanted, that is the axis. It is not in this scope because the burn chart it sits behind is also cut.

**Provenance is parked, not disproven.** Its estimation core clears a 16.3% residual on material it was designed for. What it lacks is a re-write model. That is a bounded addition, and if the carry work below lands it becomes much easier — carry tracking *is* running context composition. Revisit after R2, with a hybrid OTel capture so the gate can actually fail for the right reason (no session in the corpus currently has a single request seen by both sources).

---

## 2. Keep and build

### The reframe, in one line

Stop asking **"where did these tokens come from?"** Start asking **"what is sitting in context, for how many turns, and what did that cost?"**

Same event walk. Far less estimation. Every output has an action attached.

### The corpus, split by billing channel

| Channel | Cost | Share | What it is |
| :--- | ---: | ---: | :--- |
| **cache_read** | **$609** | **60%** | carry — re-reading context already in place |
| cache_write | $275 | 27% | first caching, plus every re-cache after a TTL expiry |
| output | $137 | 13% | what the model produced |
| input | $1 | 0% | uncached new material |

`cache_read cost ≈ context size × number of requests`. Two levers, both fully under a developer's control: keep the context smaller, or keep sessions shorter.

---

## R1 — Repository / project identity

**Why first:** the session page cannot show what a session was *about* without it, `info.repo` is currently `{cwd: null, repo_key: null, git_branch: null}` on every row, and Z3's repo list and insight scoping both depend on it. Everything downstream reads better with it in place.

- [x] **R1.1 Path-derived project root.** File paths in the blob already carry it. A regex over `tool_call.files[].path` and `file_change.path` yields **6 distinct project roots** across the corpus with no new collection. This is the fallback that always works.
- [x] **R1.2 Wire the existing git identity.** [`src/common/git_ident.py`](../../src/common/git_ident.py) already canonicalizes remotes to `github:owner/name`; the fold's `repo_info()` is what isn't populating. Evidence ranking per [`docs/modules/git-source.md`](../modules/git-source.md) §6.2: git remote > `prRepository` > path-derived.
- [x] **R1.3 Sessions are multi-project.** Measured: **38 sessions touch one project, 7 touch two, 1 touches three.** `info.repo` must become a *list* with a primary, weighted by references — not a scalar. Getting this wrong is a schema change later. *(`info.repo.repos[]`, ranked by `tool_call.files[]` refs; secondary roots found via `git_ident.sibling_project_root`.)*
- [~] **R1.4 Surface it:** repo chips on the session page and the S2 row; repo filter on the sessions list; Z3's repo top-list becomes real. *(Repo filter on `/v1/users/{id}/sessions` and Z3's top-list are done; session-page/S2-row chips are UI and stay scoped to R8.2, which owns `repo-chips.mjs`.)*

**Done when:** every session with file activity resolves at least one project, multi-project sessions show all of them ranked, and the shape-contract golden is updated deliberately.

## R2 — The carry engine *(replaces P4.2/P4.3)*

**The single biggest lever in the remaining scope.** 60% of spend, nothing currently computes it.

- [x] **R2.1 Context high-water tracking.** Per session, walk requests in order and track live context size (`cache_read + cache_write`) per request. This is exact — no estimation, no residual. *(`metrics.carry_rollup` — one pass over `assistant_message` events, `peak_context`/`mean_context`/`requests`.)*
- [x] **R2.2 Carry cost.** `cache_read` tokens × the model's cache-read rate, per request, summed. Roll up to `info.carry = {cache_read_tokens, carry_usd, peak_context, mean_context, requests}`. *(Field is `carry_usd_micros`, matching the codebase's `_usd_micros` storage convention rather than the prose's `carry_usd`; `None` — not a partial figure — when any priced request's model/date is unpriced, or when the fold ran without a `PricingConfig` at all. `PricingConfig` threaded into `EconFold` via `ingest/app.py` and `worker/cli_work.py`, both of which previously constructed the fold with no pricing.)*
- [x] **R2.3 Attribute carry to time, not origin.** The actionable statement is "context sat at 150k for 40 requests," not "12% of it was MCP schemas." Resist re-deriving origins here; that is parked provenance. *(No per-source breakdown anywhere in `carry_rollup`.)*
- [x] **R2.4 Insight — context weight.** "Your context averaged 148k over 312 requests; that carry cost $NN. Sessions under 60k averaged $NN." Ranked by recoverable spend. *(Insight #28, range-level like #22, request-count-weighted mean context. The "sessions under 60k averaged $NN" comparison clause is not implemented — it implies a threshold this pass has no basis to hardcode; revisit if that framing is wanted.)*

**Done when:** the corpus reproduces the $609 figure from folded fields alone, with no blob re-read. *(Unverified in this pass — no access to the real 44-session corpus/DB in this environment; the engine is unit- and DB-tested against synthetic fixtures only. Re-run `carry_rollup` over the real corpus before trusting the $609 figure.)*

## R3 — Resume-after-break cost

Measured: **142 post-break context reloads across 44 sessions ≈ $174** ($152 Opus, $22 Sonnet).

- [x] **R3.1 Detect the reload:** inter-request gap > TTL, followed by a cache write covering ≥ half the live context. `cache_write_detail` already carries the 5m/1h TTL split. *(`metrics.resume_reload_rollup`, gated on `info.resume_cost.events[].reload_tokens >= context_before / 2`.)*
- [x] **R3.2 Price it** at the write-minus-read delta. Exact. *(Per contributing write, on its own TTL tier and model; `None` — not partial — on the same all-or-nothing terms `carry_usd_micros` already established in R2.)*
- [~] **R3.3 Insight — the timed one.** Not "take fewer breaks." **"This session has been idle 15h and carries 150k of context; resuming costs ~$4 before you type anything."** The action is *start fresh*, and the moment it is useful is the moment of resuming. *(Not built this pass — this is a live, per-session, wall-clock-dependent computation, structurally a poor fit for the range-aggregate `/v1/users/{id}/insights` endpoint the other insights use. It belongs on the session page as one of R7's `findings`, once R4–R6 give it siblings to assemble against; the underlying data (`info.carry`'s current context, the session's own `last_event_at`) is already there for R7 to read.)*
- [x] **R3.4** Subsumes catalog insight #4 (cache decay), which is promoted from one card to a primary finding. *(Insight #4 now reads `info.resume_cost` rather than the bare `info.cache.decay_events` signal — a range-level aggregate card, same posture as insights 22/28. "Primary finding" in the fuller sense R3.3 describes is still R7's job.)*

**Done when:** the 142 events and the $174 reproduce from folded fields. *(Unverified in this pass — same caveat as R2: no access to the real 44-session corpus/DB here, only synthetic-fixture and DB-backed unit tests. Re-run `resume_reload_rollup` over the real corpus before trusting the $152 Opus / $22 Sonnet split.)*

## R4 — Beats and session segmentation

Validated: median session fires **6 of 11** beat kinds; `revision_loop` — the only inference in the taxonomy — fires in 26 of 44.

- [x] **R4.1 Beat detection** per STORY §4, read-path from `signal_blob.events`, all eleven kinds. `stall` fires in 1 of 44 sessions; keep it, it costs nothing. *(`beats.detect_beats`. Computed at fold time, not read-path — see the done-when note below.)*
- [x] **R4.2 Segmentation.** [`Task-Classification-and-Analysis.md`](../design/Task-Classification-and-Analysis.md) §4 solves this with markers, not ML — and **it is the same problem as beat detection.** Build them as one pass: beats find boundaries, segments are the spans between strong ones. *(`beats.build_segments`, boundaries = `context_squeeze`/`wrap_up`/`delegation`/a pause past a higher bar than the beat-level one.)*
- [x] **R4.3 Per-segment cost**, using R2's carry so a segment's cost includes the context it was dragging. *(Priced fresh per segment, not sliced from `info.carry`'s session total, on the same all-or-nothing pricing terms R2/R3 established.)*
- [x] **No narrator line, no acts prose.** Segments are labelled and priced; they are not narrated.

**Done when:** a session resolves to 2–6 labelled, priced segments, and the boundaries survive a re-fold unchanged. *(Fold-idempotency tested directly — same events folded twice produce byte-identical segments. "2–6" is the corpus's typical range, not enforced as a per-session floor: a single-phase session legitimately resolves to 1. One deliberate deviation from R4.1's own wording: segments are computed **at fold time**, not read-path — they persist and carry priced cost, which needs a stable, once-computed span; "survive a re-fold unchanged" is a fold-idempotency property to begin with, not a UI-read concern. Unverified against the real 44-session corpus, same caveat as R2/R3.)*

## R5 — Session type classification (M-04 Tier 0)

Taxonomy already specified — [`Task-Classification-and-Analysis.md`](../design/Task-Classification-and-Analysis.md) §2: BUG_FIX, REVIEW, COMPREHEND, DESIGN, PLAN, IMPLEMENT, OPS, MISC. Read §2 before adopting a different set.

Tier 0 separation on this corpus, from a six-line rule:

| Inferred type | Sessions | avg reads | code edits | doc edits | web |
| :--- | ---: | ---: | ---: | ---: | ---: |
| IMPLEMENT/FIX | 26 | 41 | 55 | 9 | 2 |
| DESIGN/DOCS | 12 | 16 | 1 | 19 | 6 |
| RESEARCH | 7 | 11 | 0 | 0 | 0 |
| MISC/thin | 7 | 1 | 0 | 0 | 0 |

- [x] **R5.1 Tier 0 signatures** from §6: read/edit ratio, path and extension mix, plan-mode markers, slash commands, subagent types (Explore ⇒ COMPREHEND, Plan ⇒ PLAN — pre-labelled by construction), web-tool usage. *(`session_type.classify_segment`, first-match-wins over exactly these six signals. "Plan-mode markers" is the working `ExitPlanMode` signal — the normalizer's own `user_message.plan_mode` field is a known, tracked gap, always `null`. Fixed Python rules, not §6's own aspirational config-driven predicate engine — six signals don't need one; R5.4's restraint extended to the rule engine itself. One added-but-undocumented-in-R5.1 signal: a fail-then-pass verifier pattern within a segment, the cheapest Tier-0-compatible proxy for BUG_FIX available without the full behavior graph (§5) — without it, BUG_FIX had no signal to fire on at all and IMPLEMENT/BUG_FIX becomes an unreliable-boundary guess, which §2 itself says to avoid by collapsing rather than force-splitting.)*
- [x] **R5.2 Label segments, not just sessions.** A session is often research *then* implementation — R4's segments are the natural unit, and a single session label would flatten exactly the distinction that makes it useful. *(`classify_segment` runs over each segment's own event slice only.)*
- [x] **R5.3 MISC is a real label.** Never force a guess (§2's own rule). *(Thin/no-signal segments and truly ambiguous ones both fall through to MISC, confidence 1.0 — certain we don't know, not a low-confidence guess.)*
- [x] **R5.4 No Tier 1 LLM pass in this scope.** Tier 0 covers the bulk; revisit if measured coverage is poor. *(Coverage against the real corpus is unverified here — see the done-when note.)*
- [x] **R5.5 Model × type reporting.** Measured: DESIGN/DOCS runs 9-of-13 sessions on Opus for 169M tokens; RESEARCH runs entirely on Sonnet for 7.7M. Model overkill is intractable in the abstract and straightforward per type — this is what unlocks catalog insight #11. *(Insight #11: one card, the costliest type's dominant model and token share. Reports the concentration fact and suggests comparing — not an "overkill" verdict, which would need a price-tier ranking this pass doesn't have. Session-level, not per-segment: "type" is the label of the segment with the most turns, matching how the measured example itself is phrased ("9 of 13 sessions"); a true per-segment model breakdown would need per-segment token-by-model tallying `info.segments` doesn't carry yet.)*

**Done when:** every session with activity gets a label or MISC, and spend-by-type-by-model is reportable. *(Both true by construction — `classify_segment`'s only unlabelled exit is `MISC`, and insight #11 makes spend-by-type-by-model a real, tested endpoint response. Unverified against the real 44-session corpus, same caveat as R2–R4: no access to it in this environment. In particular the "read/edit ratio" thresholds (`misc_min_reads`, `docs_dominant_fraction`) are analytically chosen, not fitted — re-tune against real data before trusting label coverage the way STORY §5's shape-archetype thresholds needed retuning.)*

## R6 — The remaining waste detectors

Selected from the probe's candidate list.

- [x] **R6.1 Read-then-discard.** Large reads shortly before a compaction — paid to load, then thrown away. Signals present: `bytes_read` + compaction timestamps. *(`metrics.read_then_discard_rollup` → `info.read_then_discard`; token estimate only via insight 18's own `bytes_read // 4` convention, no `PricingConfig` — insight 29.)*
- [x] **R6.2 Long-tail sessions.** The point where tokens/turn keeps climbing while `files_changed` flattens. Sharper than compaction churn (#3), which is the existing proxy. Pairs naturally with R3 — the action for both is *start fresh*. *(Insight 30, pure read-time computation over `session_info.turns` (already a stored column) — no fold pass needed. This supersedes the old catalog's #3 rather than sitting beside it; #3 stays unbuilt, listed in R9, but R6.2 is what its slot should now become, not a second, weaker proxy for the same phenomenon.)*
- [x] **R6.3 Interrupted requests.** Input paid, output discarded. `interruption` beats fire in **19 of 44** sessions. Also flag them so unmatched output does not distort other measures. *(`beats.interrupted_cost_rollup` → `info.interrupted`, the preceding assistant_message's tokens per interruption — insight 31, reported `est_usd=0`/no `action_text`, a fact not a recoverable claim. "Flag them so unmatched output does not distort other measures": the flag exists (`info.interrupted`); no existing rollup currently averages over turn shape in a way an interrupted turn would skew, so there was nothing to retrofit against yet — revisit if one is added.)*

**Not selected:** subagent fan-out (child tokens vs. what returns). Signals exist — `sub_agent_spawn`, the subagents rollup — and it was in the candidate list but not picked. Recorded here so it is a decision, not an oversight.

**Note on scope:** all three detectors are token-estimate-only (no `PricingConfig`), following R6's own lighter-weight posture relative to R2/R3's per-token-priced engines. Unverified against the real 44-session corpus, same caveat as R1–R5.

## R7 — API surface for the session page

R1–R6 write fold fields. Nothing currently carries them to a browser. This extends the existing `GET /v1/sessions/{id}` ([`service.session_detail`](../../src/server/api/service.py)) rather than adding a route — the page is one screen and should be one request.

**Ground rules from P2 still apply**, in particular: `app.py` is the only file importing FastAPI; SQL lives in `config/queries/dashboard.yaml`; the API never re-derives dollars from tokens; `calibration` must never reach a response body.

- [ ] **R7.1 `repos`** — ranked list, not a scalar (R1.3): `[{repo_key, display, refs, share, primary}]`. Empty list when no file activity resolved, **not** `unavailable` — a session with no file work legitimately has no repo.
- [ ] **R7.2 `cost.by_channel`** — `{carry, recache, output, input}`, each `{tokens, usd_micros, share}`, from R2's rollup. Reuse A1's `sourced()` so each figure still reports the source that produced it.
- [ ] **R7.3 `segments`** — `[{index, label, confidence, t0, t1, turns, files, repo_key, beats: {kind: count}, carry_usd_micros, cost_usd_micros}]` from R4+R5.
- [ ] **R7.4 `findings`** — `[{finding_id, kind, observation, action, usd_micros, basis}]` from R3+R6, ranked by recoverable spend. `basis` distinguishes **recoverable** from **observed** — the interrupted-requests finding is a fact with no action, and rendering it as recoverable would be a false claim.
- [ ] **R7.5 Mode contract.** All four blocks are JSONL-derived, so all four are `unavailable` in `otel_only` with `requires: "jsonl"`. The existing bug-fixed predicate is `datasource != "otel_only"`, **not** `datasource in ("jsonl_only", "both")` — a folded-but-not-yet-ticked session has `datasource` `None`.
- [ ] **R7.6 Cold-start vs unavailable, per block.** Before R2–R6 land, each block returns `null` + cold-start in every mode. This is what lets the UI ship ahead of the engines, the same way the reduced P3 did.
- [ ] **R7.7** Extend the envelope conformance test (A1) and add per-block cold-start / unavailable / empty assertions. These three are one substitution away from each other and the failure is a false sentence on screen, not an exception.

**Done when:** all three modes return a well-formed payload, `otel_only` returns the four blocks as `unavailable`, and a pre-R2 fixture returns them as cold-start.

## R8 — Session drill-down UI

**Visual contract:** [`docs/design/mockups/session-drilldown-concept.html`](../design/mockups/session-drilldown-concept.html). Port from it rather than re-deriving — the same approach that worked for P3.

Replaces [`js/screens/session-detail.mjs`](../../src/server/api/static/js/screens/session-detail.mjs), which today is a plain metrics panel. Route (`/session/{id}`), five-state primitive, `MetricsTabs`, `CostBasisCaption` and the `otel_only` path all already exist and are **reused unchanged**.

### Ground rules

Carried from the P3 plan, still binding: no framework, no build step, no external requests; `h()`-style DOM builder, **no `innerHTML`** anywhere (session titles, repo names and file paths are all user-supplied); every number `ui-monospace` + `tabular-nums`; red reserved for hard failures; every user-facing string added to `copy.mjs` and reviewed against the STORY §2 lexicon before merge.

One rule specific to this screen: **a segment label is an inference and must never be styled as a fact.** R5's Tier 0 will be wrong sometimes, and a confidently wrong label on a session page is more corrosive than no label.

- [ ] **R8.1 Tokens.** Four additions to `tokens.css`: the four cost channels, and a segment-label palette. Channels are **not** the provenance slots `--s1..--s6` — those are pinned to origins globally (BLUE §7) and reusing them would make two unrelated legends share hues. Add `--ch-carry`, `--ch-recache`, `--ch-output`, `--ch-input` as their own semantic set, and record why in a comment.

- [ ] **R8.2 `components/repo-chips.mjs`** (R1 — the piece that does not exist today).
  - Primary repo carries stronger ink; secondaries are quieter; each shows its share.
  - **Multi-project is normal, not an edge case** — measured at 8 of 46 sessions. Design for two or three chips, not one with an overflow case.
  - Zero resolved repos renders nothing at all. No empty state, no "unknown repository" — the absence of file work is not a defect to report.
  - Chips link to the sessions list filtered by that repo (R1.4).

- [ ] **R8.3 `components/type-chip.mjs`** (R5).
  - Label + an `InferenceTag`, reusing the pattern already specced for `revision_loop`.
  - Below the confidence threshold, render **MISC or nothing** — never a low-confidence guess styled like a certainty (R5.3).
  - Open question 3 in this doc governs the threshold; do not pick it here in code.

- [ ] **R8.4 `components/cost-channels.mjs`** (R2 — the reframe, and the most important block on the page).
  - Stacked proportional bar + legend. **`chart.mjs`'s existing `proportionalBar()` and `legendRows()` cover this** — extend them if needed, do not write a second bar.
  - Each row: channel name, plain-language gloss, dollars, share. Sub-notes carry the measured detail (token counts, request counts, mean context).
  - The carry callout is stated **in words, not as a colour** — the same rule the receipt's `cost_complete` follows.
  - `aria-label` naming the split; the bar is decorative without it.

- [ ] **R8.5 `components/segment-strip.mjs`** (R4 + R5).
  - Proportional horizontal strip, one span per segment, plus a time axis.
  - **Labels drop out below ~700px rather than truncating** — the mockup's approach, and the strip stays readable as pure proportion.
  - Table beneath: segment, project, turns, files, carry, cost, with the beat composition as a sub-line.
  - The per-segment **project column matters** — in the mockup's own example the design work happened in a different repo from the implementation, which is exactly the thing a session-level repo chip hides.
  - Keyboard: each span focusable, with the same detail on focus as on hover. A hover-only strip is unreadable by keyboard.

- [ ] **R8.6 `components/findings.mjs`** (R3 + R6).
  - Per finding: observation (fact, numbers emphasised) → action → dollars, ranked.
  - **`recoverable` and `observed` must render differently.** Interrupted requests are the observed case: a fact, no action, and no implication that the money was wasted — interrupting early is usually the cheaper choice.
  - Every action sentence through the lexicon. This block is where the product is most likely to sound like it is grading someone; opportunity framing on every dollar, no "waste", no "inefficient".
  - Empty is a real, good state: "nothing stood out in this session" — not a hidden panel.

- [ ] **R8.7 Assemble `screens/session-detail.mjs`.**
  - Order: header (title, type chip, repo chips, meta) → cost by channel → segments → findings → `MetricsTabs`.
  - Each block independently five-stated — the page must render with cost live and segments cold-start, because that is the real intermediate state while R2 ships before R4.
  - `otel_only` keeps its existing reduced render: cost + tokens + one `SourceNote`, and **no** segment or findings panels. Not four `unavailable` boxes.
  - Lagging sessions (`jsonl_lag_s` set) render with one quiet "still arriving" line.

- [ ] **R8.8 Responsive, accessibility, lexicon gate.**
  - Segment table drops project/files columns < 700px; findings stack to one column; the strip keeps its proportions and loses its labels.
  - Focus visible throughout; charts `aria-label`led; `prefers-reduced-motion` respected.
  - Verify against a seeded fixture DB in **all three modes**, both themes, desktop and mobile. No console errors; no network requests beyond `/v1/*`.

**Done when:** the page matches the mockup in both themes for a `both`-mode session; an `otel_only` session renders the reduced view with no empty panels; a pre-R2 session renders cold-start blocks without a broken frame; and every new string is in `copy.mjs` and lexicon-reviewed.

## R9 — Housekeeping carried forward

Unblocked, unrelated to either probe, still outstanding from the reduced P2/P3:

- [ ] Z5 practice scorecard — aggregation over the practices vector P1 already computes; currently `null` in every mode
- [ ] `POST /v1/sessions/{id}/feedback` + table — A8, never built; U7's buttons are disabled without it
- [ ] Catalog insights #3 (compaction churn) and #5 (retry burn) — unblocked, signals exist. #4 is absorbed by R3.
- [ ] A9's assertion that `api.request` logs carry no payload content

---

## Sequencing

```
R1 (repo identity) ──┬──► R2 (carry) ──► R3 (resume cost) ──┐
                     │                                       ├──► R7 (API) ──► R8 (UI)
                     └──► R4 (beats) ──► R5 (types) ────────┘
                                          R6 alongside
                          R9 anytime, independent
```

- **R1 first.** Cheap, unblocks the page and the repo filter, and a multi-project schema decided late is a migration.
- **R2 before R3.** Resume cost is a special case of carry accounting.
- **R4 before R5.** Types label segments; segments come from beats.
- **R7 can start as soon as R1 lands.** R7.6 has every block return cold-start until its engine exists, so the endpoint and the screen are not blocked on R2–R6 finishing — only on their shapes being agreed.
- **R8 builds block by block, in the order the engines land.** Cost channels (R2) first, then segments (R4+R5), then findings (R3+R6). Each block is independently five-stated precisely so the page ships incomplete and fills in — the same discipline that let the reduced P3 ship honestly.

**Suggested PRs:** (R1), (R2+R3), (R4+R5), (R7), (R8.1–R8.4), (R8.5–R8.6), (R8.7+R8.8), R6 and R9 folded in where convenient.

**Do not build R8 against illustrative data.** The mockup's segment boundaries and per-finding dollars are illustrative and labelled as such; a screen developed against them will encode assumptions the engines then have to satisfy. Build each block when its engine returns real rows, and render cold-start until then.

## Open questions

1. **Does carry dominance generalize?** One user's corpus, showing heavy multi-day session resumption. A team doing short continuous sessions would see far less `cache_read` dominance. Re-run both probes (`--dsn`) on a second corpus before this hardens into strategy.
2. **Is the 1h cache tier a product recommendation?** It costs 60% more to write and survives 12× longer. For this usage pattern it looks favourable, but that is arithmetic nobody has done yet.
3. **Segment labels in the UI — how confident is confident enough?** Tier 0 will be wrong sometimes. A wrong label on a session page is more corrosive than no label. Needs a confidence threshold and a correction affordance, reusing the `InferenceTag` pattern already specced for `revision_loop`.
4. **Does provenance come back?** Only with a hybrid capture and a narrower goal — the composition of the ~20% that is genuinely new material. Not before R2.
