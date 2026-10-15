# Phase P2 — Dashboard API: Implementation Plan

**Goal:** a read-only query service over storage — `src/server/api/` — serving the DASH §8 endpoints with the DASH §8.1 mode contract, across all three datasource modes.

**Position:** PM writes the mode fields; this reads them; P3 renders them. It is the smallest of the three phases — the endpoint contracts are already specified — but it is where two things get decided for the first time: how a response says "not derivable here", and where the read-path story computation lives.

**Spec set (read the cited section before starting a task):**
- Endpoint list, response contents, the mode contract: `docs/design/user-dashboard-design.md` (**DASH**) §3, §4.2, §6, §8, §8.1
- Mode model, precedence chain, coverage semantics: `docs/design/dashboard-datasource-modes.md` (**MODES**)
- Beats, acts, narrator templates, shapes, band: `docs/design/session-storytelling.md` (**STORY**) §3–§6
- Practice insights and the min-sample gate: `docs/design/best-practice-insights.md` (**PRACT**) §3, §4.3
- Component data bindings: `docs/design/dashboard-ux-blueprint.md` (**BLUE**) §8
- Ground rules: `docs/plan/2026-08-01-user-dashboard-implementation.md` (**PLAN**) §0

**Depends on:** PM (mode fields), P1 (fold rollups). **Blocks:** P3. **Status:** planned 2026-08-06.

---

## 0. Ground rules specific to this module

PLAN §0 applies. Five more, four of them from patterns the sibling services already established:

1. **`app.py` is the only file that imports FastAPI.** Everything else is framework-free and directly testable — the rule `src/server/uploader/app.py` states in its own docstring, and the reason its service layer can be unit-tested without a client.
2. **SQL lives in `config/queries/dashboard.yaml`**, loaded by `QueryCatalog` (`catalog.get("dashboard", "<name>")`). No inline SQL in Python. The catalog validates at load, so a malformed query fails at startup rather than on the first request.
3. **Read-only. No writes, no fold coupling** — with exactly one exception, `POST /v1/sessions/{id}/feedback`, which writes to its own small table and touches nothing the fold owns.
4. **The API never computes dollars from tokens.** It reads `cost_effective_usd_micros` (MODES §3.1). Re-deriving would produce a second, quietly different number and defeat the whole point of the true-up writing one. `GET /v1/pricing/models` still serves the table, for display.
5. **Structured JSON logs (`api.request`): route, duration, session/user ids — never payload content.** Same posture as every other module.

**Auth in v1: none, deliberately.** Single-user, local/PoC (BLUE §9.3). Follow the uploader's precedent and leave an explicit seam comment where a FastAPI dependency slots in, so the absence is a recorded decision rather than an oversight. `org_id` and `vendor` are still carried through every query and response (BLUE §9.5) — the org view must be a new screen, not a rewrite.

---

## A0 — Module skeleton

**New:** `src/server/api/` — `__init__.py`, `app.py`, `config.py`, `queries.py`, `service.py`, `errors.py`, `cli_serve_api.py`; `config/api.yaml`; `tests/server/api/`

- [ ] Mirror `src/server/uploader/`'s layout: pydantic config with the defaults → YAML → env layering (`uploader/config.py` is the model to copy, TODO comment included — it is waiting on the same shared loader).
- [ ] `config/api.yaml`: http host/port, `storage_config`, `queries_dir`, the thresholds this module reads (`insights.min_sessions` default 5, `story.min_sessions_for_band` default 5, `dashboard.flags.*`, `dashboard.precedence.*`).
- [ ] `cli_serve_api.py` — named per PLAN. Siblings use `cli_serve.py` and `python -m server.api.cli_serve` would already be unambiguous; PLAN chose the explicit form, so use it and do not mix the two.
- [ ] `/healthz`, and a `create_app(config)` factory so tests build an app without a live server.

**Done when:** `python -m server.api.cli_serve_api` starts and `/healthz` answers.

## A1 — The response envelope (do this before any endpoint)

**New:** `src/server/api/envelope.py`

DASH §8.1's three rules, implemented once. Written per-endpoint they will drift by the third one, and the drift is invisible until the UI renders the wrong state.

- [ ] **Every response carries mode.** Session-scoped: `datasource`, `vendor`. Aggregate: `modes_present: [...]`, `sessions_by_mode: {...}`.
- [ ] **Every number carries its source.** Any figure resolved through the precedence chain reports which source won; any dollar reports `cost_basis` and the session's `cost_complete`.
- [ ] **A capability is absent, not empty.** `null` plus `{unavailable: {block, requires}}` — **never `0`, never `[]`.** Zero is a measurement; null-with-a-reason is the truth, and the UI picks between its `unavailable` and cold-start states on exactly this distinction.

- [ ] One helper each: `unavailable(block, requires)` and `sourced(value, source)`. Make it *easier* to return the correct shape than an ad-hoc dict, or endpoints will hand-roll it.
- [ ] A test that asserts every endpoint's response validates against the envelope — the cheap guard against rule 3 being quietly skipped on the endpoint written last.

**Done when:** the envelope helpers exist with tests, before any endpoint uses them.

## A2 — Precedence resolver *(moved here from PM.6)*

**New:** `src/server/api/precedence.py`

MODES §3.3. Moved out of PM because nothing there consumed it and config with no reader drifts from the code it governs.

- [ ] Ordered chains in `config/api.yaml`, resolved per session, first available wins:

```yaml
dashboard:
  precedence:
    tokens: [otel_measures, otel_ledger, jsonl_models]
    cost:   [otel_vendor, estimated]
```

- [ ] `resolve(session_row, chain) -> (value, source_name)`. Never returns a bare number — the source is half the answer (rule A1.2).
- [ ] **Aggregates resolve per session, then sum.** Resolving once for a whole timeframe would pick one source for a mixed-mode range and silently drop the sessions that source cannot see. The reported source for an aggregate is therefore a *set*, not a scalar.

**Done when:** a mixed-mode fixture range sums correctly and reports both contributing sources.

## A3 — `GET /v1/users/{id}/overview?from&to`

**New:** queries in `config/queries/dashboard.yaml`; `service.overview()`

- [ ] Timeframe predicate: sessions **overlapping** the window (`started_at < to AND COALESCE(ended_at, last_event_at) >= from`), stated once as a named query fragment and reused — an inconsistent window across widgets is the kind of bug that gets debugged twice.
- [ ] Z1 strip (DASH §3.1) via A2; cost from `cost_effective_usd_micros`; active time preferring `otel.measures.active_time_s`.
- [ ] Z2: model split from the ledger (works in every mode), `by_query_source` (OTel modes; `unavailable` otherwise), provenance mix (`null` → cold start when the fold has not run; `unavailable` in `otel_only` — **these are different responses** and A1 exists to keep them apart).
- [ ] Z3 top lists: tools, skills, MCP servers, repos — aggregate over `session_info`; `unavailable` in `otel_only`.
- [ ] Z5 scorecard rollup: gated on `insights.min_sessions`, and **omitted entirely for non-`claude_code` vendors** (PRACT applicability gate).
- [ ] `modes_present` / `sessions_by_mode`: one `GROUP BY datasource`.

**Done when:** all three modes return a coherent overview and `otel_only` returns Z1+Z2 with `unavailable` for the rest.

## A4 — `GET /v1/users/{id}/sessions`

- [ ] Params: `from`, `to`, `sort`, `flags`, `vendor`, `limit`, `offset`.
- [ ] **Sorts map 1:1 to scalar columns** — `recent`, `cost` (the PM.1 column), `tokens`, `duration`, `compactions`, `subagents`, `errors`. Reject an unknown sort key rather than falling back to a default; a silently ignored sort looks like broken data.
- [ ] Flags derived at query time from scalars + `dashboard.flags.*` thresholds (P1.5 — badge booleans are deliberately not stored, so thresholds stay tunable without a refold).
- [ ] Each row: `glyph` (`info.story.curve`), `shape`, `title`, `repo_key`, badges, tokens, cost + basis, `datasource`, `vendor`, `jsonl_lag_s`.
- [ ] **`otel_only` rows carry no glyph, title, repo or badges** and must be distinguishable from a JSONL row that merely lacks a title. The `datasource` field does that; do not signal it by absence.

**Done when:** a mixed-mode fixture list paginates, every sort round-trips, and reduced rows are identifiable.

## A5 — `GET /v1/sessions/{id}`

- [ ] The §4.2 metrics panel from one `session_info` row: tokens by model, turns, tools, skills, subagents, files, compactions, cache, errors.
- [ ] Cost block fields surfaced as specified: the three dollar figures, `cost_basis` counters, `cost_complete`, `by_query_source`, `calibration` **excluded** — it is internal (MODES §3.1) and must not reach a response body at all.
- [ ] Provenance block when present, `unavailable` in `otel_only`.

**Done when:** all three modes return a well-formed detail payload and `calibration` appears in none of them.

## A6 — `GET /v1/sessions/{id}/timeline` — the heavy one

**New:** `src/server/api/story.py`

Everything else in P2 is SQL and shaping. This is computation, and it is the task most likely to overrun — give it its own PR.

- [ ] **404 in `otel_only`.** There is no blob to condense. A4 already withholds the link; this is for anyone who guesses the URL.
- [ ] Condensed ordered events: `[{ts, kind, lane, dur, tokens?, label, ref}]`, subagent lanes nested. Cap ~2k entries by collapsing runs of same-tool calls into one entry with `count`; `?from_ts&to_ts` returns the uncollapsed range for zoom.
- [ ] **Beat detection** (STORY §4) — read-path, from `signal_blob.events`, so heuristics iterate without refolds. Eleven beat kinds, each `{kind, t0, t1, evidence, confidence}`. **Revision loop is the only inference**; everything else is a fact and must be labelled as such, because the "mark as expected" affordance and the tone posture both hang off that distinction.
- [ ] **Acts**: beats grouped into 2–5 chapters with durations and per-act cost.
- [ ] **Narrator line**: deterministic string templates from the beat sequence (STORY §3.2). **No LLM, and no session content** — the line is assembled from beat kinds and counts.
- [ ] **Personal band**: p25/p75 cumulative-vs-elapsed envelope from the user's trailing sessions. Two decisions to make here, not to improvise:
  - Curves are ≤64-point JSONB arrays on a per-session elapsed axis, so they must be **resampled onto a common grid before percentiles mean anything**. Do it in Python in `story.py`, not SQL — it is array work, and putting it in SQL buys nothing and costs readability.
  - **Compute the band only over sessions that have a curve.** In a mixed-mode history, `otel_only` sessions have none, and including them as absent would quietly bias the envelope (STORY §7.4). The same rule applies to anchors and to `min_sessions_for_band`'s count.
- [ ] `band: null` below `story.min_sessions_for_band` — the cold-start state, not `unavailable`: it resolves as the user accumulates sessions.

**Done when:** the reconciliation fixture produces a plausible beat sequence, a grammatical narrator line, and a band that hides below the sample gate; a `both`-mode session mid-lag still returns without error.

## A7 — `GET /v1/users/{id}/insights?from&to`

- [ ] Computed on read for now (persistence is P5). Phase-1 set: DASH §6 #1, 3, 4, 5, 6, 7, 8 + **#26, #27** (OTel modes) + PRACT §3 #14, 15, 18, 22.
- [ ] Card shape: `{insight_id, kind, mode, observation, evidence, action_text, est_tokens, est_usd, sessions[]}`. Ranked by estimated recoverable spend.
- [ ] **Filter the catalog by the caller's mode. An insight uncomputable in this mode is omitted, never returned as "0 found"** — absence of signal is not absence of the phenomenon, and a zero here is a false statement about the user's practice.
- [ ] Correlation-backed cards need `insights.min_sessions` (default 5); below it, the citation-only fallback form (PRACT §4.3).
- [ ] **Every string here goes through the STORY §2 lexicon.** This endpoint generates the copy most likely to sound like grading. Internal metric codes (F2, K, "re-read waste") must never appear in a response body — the presentation layer owns the vocabulary, and it cannot fix what the API already named badly.
- [ ] Computed-on-read over a 90-day range touches every session in it. Measure it once on a realistic fixture set; if it is slow, say so and let P5's persistence fix it rather than adding a cache here.

**Done when:** each mode returns only its computable insights, the min-sample fallback works, and no response contains a lexicon violation or an internal code.

## A8 — The remaining endpoints and the static mount

- [ ] `POST /v1/sessions/{id}/feedback` — `{beat_ref, verdict}` into a new `feedback` table (small model + dataclass, no-migration stance). The one write in this module.
- [ ] `GET /v1/pricing/models` — the active price table from `config/pricing.yaml`, for display only.
- [ ] `app.mount("/", StaticFiles(...))` serving `src/server/api/static/` for P3. Mount it **last** so it cannot shadow `/v1/*`.

**Done when:** feedback round-trips, the price table serves, and a placeholder `index.html` loads without shadowing the API.

## A9 — Tests and logging

**New:** `tests/server/api/`

- [ ] Endpoint tests against a seeded DB with folded fixture sessions **in all three modes** — the same fixture set PM.7 builds. An API tested only against `both` grows mode-awareness as an afterthought.
- [ ] Envelope conformance test across every endpoint (A1).
- [ ] `unavailable` vs cold-start vs empty asserted **distinctly** per block. These three are one substitution away from each other and the failure is a false sentence on screen, not an exception.
- [ ] Service-layer unit tests without a FastAPI client, which ground rule 1 is what makes possible.
- [ ] `api.request` structured logging: route, duration, ids. Assert no payload content is logged — the same posture the rest of the system holds.

**Done when:** `pytest tests/server/api` green; `python -m server.api.cli_serve_api` serves overview JSON for a fixture user in each mode.

---

## Sequencing

```
A0 ──► A1 ──► A2 ──┬──► A3 ──► A4 ──► A5 ──► A8
                   └──► A6 (own PR)      A7
                                    A9 alongside
```

- A1 before every endpoint. Retrofitting the envelope means touching all seven.
- A6 is the long pole: beat detection, condensation and the band are three separate pieces of real computation. Do not bundle it with A3–A5.
- A7 depends on A3's aggregates for its evidence lines.
- Suggested PRs: (A0+A1+A2), (A3+A4+A5), A6, A7, (A8+A9).

## Out of scope, recorded

- **Auth and multi-user.** v1 is unauthenticated by decision (BLUE §9.3). The seam is a FastAPI dependency; the comment marking it is required, the implementation is not.
- **Insight persistence and dismiss/track state** — P5. A8 wires the feedback endpoint; whether an insight's dismissal survives a reload is P5's problem.
- **Provenance endpoint contents** — `GET /v1/sessions/{id}/provenance` returns `info.provenance` when P4 populates it. Until then it is `null` with cold-start, and A5 treats it the same way.
- **Any dashboard read of `otel_*` tables.** The cost block is the surface (OTEL §8). If an endpoint seems to need the staging tables, the true-up is missing a rollup and that is a PM-shaped change, not an API one.
