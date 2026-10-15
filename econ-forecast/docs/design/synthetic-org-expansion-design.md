# Synthetic Org Expansion — Design

**Status:** Draft for review. Not approved; no implementation yet.

**Last updated:** 2026-09-18

**Branch:** `feat/synthetic-org-data` (off `feat/mvp-implementation-#52`, with `forecast` merged in).

**Depends on:** [synthetic-data-generation-design.md](synthetic-data-generation-design.md) (the "series generator", Layer 1 below). Read it first.

## 1. Purpose

The MVP dashboard currently runs on one real team: 2 developers, 1 repository, ~90 sessions, ~25 PRs. Distributions, peer comparisons, team roll-ups, and forecasts are degenerate at that size, so the UI cannot be judged. We need a **realistic multi-team engineering org** in Postgres: several teams, several repositories, a few developers per team, months of history, and the GitHub activity that goes with it.

The forecast branch already has a deterministic generator for **daily token usage** (`usage.jsonl`). The cost-forecast model is built and validated against that output, and will later be integrated into the mainline. That generator therefore stays the **single source of truth for spend**. Everything the dashboard needs beyond daily totals is *expanded downward* from it.

### Goals

- G1. Produce a multi-team org dataset that loads into the mainline schema. The MVP dashboard (single-user and manager surfaces) renders every view with non-degenerate data.
- G2. Keep the series generator's `usage.jsonl` contract and forecasting behavior unchanged.
- G3. **Rollup exactness.** Summing the generated sessions back to daily grain reproduces `usage.jsonl` exactly. The dashboard and the forecast therefore describe the same org.
- G4. Simulate realistic behavior: teams that differ in efficiency, work shape, and habits, not uniform noise. Do not clone the existing 2-developer data.
- G5. Make scale configurable: teams, users per team, repositories, days, PR throughput.
- G6. Keep generation deterministic and loads idempotent. The data lives in a separate database, isolated from real data.

### Non-goals (v1)

- Raw Claude Code JSONL transcripts or GitHub webhook payloads pushed through ingest. The loader writes the stored tables directly (§7).
- OTel data (`otel_*` tables). Every synthetic session is `datasource = jsonl_only` (open question Q2).
- Subscription billing (the series generator is API-only).
- Changing the forecast model.

## 2. Architecture

```
Layer 1  (exists, econ-forecast/synthetic)                  Layer 2  (new)                       Loader (new)
users.yaml  ─► user-create   ─► users.jsonl ─┐
roster.yaml ─► roster-create ─► teams.jsonl ─┤
series.yaml ─► series-create ─► usage.jsonl ─┼─► org-expand (org.yaml) ─► org dataset dir ─► cli_load_synthetic ─► econ_synth (Postgres)
                                             │     sessions, session_info, git_*, links,        (directory.sync + table upserts)
                         forecast model ◄────┘     directory records, budgets
```

- **Layer 1 (series generator).** Unchanged apart from one opt-in identity fix (§3.1). It decides *who spends how much, on which project, with which agent/model, on which day*.
- **Layer 2 (org expansion).** A new fourth operation, `org-expand`. It reads a completed series dataset plus `org.yaml` and emits one JSONL file per target table. It decides *how that spend was shaped into sessions and what work it produced*. It never changes a token total.
- **Loader.** A new mainline CLI that writes an org dataset into a **dedicated database** (`econ_synth`). The API is then pointed at that database.

Layer 2 lives in `econ-forecast/src/forecast/synthetic/org/` beside the series generator it extends. It imports mainline helpers from `src/` (`common.model_pricing`, `common.git_ident`), which the forecast module's PYTHONPATH already includes. The loader lives in mainline `src/server/storage/` because it depends on the ORM schema and the directory module.

## 3. Layer 1 changes

### 3.1 Identity consistency (opt-in)

Faker samples the name and email independently ("Michelle Morrow" / `romerojeffrey@acme.dev`). That looks wrong on every screen. Add an optional `users.identity_style: consistent | independent` (default `independent`, so existing fixtures and regression tests keep their bytes). With `consistent`:

- email = `<first>.<last>@<org_domain>`, lower-cased and ASCII-folded, with a numeric suffix on collision;
- a new optional `UserRecord.github_login` = `<first><last-initial>`, same folding and collision rule.

This is the only Layer 1 code change. Everything else is expressed through configuration.

### 3.2 Configuration conventions for an org series

These need no code changes; the example config (§8) follows them:

- **Projects are repositories.** Each `projects[].name` in `series.yaml` maps 1:1 to a repository in `org.yaml` (§5.1). A team's `project_mix` is therefore "which repos this team works in".
- **Agent keys use mainline names.** `claude_code` (and `codex` only if Q1 resolves it), because Layer 2 copies the key into `sessions.agent` verbatim.
- **Every model must be priced** in the `common.model_pricing` catalog (a dated id resolves to its registry id). `org-expand --validate-only` rejects an unpriced model.
- **Org events use the existing event system:**
  - *New hire:* a user-targeted `zero_periods` entry from series start to the join date. Layer 2 adds an onboarding ramp (§5.6).
  - *Release crunch:* a team `dated_spikes` entry.
  - *Holiday:* an org `zero_periods` entry.
- **Relative dating** is a wrapper concern (§7.4), because `start_date` must stay a literal date for determinism.

## 4. Layer 2 record contracts

`org-expand` writes a dataset directory with the same `DatasetWriter` conventions as Layer 1: resolved `config.yaml`, `report.md`, a `manifest.json` written last, input snapshots, and a refusal to overwrite. Each file maps to exactly one mainline table. Field names match the ORM columns so the loader stays a thin mapper.

| File | Target table | Notes |
|---|---|---|
| `directory.jsonl` | `users`, `identity_links`, `teams`, `team_members`, `role_assignments` | `DirectoryRecord` / `TeamRecord` wire shape (IDENT §3.2), applied through `server.directory.sync` |
| `user_prefs.jsonl` | `users.timezone`, `users.github_user` | Fields outside the directory's own set |
| `git_installations.jsonl`, `git_repos.jsonl` | same | One installation for the synthetic org |
| `git_artifacts.jsonl` | `git_artifacts` | kinds `issue`, `pr`, `commit`, `review` |
| `git_artifact_events.jsonl` | same | opened / closed / merged / reopened |
| `pr_issue_links.jsonl` | same | |
| `sessions.jsonl` | `sessions` | root and subagent sessions |
| `session_info.jsonl` | `session_info` | all scalar columns plus `models`, `tools`, `files`, `skills`, `subagents`, `turns`, `pr_list`, `info` |
| `session_git_links.jsonl` | same | |
| `pr_classification.jsonl` | same | See Q3 |
| `repositories.jsonl` | `repositories` | Session-side repo identity |
| `budgets.jsonl` | same | Optional, from `org.yaml`. No `allowances.jsonl` since 2026-09-26: a seat's allowance is its team budget / members (code-econ `docs/plan/2026-09-26-cost-based-seat-utilization.md`) |

### ID schemes (must match mainline resolution paths)

- Session-side repo id: `github:<owner>/<name>` with `repo_id_provenance = "remote"` (the `common.git_ident` scheme).
- Git-side repo id: `github:<repo_num>`. `git_repos.full_name = <owner>/<name>`, so `aggregate.resolve_git_repo_ids` bridges the two schemes.
- Artifact ids follow git-source: `github:<repo_num>:pr:<n>`, `:issue:<n>`, `:commit:<sha>`, `:review:<id>`.
- `repo_num`, SHAs, and session ids are **derived deterministically** (UUIDv5 / SHA-1 of seeded keys), never random-at-load.
- Every synthetic row carries a marker: `sessions.info_blob.synthetic = true` and `info.synthetic = {generator_version, run_id}` on artifacts, so tooling can recognise synthetic data.

## 5. Expansion algorithms

All randomness uses the Layer 1 `rng_for(seed, domain, *keys)` pattern with a new `org.seed`. Each concern gets its own stream (sessions, work, habits, text, reviews…), so changing one knob does not reshuffle unrelated output.

### 5.1 Repositories and modules

`org.yaml` declares each repository: `project` (Layer 1 name), `full_name`, `default_branch`, `languages`, and weighted `modules` (path prefix plus file-extension mix). Modules drive file paths in `session_info.files`, `git_artifacts.files`, segment `files` counts, and `pr_classification.module`.

### 5.2 Work model: spend is converted into work

This is the backbone that makes cost-per-PR, $/KLOC, peer compare, velocity, and features meaningful.

For each **user × repository**, walk that user's active days in order. Each day's token total for the repo (from `usage.jsonl`) is split into:

1. an **unlinked share** (`work.unlinked_share`, per team): exploration, questions, and ops. It becomes sessions with no PR link;
2. the rest, which is **charged to the user's current work item(s)**. Each item is a PR in progress with a sampled **token budget**:

   `budget = size_loc × tokens_per_loc(user)`

   - `size_loc` comes from a per-team long-tailed (lognormal) PR-size distribution.
   - `tokens_per_loc(user) = calibrated_base × team_efficiency × user_efficiency`.

When an item's budget is used up, its PR is finalised (§5.4) and the next item starts. Up to `work.parallel_items` items can be in flight, so spend is split across them.

**Calibration.** `calibrated_base` is solved once per team so that the *expected* merged PRs per developer per week equals `work.target_merged_prs_per_dev_week`, given that team's realised linked spend and PR-size distribution. Throughput stays configurable (G5), while differences between users emerge from their spend and efficiency. For example, a heavy spender with low efficiency ships fewer lines per dollar. That is exactly the signal peer compare looks for.

**Features.** Consecutive items for the same repo are grouped into features (issues). The number of PRs per feature is drawn from `work.feature_size`, and a feature may span teammates on the same team (`work.shared_feature_prob`). Each feature's issue linkage is picked by `work.issue_link_mix`:

| Linkage | Source of the link |
|---|---|
| GitHub issue | `graphql` link |
| Branch | `feat/<issue>-slug` |
| Title | `(#<n>)` |
| None | Unlinked; exercises the H5 branch/label clustering |

Issues also form a backlog: some are opened and never started, and some are closed as not planned.

### 5.3 Sessions: splitting a user-day into sessions

For each (date, user, repo, agent) usage group:

- **Number of sessions.** Sampled from the group's input total divided by a profile-level mean session size (`sessions.mean_input_tokens`), clamped to `[1, sessions.max_per_day]`.
- **Token split (exactness).** Each token type (`input`, `cache_write_5m`, `cache_write_1h`, `cache_read`, `output`) of each model record is split across the group's sessions with Layer 1's `allocate_integer` and jittered weights. Every session therefore holds an exact integer share, and the shares sum back to the record. Each session gets one dominant model plus minor shares, which keeps `dominant_model` and model-fit meaningful.
- **Cache writes.** Layer 1 gives each model record its own `cache_write_5m` and `cache_write_1h` totals ([cache-write contract design](./cache-write-contract-design.md)); Layer 2 no longer derives them. Per request, the session's context shape (the cache-read curve) decides only *which request* receives how much of each write total; `input` is spread with equal weights and no minimum, so a request can have `input = 0`. A request keeps the two writes separately (`cache_write` is their sum); `session_info.models` carries the sum as one `cache_write`, as real sessions do, and the 5m/1h split lives in the in-memory ledger. The cache lifetime (`sessions.ttl_s`) defaults to 3600 s; resume gaps scale from it, so resumes become rare (about 65% fewer on the default org config).
- **Subagents.** With `sessions.subagent_prob` (scaled by team habit), a root session spawns child sessions carved out of its own allocation. Children are separate `sessions` rows with `parent_session_id` set and their own `session_info`. **Every session row counts once in the rollup** (§6, I1). This must be confirmed against how the fold writes parent vs child `tokens_total`/`models` before implementation. If parent rows are inclusive, the rollup must count roots only and children must be carved out accordingly.
- **Timing.** `started_at` is placed in the user's working hours in the series timezone: a per-user work window with jitter and a lunch gap. Sessions on the same day do not overlap. Duration scales with tokens. A session never crosses local midnight, so its local start date equals the usage record date.
- **Linkage.** A linked session's tokens go to the work item(s) it served. Sessions for a PR start before the PR is opened (implementation), and some fall after it (review-fix rework, §5.4).

### 5.4 PR lifecycle and GitHub artifacts

For each finalised work item:

| Artifact | How it is derived |
|---|---|
| Commits | Placed at the linked sessions' times on branch `feat/…` or `fix/…`. `stats` split the PR's LOC. SHAs are deterministic. |
| PR | `created_at` falls when `pr.open_at_fraction` of the budget is spent. `stats` include additions, deletions, changed_files, commits, review_count, comment_count. `files` come from the repo's modules. Labels and title come from the change type. `commit_shas`, `merge_commit_sha`, and `integration_method` (per-repo mix) are set. |
| Reviews | Written by teammates who are not the author. Time to first review is sampled per team. `changes_requested` rounds (per-team rate) each add post-open rework sessions and commits, which the post-open churn query picks up. |
| Outcome | Merged, abandoned (`closed`, not merged, so a recall candidate), or reopened (a `git_artifact_events` reopen row). All three rates are per team. |
| Reverts | With `pr.revert_rate`, a later PR by the same team reverts a merged one (`reverts_sha`, the `revert` change type). |
| `session_git_links` | `method = "sha"` with confidence 1.0 for sessions that produced a commit; `method = "heuristic"` with confidence 0.5–0.9 for the rest. |
| `pr_issue_links` | Per §5.2. |

### 5.5 `session_info` content

**Scalar columns:**

| Column | Value |
|---|---|
| `models` | `{model: {input, output, cache_read, cache_write}}` |
| `tokens_total` | Derived from `models` |
| `cost_usd` / `cost_effective_usd_micros` | Priced with `common.model_pricing.Pricer.price_tokens` (through `OrgPricer`) at the session date |
| `datasource` | `jsonl_only` |
| `repo_id` / `repo_id_provenance` | Session-side id, `remote` |
| Counters | `turn_count`, `long_turn_count`, `compaction_count`, `subagent_count`, `error_count` |

**The `info` blob.** Keys are exactly those the API reads (inventoried 2026-09-18 from `api/manager/aggregate.py`, `quality.py`, and `single_user/service.py` against the live stack):

| Key | What Layer 2 writes |
|---|---|
| `title` | Templated from work item and module, e.g. "Add retry budget to ledger sync". Unlinked sessions draw from an exploration / ops pool. |
| `repo` | `{cwd, git_branch, repo_id, repo_key, repo_id_provenance, repos[]}` |
| `cache.hit_ratio` | Computed from the session's own token split |
| `carry` | `{requests, mean_context, peak_context, carry_usd_micros, cache_read_tokens}`, consistent with `cache_read` and pricing |
| `channel_cost` | `{input, output, recache}: {tokens, usd_micros}`. The three channels sum to the session cost within rounding. |
| `cache_busts`, `resume_cost`, `interrupted` | Event lists and totals from the **habits** profile (§5.6), priced from the session's model. Always ≤ session cost. |
| `compactions`, `context_curve` | Consistent with each other and with `compaction_count` |
| `story` | `{shape ∈ steady_climb/sawtooth/cliff, curve, anchors}` |
| `segments[]` | `{index, t0, t1, label, confidence, turns, files, beats, models, repo_key, cost_usd_micros, carry_usd_micros}`. Labels come from the `worker/segments/session_type.py` vocabulary. Segment costs and models sum to the session's. |
| `practices` | `{interrupts, auto/manual_compactions, delegation_ratio, verified, …}`, driven by habits |
| Other | `reasoning_tokens`, `duration_ms`, `event_count` |

**Other JSONB columns:**

- `files`: paths from the repo modules, with lines added and removed. For linked sessions these sum to roughly the PR's LOC share.
- `tools`: a builtin tool mix per habits profile.
- `subagents`, `turns`, and `pr_list`: consistent with the rows above.

Shape rules are enforced by the invariants in §6, not left to convention.

### 5.6 Behavior profiles

Behavior profiles make the org look like an org rather than noise. Each team in `org.yaml` picks:

| Profile | Knobs | Dashboard signal it drives |
|---|---|---|
| `efficiency` | `team_efficiency`; per-user spread (lognormal σ) | $/PR, $/KLOC, peer compare |
| `work_shape` | PR-size distribution, feature size, parallel items, unlinked share | Cost-per-PR envelope, features, velocity |
| `review_culture` | Time to first review, changes-requested rate, abandon/reopen/revert rates | Quality, churn, reopen, recall views |
| `habits` | Cache-bust rate, idle-gap (resume) rate, interrupt rate, compaction tendency, subagent use, long-session share | Recoverable spend buckets, session story, practices |

**Onboarding ramp.** A user whose first active day comes after a leading user-targeted zero period gets `efficiency` and `habits` that improve linearly over `onboarding.ramp_days`. The spend itself stays as Layer 1 produced it.

### 5.7 Directory, roles, budgets

- An org root team is created, with the Layer 1 teams as children. Optional intermediate groups come from `org.tree`.
- Each team gets one `lead`. One `admin` (the "VP" demo viewer) is added as a synthetic user outside the Layer 1 roster, with no usage. The loader prints that user's id for the trusted viewer header.
- The `admin` user has no sessions. This does not affect G3.
- Optional per-team monthly budgets come from `org.yaml`. Seat allowances are derived from them by
  the dashboard (team budget / members, scaled to the viewed window); there is no separate token
  allowance to configure (removed 2026-09-26).

## 6. Invariants (acceptance criteria)

| # | Invariant |
|---|---|
| I1 | **Rollup exactness.** Group all session rows by (local start date, user, repo→project, agent, model) and sum each of the five token types (the loader sums `cache_write_5m + cache_write_1h` because the database has one `cache_write`). The result equals `usage.jsonl` exactly, record for record; `expected_rollup.jsonl` carries the five fields and the org manifest is `schema_version` 2. No sessions exist for user-days without usage. |
| I2 | **Determinism.** Same series dataset + `org.yaml` + versions → byte-identical record files. Changing only a `habits` knob leaves token splits, timing, and PR structure unchanged. |
| I3 | **Referential integrity.** See detail below. |
| I4 | **Temporal order.** Sessions lie within their local day and never overlap per user. Commit ≤ PR created ≤ first review ≤ merge/close. A linked session's start is ≤ merge time + rework window. Events are ordered per artifact. |
| I5 | **Cost consistency.** See detail below. |
| I6 | **Calibration.** Realised merged PRs per developer per week, over the whole series, is within ±25% of target for each team, for the example config and fixed seeds. |
| I7 | **Layer 1 untouched.** All existing `econ-forecast/tests` pass, and default-style users are byte-identical. |

**I3 in detail.** Every foreign-ish reference resolves inside the dataset:

- session → user, parent session, and repo (via `git_repos.full_name`);
- link → session and PR;
- review/commit → PR;
- PR → author, who has usage on that repo's project;
- `pr_issue_links` → an issue, when `issue_artifact_id` is set.

**I5 in detail.**

- `cost_effective_usd_micros` equals the priced `models`.
- `channel_cost` sums to the cost within ±1 micro per channel.
- The recoverable totals and `carry_usd_micros` each stay ≤ cost.
- Segment costs sum to the session cost.

## 7. Loader and operations

### 7.1 CLI

`src/server/storage/cli_load_synthetic.py`, wrapper `utils/load_synthetic.sh`:

```
python -m server.storage.cli_load_synthetic --dataset <org-expand dir> --dsn <postgresql+psycopg://…/econ_synth> [--replace] [--dry-run]
```

1. **Guard.** Refuse unless the target database name ends in `_synth`. There is no override flag, so pointing it at `econ` is impossible by construction.
2. **Verify** the dataset manifest (hashes, completion) before any write.
3. **Migrate** the target to head with the existing alembic path (`cli_db migrate`).
4. `--replace` truncates every table the loader writes, in the synth database only.
5. **Directory** via `server.directory.sync` (the real sole writer), then `user_prefs`.
6. **Remaining tables** in dependency order, using idempotent upserts keyed on natural PKs, in one transaction per table group with batched inserts.
7. **Post-load check.** Re-run the I1 rollup as SQL against the database and compare it with the manifest totals. Print the admin viewer id and row counts as a JSON summary. Structured JSON logs go to stderr.

Re-running the same dataset without `--replace` is a no-op: row counts are unchanged.

### 7.2 Serving the dashboard from `econ_synth`

The API reads `TALIUM_DB_DSN`, so no code change is needed. Run a second API process (or a compose override) with the synth DSN on its own port, and send the admin id as the viewer header.

**The worker must never be pointed at `econ_synth`.** Synthetic sessions have no raw events (`signal_blob = {"events": []}`), so a re-fold would blank their `session_info`. The loader README states this, and the `info_blob.synthetic` marker lets a future guard skip such rows.

### 7.3 Scale and performance

The example config (5 teams, 4–5 devs each, 5 repos, 120 days) is expected to produce:

| Item | Expected count |
|---|---|
| Sessions | ~6–8k (roots and subagents) |
| PRs | ~1k |
| Reviews | ~2k |
| Commits | ~5k |

Layer 2 streams day by day like Layer 1. `org-expand` should finish in under 1 minute and the load in under 2 minutes on a laptop. Both are verified and reported, not asserted in unit tests.

### 7.4 One-shot wrapper

`econ-forecast/utils/synth_org.sh --config-dir <dir> --out <dir> [--end-date YYYY-MM-DD|today] [--load <dsn>]` runs, in order:

1. `user-create`
2. `roster-create`
3. `series-create`, with `start_date` templated as end date minus `days` so the data ends "now"
4. `org-expand`
5. optionally, the load

## 8. Configuration

The example set lives in `econ-forecast/config/synthetic/org/`:

- `users.yaml`: count and identity style `consistent`.
- `roster.yaml`: teams and sizes, the scale knobs.
- `series.yaml`: days, profiles, project mix, and events (a new hire, a release crunch, a holiday).
- `org.yaml`: see the teams below.

`org.yaml` contains the repos and modules, the org tree, `work`/`pr`/`sessions` defaults, per-team behavior profiles, and budgets. It ships with four contrasting teams:

| Team | Repos | Character |
|---|---|---|
| **platform** | infra, shared libs | High efficiency, large PRs, low PR count, careful reviews, good cache habits |
| **payments** | payments-api | Moderate efficiency, strict review (many change requests), very low revert rate |
| **growth** | web-app, experiments | Many small PRs, fast merges, higher revert/abandon rate, weekend activity |
| **data** | data-pipeline | Heavy spenders, long sessions, poor cache habits (busts, idle-resumes), frequent compactions. This is the recoverable-spend story. |

Validation fails fast (exit 2) on:

- an unknown team, project, or repo reference;
- a project without a repo;
- an unpriced model;
- share sums that are not 1;
- a target PR rate ≤ 0.

`--validate-only` writes nothing.

## 9. Tests

| Area | Coverage |
|---|---|
| Layer 1 | Identity style `consistent` (format, collisions, determinism); default style unchanged (I7) |
| Allocation | Session split exactness under extreme values: 1 token, huge totals, zero-share models, one session vs `max_per_day` (I1) |
| Work model | Budget accounting conserves linked tokens; calibration hits target within tolerance (I6); parallel items; feature grouping; issue-link mix |
| Content | Every `info` key the API reads is present with the right type; cost and channel consistency (I5); segment sums |
| Integrity | I3 and I4 over a full small generated dataset |
| Determinism | I2, including the knob-independence case |
| Edge cases | Empty series (all inactive), a user with a single active day, a repo with no activity |
| Loader | Guard refuses non-`_synth` names; manifest tamper detected; idempotent reload; `--replace`; post-load rollup check. DB tests run only when `TALIUM_TEST_DSN` points at a `_synth` DB (same skip convention as directory tests). |
| API smoke | Against a loaded test database, manager overview, teams, projects, a team detail, a PR detail, and the single-user dashboard return 200 with non-empty payloads for the admin viewer and for one developer |

## 10. Open questions

**Resolution status (2026-09-18).**

- **Q3 resolved:** use the real `pr_classify()`, which runs on stored rows.
- **Q4 resolved:** parent rows are own-only (`inclusive_tokens` carries descendants), so every row counts once in the rollup.
- **Q1/Q2** keep their proposed defaults pending confirmation.
- **New findings folded into the implementation plan:**
  - Directory `sync` mints `user_id = github:<login>`, so Layer 1 ids are mapped through `user_map.jsonl`.
  - The org scope uses `directory.default_org_id`.
  - `info.*` values are derived from a simulated per-session request ledger.
- See [the implementation plan](../../../docs/plan/2026-09-18-synthetic-org-implementation.md) §1.

- **Q1 — Codex.** The Claude-only price table the generator used (now the `common.model_pricing` catalog, which does list other vendors) had no codex/GPT models. Either add real prices for them (a mainline pricing change), or run the synthetic org as Claude Code only (opus/sonnet/haiku mix) for now. **Proposed: Claude Code only** until codex ingestion is real.
- **Q2 — OTel.** v1 marks everything `jsonl_only`, so the datasource-mode badges show one state. A later increment could write a `both` share with `otel_api_requests`. **Proposed: defer.**
- **Q3 — `pr_classification`.** Run the real `worker/pr_class` classifier over the loaded artifacts (exercises real code and gives realistic labels, but depends on it being callable on stored rows), or write labels Layer 2 already knows. **Proposed: run the real classifier if it is callable without a live worker; otherwise synthesize and add a TODO.**
- **Q4 — Subagent token accounting** (§5.3). Confirm whether parent `session_info` rows are own-only or inclusive before fixing the rollup rule. This is an implementation-phase check, not a user decision.

## 11. Implementation order

1. Layer 1 identity style.
2. Layer 2 config models and validation.
3. Repos and directory.
4. Session split (I1 first).
5. Work model and calibration.
6. Git artifacts and links.
7. `info` content.
8. Report and manifest.
9. Loader.
10. Wrapper and example config.
11. API smoke.

Per AGENTS.md, an independent reviewer checks the finished implementation against §6 and §9 before acceptance.
