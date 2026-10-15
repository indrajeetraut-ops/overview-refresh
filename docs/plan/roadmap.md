# Implementation Roadmap (M0–M5)

**Purpose:** the milestone map for the whole build. M0–M1 have a detailed task-level plan
(`2026-07-10-m0-m1-backfill-proof-point.md`); **each later milestone gets its own detailed
plan written at milestone start** (using the same planning method), because task-level detail
written months ahead goes stale. Component references (C1–C11) point into
`component-design-implementation-plan.md`; architecture references point into
`../design/coding-econ-design-draft.md` and `../design/Task-Classification-and-Analysis.md`.

**Planning docs convention:** everything planning-related lives in `docs/plan/` — detailed
plans named `YYYY-MM-DD-<milestone>.md`, plus this roadmap and the component design.

| Milestone | Delivers | Detailed plan |
|---|---|---|
| M0–M1 | Backfill proof point: Claude Code JSONL → sessions + list costs | `2026-07-10-m0-m1-backfill-proof-point.md` ✅ written |
| M2 | Live tail + second vendor + rollups | write at M2 start |
| M3 | Classifier worker v0 (classification + behavior analysis) | write at M3 start |
| M4 | Git correlation + money completion | git acquisition half ✅ `2026-08-25-git-integration-implementation.md`; money half + correlation (pr-work-classification.md) still open |
| M5 | Hardening + packaging | write at M5 start |

---

## M2 — Live tail + second vendor (target: weeks 5–6)

Goal: a live Claude Code session visibly ticks upward in `session_metrics` within ~30 s
(parent §10.3); Codex sessions flow through the same pipeline.

Task outline (~12–15 tasks when detailed):

1. Watchdog-based file watching wired into the collector runtime (`cli_collect`); fallback
   poll tick (parent §11.1).
2. Rotation/truncation handling live: identity change resets checkpoint (extends Task 11's
   `file_identity` into the runtime loop).
3. Torn-line + partial-flush soak test: writer process appends while collector tails.
4. Codex adapter (`collector.adapters.codex`): date-organized JSONL discovery; reuses
   `jsonl_reader`; its own normalizer map + golden fixtures + content-canary tests.
5. Liveness close-out and reopen in the stream worker (`stream/liveness.py`, parent §11.4):
   idle window config, `session_end(reason=idle_timeout)` synthesis, late-event reopen test.
6. Rollup maintainer (`stream/rollups.py`): `rollups_daily` upserts; property test — rollup
   equals recompute-from-scratch on fixtures.
7. Ingest hardening: gzip enforcement, batch size limits, per-org rate guard (config).
8. Dedupe soak: duplicate-heavy replay (10× same spool) leaves state identical.
9. Latency measurement harness: file-append → `session_metrics` update timestamp; assert
   p95 < 30 s locally.
10. Collector config file (`~/.talium/collector.toml`) + `.env` credentials; opt-out switch
    honored (parent §9).

Acceptance: live session demo per parent §3.2 step 8; Codex fixture parity with Claude Code
metrics; all M1 tests still green.

## M3 — Classifier worker v0 (target: weeks 7–8)

Goal: task rows with labels + behavior measures on replayed fixture sessions, meeting the
companion doc's v0 bar (MISC visible, coverage reported); classifier spend metered.
Everything per `Task-Classification-and-Analysis.md` §4–§6.

Task outline (~15–18 tasks when detailed):

1. `tasks` + `task_measures` + `classifier_config` migrations (C6 additions).
2. Segmenter (`classify/segmenter.py`): boundary markers (time gap after completed cadence,
   plan-mode transitions, git commit/PR push, sub-agent spawn → child task) — table-driven
   tests per boundary type; over-segmentation-is-cheap bias encoded in thresholds config.
3. Behavior graph builder (`classify/behavior_graph.py`): action–resource graph from events
   (parent_id/seq edges, shared file/verb edges, exit-status edges).
4. Behavior measures (`classify/measures.py`): loop depth per file before green, no-progress
   repeats via `input_hash`, divergence, fan-out stats — one test per measure with a
   hand-built event sequence.
5. Shipped signature set v1 as config data (the 8 signatures, companion §5 table) +
   signature engine (`classify/signatures.py`): first-match-wins, fixed confidence,
   table-driven test per signature.
6. Tier 0 label decisions → task rows stamped `classifier_version`.
7. Task digest builder (`classify/digest.py`): signature summary + tool/file summary +
   intent text under opt-in flag; snapshot tests; token-length budget test.
8. LLM tier (`classify/llm_tier.py`): batched calls, cached taxonomy prefix, temperature 0,
   forced choice + confidence; recorded-response mocks; retry/permanent-failure handling;
   `pending_llm` re-claim path.
9. Classifier spend metering: each call emits a system-cost record visible in rollups
   (companion §6 requirement).
10. Classifier worker type in `runner.py` (`--type classifier`; claims sessions whose spans
    closed).
11. Dogfood gold set bootstrap: label ~50 of our own sessions; eval script comparing
    classifier output vs. gold (per-label precision/recall, coverage, MISC rate).
12. Degraded no-text mode test: same fixtures with intent text stripped; accuracy reported
    separately.

Acceptance: frozen replay set → task rows; gold-set metrics printed; rerun with config vN+1
relabels history (replay test); LLM unavailability degrades to MISC+pending, never blocks.

## M4 — Git correlation + money completion (target: weeks 9–10)

Goal: PRs/commits linked to sessions with confidence scores; billed cost respects plan
depletion; vendor totals cross-checked. Parent §11.3, §4.5 cost engine, §4.1 plan poller.

**Git acquisition (tasks 1–2 below): done, 2026-08-25.** Superseded the task outline as
originally written here (`git_event` envelope events, `ingest/github_map.py`) with the finalized
design in `docs/modules/git-source.md` and its own implementation plan,
`docs/plan/2026-08-25-git-integration-implementation.md` (Part A, phases A1–A8) — GraphQL as the
primary historic reader, the four-table schema (`git_installations`/`git_repos`/`git_artifacts`/
`git_deliveries`), a host route rather than an envelope event, tenant binding, and
author→user identity, all with real test coverage. **What tasks 4–6 became:** the correlator
(candidates/signals/scoring against `session_git_links`) is now its own design,
`docs/modules/pr-work-classification.md`, **not built** — it is Part B of the implementation plan
above, unblocked and pointed at `git_artifacts` now that acquisition is real. Money completion
(tasks 7–10) is unrelated to git and still pending as originally scoped.

Task outline (~12–15 tasks when detailed):

1. ~~`git_artifacts` + `session_git_links` + `plans` + `plan_usage` migrations.~~ →
   `git_artifacts`/`git_installations`/`git_repos`/`git_deliveries` done (git-source.md §9);
   `session_git_links`/`plans`/`plan_usage` remain, owned by pr-work-classification.md /
   the cost-engine work respectively.
2. ~~GitHub webhook receiver in ingest (`ingest/github_map.py`): signature verification,
   metadata-only reduction to `git_event` events; fixture payloads for pr_opened/merged/push.~~ →
   done as `src/server/ingest/git/` (webhook route, `map_github.py`, `worker/git_map.py`) +
   GraphQL/REST backfill readers; fixture payloads in `tests/fixtures/github/` (synthetic —
   see its README for why, and what replacing them with a real recording still needs).
3. Scheduled worker type scaffolding in `runner.py` (timer-driven, no claim). — unrelated to
   git; still open.
4. Correlator candidates (`git_correlator/candidates.py`): repo + author-mapped user +
   time-window overlap. — **now pr-work-classification.md's B2**, not built here.
5. Correlator signals (`git_correlator/signals.py`): commit-trailer/token exact match; file
   overlap weighted toward session-written files; time proximity. Fixture repo timeline with
   known ground truth. — **now pr-work-classification.md's B2**, not built here.
6. Scoring + link persistence (`git_correlator/scoring.py`): transparent weighted sum,
   weights in config; ambiguous links stored with scores; idempotent re-runs. — **now
   pr-work-classification.md's B2/B6**, not built here.
7. Plan-usage poller adapter (`collector.adapters.plan_usage`): vendor endpoint poll with
   local credentials, `plan_usage_snapshot` events; disclosed-use config flag.
8. Cost engine completion: plan-attributed + billed with burst after depleted windows
   (synthetic snapshot fixtures covering: under budget, depletes mid-session, resets).
9. Usage-API reconciler (`scheduled/usage_reconciler.py`): recorded vendor API fixtures,
   drift report + alarm threshold.
10. Cursor adapter spike (time-boxed): read-only sqlite polling against a real Cursor
    install; go/no-go writeup appended to this roadmap (parent §14.3).

Acceptance (git half): a real PR opened/reviewed/merged on a test org appears as `pr`+`review`
(+`commit`) artifacts with correct state, merge-transition, and file list within seconds; a
year of history backfills through the same mapper via GraphQL; running the whole pipeline twice
changes nothing; `coverage_counts` reports a real attributed/unattributed number. Fixture-replay
correlation-linking acceptance ("82% of PRs attributed"-style, trailer precedence) moves to
pr-work-classification.md's own acceptance criteria once Part B is built.
Acceptance (money half, unchanged): billed cost switches to burst after a depleted snapshot;
reconciler flags a seeded 10% drift.

## M5 — Hardening + packaging (target: weeks 11–12)

Goal: installable on a clean machine with zero Python setup; production failure modes
handled; standing regression gate.

Task outline (~8–10 tasks when detailed):

1. PyInstaller single-binary collector per OS (macOS first); signing checklist doc.
2. Poison-batch isolation in the worker (`processed_error` after N failures + alarm log).
3. Spool cap + eviction alarm behavior under long offline soak.
4. Import-boundary linter in CI (collector never imports server; both import only common) —
   adopt if M2–M4 reviews found violations (roadmap Q5).
5. Replay-set integration suite as the standing regression gate in CI (backfill fixtures →
   full stack → golden sessions/tasks/links).
6. Operational runbook: deploy (3 containers + Postgres), migrate, seed, monitor
   (queue-depth row count, drift alarms, spool alarms).
7. Enrollment stopgap documented (manual token issuance) + open design item for real
   enrollment (parent §14.2).

Acceptance: clean-machine collector install → events flowing, no Python installed; kill-mid-
batch chaos test leaves no lost or duplicated state; CI runs the full replay gate.

---

## Deferred beyond M5 (named seams, do not build early)

- **Local ML classifier tier** — companion doc §6, config-flip slot between Tier 0 and
  Tier 1; requires accumulated Tier 1 labels.
- **Hunk-range git correlation** — C10 v2 seam; server-side diff fetch, extract ranges,
  discard content (parent §11.3).
- **API + Dashboard (P4)** — excluded from this plan by scope; reads the tables C6 already
  provides.
- **Enrollment/SSO, fleet auto-update, governance hooks, quality scoring (RQ-03)** — each
  has a named seam in the parent design (§13, §14).
