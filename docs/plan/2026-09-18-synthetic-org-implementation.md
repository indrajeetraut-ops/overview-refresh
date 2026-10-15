# Synthetic Org Data — Implementation Plan

> **2026-09-26:** token allowances (`allowances:` config, `allowances.jsonl`, `put_allowance`) were
> removed by `docs/plan/2026-09-26-cost-based-seat-utilization.md`. References to them below are
> historical; the loader now ignores an `allowances.jsonl` in older datasets.

> **2026-10-06 (#67):** the combined `input_combined` category below was replaced by five token
> types (`input` uncached, `cache_write_5m`, `cache_write_1h`, `cache_read`, `output`) given by
> Layer 1; writes are no longer derived from the request shape and there is no minimum input per
> request. References to `input_combined` and the cache-write split (P4, P7) are historical; see
> `econ-forecast/docs/design/cache-write-contract-design.md`.

**Date:** 2026-09-18
**Branch / worktree:** `feat/synthetic-org-data` at `.claude/worktrees/synth-org` (off `feat/mvp-implementation-#52`; `forecast` merged in `a525bbf`).
**Design (the acceptance contract):** [`econ-forecast/docs/design/synthetic-org-expansion-design.md`](../../econ-forecast/docs/design/synthetic-org-expansion-design.md). Read it in full first. This plan makes every open choice in that design concrete. Where the two disagree, **this plan wins** for implementation detail and **the design wins** for intent. Report any real conflict to the parent instead of resolving it yourself.
**Also read before coding:**
- `econ-forecast/docs/design/synthetic-data-generation-design.md` (Layer 1)
- `econ-forecast/src/forecast/synthetic/*` (all files; they are short)
- `AGENTS.md` / `CLAUDE.md` (repo rules, including the independent-review rule)
- `src/server/worker/graph_fold.py` lines 556–1270 (the producers of every `session_info.info` key)

---

## 0. Rules for the implementer

These are not suggestions. A deviation is a defect even if tests pass.

1. **Layer 1 is frozen** except for Task P1:
   - Do not change `series.py`, `allocation.py`, `randomness.py`, `contracts.py`, `runner.run_series`, or anything under `econ-forecast/src/forecast/forecasting/`.
   - Every existing test under `econ-forecast/tests/` must pass unmodified.
2. **Do not modify mainline worker, fold, ingest, or API business logic.** The only permitted mainline edits are:
   - the new loader files;
   - `config/queries/synthetic.yaml`;
   - `utils/load_synthetic.sh`;
   - a `--port` flag on `cli_serve_api.py` (P10);
   - an env-overridable proxy target in `src/webui/vite.config.ts` (P10);
   - new tests.
3. **Randomness:**
   - Only `forecast.synthetic.randomness.rng_for(org.seed, <domain>, *keys)`, using the domains listed in §4.
   - Never `random.*` module functions, `uuid4`, `time.time()`, `datetime.now()`, or set/dict iteration order inside record generation.
   - `run_id` and timestamps appear only in `manifest.json` and `report.md`, never in record files.
4. **Exactness:**
   - Every integer token split uses Layer 1's `allocate_integer`.
   - Never distribute tokens with float multiply-and-round.
   - Money is `Decimal` until converted with `common.pricing.price_to_micros`.
5. **No raw SQL strings in Python.** Statements go in `config/queries/synthetic.yaml` (loaded by `QueryCatalog`) or use SQLAlchemy Core constructs over ORM tables (`sqlalchemy.dialects.postgresql.insert(Model.__table__)`).
6. **No new third-party dependencies.** Pure Python: no numpy or pandas in Layer 2.
7. **Structured JSON logging** via `common.msging.log.get_logger`, event names prefixed `synthetic.org.` (Layer 2) and `synthetic_load.` (loader). Never log user names or emails; ids and counts only.
8. **Commits:**
   - conventional-commit style, one commit per task (P1…P11);
   - **no `Co-Authored-By` lines and no mention of Claude or AI tooling** anywhere (CLAUDE.md rule 3);
   - `git` without `-C`.
9. **TODOs:** anything consciously deferred gets a `# TODO(synthetic-org): <what and why>` at the exact code site and a line in the final handoff. No silent omissions.
10. **Tests are acceptance, not decoration.** Never delete, skip, or loosen a test to get green. If a requirement is impossible, stop and report.
11. **Do not read or copy content from the real `econ` database.** All text is generated from `lexicon.py`.

---

## 1. Locked decisions

Decisions below with evidence were verified against the code or the live stack on 2026-09-18.

| # | Decision | Evidence / reason |
|---|---|---|
| D1 | **Codex is out of scope.** The example config uses `claude_code` only, with models `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`. Layer 2 validation rejects any model `price_tokens` returns `None` for. | `config/pricing.yaml` has no GPT models (design Q1; assumed default, confirm before hand-off) |
| D2 | **No OTel.** `session_info.datasource = "jsonl_only"`, `otel = {}`, `jsonl_lag_s = NULL`. | Design Q2 (assumed default) |
| D3 | **`pr_classification` uses the real classifier**: `server.worker.pr_class.pr_classify.pr_classify(s, catalog, config, {"artifact_id": id})` per PR, after artifacts are loaded. `rework_ratio` (which nothing in mainline writes yet) is applied afterwards from `pr_rework.jsonl`. | `pr_classify.py:39` reads one stored row and upserts; the upsert `COALESCE`s `rework_ratio` |
| D4 | **Parent sessions are own-only.** Every session row (root or child) counts once in the rollup. A parent's `info.inclusive_tokens` = own + all descendants and is present only on sessions with children. Children have their own `repo_id`. | Live DB: parent `tokens_total == Σ models`, `inclusive_tokens − tokens_total == Σ child tokens_total`. All 24 children have `repo_id` |
| D5 | **Mainline `user_id` = `github:<github_login>`.** Layer 1 `user_id` (uuid5) is mapped to it through `user_map.jsonl`. | `directory.reconcile.mint_user_id` mints `github:<login>` when a `github` key exists; the real rows use that shape |
| D6 | **Directory org id** = `org.directory_org_id` (default `"default"`). The loader asserts it equals `config/directory.yaml → directory.default_org_id`. Teams, sessions, artifacts, and role grants use it. | `manager/service.py:139` scopes the org view by that id; the `org` scope = all active users |
| D7 | **Team ids are readable slugs:** the Layer 1 `team_name` lower-cased with `[^a-z0-9-]` → `-`. Root team id is `org.root_team.team_id` (default `eng`). | Matches real rows (`eng`, `platform`, `data`) |
| D8 | **Repo ids.** Details below. | `common.git_ident` scheme; `aggregate.resolve_git_repo_ids` bridges by `lower(full_name)` |
| D9 | **`channel_cost` semantics copy `graph_fold.channel_cost_of`.** Details below. | `graph_fold.py:583` |
| D10 | **Children sessions are linked to the same PR as their parent**, same method, so `pr_costs_for_ids` (a Σ over links) includes the subagent spend. | Design decision; cost-per-PR otherwise understates |
| D11 | **Viewer ids.** The admin (VP) is an extra user with login `org.admin.github_login`, an `admin@org/<org_id>` grant, and no sessions. Each team lead gets `lead@team/<team_id>`. Every user gets the directory default `dev` grant (handled by `sync`). | `gate._MANAGER_ROLES = (lead, admin)`; header `x-talium-viewer` |
| D12 | **Artifact shapes mirror `server/ingest/git/map_github.py` exactly.** Details below. | `map_github.py` |
| D13 | **Target database name must end with `_synth`.** There is no override flag. | Design §7.1 |

**D8 in detail (repo ids).**

| Id | Format / value |
|---|---|
| Session-side `repo_id` | `github:<owner>/<repo name>` (lower-case) |
| `repo_id_provenance` | `"remote"` |
| Git-side `repo_id` | `github:<repo_num>` |
| `git_repos.full_name` | `<owner>/<repo name>` |
| `repo_num` | `900_000_000 + int(sha256(f"synthetic-org:repo:{full_name}").hexdigest()[:8], 16) % 90_000_000` |
| `installation_id` | `800_000_000 + int(sha256(f"synthetic-org:installation:{github_owner}").hexdigest()[:8], 16) % 90_000_000` |

**D9 in detail (channel semantics).**

| Channel | Tokens | Priced as |
|---|---|---|
| `input` | uncached input only | the `input` kind |
| `output` | output | output |
| `recache` | `cache_write` | cache writes |
| `carry` | `cache_read` | lives in `info.carry.carry_usd_micros` |

Session cost = input + output + recache + carry, within per-channel micro rounding.

**D12 in detail (artifact shapes).**

- Artifact ids: `github:<repo_num>:{pr|issue|commit|review}:<key>`.
- PR `state ∈ {open, closed, merged}`; issue `state ∈ {open, closed}`; commit `state = "pushed"`.
- Review `state ∈ {approved, changes_requested, commented}` (lower-case).
- `git_artifact_events.action ∈ {opened, closed, reopened, merged}`, with `source_key = f"{artifact_id}:{action}:{occurred_at_iso}"`.
- PR `stats` keys: `additions, deletions, changed_files, commits, review_count, comment_count`. Issue `stats`: `{comment_count}`.
- PR `files[]`: `{path, op, additions, deletions}` with `op ∈ {added, modified, removed}`.
- PR `info`: `{"github.draft": false, "github.merged_by": <login|null>}`. Revert PRs add `"github.is_revert": true`.
- Every artifact's `info` also gets `"synthetic": {"generator_version": ..., "org_seed": ...}` (no `run_id`).

---

## 2. File layout

Create exactly these files; do not add others without reporting.

```
econ-forecast/src/forecast/synthetic/
  config.py                 (P1: UsersConfig.identity_style)
  models.py                 (P1: UserRecord.github_login)
  users.py                  (P1: consistent identity branch)
  cli_synt_gen.py           (P3: add --op org-expand)
  org/
    __init__.py             public: OrgConfig, load_org_config, run_org_expand
    config.py               P2  OrgConfig pydantic models + load + cross-validation
    models.py               P2  output record models (one per output file)
    ids.py                  P2  every deterministic id / sha / number function
    lexicon.py              P2  fixed word lists (titles, file stems, purposes, reviews)
    pricing.py              P2  snapshot-bound wrapper over common.pricing
    directory.py            P3  directory.jsonl, user_map.jsonl, user_prefs.jsonl
    repos.py                P3  git_installations/git_repos/repositories
    sessions.py             P4  group split, subagents, day layout
    work.py                 P5  calibration, work streams, items, features
    git.py                  P6  PR/commit/review/issue/events/links/rework
    requests.py             P7  per-session request ledger simulation
    content.py              P7  session_info assembly from the ledger
    report.py               P8  report.md sections
    expand.py               P8  run_org_expand orchestration + DatasetWriter
    cli_synth_org.py        P9  one-shot chain with --end-date retiming
econ-forecast/config/synthetic/org/
    users.yaml roster.yaml series.yaml org.yaml     P9 example set
econ-forecast/utils/synth_org.sh                    P9
econ-forecast/tests/synthetic/org/
    conftest.py test_identity.py test_org_config.py test_ids.py test_sessions.py
    test_work.py test_git.py test_requests.py test_content.py test_parity_graph_fold.py
    test_expand_invariants.py test_determinism.py test_cli_org.py
src/server/storage/synthetic_load.py                P10 library
src/server/storage/cli_load_synthetic.py            P10 CLI
config/queries/synthetic.yaml                       P10
utils/load_synthetic.sh                             P10
tests/server/storage/test_synthetic_load.py         P10 (DB-gated)
tests/server/storage/test_synthetic_load_unit.py    P10 (no DB)
tests/server/api/test_synthetic_smoke.py            P11 (DB-gated)
econ-forecast/src/forecast/synthetic/org/README.md  P11
```

---

## 3. Configuration reference (`org.yaml`, schema_version 1)

All models subclass Layer 1's `Strict` (`extra="forbid"`) and reuse its annotated types (`Ratio`, `Pct`, `PositiveInt`, `Seed`, `Number`, `Name`) and its `shares()` helper for mixtures. Load with `UniqueLoader`. Paths resolve relative to the YAML file. Every field below has the stated default unless marked **required**.

### 3.1 Top level

```yaml
schema_version: 1
org:
  series_dir: <path>                     # required. A completed series-create dataset dir
  pricing_file: <path>                   # required. Mainline config/pricing.yaml; snapshotted into the output
  seed: 7                                # required. Layer 2 master seed
  directory_org_id: default
  github_owner: acme                     # required. Lower-case [a-z0-9-]+
  root_team: {team_id: eng, name: Engineering}
  groups: []                             # optional: [{team_id, name, children: [<layer1 team_name>...]}]
  admin: {github_login: vp-eng, display_name: <required>, email: <required>}
  leads: {}                              # optional {<team_name>: <layer1 user_id>}; missing -> auto (§5.P3)
  agent_versions: ["2.1.250", "2.1.260", "2.1.270"]
  timezone_from_series: true             # users.timezone := series.timezone (only supported value: true)
calibration:
  target_merged_prs_per_dev_week: 3.0    # > 0
repos: [...]                             # required, §3.2
defaults: {...}                          # §3.3; every block optional, falls back to built-in defaults
teams: {<layer1 team_name>: {...}}       # §3.4; every rostered team must appear
onboarding: {ramp_days: 30, initial_multiplier: 2.0}   # ramp_days >= 1, initial_multiplier >= 1
budgets: null                            # optional: {period: monthly, org_usd: 20000, teams: {<team_name>: usd}}
allowances: null                         # REMOVED 2026-09-26 (cost-based seats plan) -- seats derive from team budgets
```

### 3.2 `repos[]`

```yaml
- project: payments_api          # required. Must equal a series projects[].name; each project exactly once
  name: payments-api             # required. [a-z0-9-]+, unique
  default_branch: main
  private: true
  integration_mix: {squash: 0.7, merge: 0.2, rebase: 0.1}
  modules:                       # required, >= 1; weights sum to 1
    - path: services/ledger/     # must end with "/"
      weight: 0.4
      extensions: {.py: 0.8, .sql: 0.2}   # shares; keys start with "."
```

### 3.3 `defaults` blocks

Each block is overridable per team; §3.4 shows how overrides merge.

```yaml
work:
  unlinked_share: 0.20                 # Ratio. Share of root sessions not tied to a work item
  parallel_items: 1                    # 1..3
  new_item_prob: 0.25                  # used only when parallel_items > 1
  feature_size: {"1": .35, "2": .25, "3": .20, "4": .10, "5": .05, "6": .05}   # keys "1".."8"
  shared_feature_prob: 0.15
  issue_link_mix: {graphql: .50, branch: .20, title: .10, none: .20}
  change_mix: {feature: .45, fix: .25, refactor: .12, test: .06, docs: .06, chore: .06}
  backlog_issues_per_repo_week: 1.5    # >= 0; never-started issues
  not_planned_share: 0.2               # share of backlog issues closed as not planned
pr:
  size_median_loc: 180
  size_sigma: 0.9                      # lognormal sigma, > 0
  size_min_loc: 5
  size_max_loc: 4000
  open_at_fraction: 0.7                # (0, 1]
  deletion_share: 0.3
  label_prob: 0.6
  commits_per_session: {"0": .3, "1": .4, "2": .2, "3": .1}
  comments_per_pr_mean: 2.0
  merge_delay_hours: {min: 0.2, max: 8}
review:
  first_review_median_hours: 4
  first_review_sigma: 0.8
  changes_requested_rate: 0.30         # per round, geometric; max 3 rounds
  commented_review_prob: 0.3
  abandon_rate: 0.06
  reopen_rate: 0.03
  revert_rate: 0.02
  rework_ratio: {min: 0.02, max: 0.25}
habits:                                # affects ONLY intra-session content (I2b)
  cache_bust_per_100_requests: 0.5
  resume_prob_per_turn: 0.04
  interrupt_prob_per_turn: 0.03
  compactions_per_100_requests: 0.8
  manual_compaction_share: 0.3
  verified_prob: 0.6
  plan_mode_prob: 0.2
sessions:                              # affects token splits and timing
  mean_input_tokens: 1200000           # processed input (input + cache_read) per root session
  min_input_tokens: 20000
  max_per_day: 8
  subagent_prob: 0.25
  max_subagents: 4
  subagent_share: {min: 0.03, max: 0.15}   # per child, of each parent (model, category)
  mean_context_tokens: {min: 40000, max: 140000}
  context_window_tokens: 1000000
  turn_size_mean_requests: 8
  work_window_start_hours: {min: 8.5, max: 10.5}   # local time
  work_window_length_hours: {min: 8, max: 10}
  dominant_model_share: 0.85
  ttl_s: 300                           # cache TTL used to classify resume vs bust gaps
```

Built-in defaults are exactly the values above. Put them in `config.py` as pydantic field defaults, and test that an empty `defaults: {}` yields them.

### 3.4 `teams.<team_name>`

```yaml
tokens_per_loc_multiplier: 1.0          # > 0. >1 = MORE tokens per line (less efficient)
user_multiplier_sigma: 0.35             # lognormal sigma for per-user spread, >= 0
work: {...}                              # partial override of defaults.work
pr: {...}
review: {...}
habits: {...}
sessions: {...}
```

Merge rule: a team block is a **field-level** override of the corresponding `defaults` block. Mixture maps (`*_mix`, `feature_size`, `commits_per_session`) are replaced whole, never merged key by key.

### 3.5 Validation (all raise `ConfigError`, exit 2, before any output)

- `series_dir` has a `manifest.json` with `status == "complete"` and `operation == "series"`. Its record-file hashes match (reuse Layer 1's manifest-reading approach; snapshot `usage.jsonl`, `users`, and `roster` into `inputs/`).
- Every series project maps to exactly one repo, and every repo to exactly one existing project.
- Every rostered team has a `teams.<name>` entry, and there are no extra names.
- `leads` values are members of that team. `groups[].children` reference existing teams, with no team in two groups.
- Every user in the snapshotted users file has a non-null `github_login` (requires Layer 1 `identity_style: consistent`).
- `admin.github_login` collides with no user login.
- Every `(agent, model)` in the series config has `price_tokens(model, {"input": 1}, series.start_date)` not `None`, **and** at `series end_date`.
- Every agent key ∈ `{"claude_code"}` (D1). Reject others with a message naming D1.
- All mixtures sum to 1 (`shares`). `size_min_loc < size_max_loc`. Every `min <= max` pair holds.

---

## 4. Random-stream domain registry

Every `rng_for(org.seed, domain, *keys)` call uses exactly one of these domains with exactly these keys. Add a module constant `DOMAINS` in `ids.py` and a test asserting each domain string used in the package is in it. Grep the source in the test.

| Domain | Keys | Used for |
|---|---|---|
| `lead` | team_id | auto lead pick |
| `user_mult` | l1_user_id | per-user tokens_per_loc lognormal |
| `window` | l1_user_id | per-user work window start/length |
| `sess_count` | l1_user, date, project, agent | number of root sessions |
| `sess_weight` | l1_user, date, project, agent, k | session weight |
| `dominant` | l1_user, date, project, agent, k | dominant model draw |
| `subagent` | session_id | spawn decision, child count, child shares |
| `layout` | l1_user, date | session order + gaps in the day |
| `link` | session_id | unlinked vs linked |
| `item` | l1_user, repo, item_seq | budget size, change type, branch word |
| `item_pick` | session_id | which in-flight item a session serves |
| `feature` | l1_user, repo, feature_seq | feature size, sharing, link method |
| `commits` | session_id | commit count per session |
| `review` | pr_key | reviewers, delays, rounds, comments |
| `outcome` | pr_key | abandon/reopen/merge delay |
| `revert` | pr_key | revert decision + timing |
| `rework` | pr_key | rework_ratio |
| `backlog` | repo, week_index | backlog issues |
| `text` | entity_kind, entity_key | titles/purposes/words |
| `files` | entity_kind, entity_key | file paths and line splits |
| `requests` | session_id | request count, context profile, turns, gaps |
| `habit_bust` | session_id | cache-bust positions |
| `habit_resume` | session_id | resume positions |
| `habit_interrupt` | session_id | interrupt positions |
| `habit_compact` | session_id | compaction positions / trigger |
| `practices` | session_id | practices block |
| `tools` | session_id | tool mix |
| `segments` | session_id | segment boundaries/labels/beats |

`pr_key = f"{repo_name}:{l1_user}:{item_seq}"`. Revert PRs use `f"revert:{orig_pr_key}"`.

**Knob independence** (I2b) depends on this. Habit domains are only ever read inside `requests.py`/`content.py`. `sessions.py` reads only `sess_*`, `dominant`, `subagent`, `layout`, `window`.

---

## 5. Tasks

Each task lists: files, interfaces, the exact algorithm, tests, and **done-when**. Run the task's tests plus `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` after every task.

### P1 — Layer 1 identity style

**Files:** `synthetic/config.py`, `synthetic/models.py`, `synthetic/users.py`, `econ-forecast/tests/synthetic/org/test_identity.py`.

- **`config.py`:** add `identity_style: Literal["independent", "consistent"] = "independent"` to the users options model.
- **`models.py`:** add `github_login: Nonempty | None = None` to `UserRecord`.
- **Byte-identity requirement (I7):** a user record with `github_login=None` must serialize **byte-identical** to today. Achieve it by excluding that field when `None` at write time. Check how `artifact_io.RecordStream.write` / `_json` dumps. If it dumps all fields, add `exclude_none`-style handling **only for this optional field**, e.g. a `model_serializer` on `UserRecord` that drops `github_login` when `None`. Do not change `artifact_io.py` behavior for other records.
- **`users.py` independent branch:** exactly today's code path.
- **`users.py` consistent branch** (per index, same Faker seeding call):
  - `first = fake.first_name()`, `last = fake.last_name()`, `name = f"{first} {last}"`.
  - Email local: `_local(f"{first}.{last}")[:64]`, with the existing `_N` collision loop.
  - Login: `re.sub(r"[^a-z0-9-]", "", _local(first) + _local(last)[:1])`, falling back to `"user"` when empty. Collision suffix `-2`, `-3`…; max length 39.
  - `user_id` stays `uuid5(... f"forecast:user:{domain}:{index}")`, identical to the independent style for the same index.

**Tests:**

| Test | Asserts |
|---|---|
| `test_consistent_email_matches_name` | Email local starts with the ASCII-folded first name |
| `test_login_unique_and_valid` | 500 users: unique, `^[a-z0-9-]{1,39}$` |
| `test_consistent_deterministic` | Two runs are byte-identical |
| `test_user_ids_style_independent` | Same count, domain, and seed → identical `user_id` lists across styles |
| `test_independent_bytes_unchanged` | Generate with the default config, compare the `users.jsonl` sha256 to a hash computed from the pre-change code path (compute it once at the start of P1 and hard-code it in the test with a comment saying how it was obtained) |

**Done when:** new tests pass and all pre-existing `econ-forecast/tests` pass unmodified.

### P2 — Layer 2 foundations: config, models, ids, lexicon, pricing

**`org/config.py`:**
- `OrgConfig` and its sub-models per §3, plus `load_org_config(path) -> OrgConfig`.
- `validate_org_inputs(cfg, series_manifest, users, teams, series_cfg, pricing) -> ResolvedOrg`.
- `ResolvedOrg` (a frozen dataclass or pydantic model) holds: effective per-team blocks after merge, the project→repo map, the lead per team, and the ordered team list.
- Reading the series dataset: `usage.jsonl` → `DailyUsageRecord` via `read_records`; `inputs/users/users.jsonl` and `inputs/roster/teams.jsonl` (the series' own snapshots, not the originals); the series `config.yaml` → `SeriesConfig` for timezone, projects, dates, and the events list.

**`org/models.py`:** one pydantic model per output file (`Rec`-style, `extra="forbid"` for writers). Fields are named exactly as the ORM columns or the directory wire shape. Also:
- `UserMapRecord{l1_user_id, user_id, github_login, team_id}`;
- `ExpectedRollupRecord{date, user_id, repo_id, agent, model, input_combined, cache_read, output}`;
- `PrReworkRecord{artifact_id, rework_ratio}`.

**`org/ids.py`:** pure functions.

| Function | Returns |
|---|---|
| `repo_num(full_name)` | Per D8 |
| `installation_id(owner)` | Per D8 |
| `session_id(l1_user, date_iso, project, agent, k)` | `str(uuid5(NAMESPACE_URL, f"synthetic-org:session:{l1_user}:{date_iso}:{project}:{agent}:{k}"))` |
| `child_session_id(parent_id, j)` | `f"agent-{sha1(f'{parent_id}:{j}').hexdigest()[:17]}"` (matches the real `agent-<hex>` look) |
| `sha(*parts)` | `sha1("synthetic-org:" + ":".join(parts)).hexdigest()` |
| `review_id(pr_key, i)` | `int(sha256(...).hexdigest()[:12], 16)` |
| `team_slug(name)` | Per D7 |
| `artifact_id(repo_num, kind, key)` | `github:<repo_num>:<kind>:<key>` |

Also the `DOMAINS` tuple (§4).

**`org/lexicon.py`:** tuples of fixed words, no Faker:
- per change type: `VERBS[change_type]`, `OBJECTS`, `QUALIFIERS`;
- `FILE_STEMS`, `SUBAGENT_PURPOSES`, `UNLINKED_TITLES` (exploration/ops/questions);
- `FEATURE_NOUNS`, `SUBAGENT_TYPES = ("Explore", "general-purpose", "Plan")`.

Helper functions:
- `pr_title(rng, change_type, module_name)` → conventional-commit style `"feat(ledger): add retry budget to sync"`. The prefix is `{feature: feat, fix: fix, refactor: refactor, test: test, docs: docs, chore: chore}`.
- `session_title(rng, pr_title)` → an imperative sentence without the prefix.
- `issue_title(rng, feature_noun, module_name)`.
- `branch(change_type, issue_number|None, slug)`: `feat/`, `fix/`, `refactor/`, `test/`, `docs/`, `chore/`; with a link method of branch, the form `feat/<n>-<slug>`.

At least 30 entries per list, so 1k PRs rarely collide.

**`org/pricing.py`:**
- `class Pricer` wraps a `PricingConfig` loaded from the **snapshotted** pricing file.
- `price_micros(model, tokens: dict, at: date) -> int` raises `ConfigError` on `None`.
- `channel_micros(models: dict, at) -> dict` mirrors `graph_fold._price_channel` exactly: read that function and port its per-model summation and rounding order.
- `cost_micros(models, at) -> int` = Σ over models of `price_to_micros(price_tokens(m, tokens_m, at))`.

**Tests (`test_org_config.py`, `test_ids.py`):**
- Every §3.5 rule has one failing-config test that asserts the exception message names the field.
- Built-in defaults test.
- Override merge test (field-level, and a mixture replaced whole).
- Id functions are stable (golden values hard-coded) and match the D8/D12 regexes.
- The domain-registry grep test.

**Done when:** config tests and id tests pass, and `--validate-only` works (wired in P3).

### P3 — CLI wiring, directory, repos

**`cli_synt_gen.py`:**
- Add `"org-expand": "org"` to `OPERATIONS`.
- Loaders: `load_org_config`. Validation: `validate_org_inputs`. Runner: `run_org_expand` (a stub raising `NotImplementedError` until P8, with a TODO).
- Update the epilog text.
- Exit codes and stdout/stderr contract are unchanged from Layer 1.

**`org/directory.py`:** `build_directory(resolved) -> (DirectoryWire, list[UserMapRecord], list[UserPrefsRecord])`.

- **Team records:**
  - root `{team_id: root.team_id, org_id: directory_org_id, name: root.name, parent_team_id: null, source_provider: "csv"}`;
  - each group (parent = root);
  - each Layer 1 team (`team_id = team_slug(team_name)`, `name = team_name.replace("_", " ").title()`, parent = its group or root).
- **Leads:** from `org.leads`, else auto: the member with the **highest** profile (heavy > mid > light, from series `user_profiles.jsonl`). Ties are broken by `rng_for(seed, "lead", team_id)` shuffle of sorted user ids.
- **Person record** per rostered Layer 1 user:

  ```
  {external_keys: [{provider: "github", external_id: login}, {provider: "synthetic", external_id: l1_user_id}],
   email, display_name: name, status: "active",
   teams: [{team_id, role_in_team: "lead"|"member"}],
   roles: [{role: "lead", scope_type: "team", scope_id: team_id}] if lead else [],
   source_provider: "csv"}
  ```

- **Admin record:** `external_keys [{github, admin.github_login}]` and `roles [{admin, org, directory_org_id}]`, no teams.
- **Unassigned** Layer 1 users (not rostered) are **not** emitted.
- **`UserMapRecord`:** `user_id = "github:" + login`.
- **`UserPrefsRecord{user_id, github_user: login, timezone: series.timezone, primary_team_id: team_id}`.** The admin gets its own `github_user`, the timezone, and `primary_team_id = root`.

**`org/repos.py`:**
- `git_installations`: one row `{installation_id, org_id: directory_org_id, account_login: github_owner, account_type: "Organization", repo_selection: "all", state: "active", installed_at: series start − 30d at 00:00 UTC, updated_at: same, info: {synthetic}}`.
- `git_repos` per repo: `{repo_id, repo_num, full_name, owner, default_branch, private, installation_id, org_id, backfill_cursor: null, backfill_state: "done", first_seen_at: installed_at, updated_at: installed_at, info: {synthetic}}`.
- `repositories` per repo: `{repo_id: session-side id, kind: "git_remote", label: full_name, first_seen_at, last_seen_at: series end 23:59 local→UTC, info: {synthetic}}`.

**Tests:**
- Directory wire records parse with `server.directory.models.DirectoryRecord.from_dict` / `TeamRecord.from_dict` (import mainline in the test).
- Every rostered user has exactly one team and a unique login; each team has exactly 1 lead.
- The admin has an org admin grant and no team.
- Auto-lead picks the heaviest profile.
- Repo rows match the D8 regexes, and `lower(full_name)` stripped of `github:` equals the session-side suffix.

### P4 — Sessions: split, subagents, day layout (I1 lives here)

**`org/sessions.py`.** Output: an in-memory list of `SessionPlan` (dataclass):

```
session_id, parent_session_id|None, l1_user, user_id, date, project, repo, agent,
models: {model: {"input_combined": int, "cache_read": int, "output": int}},
started_at, ended_at (UTC datetimes), planned_duration_s, children: [session_id], k (index)
```

**Algorithm, per usage group** `G = (date, l1_user, project, agent)`. Build it from `usage.jsonl` rows. Each row is one model: `input_combined = tokens.input`, `cache_read`, `output`; `None` counts as 0.

1. **Session count.** `W = Σ_m (input_combined + cache_read)`. `n_raw = W / sessions.mean_input_tokens × rng("sess_count", ...).uniform(0.6, 1.4)`. `n = max(1, min(round(n_raw), sessions.max_per_day, max(1, W // sessions.min_input_tokens)))`.
2. **Weights.** `w_k = rng("sess_weight", ..., k).lognormvariate(0, 0.6)` for `k in range(n)`, keyed `f"s{k:02d}"`.
3. **Dominant model per session.** Draw from the models with weights = each model's `input_combined + cache_read + output`, using `rng("dominant", ..., k).random()` over the sorted model keys (the same `_choice` technique as Layer 1 `series._choice`; copy it into `sessions.py`; do not import the private name).
4. **Per-(model, session) weight.** `w_k × (dominant_model_share if m == d_k else (1 − dominant_model_share) / max(1, n_models − 1))`. For every model `m` and category `c ∈ {input_combined, cache_read, output}`, `allocate_integer(total[m][c], weights_m)`. A session whose three categories are all 0 for model `m` omits `m`. A session that ends up with **all** models empty is merged into session `s00`: re-run allocation with that key removed and `n` reduced. Loop until none are empty; `n ≥ 1` always holds because `W > 0` or the group is skipped.
5. **Groups with `W + Σ output == 0`** produce no sessions (Layer 1 never emits them; assert it).
6. **Subagents.** For each root session, `r = rng("subagent", session_id)`:
   - if `r.random() < sessions.subagent_prob` and the root's processed input `≥ 2 × min_input_tokens`, spawn `j = r.randint(1, max_subagents)` children;
   - child `i` takes `f_i = r.uniform(subagent_share.min, subagent_share.max)`;
   - for each `(m, c)`: `child_amount = floor(parent_amount_before_any_child × f_i)`, computed from the parent's **pre-spawn** amounts, subtracted from the parent;
   - if the parent would go below `min_input_tokens` processed input, stop spawning further children.
   - A child has the same agent, project, repo, and date.
   - Child ids come from `ids.child_session_id`.
   - Children with all-zero models are not created.
7. **Day layout** (per `l1_user × date`, across all that user's root sessions of every project and agent):
   - Order: sort roots by `(project, agent, k)`, then shuffle with `rng("layout", l1_user, date)`.
   - Window: `start_h = rng("window", l1_user).uniform(...)` and `len_h` likewise. These are per user, so fixed across days.
   - Local tz = series timezone (`zoneinfo`).
   - `planned_duration_s(session) = clamp(processed_input / 25_000 × 60, 300, 6 × 3600)`, i.e. 25k processed tokens per minute (a module constant `PROCESSED_TOKENS_PER_MIN`).
   - If Σ durations > window length − 60 min (lunch), scale all durations down proportionally, to a minimum of 120 s each.
   - Distribute the remaining idle time as gaps: `n+1` gap weights from `rng("layout", ...)`, with a fixed 60-min lunch gap inserted at the first gap boundary after 12:30 local.
   - If the last session would end after 23:59:00 local, shift the whole day earlier.
   - If it still overflows (only possible with extreme configs), compress gaps to 0, then raise `InputDataError`.
   - Times are converted to UTC. `ended_at = started_at + planned_duration_s`.
8. **Child timing:** `child.started_at = parent.started_at + r.uniform(0.1, 0.7) × parent_duration`; `child_duration = min(child processed / PROCESSED_TOKENS_PER_MIN × 60, parent.ended_at − child.started_at)`, floor 30 s.

**Tests (`test_sessions.py`):**

| Test | Asserts |
|---|---|
| `test_split_exact_per_category` | Randomized property test, 200 groups: Σ over sessions (roots + children) per (model, category) == input |
| `test_single_token_group` | `W=1` → exactly 1 session; totals conserved |
| `test_huge_group` | `W = 5e11` → `n == max_per_day`, exact |
| `test_zero_share_model_absent` | A model with all-zero categories never appears |
| `test_empty_session_merged` | Force weights that empty a session → no empty sessions, exact |
| `test_children_carved_from_parent` | Exactness holds; the parent stays ≥ min |
| `test_layout_same_local_day` | Every root `started_at` and `ended_at` in local tz has `.date() == group date` |
| `test_layout_no_overlap_roots` | Per user-day, root intervals are disjoint |
| `test_layout_deterministic` | |
| `test_child_within_parent` | |

**Done when:** all pass.

### P5 — Work model: link flags, calibration, work streams, features

**`org/work.py`.** Inputs: the root `SessionPlan` list, the resolved config, `user_profiles`. Output: `WorkItem`s, `Feature`s, backlog `Issue` stubs, and a `session → item` map.

1. **Link flag** per root session: `linked = rng("link", session_id).random() >= team.work.unlinked_share`. Children inherit the parent's flag.
2. **Inclusive tokens** `T(s)` = Σ over the session and its children of `input_combined + cache_read + output`.
3. **User multiplier:** `u_mult(l1_user) = rng("user_mult", l1_user).lognormvariate(0, team.user_multiplier_sigma)`.
4. **Ramp:** for a user whose first active date `j` is after `series.start_date + 7 days`, `ramp(d) = 1 + (initial_multiplier − 1) × max(0, 1 − (d − j).days / ramp_days)`; else `ramp = 1`.
5. **Calibration (closed form, org-wide):**

   ```
   E_size(t) = size_median_loc_t × exp(size_sigma_t² / 2)            # lognormal mean, before clamping
   L_u       = Σ T(s) over linked root sessions of user u
   dev_weeks = Σ_u (series_end − first_active_date_u + 1 day).days / 7   # rostered users with ≥1 active day
   base      = Σ_u [ L_u × (1 − abandon_rate_t(u)) / (E_size(t(u)) × tokens_per_loc_multiplier_t(u) × u_mult(u)) ]
               / (target_merged_prs_per_dev_week × dev_weeks)
   ```

   `base` is tokens per LOC at multiplier 1. Log it (`synthetic.org.calibrated`, value + inputs) and put it in the report. Clamping and ramp are deliberately excluded; the ±25% tolerance (I6) absorbs them.
6. **Work streams** per `(l1_user, repo)`: the linked root sessions sorted by `started_at`. State: `in_flight: list[WorkItem]` (FIFO), `item_seq` counter. For each session `s`:
   - **New item** if `in_flight` is empty, or if `len(in_flight) < parallel_items` and `rng("item_pick", sid).random() < new_item_prob`:
     - `size = clamp(round(rng("item", u, repo, seq).lognormvariate(ln(size_median_loc), size_sigma)), size_min_loc, size_max_loc)`;
     - `change_type` from `change_mix` (same rng);
     - `budget = size × base × tokens_per_loc_multiplier × u_mult(u) × ramp(s.date)`;
     - `module` = weighted choice over the repo modules (same rng).
   - **Pick an item:** the oldest in-flight with probability 0.7 (when > 1 in flight), else uniform among the others, using `rng("item_pick", sid)`.
   - `item.sessions.append(s)`; `item.spent += T(s)`.
   - **Open:** if `item.opened_at is None` and `spent ≥ open_at_fraction × budget`, set `opened_at = s.ended_at + uniform(5, 60) min` using `rng("item", ..., "open")`.
   - **Finalize:** if `spent ≥ budget`, set `item.finalized_at = s.ended_at`; remove it from `in_flight`.
   - **End of series:** remaining items stay in-flight. If opened, they become **open PRs**. If never opened, the item has **no PR**: its sessions keep `linked=True` for accounting, but get **no** link rows (see P6).
7. **Features** per `(l1_user, repo)`:
   - Walk the finalized and in-flight items in order; group consecutive items into features of size `k` drawn from `feature_size` via `rng("feature", u, repo, fseq)`.
   - **Sharing:** with `shared_feature_prob`, a feature adopts the issue of a teammate's (same team, same repo) feature whose time span overlaps. Pick the earliest such by `(start, pr_key)`. Otherwise it gets its own issue.
   - **Link method** from `issue_link_mix` (feature rng); `none` = no issue at all.
8. **Backlog issues** per repo per ISO week in the series: `count = poisson(backlog_issues_per_repo_week)`, sampled as the count of exponential inter-arrivals within 1.0 using `rng("backlog", repo, week)`.
   - Each backlog issue has `created_at` uniform in that week during working hours and a random rostered author from a team with that repo in its mix.
   - `not_planned_share` of them are closed after `uniform(3, 30)` days, if still in the series, with `info["github.state_reason"] = "not_planned"`.

**Tests (`test_work.py`):**
- `test_budget_accounting` — Σ `item.spent` over items + Σ `T` of unlinked = Σ `T` of all sessions.
- `test_calibration_hits_target` — the example config, full 120 days: org merged PRs/dev-week within ±25% of target (I6). Also: the team with the lowest `tokens_per_loc_multiplier` has the highest merged LOC per $ (monotonic check across the example's 4 teams).
- `test_parallel_items_bounded`
- `test_feature_sizes_respected`
- `test_shared_feature_same_team_same_repo`
- `test_link_mix_frequencies` — 2k features within ±5pp.
- `test_ramp_increases_budget_early`
- `test_unopened_items_have_no_pr`

### P6 — GitHub artifacts, events, links, rework

**`org/git.py`.** Inputs: work output + sessions + directory map.

1. **Numbering:**
   - Per repo, collect every issue (feature issues and backlog) and every PR (items that opened, plus reverts) with its `created_at`.
   - Sort by `(created_at, kind_order{issue: 0, pr: 1}, pr_key|issue_key)` and assign `number = 1..N`.
   - Titles with a link method of `title` then include `(#<issue number>)`.
   - Branches with a link method of `branch` use `feat/<issue number>-<slug>`.
2. **Commits** per linked root session of an item that has a PR:
   - `n_c` from `commits_per_session` via `rng("commits", sid)`. Force `≥ 1` for the session that opened the item and for the finalizing session.
   - Commit times are uniform within the session interval, sorted.
   - `commit sha = ids.sha(repo, pr_key, str(index))`.
   - Commit lines: total = `size × (1 + churn_extra)`, where `churn_extra = rounds × uniform(0.05, 0.2)` (`rounds` from step 4), split by `allocate_integer` over commits with lognormal weights. Additions/deletions split by `deletion_share`.
   - Commit artifact: `kind commit`, `state "pushed"`, `branch = item branch`, `sha`, `author_login`, `user_id`, `stats {additions, deletions}`, `created_at = updated_at = commit time`, `parent_artifact_id = PR artifact id`, other fields null or empty as in `_map_push`.
   - `info`: the synthetic marker; also `github.push_meta` on the last commit of each session, matching `_map_push`'s head-commit key set.
3. **PR row:**
   - `created_at = opened_at`, `updated_at = max(event times)`.
   - `state`, `merged_at`/`closed_at` per step 4; `title`, `branch`, `base_branch = default_branch`.
   - `stats`: `additions`/`deletions` = size split by `deletion_share`; `changed_files = len(files)`; `commits = n commits`; `review_count`; `comment_count = poisson(comments_per_pr_mean)`.
   - `files`: `n_files = clamp(round(size / 40 × uniform(0.6, 1.4)), 1, 60)`. Paths are `f"{module.path}{stem}_{i}{ext}"` (stem from `FILE_STEMS`, ext by the module's extension mix, `rng("files", "pr", pr_key)`). `op`: `added` for 30%, else `modified`. Lines come from `allocate_integer`.
   - `labels` per `label_prob`: `{feature: ["enhancement"], fix: ["bug"], refactor: ["refactor"], docs: ["documentation"], test: ["tests"], chore: ["chore"]}`.
   - `linked_issue` = issue artifact id only for the `graphql` method, else null.
   - `integration_method` from the repo `integration_mix` (`rng("outcome", pr_key)`).
   - `commit_shas` = its commits; `merge_commit_sha = ids.sha(repo, pr_key, "merge")` if merged; `before_sha`/`after_sha` = `ids.sha(repo, pr_key, "before")` / `ids.sha(repo, pr_key, "after")` if merged, else null.
   - `user_id`, `author_login`, `is_bot=false`, `milestone=null`, `reverts_sha=null`.
4. **Reviews and outcome** (`rng("review", pr_key)`, `rng("outcome", pr_key)`):
   - Reviewers are teammates ≠ author, sorted then shuffled; take 1–2. If the team has no other member, use the lead of the root-nearest other team, else the admin.
   - `first_review_at = opened_at + lognormal(ln(first_review_median_hours), first_review_sigma) hours`.
   - `rounds = 0`; `while rounds < 3 and random() < changes_requested_rate: rounds += 1`. Cap `rounds` at the number of the item's linked root sessions that start after `opened_at`.
   - Sequence: for each round, a `changes_requested` review placed before the next post-open session start (at least 10 min after the previous review). Optional `commented` reviews (`commented_review_prob` per round). A final `approved` review after the last session end.
   - Review rows: `kind review`, `state`, `sha` = latest commit sha before the review time, `created_at = updated_at = review time`, `number = PR number`, `parent_artifact_id = PR id`, reviewer `user_id`/`author_login`.
   - **Outcome** for finalized items: `abandon` with `abandon_rate` → `state closed`, `closed_at = last session end + merge_delay`, and no approved review; else merged → `merged_at = max(last session end, approved time) + uniform(merge_delay_hours) h`, `state merged`, `closed_at = merged_at`.
   - `reopen_rate` (merged path only): insert `closed` at `t1 = midpoint(opened_at, merged_at)` and `reopened` at `t1 + uniform(1, min(48 h, merged_at − t1 − 10 min))`. Skip if the window is < 1 h.
   - Any time beyond `series_end_local 23:59` → the PR stays `open` (or `closed` if abandoned before the end), with no merge.
   - In-flight opened items: `state open`, reviews only before the series end.
5. **Events:** `opened` at `created_at`; `closed`/`reopened` per reopen; a final `merged` (to_state merged) or `closed` (to_state closed). `occurred_at` = the event time; `recorded_at = occurred_at`.
6. **Reverts:** for each merged PR, if `rng("revert", pr_key).random() < revert_rate`:
   - Author: a teammate (≠ author if possible). `created_at = merged_at + uniform(0.5, 10) days`; skip if beyond the series end.
   - `title = f'Revert "{orig title}"'`, `branch = f"revert-{orig number}-{slug}"`.
   - `stats` = the original's additions/deletions swapped. `files` = the original with ops inverted (`added`↔`removed`).
   - `reverts_sha = orig merge_commit_sha`; `info["github.is_revert"] = True`.
   - Merged after `uniform(0.5, 6) h`, `integration_method` = the repo mix. One approved review from the lead.
   - **No linked sessions, no commits.**
7. **Issues:**
   - `kind issue`; `number`; `title` (`issue_title`); `author` = the feature's first user or the team lead (50/50, `text` rng).
   - `created_at = first item start − uniform(0, 10) days`, clamped to `≥ series start − 30 days`.
   - `state closed` when every PR in the feature is merged or closed and `last + uniform(0, 2) days ≤ series end`, else `open`. `closed_at` accordingly.
   - `stats {comment_count: poisson(1.5)}`, `labels ["enhancement"|"bug"]` from the first item's change type, `info {"github.assignees": [logins]}`.
8. **`pr_issue_links`:** for methods `graphql|branch|title`, one row per PR in the feature: `{pr_artifact_id, tracker: "github", project_key: full_name, number: issue number, issue_artifact_id: issue id, method, confidence: linkage._CONFIDENCE[method], created_at: PR created_at}`. Import `_CONFIDENCE` in the test only. In code, copy the three values into a constant with a comment pointing at `linkage.py:41`, and add a parity test.
9. **`session_git_links`** for every session (root **and** its children, per D10) of an item **that has a PR**:
   - a root session that produced ≥ 1 commit: `method "sha"`, `confidence 1.0`, `signals {"sha": first commit sha}`;
   - otherwise `method "heuristic"`, `confidence = round(uniform(0.55, 0.9), 2)` via `rng("item_pick", sid)`, `signals {"files": n_overlap, "window_s": gap}`.
   - Children copy the parent's method, confidence, and signals.
   - `created_at` = the PR `created_at`.
10. **`pr_rework.jsonl`:** merged PRs only, `rework_ratio = round(uniform(rework_ratio.min, rework_ratio.max) × (1 + 0.5 × rounds), 3)` capped at 0.95.

**Tests (`test_git.py`):**
- `test_artifact_keyset_matches_mapper` — build one PR, review, commit, and issue dict and compare **key sets** with `map_github` outputs from the existing fixtures under `tests/server/ingest/git/` (find the fixture loader the mapper tests use; if none can produce all four kinds, construct minimal webhook payloads in the test).
- `test_numbering_unique_per_repo_chronological`
- `test_temporal_order_per_pr` — see below.
- `test_reviewers_not_author`
- `test_revert_references_merge_sha`
- `test_link_confidence_parity`
- `test_links_only_to_existing_prs`
- `test_children_linked_like_parent`
- `test_open_prs_at_series_end`
- `test_events_source_key_unique`

`test_temporal_order_per_pr` checks: commit ≤ first review; `created_at ≤ reviews ≤ merged/closed`; events monotonic; the first linked session start < `created_at`.

### P7 — Request ledger and `session_info` content

This is the core of realism. Read `graph_fold.py` lines 556–1270 before starting.

**`org/requests.py`** — `simulate(session: SessionPlan, cfg_team, pricer, rng_domains) -> Ledger`. The ledger is a list of `Request{ts, model, input, cache_read, cache_write, output, turn, kind ∈ {normal, bust, resume, first, post_compaction}, interrupted: bool}` plus `turns` and `compactions`.

1. **Cache-write split** (the only place `input_combined` is split):
   - The request **shape** decides it: compute the shape (steps 2–4) first, then `cw_total = min(Σ_m input_combined_m − R_m, round(cr_total_m × CW_shape_m / CR_shape_m))` per model, where `R_m` is the model's request count (so ≥ 1 uncached input token per request).
   - `input_m = input_combined_m − cw_total_m`. If `cr_total_m == 0`, then `cw_total_m = input_combined_m − R_m`, floored at 0.
   - This keeps `input + cache_write == input_combined` exactly (I1).
2. **Request count:**
   - `mean_ctx = rng("requests", sid).uniform(mean_context_tokens.min, .max)`.
   - `R = clamp(round(Σ_m cr_total_m / mean_ctx), 2, 1500)`; `R = max(R, n_models_with_tokens)`.
   - Partition requests over models into contiguous runs: the dominant model first, then the others, with counts via `allocate_integer(R, model_processed_share)`, and ≥ 1 for each model with any tokens (steal from the largest).
3. **Turns:** split the `R` requests into turns with sizes geometric(mean `turn_size_mean_requests`, ≥ 1).
4. **Context shape:** unit scale; tokens are assigned later.
   - `c_0 ~ U(0.1, 0.3)`; per request `c_i = c_{i-1} + Exp(1) × 0.05`.
   - **Compactions:** `k_comp = poisson(compactions_per_100_requests × R / 100)` (`habit_compact`), at distinct positions chosen uniformly from requests 3..R-1. At a compaction position the context resets to `c_i = U(0.08, 0.2)` and that request is `post_compaction`. The trigger is `manual` with `manual_compaction_share`, else `auto`.
   - **Resumes** (`habit_resume`): each turn start except the first has probability `resume_prob_per_turn`. The request becomes `resume` (full rewrite) and the gap before it gets `≥ ttl_s + 60`.
   - **Busts** (`habit_bust`): each non-first request has probability `cache_bust_per_100_requests / 100`, excluding positions already resume or compaction. It becomes `bust`, with a gap `< ttl_s`.
   - **Per request:** `cr_shape_i = c_{i-1}` for a `normal` request, else 0 (`first`, `bust`, `resume`, `post_compaction`). `cw_shape_i = max(c_i − c_{i-1}, 0.002)` for `normal`, else `c_i`.
   - `CR_shape_m`, `CW_shape_m` = sums over the model's requests.
5. **Integer tokens per model:**
   - `cache_read`: `allocate_integer(cr_total_m, {i: cr_shape_i})`; if all shapes are 0, use uniform weights.
   - `cache_write`: `allocate_integer(cw_total_m, {i: cw_shape_i})`.
   - `input`: `allocate_integer(input_m, uniform)`.
   - `output`: `allocate_integer(output_m, {i: lognormvariate(0, 0.8)})`.
   - Keys `f"r{i:05d}"`.
6. **Context window:** if `max_i(cr_i + cw_i) > context_window_tokens`, add one compaction at the peak position and redo steps 4–5 (max 20 iterations, then raise `InputDataError` naming the session).
7. **Timestamps:** gaps are intra-turn `U(2, 40) s`, inter-turn `U(30 s, 10 min)`, resume `≥ ttl_s + 60`, bust `< ttl_s`.
   - Scale all **non-resume** gaps so the total equals `planned_duration_s`.
   - If resume gaps alone exceed 80% of the planned duration, drop resumes (convert to `normal`, recompute steps 4–5) until they fit.
   - `ts_0 = started_at`; the last `ts ≤ ended_at`.
8. **Interrupts:** each turn's last request has probability `interrupt_prob_per_turn` (`habit_interrupt`) of being `interrupted = True`.

**`org/content.py`** — `build_session_info(plan, ledger, item|None, pr|None, children_infos, cfg, pricer, lexicon) -> SessionInfoRecord`. Each field and its definition (`at = plan.started_at.date()`):

| Column / key | Definition |
|---|---|
| `models` | `{m: {input, output, cache_read, cache_write}}` summed from the ledger |
| `tokens_total` | Σ all four over `models` (matches `graph_fold.tokens_total_of`) |
| `cost_effective_usd_micros` | `pricer.cost_micros(models, at)` |
| `cost_usd` | `Decimal(cost_micros) / 1_000_000` quantized to 4 dp |
| `datasource` / `jsonl_lag_s` / `otel` | `"jsonl_only"` / `None` / `{}` |
| `repo_id` / `repo_id_provenance` | Session-side id / `"remote"` |
| `turn_count` / `long_turn_count` | `len(turns)` / turns with `duration_ms > graph_fold._LONG_TURN_THRESHOLD_S × 1000` (copy the constant; parity test) |
| `compaction_count` | `len(compactions)` |
| `subagent_count` | `len(children)` |
| `error_count` | Σ tools `failed` |
| `turns` (root only; `[]` for children) | per turn `{index (1-based), started, ended, duration_ms, tokens, long, source: "fallback", files_changed, message_count: 2 × requests_in_turn + 1, subagents_spawned, subagent_session_ids}` |
| `files` | Linked with PR: this session's share of the PR's files (P6 list), keyed by absolute path `f"/workspace/{repo_name}/{path}"`, value `{changes, created, extension, first_seen, last_seen, lines_added, lines_removed}`, lines = allocate over sessions of the PR by `T(s)`. Unlinked: 60% `{}` (`files` rng), else 1–3 doc/config paths with small line counts. Children: `{}` |
| `tools` | `{name: {ok, type: "builtin", count, failed, bytes_read_total, duration_ms_total: 0}}` for `Read, Edit, Write, Bash, Grep, Glob, TaskCreate, TaskUpdate`. Counts scale with R via fixed per-tool rates × `uniform(0.7, 1.3)`. `failed ~ binomial(count, 0.03)`. Add `Task` with `count = subagent_count` when > 0 |
| `skills` | `[]` |
| `subagents` (root) | per child `{depth: 1, files: {}, model: child dominant model, tools: child tools, origin: "human", tokens: child tokens_total, purpose: lexicon, agent_type: SUBAGENT_TYPES choice, duration_ms, inclusive_tokens: child tokens_total}`; `{}` for children |
| `pr_list` | linked with PR: `[{method, number, repo_id: git repo id, confidence, artifact_id, change_type: null}]`; else `[]` |

**The `info` blob:**

| Key | Definition |
|---|---|
| `title` | Linked: `session_title(pr title)`. Unlinked: `UNLINKED_TITLES`. Children: the purpose |
| `repo` | `{cwd: f"/workspace/{repo_name}", git_branch: item branch or default_branch, repo_id, repo_key: repo_name, repo_id_provenance: "remote", repos: [{refs: R, primary: true, repo_id, repo_key, repo_id_provenance: "remote"}]}` |
| `cache` | `{hit_ratio: round(Σcr / (Σcr + Σinput), 4)}`, or `0.0` when the denominator is 0 (mirror `cache_of`) |
| `carry` | `{requests: R, cache_read_tokens: Σcr, carry_usd_micros: pricer.channel_micros(... "cache_read"), peak_context: max(cr_i + cw_i), mean_context: round(mean(cr_i + cw_i))}` |
| `channel_cost` | `{input, output, recache}` per D9 via `pricer.channel_micros`. Must equal `graph_fold.channel_cost_of(models, pricing, at)` (parity test) |
| `cache_busts` | Events for `bust` requests `{ts, gap_s, model, usd_micros, context_before, rewrite_tokens}`, plus `count` and `total_usd_micros`. `rewrite_tokens = cw_i`, `context_before = cr_{i-1} + cw_{i-1}`. Price `usd_micros` **exactly as `graph_fold.cache_busts_of` does**: read lines 883–948 and port the same formula, including `_rewrite_recoverable_usd_micros` if it uses it |
| `resume_cost` | Same approach for `resume` requests, mirroring `graph_fold.resume_cost_of` (lines 778–882): events `{ts, turn, gap_s, ttl_s, usd_micros, reload_tokens, context_before, recoverable_usd_micros}`, `count`, `total_usd_micros`, `total_recoverable_usd_micros`, `projected{context_tokens, ttl_s, usd_micros}` |
| `interrupted` | Events for interrupted requests `{ts, tokens: all 4 kinds of that request, usd_micros: price of that request}`, plus `count`, `total_tokens`, `total_usd_micros` (mirror `interrupted_of`) |
| `compactions` | `[{ts, before, after, trigger, reclaimed: before − after, duration_ms: U(20000, 150000)}]` |
| `context_curve` (root only) | per turn `{ts: turn end, index, compaction: any compaction in turn, context_tokens: cr + cw of the turn's last request}` |
| `story` (root only) | `curve` = per request `[minutes_since_start, cumulative_tokens, cumulative_tokens]` downsampled with a port of `graph_fold._story_downsample`; `shape` via a port of `graph_fold._story_shape`; `anchors: {superlatives: []}` |
| `segments` (root only) | See below |
| `practices` (root only) | Keys exactly as `graph_fold.practices_of` returns (read it): `at_refs, auto_compactions, manual_compactions, clear_used: null, delegation_ratio: child tokens / inclusive, exitplan_count (plan_mode_prob), first_prompt_chars U(40, 1200), fresh_session, interrupts, plan_phase: null, review_subagent, subagent_research, verified (verified_prob), verifier_pass_rate, verifier_runs` |
| `reasoning_tokens` | `round(Σ output × U(0.2, 0.5))` |
| `duration_ms` | `(ended_at − started_at)` in ms |
| `event_count` | `3R + 2 × turns` |
| `own_event_count` | = `event_count` |
| `inclusive_tokens` | Only when children exist: own `tokens_total` + Σ children `tokens_total` |
| `mcp_servers` | `{attached: [], by_server: {}}` |
| `git_commands` | `{commit: n commits, push: n commits > 0 ? 1 : 0, checkout: 1 if linked else 0, branch: 0, fetch: 0 or 1, pull: 0 or 1, merge: 0}` |
| `read_then_discard` | `{events: [], total_bytes_read: 0, total_est_tokens: 0}` with a `TODO(synthetic-org)` noting it is not simulated |
| `synthetic` | `{generator_version, org_seed}` |
| `tokens_total` | mirrors the column |

**`segments`** (root only): 1–4 contiguous turn ranges (`segments` rng; count = `min(len(turns), 1 + R // 150)`, capped at 4).

- Labels: linked sessions use `[COMPREHEND|PLAN]? → main → [REVIEW|OPS]?`, where main = `{feature: IMPLEMENT, fix: BUG_FIX, refactor: IMPLEMENT, test: IMPLEMENT, docs: DESIGN, chore: OPS}`. Unlinked sessions pick from `COMPREHEND, DESIGN, OPS, MISC`. Labels come from `session_type._SCORED_LABELS + ("MISC",)`.
- Fields: `{index, t0, t1, label, confidence: U(0.5, 0.95), turns, files, beats: {setup, survey, build, check, pause, revision_loop} counts ≥ 0, models: Σ of the segment's requests, repo_key, cost_usd_micros: price(models), carry_usd_micros: price(cache_read)}`.
- Σ segment models == session models (exact, since requests partition).

**Children's `info`:** the same keys **except** `story`, `context_curve`, `segments`, `practices` (D4 observation). `inclusive_tokens` is absent.

**Tests:**

`test_requests.py`:
- per-model exactness of all four categories vs the plan (I1 at session level);
- `input + cache_write == input_combined`;
- `input ≥ R_m`;
- context window never exceeded;
- `ts` within `[started_at, ended_at]` and monotonic;
- resume gaps ≥ `ttl_s + 60`, bust gaps < `ttl_s`;
- a habits change leaves per-model `input_combined`, `cache_read`, and `output` unchanged (I2b at unit level).

`test_content.py`:
- every key in the table present with its type (a schema dict in the test);
- `hit_ratio` formula;
- I5: `|input + output + recache + carry − cost| ≤ 4`, each recoverable total ≤ cost, `carry ≤ cost`;
- Σ segments cost == Σ over segments of `price(segment models)`, and Σ segment models == session models;
- `turn_count` / `compaction_count` / `subagent_count` consistent with the lists;
- children lack the four root-only keys.

`test_parity_graph_fold.py` imports `server.worker.graph_fold` (test-side only) and asserts equality on generated data for:
- `channel_cost_of`;
- `tokens_total_of`;
- `_story_shape`;
- `_story_downsample`;
- `_LONG_TURN_THRESHOLD_S`;
- the bust and resume pricing helpers (call `_rewrite_recoverable_usd_micros` with the same inputs the port used).

### P8 — Orchestration, output, report (`org-expand` end to end)

**`org/expand.py`** — `run_org_expand(config, output_dir, *, log=None, run_id=None) -> GenerationResult`. It follows `runner.run_series`'s structure exactly (`_operation`, `_check_output`, `DatasetWriter`, `_snapshot`, `_finish`). Reuse those helpers by importing them from `forecast.synthetic.runner`; they are module-level. If a leading underscore makes you hesitate: importing is permitted; do not copy them.

**Snapshots under `inputs/`.** `runner._snapshot` only handles JSONL record files, because it re-reads them with a record type and also copies the sibling `manifest.json`. Use it for `usage.jsonl`, `user_profiles.jsonl`, users, and roster. For `config.yaml`, `manifest.json`, and `pricing.yaml`, call `writer.snapshot(source, destination)` directly and record their sha256 in `input_sources`.
- `series/usage.jsonl`
- `series/user_profiles.jsonl`
- `series/config.yaml`
- `series/inputs/users/users.jsonl`
- `series/inputs/roster/teams.jsonl`
- `series/manifest.json`
- `pricing/pricing.yaml`

**Order:**
1. validate
2. directory + repos
3. sessions (P4)
4. work (P5)
5. git (P6)
6. requests + content per session (P7), streamed into the writer in `(started_at, session_id)` order
7. expected rollup
8. report + manifest

**Record files (exact names):**
- `directory_teams.jsonl`, `directory_people.jsonl` (the admin included), `user_map.jsonl`, `user_prefs.jsonl`
- `git_installations.jsonl`, `git_repos.jsonl`, `repositories.jsonl`
- `git_artifacts.jsonl` (sorted by `(repo_id, kind, number|created_at, artifact_id)`), `git_artifact_events.jsonl`, `pr_issue_links.jsonl`
- `sessions.jsonl`, `session_info.jsonl`, `session_git_links.jsonl`
- `pr_rework.jsonl`, `budgets.jsonl`, `allowances.jsonl` (empty files when not configured)
- `expected_rollup.jsonl`

**`sessions.jsonl` fields:**
- `session_id`, `producer_id: f"synthetic:{github_owner}"`, `org_id: directory_org_id`, `user_id` (mainline), `agent`
- `agent_version`: `rng("layout", l1_user, date)` choice from `agent_versions`
- `status: "idle_closed"`, `started_at`, `last_event_at = ended_at`, `ended_at`
- `signal_blob: {"events": []}`, `info_blob: {"synthetic": true, "generator_version", "org_seed"}`
- `parent_session_id`, `subagents: [child ids]`

**`expected_rollup.jsonl`:** one row per `usage.jsonl` row, mapped: `date`, `user_id` (mainline), `repo_id` (session-side), `agent`, `model`, `input_combined = tokens.input`, `cache_read`, `output`.

**Before the manifest:** re-aggregate `sessions` + `session_info` in memory and assert equality with it (I1). Raise `InputDataError("rollup mismatch ...")` on any difference.

**Manifest summary counts:** teams, users, repos, sessions_root, sessions_child, prs, prs_merged, prs_open, prs_closed_unmerged, reverts, issues, reviews, commits, links, calibrated_base, merged_prs_per_dev_week (realised), total_cost_usd.

**`org/report.py`** sections:
- inputs + seeds
- calibration (target vs realised, per team)
- per team: devs, sessions, cost, merged PRs, $/PR p50/p90, LOC per $, recoverable totals by bucket, review rounds mean, revert/abandon counts
- per repo: PRs, issues, cost
- rollup check result
- warnings

**Tests:**
- `test_expand_invariants.py` runs a **small** org (2 teams × 3 users, 2 repos, 21 days) end to end, then checks I1, I3, I4, I5 over the files.
- `test_cli_org.py`: `--validate-only` writes nothing; an existing output → exit 1; a bad config → exit 2; the success JSON summary has the counts; stderr lines are JSON.
- `test_determinism.py`:
  - I2a: two runs → byte-identical record files (compare sha256 of every `*.jsonl`);
  - I2b: change only `habits.cache_bust_per_100_requests` → `sessions.jsonl`, `git_artifacts.jsonl`, `session_git_links.jsonl`, `expected_rollup.jsonl` byte-identical, and per-session `(input + cache_write, cache_read, output)` per model identical.

### P9 — Example configs, retiming chain, wrapper

**`econ-forecast/config/synthetic/org/`:**

- **`users.yaml`:** `count: 22`, `org_domain: acme.dev`, `seed: 42`, `identity_style: consistent`.
- **`roster.yaml`:** platform 4, payments 5, growth 5, data 4 (18 rostered, 4 unassigned).
- **`series.yaml`:**
  - `days: 120`, `timezone: America/Los_Angeles`, `start_date: 2026-05-22` (placeholder; retimed by the chain).
  - Profiles light 300k / mid 1.2M / heavy 4M.
  - Agent `claude_code`, `usage_share 1.0`, models `{claude-sonnet-5: .70, claude-opus-5: .20, claude-haiku-4-5-20251001: .10}`.
  - Projects: `platform_infra, payments_api, web_app, experiments, data_pipeline`. Team project mixes:

    | Team | Project mix |
    |---|---|
    | platform | platform_infra .80, payments_api .10, web_app .10 |
    | payments | payments_api .85, platform_infra .15 |
    | growth | web_app .75, experiments .25 |
    | data | data_pipeline .85, platform_infra .15 |

  - Profile mixes: platform light .25 / mid .5 / heavy .25; payments light .4 / mid .4 / heavy .2; growth light .2 / mid .6 / heavy .2; data mid .25 / heavy .75.
  - `growth` activity: `weekend_zero_probability: 0.4`.
  - Events: random spikes as in the Layer 1 default; `dated_spikes` for payments `release-crunch`, days 95–99, ×2.5; `zero_periods` for org `holiday`, days 60–61, and user `<one growth member's l1 id>` from day 0 to day 44 (new hire).
  - To fill the new-hire id: run user-create + roster-create once, pick the growth member with the lexicographically smallest `user_id`, and hard-code it with a comment explaining the choice.

- **`org.yaml`:** `github_owner: acme`, admin `vp-eng` / "Jordan Avery" / `jordan.avery@acme.dev`, `target_merged_prs_per_dev_week: 3.0`. Five repos (names `platform-infra, payments-api, web-app, experiments, data-pipeline`) with 3–5 modules each and realistic extensions:

  | Repo | Main extensions |
  |---|---|
  | payments-api | .py / .sql |
  | web-app | .tsx / .ts / .css |
  | data-pipeline | .py / .sql / .yaml |
  | platform-infra | .tf / .go / .yaml |
  | experiments | .ts / .py |

  Team overrides implementing the design §8 table exactly:

  | Team | Overrides |
  |---|---|
  | platform | multiplier 0.7; pr size_median 320; review changes_requested .35; habits cache_bust .2, resume .02 |
  | payments | multiplier 1.0; review changes_requested .55, first_review_median 8, revert .005, abandon .03 |
  | growth | multiplier 1.1; pr size_median 90; work unlinked .15; review first_review_median 2, changes_requested .15, revert .06, abandon .10 |
  | data | multiplier 1.6; sessions mean_input_tokens 2.5M, subagent_prob .45; habits cache_bust 2.0, resume .12, compactions 2.0, interrupt .06; work unlinked .30 |

  Budgets: org 25000 and per team (platform 6000, payments 6000, growth 5000, data 9000) monthly USD.

**`org/cli_synth_org.py`** (`python -m forecast.synthetic.org.cli_synth_org`):
- Arguments: `--config-dir DIR --output DIR [--end-date YYYY-MM-DD|today] [--validate-only]`.
- Steps:
  1. create `--output` (refuse if it exists);
  2. copy the four YAMLs into `--output/configs/`;
  3. if `--end-date`, rewrite `series.yaml`: `start_date = end − days + 1`, and shift every event date by the same delta;
  4. rewrite relative paths so the chain points at `--output/{users,roster,series,org}`;
  5. run `run_users`, `run_roster`, `run_series`, `run_org_expand` in-process;
  6. print one JSON summary (all four run ids and output dirs, org counts).
- `today` is resolved **once** in the CLI (the only allowed wall-clock read) and written into the copied config, so the saved configs replay deterministically.

**`econ-forecast/utils/synth_org.sh`:** follows `synt_gen.sh`/`_forecast_env.sh` conventions and calls `python -m forecast.synthetic.org.cli_synth_org "$@"`.

**Tests (`test_cli_org.py` additions):**
- the retiming shift is correct (events move with the start);
- `--end-date 2026-09-18` gives a series end of 2026-09-18;
- the chain on a tiny config dir produces all four datasets.

**Done when:** the full example runs through the wrapper. Record wall time (target < 60 s) and paste `report.md`'s calibration and per-team tables into the handoff.

### P10 — Loader (mainline)

**`config/queries/synthetic.yaml`:**

- **`truncate_all`:** `TRUNCATE` of exactly these tables:

  ```
  session_git_links, pr_classification, pr_issue_links, git_artifact_events, git_artifacts,
  git_repos, git_installations, session_info, sessions, repositories, budgets, allowances,
  role_assignments, team_members, teams, identity_links, users, feedback, queue_jobs
  ```

  with `RESTART IDENTITY`.
- **`set_user_prefs`:** `UPDATE users SET github_user=:github_user, timezone=:timezone, primary_team_id=:primary_team_id WHERE user_id=:user_id`.
- **`set_rework_ratio`:** `UPDATE pr_classification SET rework_ratio=:rework_ratio WHERE artifact_id=:artifact_id`.
- **`daily_rollup`:**

  ```sql
  SELECT (s.started_at AT TIME ZONE :tz)::date AS date, s.user_id, i.repo_id, s.agent, m.key AS model,
         SUM((m.value->>'input')::bigint + (m.value->>'cache_write')::bigint) AS input_combined,
         SUM((m.value->>'cache_read')::bigint) AS cache_read, SUM((m.value->>'output')::bigint) AS output
  FROM sessions s JOIN session_info i USING (session_id), jsonb_each(i.models) m
  WHERE s.info_blob->>'synthetic' = 'true'
  GROUP BY 1,2,3,4,5
  ```

- **`row_counts`:** one `SELECT count(*)` per loaded table, as a UNION.

**`src/server/storage/synthetic_load.py`:**
- `load_dataset(dataset_dir: Path, dsn: str, *, replace: bool, dry_run: bool, log) -> LoadResult`.
- Steps, in this exact order:
  1. **Guard (D13):** parse the dsn with `sqlalchemy.engine.make_url`. If `url.database` is missing or does not end with `_synth`, raise `SyntheticLoadError` (exit 2) before connecting.
  2. **Manifest:** read `manifest.json`; require `operation == "org"` and `status == "complete"`; verify sha256 of every file listed. Mismatch → exit 2.
  3. **Org-id check:** the dataset's `directory_org_id` must equal `server.directory.config.load_config("config/directory.yaml").default_org_id`, else exit 2.
  4. **`--dry-run`:** print planned row counts per table and exit 0 without connecting.
  5. **Migrate:** `server.storage.dbm.operations.migrate(StorageConfig(dsn=dsn))`.
  6. **`--replace`:** run `truncate_all` in one transaction.
  7. **Directory:** `server.directory.sync(session, catalog, [DirectoryRecord.from_dict(r) ...], teams=[TeamRecord.from_dict(t) ...])`, then `set_user_prefs` per row. Assert that every `user_map.user_id` now exists in `users`; a mismatch means D5 broke (exit 1).
  8. **Tables** in this order, each via `pg_insert(Model.__table__).values(batch).on_conflict_do_update(index_elements=<pk cols>, set_=<all non-pk cols>)` in batches of 1000: `git_installations → git_repos → repositories → git_artifacts → pr_issue_links → sessions → session_info → session_git_links`. `git_artifact_events` uses `on_conflict_do_nothing(index_elements=["source_key"])` and omits `event_id`. Convert ISO strings to aware datetimes and JSON to Python objects before insert; set `created_at`/`updated_at` columns the record lacks to the record's own `started_at` / `created_at`.
  9. **Classification:** `pr_classify(s, catalog, PrClassConfig, {"artifact_id": id})` for every PR artifact, then `set_rework_ratio` from `pr_rework.jsonl`. Find the `PrClassConfig` loader in `server/worker/pr_class/config.py` and load `config/pr_class.yaml`.
  10. **Budgets:** `server.api.manager.budgets.put_budget(s, catalog, scope_type, scope_id, period, amount)` / `put_allowance(...)` for each row.
  11. **Post-load check:** run `daily_rollup` with the series tz from the dataset config and compare with `expected_rollup.jsonl` as a multiset of tuples. Any difference → exit 1, listing up to 20 diffs.
  12. **Result:** `LoadResult{row_counts, admin_viewer_id, sample_lead_viewer_id, sample_dev_viewer_id, rollup_ok: True, duration_ms}`.
- Each numbered step logs `synthetic_load.<step>` with counts and duration.

**`src/server/storage/cli_load_synthetic.py`:**
- Arguments: `--dataset DIR --dsn DSN [--replace] [--dry-run]`.
- JSON summary on stdout; JSON logs on stderr.
- Exit codes: 0 ok, 2 guard/manifest/config error, 1 unexpected or rollup failure.
- The summary includes a `next_steps` list: the exact API command (see P10b) and the web UI env vars.

**`utils/load_synthetic.sh`:** sources the repo env like the other utils wrappers and runs `python -m server.storage.cli_load_synthetic "$@"`.

**Tests:**
- `test_synthetic_load_unit.py` (no DB):
  - the guard rejects `econ`, `econ_synth_x`, and a missing db name, and accepts `econ_synth`, `talium_test_synth`;
  - manifest tamper is detected;
  - an org-id mismatch is detected;
  - dry-run makes zero connections (monkeypatch `get_engine` to raise).
- `test_synthetic_load.py` (skip unless `TALIUM_TEST_DSN` is set **and** its database ends with `_synth`):
  - generate the small org from P8 into `tmp_path`;
  - load with `--replace`;
  - assert row counts equal the file counts, the rollup check passed, and a second load without `--replace` leaves every count unchanged (idempotent);
  - `pr_classification` has one row per PR, and `rework_ratio` is set on merged PRs;
  - `viewer_is_user_id` resolves the admin id.

**P10b — serving (mainline, minimal):**
- `cli_serve_api.py`: add `--port` (default: config value). No other change.
- `src/webui/vite.config.ts`: `target: process.env.VITE_API_TARGET ?? "http://localhost:8090"`.
- Document in the org README:

```bash
TALIUM_DB_DSN=postgresql+psycopg://postgres@localhost:5433/econ_synth python -m server.api.cli_serve_api --port 8091
VITE_DATA_SOURCE=api VITE_API_TARGET=http://localhost:8091 VITE_VIEWER_ID=github:vp-eng VITE_VIEWER_ROLE=admin npm run dev
```

  Before running these, confirm how the stack's Postgres credentials are provided (`.env`, `TALIUM_DB_DSN`). Never hard-code a password in docs or code. The database is created by the operator: `CREATE DATABASE econ_synth;` (the loader does not create databases; `operations.init` has the same TODO).

### P11 — API smoke test, README, handoff

**`tests/server/api/test_synthetic_smoke.py`** (same DB gate as P10). Load the small org, build the app with `create_app(ApiConfig, StorageConfig(dsn))`, and use FastAPI `TestClient`:

- **Admin viewer** (header `x-talium-viewer: github:<admin>`): `GET /v1/org/overview`, `/v1/org/usage`, `/v1/teams`, `/v1/projects`, `/v1/teams/{first team}`, `/v1/projects/{first repo session-side id}`, `/v1/prs/{first merged PR}`, `/v1/people/{first dev}`, `/v1/economics`, `/v1/features/{first graphql issue}`.
  - Each → 200.
  - Payloads have a non-zero spend and a non-empty team/project list where applicable. Assert on the specific keys, found by reading `server/api/schemas/manager.py`; do not assert only on status codes.
- **One developer:** `GET /v1/users/{id}/overview`, `/sessions`, `/insights`, `/repos`, and one `/v1/sessions/{id}` → 200 and non-empty.
- **A plain dev** hitting `/v1/org/overview` → 403.

**`econ-forecast/src/forecast/synthetic/org/README.md`:** purpose, pipeline diagram, quick start (wrapper → create db → load → serve → web UI), config reference pointer (this plan §3), invariants, the worker warning (never point the worker or `cli_refold` at `*_synth`), troubleshooting.

**Handoff report:** see the list below.
- mapping table (§6) with evidence per row;
- exact commands run and their outputs (test counts; example run wall time; load summary JSON);
- the report.md calibration/team tables;
- screenshots of the web UI Overview, Teams, and one PR detail against `econ_synth`, using the Browser tools;
- every `TODO(synthetic-org)` added;
- known limitations;
- the status "ready for independent review" (not "done").

---

## 6. Acceptance mapping

| Req | Verified by |
|---|---|
| I1 rollup exactness | `test_sessions::test_split_exact_per_category`, `test_requests` exactness, `expand` in-memory assert, `test_expand_invariants`, loader `daily_rollup` check, `test_synthetic_load` |
| I2a determinism | `test_determinism` byte-identical rerun; P1 `test_consistent_deterministic` |
| I2b knob independence | `test_determinism` habits change; `test_requests` unit variant |
| I3 referential integrity | `test_expand_invariants` (every id reference resolves in-dataset); loader directory assert |
| I4 temporal | `test_sessions` layout tests; `test_git::test_temporal_order_per_pr`; `test_requests` ts checks |
| I5 cost consistency | `test_content`; `test_parity_graph_fold` |
| I6 calibration | `test_work::test_calibration_hits_target` |
| I7 Layer 1 untouched | full pre-existing `econ-forecast/tests`; `test_independent_bytes_unchanged` |
| G1 dashboard renders | `test_synthetic_smoke` + handoff screenshots |
| D13 guard | `test_synthetic_load_unit` |

---

## 7. Commands

```bash
# unit + generator tests
venv/bin/python -m pytest econ-forecast/tests/synthetic -q
# full example
./econ-forecast/utils/synth_org.sh --config-dir econ-forecast/config/synthetic/org --output econ-forecast/artifacts/org-demo --end-date today
# DB tests (operator creates econ_synth_test first)
TALIUM_TEST_DSN=postgresql+psycopg://<user>@localhost:5433/econ_synth_test venv/bin/python -m pytest tests/server/storage/test_synthetic_load.py tests/server/api/test_synthetic_smoke.py -q
# load the demo
./utils/load_synthetic.sh --dataset econ-forecast/artifacts/org-demo/org --dsn postgresql+psycopg://<user>@localhost:5433/econ_synth --replace
# regression: everything else still green
./utils/test_all.sh -m "not slow"
```

---

## 8. Out of scope (do not build)

- Raw JSONL transcripts, webhook payload replay, OTel tables, the `otel_*` true-up.
- Codex/GPT sessions (D1).
- Any change to fold, worker, ingest, API aggregation, or the forecast engine.
- Database creation or role provisioning.
- A UI toggle between real and synthetic data.
- `read_then_discard` simulation (TODO only).
