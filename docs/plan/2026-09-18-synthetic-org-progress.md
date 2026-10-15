# Synthetic Org Data — Progress Log

Tracks task-by-task progress against `docs/plan/2026-09-18-synthetic-org-implementation.md`.
Append one entry per completed task: task id, commit sha, tests run + pass counts, deviations,
TODOs added, open issues. Re-read the relevant plan section before starting each task.

## Setup (pre-P1)

- Confirmed `git log --oneline -3` includes `090d31e` on `feat/synthetic-org-data`. OK.
- Read in full, in order: `CLAUDE.md`, `AGENTS.md`, the implementation plan (1011 lines),
  `econ-forecast/docs/design/synthetic-org-expansion-design.md`,
  `econ-forecast/docs/design/synthetic-data-generation-design.md`, every file under
  `econ-forecast/src/forecast/synthetic/` (Layer 1) plus `contracts.py`/`artifact_io.py`,
  and `src/server/worker/graph_fold.py` (the pricing/story/practices/cache-bust/resume-cost
  section, lines ~556-1275).
- Environment: no `venv/` existed in this worktree. System `python3` resolved to 3.14; created
  `venv/` with `/usr/local/bin/python3.11` (3.11.1) instead, per the plan's Python 3.11+
  requirement. `pip install -r requirements.txt` succeeded; Faker==40.38.0 confirmed installed.
- Baseline: `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` → **88 passed**, 0
  failures, before any change. This is the pre-P1 baseline referenced by I7.

Proceeding to P1.

## P1 — Layer 1 identity style

- Commit: `e60049e` — feat(synthetic): add opt-in consistent identity style (P1)
- Files: `econ-forecast/src/forecast/synthetic/config.py` (`UserOptions.identity_style`),
  `models.py` (`UserRecord.github_login` + a `model_serializer(mode="wrap")` that drops it when
  `None`, for byte-identity), `users.py` (consistent branch: `first`/`last` via
  `fake.first_name()`/`fake.last_name()`, email local `_local(f"{first}.{last}")[:64]` reusing the
  existing `_N` collision loop, login `re.sub(r"[^a-z0-9-]", "", _local(first)+_local(last)[:1])`
  or `"user"`, `-2`/`-3`… collision suffix, truncated to 39 chars), plus
  `econ-forecast/tests/synthetic/org/test_identity.py` (new `org/` test subdir per plan §2).
- Pre-change baseline hash (computed **before** editing `users.py`/`models.py`, from the
  then-current independent-only code path): config `{count: 20, org_domain:
  baseline-check.test, seed: 999}` → `users.jsonl` sha256
  `e6f9540e9701d07727fe2cb24f15c09b621c65eb1c9423e01670aa02d0e8a150`. Hard-coded in
  `test_independent_bytes_unchanged` with the exact command used, and it still matches after the
  change (github_login omitted when unset keeps independent-style bytes identical).
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_identity.py -q` → **5
  passed** (`test_consistent_email_matches_name`, `test_login_unique_and_valid`,
  `test_consistent_deterministic`, `test_user_ids_style_independent`,
  `test_independent_bytes_unchanged`). Full suite:
  `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` → **93 passed** (88 pre-existing +
  5 new), 0 failures.
- Deviations: none from the plan.
- TODOs added: none.
- Open issues: none.
- Note: first commit accidentally included a `Co-Authored-By` trailer from the session's default
  attribution reminder; amended immediately per CLAUDE.md rule 3 / plan §0.8 (no Co-Authored-By,
  no AI-tooling mentions, anywhere). Watch for this on every subsequent commit.

Proceeding to P2.

## P2 — Layer 2 foundations: config, models, ids, lexicon, pricing

- Files added: `econ-forecast/src/forecast/synthetic/org/{__init__,config,models,ids,lexicon,pricing}.py`,
  `econ-forecast/tests/synthetic/org/{conftest,test_org_config,test_ids}.py`.
- `org/config.py`: `OrgConfig` + sub-models per plan §3 (`OrgOptions`, `RootTeam`, `Group`,
  `Admin`, `Calibration`, `Repo`/`Module`, `WorkDefaults`/`PrDefaults`/`ReviewDefaults`/
  `HabitsDefaults`/`SessionsDefaults`, `TeamOverride`, `Onboarding`, `BudgetsConfig`,
  `AllowancesConfig`), all built-in defaults matching §3.3 exactly (asserted by
  `test_empty_defaults_yields_built_ins`). `load_org_config(path)` (pure parse + path
  resolution) and `validate_org_inputs(config, series_manifest, users, teams, series_cfg,
  pricing) -> ResolvedOrg` (every §3.5 cross-file rule, one `ConfigError` naming the field per
  rule). Field-level team-override merge (`_merge_block`): an override field replaces the
  default field whole (mixtures included), fields the operator didn't write still come from
  the (possibly org-level-overridden) default -- tested by
  `test_team_override_is_field_level`/`test_mixture_override_replaces_whole`.
- `org/models.py`: `OrgRec` (`extra="forbid"`, unlike Layer 1's tolerant `Rec`) +
  `UserMapRecord`, `ExpectedRollupRecord`, `PrReworkRecord`. Grows further in P3+.
- `org/ids.py`: `repo_num`, `installation_id`, `session_id`, `child_session_id`, `sha`,
  `review_id`, `team_slug` (D7), `artifact_id`, and the `DOMAINS` tuple (plan §4, 27 domains).
  `test_domain_registry_complete` ASTs every `org/*.py` file for `rng_for(`/`rng(` calls and
  asserts every domain literal used is in `DOMAINS` -- passes vacuously now (no rng calls exist
  before P4) and will start exercising real calls once `sessions.py` etc. land.
- `org/lexicon.py`: `VERBS` (6 change types x >=30 entries), `OBJECTS`, `QUALIFIERS`,
  `FILE_STEMS`, `SUBAGENT_PURPOSES`, `UNLINKED_TITLES`, `FEATURE_NOUNS` (all >=30 entries),
  `SUBAGENT_TYPES`, and `pr_title`/`session_title`/`issue_title`/`branch` helpers. Manually
  smoke-tested (not committed as a test, since plan §2's file list has no `test_lexicon.py`);
  P7's `test_content.py` exercises these for real once sessions produce PR titles.
- `org/pricing.py`: `Pricer` wraps a `PricingConfig` loaded from the snapshotted pricing file.
  `price_micros` raises `ConfigError` on an unpriced model (never `None`). `channel_micros`
  ports `graph_fold._price_channel` verbatim (per-model Decimal sum for one token kind, round
  once). `channel_cost` assembles the 3-channel dict (`input`/`output`/`recache`) matching
  `graph_fold.channel_cost_of`'s shape, for the P7 parity test. `cost_micros` sums every
  model's full-tokens price.
- **Deviation / documented judgment call:** plan §5 P2 states `channel_micros(models: dict, at)
  -> dict`, but P7's content.py table calls `pricer.channel_micros(... "cache_read")` -- a
  3-argument, single-channel call returning `int | None`, not a `dict`. These two spellings
  conflict. Resolved by implementing `channel_micros(models, kind, at) -> int | None` (the
  generic per-kind pricer P7 actually calls, mirroring `_price_channel`'s signature) plus a
  separate `channel_cost(models, at) -> dict` for the 3-channel `content.py` field and its
  parity test. Manually verified against `config/pricing.yaml` (see progress log commands run
  this session); no test file added per plan §2's fixed file list -- covered for real by P7's
  `test_parity_graph_fold.py`. Flagging this for the independent reviewer rather than resolving
  it silently.
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_org_config.py
  econ-forecast/tests/synthetic/org/test_ids.py -q` → **29 passed** (19 + 10). Full suite:
  `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` → **122 passed** (93 + 29), 0
  failures.
- `--validate-only` wiring is explicitly deferred to P3 per the plan ("Done when: config tests
  and id tests pass, and `--validate-only` works (wired in P3)").
- TODOs added: none.
- Open issues: the channel_micros signature deviation above, for reviewer sign-off.

Proceeding to P3.

## P3 — CLI wiring, directory, repos

- Files added: `econ-forecast/src/forecast/synthetic/org/{directory,repos,expand}.py`,
  `econ-forecast/tests/synthetic/org/test_repos_directory.py`. Files modified:
  `econ-forecast/src/forecast/synthetic/cli_synt_gen.py`,
  `econ-forecast/src/forecast/synthetic/org/config.py` (added `read_series_snapshot`, a shared
  helper for the CLI's org-expand `_validate` path and the test conftest -- not itself listed
  in plan §2, but it only extracts logic that both the CLI and the test suite independently
  needed; no new file added), `econ-forecast/src/forecast/synthetic/org/models.py` (added
  `GENERATOR_VERSION`, `synthetic_marker`, and the directory-wire / git-repo record models).
- `cli_synt_gen.py`: `OPERATIONS["org-expand"] = "org"`; `_validate("org", ...)` reads the
  series snapshot + pricing and runs `validate_org_inputs`, returning
  `{users, teams, rostered_users, repos}`; the loader/runner dispatch dicts gained an `"org"`
  entry (`load_org_config` / `run_org_expand`); epilog text updated. Exit-code contract
  unchanged (`ConfigError` -> 2, the P8-pending `NotImplementedError` -> 1 via the generic
  unexpected-failure path, `ArtifactError` -> 1).
- `org/expand.py`: stub `run_org_expand` raising `NotImplementedError`, with a
  `TODO(synthetic-org)` pointing at task P8.
- `org/directory.py`: `build_directory(resolved) -> (DirectoryWire, user_map, user_prefs)`.
  Root team + groups + Layer-1 teams (via `ids.team_slug`, D7); leads = explicit `org.leads`
  first, else the team member whose *profile's* `daily_input_tokens` is highest (generalizes
  "heavy > mid > light" beyond the example config's literal profile names), ties broken by
  `rng_for(seed, "lead", team_id)`; person records match the §3 P3 wire shape exactly (verified
  by parsing every emitted record with `server.directory.models.DirectoryRecord.from_dict` /
  `TeamRecord.from_dict`); admin gets an org-scope admin grant and no team; unrostered Layer 1
  users are never emitted (checked directly against `user_map`'s l1 ids).
- `org/repos.py`: `build_repos(resolved) -> (installation, git_repos, repositories)` per D8;
  `installed_at` = series start - 30d @ 00:00 UTC, `last_seen_at` = series end 23:59 local ->
  UTC; `lower(full_name)` bridges the git-side and session-side repo id schemes (tested
  directly).
- Manually exercised end-to-end (commands run this session, output in scrollback, not
  committed): built an 8-user/2-team/2-repo series + org.yaml by hand, ran
  `cli_synt_gen.py --op org-expand --validate-only` (exit 0, correct JSON summary), then called
  `build_directory`/`build_repos` directly and parsed every record with the mainline directory
  models -- all green before writing the corresponding pytest tests.
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_repos_directory.py
  -q` → **12 passed**. Full suite: `venv/bin/python -m pytest econ-forecast/tests/synthetic -q`
  → **134 passed** (122 + 12), 0 failures.
- Deviations: none beyond the P2 `channel_micros` note already logged.
- TODOs added: one, `# TODO(synthetic-org): implement the org-expand pipeline ... per plan §5
  task P8` in `org/expand.py::run_org_expand`. Will be removed when P8 lands.
- Open issues: none new.

Proceeding to P4.

## P4 — Sessions: split, subagents, day layout (I1 lives here)

- Files added: `econ-forecast/src/forecast/synthetic/org/sessions.py`,
  `econ-forecast/tests/synthetic/org/test_sessions.py`. `conftest.py` gained shared `resolved`
  and `usage_records` fixtures (moved `resolved` out of `test_repos_directory.py`, where it was
  local, so P4+ files can reuse it without duplicating the build_org_config/validate_org_inputs
  boilerplate).
- `SessionPlan` dataclass exactly per plan's field list. `plan_sessions(resolved, usage) ->
  list[SessionPlan]`: groups `usage.jsonl` by `(date, l1_user, project, agent)` (project name
  recovered from Layer 1's `project_id` UUID by recomputing the same `uuid5` formula
  `series.py` uses -- there is no reverse lookup on disk), then per group: session count/weights
  /dominant-model draw (steps 1-3), the per-(model,category) `allocate_integer` split with the
  empty-session merge loop (step 4, session `s00` never removed -- it is the guaranteed
  survivor), subagent carve-out (step 6, using PRE-spawn parent amounts as the plan specifies),
  then day layout (step 7) and child timing (step 8).
- Step 5's "groups with `W + Σ output == 0` never occur" is enforced as an `InputDataError`
  guard (Layer 1 should never emit such a row; this is defense, not expected to fire).
- **Implementation judgment calls** (the plan leaves these at the prose level; flagging for
  the reviewer rather than treating them as settled):
  - Step 6's "if the parent would go below `min_input_tokens`, stop spawning further children"
    is implemented as a *predictive* check -- computed BEFORE subtracting a candidate child's
    carve-out, so a child that would breach the floor is never created (not created-then-undone).
  - Step 8's `r.uniform(0.1, 0.7)` timing draw is taken from the *same* `subagent` RNG stream
    position, immediately after each child's carve-out `f_i` draw in `_spawn_subagents` (one
    interleaved pass), rather than a second pass over the same stream after day layout -- the
    offset fraction is stored alongside the child and applied once the parent's timing is known.
    Replaying RNG state to re-derive the same draws later was considered and rejected as
    fragile (an early draft did this; replaced before committing).
  - "First gap boundary after 12:30 local" (lunch insertion) is read as: walk the day's
    sessions front-to-back, and the lunch minute is added to the first PRE-session gap whose
    local start time is already >= 12:30. A day whose sessions all finish before 12:30 simply
    gets no lunch gap (I4 does not require one).
  - "Compress gaps to 0" (the last-resort overflow handling) is implemented as: recompute the
    day with every gap forced to 0 (lunch included) and check once more before raising
    `InputDataError`; not reachable by the example config (only "extreme configs" per the plan).
- Manually verified before writing tests (commands run this session, not committed): ran
  `plan_sessions` over the P3 manual example's full `usage.jsonl` (150 rows) -> 102 root + 58
  child sessions, 0 rollup mismatches (I1), 0 bad timing pairs, 0 root overlaps, 0 bad child
  containment. Separately exercised `_split_group` directly for `W=1`, a `W=5e11` huge group
  (n == max_per_day, exact), a zero-share model, and a tiny-total forced merge (n collapsed to
  1, exact) -- all before writing the corresponding pytest tests.
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_sessions.py -q` →
  **10 passed** (all plan-required tests: exact-per-category property test over 200 groups,
  single-token, huge, zero-share-absent, empty-session-merged, children-carved,
  child-within-parent, layout-same-local-day, layout-no-overlap, layout-deterministic). Full
  suite: `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` → **144 passed** (134 +
  10), 0 failures. `test_domain_registry_complete` (P2) now exercises real `rng_for` calls
  (the `sess_count`/`sess_weight`/`dominant`/`subagent`/`layout`/`window` domains) and still
  passes.
- TODOs added: one, `# TODO(synthetic-org): revisit if a much larger example config makes this
  a memory concern` in `sessions.py` (in-memory rather than streamed grouping of `usage.jsonl`).
- Open issues: the judgment calls above, for reviewer sign-off; none block correctness as
  tested.

Proceeding to P5.

## P5 — Work model: link flags, calibration, work streams, features

- Files added: `econ-forecast/src/forecast/synthetic/org/work.py`,
  `econ-forecast/tests/synthetic/org/test_work.py`. Refactor: moved `sessions.py`'s private
  `_choice` into `ids.choice` (a shared, un-normalized-weights-accepting version) since
  `work.py` also needs the same weighted-pick technique for `change_mix`/module/feature-size/
  `issue_link_mix` draws -- still never importing Layer 1's private `series._choice`, per the
  plan's rule; `test_ids.py` gained a direct test for it.
- `work.py` implements steps 1-8 exactly: link flags (children inherit), inclusive tokens
  `T(s)`, `u_mult`, `ramp`, the closed-form org-wide `base` calibration (logged as
  `synthetic.org.calibrated`), FIFO work streams with new-item/pick decisions, features with
  sharing + link method, and backlog issues.
- **Implementation judgment calls** (documented in the module docstring too):
  - `item_pick`'s RNG is one instance per session, consumed in sequence (new-item draw, then --
    only if no new item started -- the pick-among-in-flight draw), rather than two independent
    `rng_for` calls that would replay the same first value for two different decisions.
  - Feature link method is decided before the sharing decision (`none` => no issue, so nothing
    to share); a share candidate is only looked for among features already built earlier in a
    deterministic (but not temporally sorted) generation order -- it never looks forward to a
    teammate's not-yet-generated feature. Still fully deterministic (I2a) and matches "pick the
    earliest such by (start, pr_key)" among whatever is available to pick from.
  - Backlog cadence (`backlog_issues_per_repo_week`, `not_planned_share`) uses the UNMERGED
    `defaults.work` block, not any one team's override: a repo can appear in more than one
    team's `project_mix` (the P9 example's `payments-api` is worked by both `platform` and
    `payments`), and the plan does not say which team's override would govern a shared repo's
    backlog. The author pool is the union of every team whose `project_mix` includes that repo.
- **Calibration test note (important for the reviewer):** `test_calibration_hits_target` uses a
  purpose-built 4-team/20-user/90-day scenario, NOT the real P9 example config (which does not
  exist until task P9) -- the plan's own test name says "the example config, full 120 days".
  "Merged PRs" also don't exist yet at P5 (P6 computes actual merge/abandon outcomes); the test
  uses `opened_items * (1 - team_abandon_rate)` as the proxy, which is mathematically exactly
  what `_calibrate_base`'s own `(1 - abandon_rate)` factor targets. Measured: realised rate
  2.366/week vs target 3.0/week (ratio 0.79, inside the ±25% band with a few points of margin)
  and the monotonic $/KLOC-vs-multiplier check both passed. **This must be re-verified once
  the real P9 example config and P6's real merge outcomes exist** -- flagging explicitly rather
  than treating this as final I6 evidence. The full commands used to build and inspect this
  scenario are in this session's scrollback (not committed as a script).
  - `test_link_mix_frequencies` similarly bypasses the full pipeline (2k real sessions would be
    slow to generate) and calls the private `_features` directly against 2000 synthetic
    single-item `(l1_user, repo)` groups -- exercises the exact same `ids.choice` +
    `issue_link_mix` code path P8's real pipeline will call.
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_work.py -q` → **8
  passed** (all 8 plan-required tests). Full suite:
  `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` → **153 passed** (144 + 1 new
  `ids.choice` test + 8), 0 failures.
- TODOs added: none.
- Open issues: the calibration/merged-PR proxy above needs re-verification at P9 (real example
  config) and ideally again once P6 gives real merge outcomes; the feature-sharing
  generation-order note; both flagged for the independent reviewer.

Proceeding to P6.

## P6 — GitHub artifacts, events, links, rework

- Files added: `econ-forecast/src/forecast/synthetic/org/git.py`,
  `econ-forecast/tests/synthetic/org/test_git.py`. Refactor: `directory._resolve_leads` ->
  public `directory.resolve_leads`, since `git.py`'s reviewer-fallback ("the lead of the
  root-nearest other team") needs the exact same lead resolution `directory.py` uses, and
  duplicating it would risk the two silently diverging. `work.py` gained
  `WorkItem.opening_session_id` (which session's spend crossed `open_at_fraction`) --
  `commits_per_session`'s "force >=1 for the session that opened the item" needs to know
  exactly which session that was, not just that it happened; a small, necessary addition to
  P5's output, noted here rather than silently expanding an already-committed task's contract.
  Also moved the shared 4-team/90-day scenario fixture (`four_team_org`, built for P5's
  calibration test) into `conftest.py` as `four_team_org`/`four_team_git`, so P6+ files reuse
  the same large, realistic dataset instead of each building their own.
- `git.py` builds, per opened work item: commits (§2, forced >=1 on the opening and finalizing
  sessions, churn-scaled total lines split by `allocate_integer` over lognormal per-commit
  weights), the PR row (§3, files/labels/linked_issue/shas), reviews + outcome (§4: reviewer
  pool excluding the author with a same-team/other-team-lead/admin fallback ladder, rounds of
  changes-requested capped at post-open session count, abandon/merge/reopen resolution against
  the series end), events (§5), reverts (§6, author/timing/swapped stats/inverted file ops),
  issues (§7, one per canonical/non-shared feature plus one per backlog issue, state from
  every-PR-finalized), `pr_issue_links` (§8), `session_git_links` (§9, root+children, sha vs
  heuristic method), and `pr_rework` (§10). Numbering (§1) runs as an explicit final pass across
  every issue+PR (regular and revert) per repo, because `title`/`branch` link-method
  embellishments need an issue number that doesn't exist until numbering completes -- the
  module docstring documents this ordering choice.
- Artifact dicts use one shared 30-key shape (`_ARTIFACT_KEYS`, `_shell()`) matching
  `server.ingest.git.map_github`'s mapper output field-for-field (D12); confirmed directly by
  `test_artifact_keyset_matches_mapper` against real fixtures in `tests/fixtures/github/`
  (`pull_request_closed_merged`, `pull_request_review_submitted`, `push_squash_merge`,
  `issues_opened`) run through the real `map_delivery`.
- `_LINK_CONFIDENCE = {"graphql": 1.0, "branch": 0.7, "title": 0.6}` is a literal copy of the
  three values this package uses from `linkage.py:41`'s `_CONFIDENCE`, with a comment pointing
  there; `test_link_confidence_parity` imports the real dict (test-only, per the plan) and
  checks equality.
- **Implementation judgment calls** (documented in the module docstring too): reviewer count is
  `randint(1, 2)` (no split probability was given); the reopen check is a `random() <
  reopen_rate` gate before the described insertion; "root-nearest other team" is read as the
  first other team in `resolved.team_order` (no team-hierarchy distance exists anywhere else in
  the plan to make "nearest" concrete).
- Manually verified before writing tests (this session's scrollback, not committed): the small
  8-user/14-day scenario (191 artifacts, 0 numbering violations, 0 duplicate event source keys,
  0 reviewer==author cases, 0 temporal violations) and the 4-team/90-day scenario (4075
  artifacts: 654 PRs/267 issues/2348 commits/806 reviews, 7 reverts, 3 open PRs at series end,
  604 rework rows, numbering unique per repo across all 4 repos).
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_git.py -q` → **12
  passed** (all 10 plan-required tests, split as `test_artifact_keyset_matches_mapper`,
  `test_link_confidence_parity`, `test_numbering_unique_per_repo_chronological`,
  `test_temporal_order_per_pr`, `test_events_source_key_unique`,
  `test_events_monotonic_per_artifact` [extra, folded into "events ordered per artifact"],
  `test_reviewers_not_author`, `test_revert_references_merge_sha`,
  `test_links_only_to_existing_prs`, `test_children_linked_like_parent`,
  `test_session_links_sha_method_has_real_commit` [extra], `test_open_prs_at_series_end`).
  Full suite: `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` → **165 passed**
  (153 + 12), 0 failures.
- TODOs added: none.
- Open issues: the judgment calls above, for reviewer sign-off.

Proceeding to P7.

## P7 — Request ledger and `session_info` content ("the core of realism")

- Files added: `econ-forecast/src/forecast/synthetic/org/{requests,content}.py`,
  `econ-forecast/tests/synthetic/org/{test_requests,test_content,test_parity_graph_fold}.py`.
  `org/pricing.py` gained `Pricer.rewrite_recoverable_usd_micros` (ports
  `graph_fold._rewrite_recoverable_usd_micros`, used by both `cache_busts`/`resume_cost` and the
  parity test). `org/models.py` gained `SessionInfoRecord`. `conftest.py` gained a shared
  `records` fixture (`content.build_all` over `four_team_git`) and `mainline_pricing_path` moved
  to session scope (needed by module-scoped fixtures; it has no side effects).
- `org/requests.py` — `simulate(resolved, plan, cfg, habits) -> Ledger`. Implements steps 2-8 of
  the plan in dependency order (count/partition, turns, shape+compactions/resumes/busts, THEN
  the cache-write split which needs the shape, then integer allocation, context-window retry
  loop, timestamps, interrupts). **Domain separation is the load-bearing design choice**: only
  the `requests` stream decides token math/timing magnitudes; only the four `habit_*` streams
  decide which positions become bust/resume/compaction/interrupted -- this is what makes I2b
  true by construction, not by coincidence, and is documented at the top of the module.
- `org/content.py` — `build_session_info(...) -> SessionInfoRecord` plus every `info.*` builder:
  `channel_cost` (delegates to `Pricer.channel_cost`, parity-tested), `cache_busts`/`resume_cost`
  (port `_rewrite_recoverable_usd_micros` per-event), `interrupted`, `compactions`,
  `context_curve`/`story`/`segments`/`practices` (root only), `git_commands`,
  `read_then_discard` (TODO(synthetic-org), not simulated). `_story_shape`/`_story_downsample`
  are byte-for-byte ports of the `graph_fold` functions (parity-tested). Also added
  `build_all(resolved, plans, work, git_result, pricer) -> dict[session_id, SessionInfoRecord]`,
  an orchestration helper (children before parents, since a parent's `subagents` field needs its
  children's already-built records) -- not explicitly named in the plan, but P8's per-session
  loop needs exactly this wiring and P7's own tests needed it first to avoid duplicating it;
  P8 will stream through the same resolution logic rather than re-deriving it.
- **Real bugs caught and fixed before committing** (worth flagging for the reviewer as evidence
  of what the manual smoke-testing pass was for, not just a formality):
  - `files` was initially inheriting the parent's linked/unlinked branch for child sessions;
    the plan's table has a THIRD, unconditional case ("Children: `{}`") that overrides both --
    fixed so `not is_root` is checked first.
  - `title`'s three-way branch had the same defect (children fell into an unreachable "else"
    keyed on `item is None`, not on `is_root`); fixed to check `is_root` first, with an explicit
    documented fallback (UNLINKED_TITLES) for the plan-undefined "linked but never got a PR"
    case.
  - A microsecond of float drift after the gap-scaling step could push the last request's `ts`
    a hair past `ended_at`; added a defensive final clamp (`min(max(t, started_at), ended_at)`),
    which preserves monotonicity (clamping a non-decreasing sequence to a fixed range stays
    non-decreasing).
- Manually verified before writing tests (this session's scrollback, not committed): `simulate`
  over all 3,981 sessions of the 4-team/90-day scenario -- 0 exactness violations, 0 context-window
  violations, 0 timestamp violations, out of the box after the clamp fix; I2b confirmed directly
  (changing 3 habits knobs at once left per-model totals byte-identical). `build_all` over the
  same scenario -- 160/160 (small scenario) and all sessions of the big one built with 0
  exceptions; I5 held within 4 micros on every session; segment-model sums matched session
  models exactly; parent/child purpose-title consistency held exactly; `channel_cost_of`,
  `tokens_total_of`, `_story_shape`, `_story_downsample`, and
  `_rewrite_recoverable_usd_micros` all matched the real `graph_fold` functions exactly on
  hand-built cases before the parity tests were written.
- TODOs added: one, `# TODO(synthetic-org): read-then-discard waste is not simulated; always
  empty` in `content.py`, next to `info["read_then_discard"]`.
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_requests.py
  econ-forecast/tests/synthetic/org/test_content.py
  econ-forecast/tests/synthetic/org/test_parity_graph_fold.py -q` → **20 passed** (7 + 5 + 8).
  Full org suite: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org -q` → **97
  passed** in ~37s. Full synthetic suite: `venv/bin/python -m pytest econ-forecast/tests/synthetic
  -q` → **185 passed** in ~46s, 0 failures.
- Open issues (documented judgment calls, for reviewer sign-off): compaction/resume/bust
  position-collision priority (compaction > resume > bust); gap magnitudes drawn from the
  `requests` stream even at resume/bust positions (kind selection vs. magnitude are different
  concerns); dropping resume gaps that exceed the 80% budget drops the highest-index resume
  first; `tools`' fixed per-tool rates and `practices.at_refs`/`.review_subagent`/
  `.subagent_research` have no stated formula in the plan and are this module's own reasonable
  choice.

Proceeding to P8.

## P8 — Orchestration, output, report (`org-expand` end to end)

- Files added: `econ-forecast/src/forecast/synthetic/org/{expand,report}.py` (rewritten;
  `expand.py` existed as the P3 stub raising `NotImplementedError` and is now the full
  pipeline -- its `TODO(synthetic-org)` is removed), `econ-forecast/tests/synthetic/org/
  {test_expand_invariants,test_cli_org,test_determinism}.py`. `org/models.py` gained
  `GitArtifactRecord`, `GitArtifactEventRecord`, `PrIssueLinkRecord`, `SessionGitLinkRecord`,
  `SessionRecord`, `BudgetRecord`, `AllowanceRecord` (writer-side wrappers around git.py's plain
  dicts and P8's own new record kinds). Moved the `org-expand` CLI's own tests out of
  `test_repos_directory.py` (added ahead of schedule at P3, before `test_cli_org.py` existed)
  into the plan's dedicated `test_cli_org.py`, and replaced the P3 "stub not implemented yet"
  test with real generation-path assertions now that P8 makes it work.
- **Deliberate deviation from "reuse `_finish`"** (documented at the top of `expand.py` and
  here): `runner._finish` is hardwired to Layer 1's own `reporting.render_report`, which
  branches on `result.operation in {"users","roster","series"}` and would silently fall into
  the *series* report template for an `"org"` operation -- reading `details` keys
  (`start_date`, `profiles`, `breakdowns`, ...) this module never produces. `run_org_expand`
  reuses `_operation`/`_check_output`/`_snapshot` and Layer 1's generic `GenerationResult` type
  directly (all genuinely operation-agnostic), but writes its own finishing routine calling
  `org.report.render_org_report`, keeping the same shape (config.yaml -> report.md ->
  manifest.json, manifest published last). Flagging this for the reviewer since the plan's
  literal text says "reuse `_finish`"; reusing it as-is would have produced a nonsense report.
- Snapshot layout matches the plan's literal list once read as paths already relative to
  `inputs/`: `inputs/series/usage.jsonl`, `inputs/series/user_profiles.jsonl`,
  `inputs/series/config.yaml`, `inputs/series/inputs/users/users.jsonl` (the series' OWN
  users-stage snapshot, preserved nested), `inputs/series/inputs/roster/teams.jsonl`,
  `inputs/series/manifest.json`, `inputs/pricing/pricing.yaml`.
- `git_artifacts.jsonl` sorted by `(repo_id, kind, number|created_at, artifact_id)` per spec.
  `sessions.jsonl` fields exactly per spec, including `agent_version` from
  `rng_for(seed, "layout", l1_user, date)` (a fresh instance from the same domain+keys
  `sessions.py`'s day-layout already uses for a different purpose -- both are independently
  deterministic, so this is not a conflict, just two separate uses of one seed).
- The pre-manifest I1 re-aggregation check (`_assert_rollup`) compares `sessions.jsonl` +
  `session_info.jsonl` against `expected_rollup.jsonl` and raises `InputDataError` on any
  difference; ran clean on both the small and the 4-team/90-day scenario every time once the
  bug below was fixed.
- Manifest `summary`: the plan's integer counts (teams/users/repos/sessions_root/
  sessions_child/prs/prs_merged/prs_open/prs_closed_unmerged/reverts/issues/reviews/commits/
  links) go through `GenerationResult.summary_counts`, which is typed `dict[str, int]` (Layer
  1's type) -- `calibrated_base` (float), `merged_prs_per_dev_week` (float), and
  `total_cost_usd` (float) are real-valued and would fail that type's validation, so they are
  merged into the manifest's own (unconstrained) `summary` dict separately rather than routed
  through the typed `GenerationResult`. All three ARE present together in the written
  `manifest.json`, satisfying the plan's literal list; only the in-memory typed object splits
  them. Flagged as a judgment call, not a missing field.
- **A real, reproducible bug found and fixed via manual smoke-testing before writing the
  formal tests** (worth surfacing for the reviewer): every review artifact was built with a
  placeholder `"number": item.seq` (a work-item sequence index, not a GitHub PR number) that
  was never corrected once the real per-repo PR number was assigned in git.py's numbering pass
  -- except revert-PR reviews, whose `_shell()`-built dict left `number: None`. The moment both
  kinds of review existed in one repo (any real-scale run), sorting `git_artifacts.jsonl` by
  `(repo_id, kind, number|created_at, artifact_id)` crashed (`TypeError: '<' not supported
  between instances of 'str' and 'int'`) because "review"-kind rows within one repo carried a
  mix of int (`item.seq`) and str (`created_at`, the `None`-number fallback) in that sort
  position. Fixed in `git.py`'s numbering pass: every review's `number` is now explicitly
  backfilled from its parent PR's assigned number (matching `map_github`'s real review mapping,
  which sets `"number": pr["number"]`) instead of being decided at review-build time.
- Manually verified before writing tests (this session's scrollback, not committed): full
  `run_org_expand` against the small (8-user/2-team/14-day) and the 4-team/90-day scenarios --
  both produced every file, the I1 rollup assertion passed silently both times, `report.md` and
  `manifest.json` read as expected (calibration table, per-team $/PR percentiles, per-repo
  costs, recoverable-spend totals all populated with real numbers). Two full reruns of the
  small scenario were byte-identical across all 17 `*.jsonl` files (I2a) before the
  corresponding pytest was written.
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_expand_invariants.py
  econ-forecast/tests/synthetic/org/test_cli_org.py econ-forecast/tests/synthetic/org/test_determinism.py
  -q` → **12 passed** (4 + 5 + 3). Full org suite: `venv/bin/python -m pytest
  econ-forecast/tests/synthetic/org -q` → **107 passed** in ~41s. Full synthetic suite:
  `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` → **195 passed** in ~49s, 0
  failures.
- TODOs added: none (the one P3 TODO in `expand.py` is now resolved and removed).
- Open issues: the `_finish`/manifest-summary-typing deviations above, for reviewer sign-off.
  Wall time for the 4-team/20-user/90-day scenario end to end: ~13s (well under the design
  doc's "under 1 minute" target for a somewhat larger example config).

Proceeding to P9.

## P9 — Example configs, retiming chain, wrapper

- Files added: `econ-forecast/config/synthetic/org/{users,roster,series,org}.yaml`,
  `econ-forecast/src/forecast/synthetic/org/cli_synth_org.py`, `econ-forecast/utils/synth_org.sh`.
  Test additions to `test_cli_org.py` (already created at P8): `test_retiming_shift_is_correct`,
  `test_end_date_gives_matching_series_end`, `test_chain_produces_all_four_datasets`, plus a
  `tiny_config_dir` fixture (2 teams x 4 users, 2 repos, 10 days, one dated spike + one zero
  period, to exercise retiming).
- Example config follows the plan's numbers exactly: `users.yaml` (count 22, seed 42, consistent
  identity), `roster.yaml` (platform 4 / payments 5 / growth 5 / data 4, 4 unassigned),
  `series.yaml` (120 days, `America/Los_Angeles`, the 5-project team mixes and profile mixes
  from §9's tables, `growth.activity.weekend_zero_probability: 0.4`, the `payments-release-
  crunch` dated spike and `org-holiday`/new-hire zero periods with exact day-offset dates),
  `org.yaml` (5 repos with 3-4 modules and the specified extension mixes, all four teams'
  overrides from the design §8 table verbatim, the $25k/$6k/$6k/$5k/$9k monthly budgets).
  New-hire id `38607330-4301-536a-88dc-2ed239a03aa5` found by a one-time `user-create` +
  `roster-create` run (seed 42/42) and hard-coded with a comment recording exactly how, per the
  plan's own instruction.
- **Judgment call:** "random spikes as in the Layer 1 default" is read as matching Layer 1's own
  *example config's* demonstration values (`probability: 0.02, multiplier_min: 3.0,
  multiplier_max: 5.0`) rather than the schema's zero-probability default -- the literal "Layer
  1 default" phrase is ambiguous between the two, and the non-zero values make for a more
  realistic demo dataset, which is the actual purpose of this file. Flagged for the reviewer.
- `cli_synth_org.py` implements the 6-step chain exactly (create `--output`; copy the 4 YAMLs
  into `--output/configs/`, resolving `org.yaml`'s `pricing_file` to an absolute path first
  since it points outside the config-dir/output tree; retime `series.yaml` if `--end-date` is
  given, `today` resolved exactly once; rewrite the users/roster/series/org cross-references to
  the chain's own output; run all four stages in-process; print one JSON summary with every
  stage's run id, output dir, and counts).
- **A real bug found and fixed via manual testing before writing the formal tests**: the
  cross-reference rewriting (step 4) wrote `--output`-relative path STRINGS into the copied
  YAMLs, but Layer 1's own config loader re-resolves any relative path in a YAML file relative
  to THAT FILE's own directory (`--output/configs/`), not the caller's cwd -- so a relative
  `--output` (the normal case: `--output econ-forecast/artifacts/org-demo`) silently produced
  paths like `.../configs/econ-forecast/artifacts/org-demo/users/users.jsonl`, which never
  exist. `roster-create` failed immediately after `user-create` succeeded
  (`InputDataError: cannot read dataset users.jsonl: FileNotFoundError`). Fixed by resolving
  `args.output`/`args.config_dir` to absolute paths once, right after argument parsing, before
  any cross-reference is written. Confirmed via the tiny config-dir chain AND the full real
  example both before writing `test_chain_produces_all_four_datasets` (which used an already-
  absolute `tmp_path`, so would not itself have caught this -- flagging that gap for the
  reviewer rather than silently relying on the manual check alone).
- **Done-when: the full example ran through the real wrapper** (not just the Python API):
  ```
  ./econ-forecast/utils/synth_org.sh --config-dir econ-forecast/config/synthetic/org \
    --output econ-forecast/artifacts/org-demo --end-date today
  ```
  Wall time: **14.1s** total (users+roster+series+org-expand), well under the 60s target.
  Output: 22 users, 18 rostered / 4 unassigned, 4 teams, 5 repos, 2416 root + 1741 child
  sessions, 677 PRs (630 merged / 5 open / 42 closed-unmerged), 21 reverts, 361 issues, 894
  reviews, 2353 commits, 536 links. Calibration and per-team tables (from the real
  `report.md`, pasted verbatim -- also required for the final handoff):

  ```
  Target: 3.00 merged PRs/dev-week. Calibrated `base` (tokens/LOC at multiplier 1): 11115.67.

  | Team     | Devs | Merged PRs | Dev-weeks | Realised/dev-week |
  |----------|-----:|-----------:|----------:|-------------------:|
  | data     |    4 |        150 |      68.6 |               2.19 |
  | growth   |    5 |        222 |      79.1 |               2.81 |
  | payments |    5 |        128 |      85.7 |               1.49 |
  | platform |    4 |        130 |      68.6 |               1.90 |

  | Team     | Sessions | Cost USD | Merged PRs | $/PR p50 | $/PR p90 |   LOC/$ | Review rounds | Reverts | Abandoned |
  |----------|---------:|---------:|-----------:|---------:|---------:|--------:|---------------:|--------:|----------:|
  | data     |     1074 |  1556.74 |        150 |     4.34 |    10.90 | 27.6648 |           0.15 |       4 |        12 |
  | growth   |     1063 |   950.47 |        222 |     2.48 |     6.33 | 35.0658 |           0.09 |      11 |        20 |
  | payments |     1111 |   897.11 |        128 |     4.10 |     9.14 | 37.1803 |           0.74 |       2 |         3 |
  | platform |      909 |   815.31 |        130 |     3.49 |     9.14 | 76.8987 |           0.28 |       4 |         7 |
  ```

  LOC/$ is exactly monotonic with `tokens_per_loc_multiplier` (platform 0.7 -> 76.9, payments
  1.0 -> 37.2, growth 1.1 -> 35.1, data 1.6 -> 27.7) and review-rounds mean tracks
  `changes_requested_rate` (payments 0.55 -> 0.74 rounds, the highest by a wide margin) -- both
  exactly the signals the design intends these teams to demonstrate.

- **Open finding for the reviewer, NOT silently resolved (important):** the ORG-WIDE I6
  calibration check, computed the same way `test_calibration_hits_target` (P5) computes it
  (`Σ merged PRs / Σ dev-weeks` vs `target_merged_prs_per_dev_week`), misses the ±25% tolerance
  with this real example config: `630 / 302.0 = 2.086` vs target `3.0` is a ratio of **0.70**,
  just outside the `[0.75, 1.25]` band (P5's purpose-built 4-team scenario landed at 0.79,
  inside it). Root-caused by direct measurement (not guessed): average tokens actually SPENT
  per finalized item is 15-40% higher than its BUDGET across all four teams (e.g. growth:
  budget ~1.70M, spent ~2.39M) -- because `work.py`'s FIFO finalizes an item the instant
  `spent >= budget`, and that crossing happens at SESSION granularity (a single session can
  push spend well past budget in one jump when the item's budget is small relative to a
  session's typical size, which growth's `pr.size_median_loc: 90` makes common). This is a
  structural consequence of the exact algorithm plan §5 P5 specifies (a discrete
  threshold-crossing renewal process), not an implementation bug -- `_calibrate_base`'s closed
  form assumes items consume EXACTLY their budget, and the plan's own text already excludes two
  other known-imprecise factors ("clamping and ramp are deliberately excluded; the ±25%
  tolerance absorbs them") from the same formula. Session-granularity overshoot is a third such
  factor the plan's text does not name, and with these specific example numbers it is the
  dominant one. **I have not altered the example config's pinned values (target, team overrides)
  or the finalization algorithm to force compliance** -- both are specified exactly by the plan
  and design §8, and doing so would be inventing behavior/gaming the acceptance check rather
  than reporting a real finding. Options for the parent/reviewer: (a) accept this as a known
  modeling limitation and relax/re-scope I6's acceptance bar for the real example config
  specifically (P5's purpose-built scenario still demonstrates the formula works when the
  budget/session-size ratio is more favorable); (b) extend `_calibrate_base` with an overshoot
  correction term (not specified anywhere in the plan, so out of scope without explicit
  direction); (c) adjust the example config's PR-size/session-size ratios. Not resolved here;
  flagged prominently for the final handoff.
- Tests: `venv/bin/python -m pytest econ-forecast/tests/synthetic/org/test_cli_org.py -q` →
  **8 passed** (5 from P8 + 3 new P9 tests). Full org suite: `venv/bin/python -m pytest
  econ-forecast/tests/synthetic/org -q` → **110 passed** in ~42s. Full synthetic suite:
  `venv/bin/python -m pytest econ-forecast/tests/synthetic -q` → **198 passed** in ~49s, 0
  failures.
- TODOs added: none.
- Open issues: the I6/real-example-config calibration miss above (the important one), and the
  "Layer 1 default" random-spikes interpretation, both for reviewer sign-off.

Proceeding to P10 -- will need the user to create `econ_synth_test` and `econ_synth` databases
and provide a DSN before the DB-gated tasks can run.

## P10 — Loader (mainline) -- code + non-DB tests complete; DB-gated tests pending a DSN

- Files added: `config/queries/synthetic.yaml`, `src/server/storage/synthetic_load.py`,
  `src/server/storage/cli_load_synthetic.py`, `utils/load_synthetic.sh`,
  `tests/server/storage/test_synthetic_load_unit.py` (no DB, all passing),
  `tests/server/storage/test_synthetic_load.py` (DB-gated, written and collecting cleanly, not
  yet run -- needs a DSN). Files modified: `src/server/api/cli_serve_api.py` (`--port`),
  `src/webui/vite.config.ts` (`VITE_API_TARGET`).
- **Dependency direction kept one-way**: `synthetic_load.py` reads the dataset's JSONL files as
  plain dicts via a small local `_read_jsonl`, never by importing `forecast.synthetic.org.*`
  types -- mainline does not depend on the forecast package (only the reverse, via PYTHONPATH).
  This only works because the P2-P8 record models were deliberately designed to already match
  the ORM column names field-for-field, so `_upsert_table` is one generic helper reused for
  every table except `git_artifact_events` (which needs `on_conflict_do_nothing` and drops the
  DB-generated `event_id`, per the plan) and `session_info` (whose `created_at`/`updated_at`
  the plan says to backfill from the paired session's own `started_at`, since the record has no
  timing of its own).
- **A real gap found and fixed via manual testing, before writing the loader** (not caught by
  any P6/P7/P8 test, since none of them checked the FULL `GitArtifactEvent` column set against
  the real ORM schema): `git_artifact_events` rows were missing `recorded_at` -- the mainline
  `GitArtifactEvent` table has both `occurred_at` and `recorded_at` columns, and P6's git.py
  only ever produced `occurred_at`. Fixed by adding a shared `_event()` helper in `git.py`
  (`recorded_at = occurred_at`, per plan §5 P6 step 5's own text, which I had read but not
  fully wired through) and replacing all 6 event-construction call sites (regular PR events x4,
  revert PR events x2) to use it; added the field to `GitArtifactEventRecord`. Full synthetic
  suite re-run clean after the fix (198 passed) -- this is exactly the kind of gap that only
  surfaces when building the consumer (the loader) against the real target schema, which is
  why P10 was sequenced after, not merged into, P6/P8.
- `load_dataset` implements all 12 steps in the exact order: D13 guard (pure string parsing, no
  connection) -> manifest verify (sha256 every listed file) -> org-id check against
  `config/directory.yaml` -> dry-run short-circuit (computes row counts from the JSONL files
  directly, `get_engine` never called -- confirmed by
  `test_dry_run_never_calls_get_engine`'s monkeypatch) -> migrate -> `--replace` truncate ->
  directory sync + `user_prefs` + the D5 existence assertion -> the 8 ordered table upserts ->
  PR classification (real `pr_classify()` per PR artifact, kind `pr`) + `rework_ratio` backfill
  -> budgets/allowances via the real manager functions (which validate `scope_id` against the
  just-synced directory) -> the post-load `daily_rollup` multiset-of-tuples check against
  `expected_rollup.jsonl`. `SyntheticLoadError` (guard/manifest/org-id, exit 2) and
  `RollupMismatchError` (exit 1) are distinct exception types so `cli_load_synthetic.py` maps
  them to the plan's exact exit codes without string-matching messages.
- Manually verified end-to-end against the real demo dataset (`econ-forecast/artifacts/org-demo/org`,
  regenerated after the `recorded_at` fix -- 22 users, 5 repos, 4306 artifacts): `--dry-run`
  printed correct planned row counts for all 17 files with zero DB connection attempted; the
  guard correctly refused `postgresql+psycopg://x@localhost:5433/econ` with exit 2
  (`D13; there is no override`) before this session ever touched a real database.
- Tests: `venv/bin/python -m pytest tests/server/storage/test_synthetic_load_unit.py -q` →
  **13 passed** (guard accepts/rejects x5 parametrized cases, unparseable-DSN, 4 manifest-tamper
  variants, org-id mismatch + match, dry-run-never-connects). `test_synthetic_load.py` (4 tests:
  load-with-replace row counts, idempotent reload, `pr_classification` one-row-per-PR +
  `rework_ratio` set on every merged PR, `admin_viewer_id` resolves) is written and collects
  cleanly but **has not been run** -- it needs `TALIUM_TEST_DSN` pointed at a real `*_synth`
  database, which this session does not have. Full regression check:
  `venv/bin/python -m pytest tests -q -m "not slow"` → **2093 passed, 552 skipped, 11
  deselected**, 0 failures -- confirms the mainline `cli_serve_api.py`/`vite.config.ts`/new-file
  changes broke nothing pre-existing.
- **STOPPING HERE per the task instructions to ask the user for the database**, exactly as
  directed: "Database tests (P10, P11) need a Postgres database whose name ends in `_synth`
  ... ask the user to create `econ_synth_test` (and `econ_synth` for the demo load) and to give
  you the DSN." P10b's live verification (running the API against `econ_synth` and taking
  screenshots, needed for P11) is also blocked on this. Everything else in P10 that does not
  need a live database is complete and tested.
- TODOs added: none.
- Open issues: none beyond needing the DSN.

## P10 (DB-gated tests, continued after DSN provided)

- Asked the user to create the two `*_synth` databases on the local stack (Postgres 16,
  `talium_stack_pg`, localhost:5433). First DSN they created was `econ_synth_test` -- this
  does NOT satisfy D13 (`_synth_ready`/`_guard_dsn` both require the name to literally *end*
  with `_synth`, not merely contain it; `econ_synth_test` ends with `_test`). This was my own
  mistake in the original ask -- the unit-test parametrization (`talium_test_synth`) already
  showed the correct pattern and I should have used it in the request. Caught immediately when
  the DB-gated tests silently skipped (`_synth_ready` False) instead of running. Asked the user
  to rename it (`ALTER DATABASE econ_synth_test RENAME TO econ_test_synth`) rather than doing it
  myself, consistent with not having DB-creation/alteration permission. `econ_synth` (for the
  demo load) was already named correctly.
- With DSN `postgresql+psycopg://postgres@localhost:5433/econ_test_synth`, ran
  `test_synthetic_load.py` for the first time -- **found a real path bug** in
  `synthetic_load.py`'s Step 11 (post-load rollup check): it read
  `dataset_dir / "inputs" / "series" / "config.yaml"`, but that path does not exist in any
  org-expand output. `run_org_expand` (P8) writes the series config Layer 1 actually used to
  `dataset_dir / "series" / "config.yaml"` (a plain copy for downstream reference); the
  `inputs/series/` directory is `DatasetWriter`'s *lineage* copy of the series dataset's own
  outputs (`user_profiles.jsonl`, `usage.jsonl`, `manifest.json`, and a further-nested
  `inputs/` for series' own upstream users/roster lineage) and never contained a `config.yaml`
  at that depth. This bug was invisible until now because `--dry-run` (the only path exercised
  against the real demo dataset so far) returns before Step 11 ever runs. Fixed by changing the
  path to `dataset_dir / "series" / "config.yaml"`. Verified against both the real demo dataset
  layout (`find .../org/inputs .../org/series`) and the P10 test's own generated dataset before
  committing the fix.
- Tests after the fix: `test_synthetic_load.py` → **4 passed** (load-with-replace row counts
  incl. `rollup_ok=True`, idempotent reload, PR classification one-row-per-PR +
  `rework_ratio` set on every merged PR, `admin_viewer_id` resolves against a real `users` row).
  Re-ran `test_synthetic_load_unit.py` → 13 passed (unaffected). Re-ran the full synthetic
  suite → 198 passed (unaffected, since the bug was purely in mainline's loader, not in any
  Layer 2 generator code).
- TODOs added: none.
- Open issues: none. P10 is now fully complete, including the DB-gated tests.

## P11 (API smoke test)

- Refactored `small_org_dataset` out of `test_synthetic_load.py` into a new
  `tests/server/conftest.py` (session-scoped), shared with the new
  `tests/server/api/test_synthetic_smoke.py` -- avoids a ~90-line duplicate fixture. Each DB-
  gated test file keeps its own local `TEST_DSN`/`requires_synth_db` guard (matching
  `tests/server/api/conftest.py`'s existing precedent of a per-area guard, not a shared one) --
  only the large fixture body is shared, not the gate. `test_synthetic_load.py` re-verified
  unaffected by the refactor (4 passed).
- Wrote `tests/server/api/test_synthetic_smoke.py` per plan §5 P11: admin walks every listed
  manager route (`/v1/org/overview`, `/v1/org/usage`, `/v1/teams`, `/v1/projects`,
  `/v1/teams/{id}`, `/v1/projects/{id}`, `/v1/prs/{id}`, `/v1/features/{id}`, `/v1/people/{id}`,
  `/v1/economics`), asserting on real schema fields read from `server/api/schemas/manager.py`
  (not just status codes) -- `headline.spend.usd_micros > 0`, `teams`/`items` non-empty,
  `team.team_id`/`project.repo_id`/`pr.artifact_id`/`person.user_id` match the requested id, per
  plan instruction. One developer walks their own `/overview`, `/sessions`, `/insights`,
  `/repos`, `/sessions/{id}`. A plain dev (`sample_dev_viewer_id`) hitting `/v1/org/overview` →
  403. IDs (first team/repo/merged-PR/issue) are discovered by querying the just-loaded tables
  directly, never hard-coded, since they depend on the deterministic-but-opaque generator
  output.
- **A real, more significant bug found on the first run** (not caught by any P6-P10 test):
  `GET /v1/features/{id}` 500'd with `ValueError: too many values to unpack (expected 4)` inside
  `server/api/manager/quality.py::feature_by_id`, which parses a `github:...:issue:...` feature
  id as EXACTLY `feature_id.split(":")` -> `(prefix, repo_num, kind, number)`. Root-caused
  against the real `server/ingest/git/map_github.py` (read in full per this task's own setup
  instructions): production issue artifact_ids are always `github:<repo_num>:issue:<n>` where
  `n` is the actual GitHub-assigned issue number (`_artifact_id(repo_num, "issue", number)`,
  confirmed at `map_github.py`'s `issue_artifact`/`_map_comment_bump`), and `quality.py`'s own
  `_feature_id` docstring states this explicitly: "An issue-keyed feature's own `artifact_id` is
  already globally unique (`github:<repo_num>:issue:<n>`)". P6's `git.py`, however, built every
  issue's `artifact_id` from an internal stable UUID-ish key (`feature_id`, or for backlog
  issues `f"backlog:{repo}:{week}:{created_at}"`) at shell-construction time, before the
  issue-numbering pass ever ran -- so the real assigned `number` was backfilled into the
  `number` field but never propagated into `artifact_id`, leaving ids like
  `github:931931689:issue:backlog:platform-infra:0:2026-01-09T08:44:42.016160+00:00` (far more
  than 4 colon segments) instead of `github:931931689:issue:1`. This directly violates D12
  ("Artifact shapes mirror `map_github.py` exactly") for the one field a real downstream
  consumer (`feature_by_id`, reachable from production `GET /v1/features/{id}` on real data, not
  just synthetic) actually parses positionally.
  - **Scoped the fix to `issue` kind only, deliberately not `pr`.** Audited every place in
    mainline that parses an artifact_id string (`grep '.split(":"' across
    `server/api/manager`, `server/worker`, `server/directory`, `common`) -- only
    `quality.py`'s `github:...:issue:...` branch does positional parsing; its `pr:`/`branch:`/
    `label:` branches, and every PR consumer this session exercised (`pr_detail`,
    `resolve_pr_visibility_repo_id`, `pr_classify`, `session_git_links`, budgets), treat
    `artifact_id` as an opaque primary key via exact-match lookups, never split. PR/commit/
    review artifact_ids keep their existing UUID-keyed scheme (already proven correct against
    every consumer this session tested); only issue artifact_ids needed correcting. This is a
    deliberately narrower fix than "make every kind's key the real GitHub number," chosen to
    avoid re-touching PR/commit/review code and tests that are already verified correct,
    consistent with fixing exactly the demonstrated defect and nothing more.
  - Fixed in `org/git.py`: the numbering pass (`compute_git`, "Numbering (step 1)") now also
    rewrites `ref["artifact_id"] = ids.artifact_id(repo_num, "issue", i)` for issue entries once
    the final number `i` is known (mirroring the existing PR-side comment for why numbering is a
    late pass at all). The backfill loop's issue lookup previously matched by
    `i["artifact_id"].endswith(f":issue:{issue_id}")` -- a substring match that would have
    broken instantly once `artifact_id` stopped containing the old key as a substring -- so it
    was replaced with a new `issue_by_key: dict[str, dict]` (keyed by the original stable
    `issue_key`, populated at issue-creation time, never mutated) for a clean lookup that
    survives the rename. `pr_issue_links.issue_artifact_id`, `linked_issue` (graphql method),
    and the title `"(#n)"` backfill all read `issue["artifact_id"]`/`issue["number"]` live from
    the mutated dict *after* the numbering pass already ran, so they picked up the corrected id
    automatically with no further change.
  - Verified no existing test asserts the exact shape of an issue `artifact_id` (only
    referential integrity: `issue_artifact_id in <the set of known issue ids>`, in
    `test_git.py`/`test_expand_invariants.py`), so nothing needed to change in the test suite
    itself for this fix -- re-ran the full synthetic suite (198 passed) and the new smoke test
    to confirm.
  - Regenerated `econ-forecast/artifacts/org-demo` (14.8s) and reloaded `econ_synth` --
    confirmed a sample issue artifact_id is now `github:902909772:issue:1` (4 segments, numeric
    key), and `rollup_ok: true`.
- Tests: `test_synthetic_smoke.py` → **3 passed** (admin walk, one-developer walk, plain-dev
  403). Full `test_synthetic_load.py` + `test_synthetic_load_unit.py` + `test_synthetic_smoke.py`
  together → 20 passed. Full synthetic suite (`econ-forecast/tests/synthetic`) → 198 passed.
  Full mainline regression with `TALIUM_TEST_DSN` set (`tests -q -m "not slow"`) → **1 failed,
  2649 passed** -- the one failure, `test_manager_service_org_usage.py::
  test_session_types_and_no_output_pct`, is unrelated to every file this task touched (verified:
  it reproduces in isolation with no other test run first; its fixture data uses fixed
  2026-01-01..01-31 dates, not wall-clock-relative; it exercises `org_usage`/`session_types`
  classification, code this task never modified). It is gated `@requires_db` and therefore
  normally SKIPPED by the plan's own regression command (`utils/test_all.sh`, which never sets
  `TALIUM_TEST_DSN`) -- it only ran because this session additionally passed `TALIUM_TEST_DSN`
  to re-verify DB behavior, which is not what the plan's §7 command does. Re-ran the plan's
  exact command, `venv/bin/python -m pytest tests -q -m "not slow"` (no DSN) → **2093 passed,
  559 skipped, 11 deselected, 0 failures** -- clean, confirming this task's changes broke
  nothing in the standard regression posture. Flagged for the reviewer as a pre-existing gap
  (a DB-gated test with no coverage in the normal CI path) but explicitly NOT fixed, since
  `server/api/manager/service.py::org_usage` is mainline aggregation code, out of this task's
  scope (plan §8).
- **A second, more significant bug found while taking the P11 screenshots** (clicking through
  the actual web UI, not just the API test): `GET /v1/features/{id}` 404s at the FastAPI
  router level -- never even reaching the handler -- for any feature id containing a literal
  `/`, e.g. `branch:902909772:feat/feature-pipelines-ingest` (an H5 branch-clustered feature,
  produced for our synthetic PRs whose `link_method` is `"none"`, i.e., legitimately
  issue-less, clustered by shared branch prefix -- exactly matching real production behavior,
  confirmed by calling `quality.feature_by_id`/`service.feature_detail` directly in a script
  against the loaded `econ_synth` db, which both returned complete, correct payloads). The
  route `@app.get("/v1/features/{id}")` in `src/server/api/app.py` uses the default FastAPI
  path converter, which stops at the first `/` -- confirmed directly via `TestClient`: the
  response body is FastAPI's own generic `{"detail":"Not Found"}`, not this app's
  `ApiError`/`NotFoundError` shape (`{"reason": ...}`), proving no route matched at all. This
  is the exact same class of bug `/v1/projects/{id:path}` and
  `/v1/budgets/{scope_type}/{scope_id:path}` already carry an explicit code comment about and
  guard against with `:path` -- `/v1/features/{id}` was simply never given the same treatment,
  and since H5 branch-clustered feature ids are a real production concept (a real GitHub
  branch name containing `/`, e.g. `feature/foo`, is extremely common), **this is almost
  certainly a genuine pre-existing mainline bug that would break identically on real
  production data**, not something specific to synthetic data -- it was simply never exercised
  by an existing test with a slash-bearing branch name. **Deliberately NOT fixed**:
  `src/server/api/app.py` is mainline API routing, explicitly out of this task's scope (plan
  §2 never lists it as a file to touch; plan §8 excludes "any change to... API aggregation").
  Flagged prominently for the reviewer/maintainers as a one-line fix (`{id}` -> `{id:path}` on
  that route, mirroring the two routes that already do this) with a suggested follow-up task.
  Confirmed this does NOT affect P11's own smoke test: `test_synthetic_smoke.py`'s
  `feature_id` fixture deliberately queries `kind = 'issue'` artifacts (numeric ids, no `/`),
  so it never hits this path; verified separately, directly, that an issue-linked feature id
  and a PR artifact_id both navigate and render correctly in the actual browser (see below).
- P11 screenshots taken with the built-in Browser tools against the real running stack (API on
  `:8091` with `TALIUM_DB_DSN` pointed at `econ_synth`; web UI via `npm run dev` with
  `VITE_API_TARGET=http://localhost:8091`, `VITE_VIEWER_ID=github:vp-eng`,
  `VITE_VIEWER_ROLE=admin`) -- all three required views confirmed rendering real synthetic
  data correctly: **Overview** ($716 spent, 91 merged PRs, spend-by-agent donut, initiatives
  trend), **Teams** (4 teams table with spend/PRs/$-per-PR/rework/last-active, all live), and
  a **PR detail** (`#218 feat(config): launch the permission model` -- cost-to-produce
  breakdown, review rounds, time-to-merge, quality tag, full open->review->merge lifecycle
  timeline, and the review list with reviewer/verdict/date). `npm install` was required first
  (`src/webui/node_modules` did not exist in this worktree).
- TODOs added: none.
- Open issues: none new in this package's own scope. Two findings flagged for the reviewer,
  both outside this task's file-touch scope: (1) the pre-existing I6 calibration finding from
  P9 (§ above); (2) the `/v1/features/{id}` missing `:path` converter bug just described,
  discovered via manual UI testing, confirmed real via direct service-layer + TestClient
  reproduction, left unfixed as mainline/out-of-scope.

## Post-handoff: decisions from the parent session, and two follow-up fixes

After the P11 handoff report, the parent session reviewed every open item and gave explicit
direction on four of them:

- **`/v1/features/{id}` routing bug (P11 finding #2)** -- "unrelated to synthetic datagen but
  we need to fix it regardless." **Fixed**, even though `src/server/api/app.py` is mainline
  API code outside this task's own file-touch scope, because the parent session explicitly
  authorized it. Changed `@app.get("/v1/features/{id}", ...)` to
  `@app.get("/v1/features/{id:path}", ...)` (one line), matching the same fix already applied
  to `/v1/projects/{id:path}` and the budgets routes. Added a regression test,
  `tests/server/api/test_app_feature_detail.py::
  test_branch_clustered_feature_id_with_a_slash_is_routable`, which seeds an orphan PR with a
  branch name containing `/` (`feat/widget`) so it clusters into an H5 branch feature with a
  slash-bearing id, then asserts `GET /v1/features/branch:<repo_num>:feat/widget` returns 200.
  **Verified the test actually catches the bug**: temporarily reverted the route to `{id}`
  (no `:path`), reran just this test, confirmed it fails with a 404 exactly as the original
  bug report described, then restored the fix and confirmed it passes again. Full
  `tests/server/api` directory with `TALIUM_TEST_DSN` set → 443 passed, 1 failed (the
  pre-existing, unrelated `test_manager_service_org_usage.py` failure from item #2 below --
  nothing else regressed).
- **The pre-existing unrelated `test_manager_service_org_usage.py` failure (P11 finding #2)**
  -- "let #2 be as is for now." Left untouched, as directed.
- **The I6 calibration divergence on the real example config (P9 finding, restated in P11)**
  -- "I can live with this divergence." No code or config change made; the finding stands
  as a documented, accepted limitation, not something requiring a fix.
- **The P2 `channel_micros`/`channel_cost` naming conflict (P2 finding, restated in P11)** --
  the parent session picked the literal plan §5 P2 reading: `channel_micros(models, at) ->
  dict`. Re-checked the real `graph_fold.py` source before implementing (not from memory) and
  found the plan's own text mis-attributes this shape to `_price_channel` -- the real
  `graph_fold._price_channel(model_tokens, kind, pricing, at) -> int | None` is actually the
  single-channel, kind-taking function, and the dict-returning shape the parent session wants
  belongs to `graph_fold.channel_cost_of`. **Fixed** by renaming to match the real source's own
  split exactly: `Pricer.channel_micros(models, at) -> dict` (was `channel_cost`, now the
  literal plan-matching public name) calls a new private helper `Pricer._price_channel(models,
  kind, at) -> int | None` (was the old 3-arg `channel_micros`, renamed to match the real
  function's own name -- the leading underscore mirrors the real source, not a Python-privacy
  claim, since `content.py` legitimately calls it cross-module for the `carry`/resume-cost
  single-channel computations, the same way `graph_fold.py` calls its own `_price_channel`
  from more than one place within that module). Updated all 6 call sites in `content.py` and
  the 1 in `test_parity_graph_fold.py`. The `"channel_cost"` string used as a JSON field name
  in `session_info.info` (D9's own field name) is unrelated and was NOT touched -- only the
  `Pricer` method names changed.
- Tests after both fixes: full `econ-forecast/tests/synthetic` suite -> **198 passed**
  (unaffected by the app.py fix, which is outside this package; the rename is internal to this
  package and every call site was updated in the same change). Full `tests/server/api`
  directory with `TALIUM_TEST_DSN` set -> **443 passed, 1 failed** (the known, accepted,
  unrelated `test_manager_service_org_usage.py` failure only). Full mainline regression
  without `TALIUM_TEST_DSN` (the plan's own §7 command, `pytest tests -q -m "not slow"`) run
  again after both fixes -> **2093 passed, 560 skipped, 11 deselected, 0 failures** (560 vs.
  the earlier 559 is exactly the one new regression test, correctly gated behind
  `TALIUM_TEST_DSN` and therefore skipped here) -- confirms nothing else regressed.
- Commits: `c7ab1cc` (the `/v1/features/{id:path}` fix + regression test), `faa5e07` (the
  `channel_micros`/`_price_channel` rename).
- TODOs added: none.
- Open issues: none. All four post-handoff decisions are resolved (two fixed, two left as
  directed).

## Post-handoff: root-causing "spend is zero" and fixing the worker (mainline)

The user reported the loaded `econ_synth` dashboard showed correct token usage but $0 spend
everywhere, plus asked whether commit->PR linkage was incomplete (partial costs, no total).

- **Root-caused precisely, not by inference.** Isolated `_upsert_table`'s own write path first
  (wrote a test row directly, confirmed it landed correctly and stayed stable for 2s) to rule
  out the loader itself. Then caught the corruption live: `cost_effective_usd_micros` was
  correct immediately after a fresh `--replace` load, then zero again within about a minute,
  with `otel_watermarks.updated_at` landing one second after the reload -- proof something was
  actively ticking against this exact database. `docker ps` + `docker inspect` found the
  answer: the user's local `talium_stack_worker`/`collector`/`ingest`/`api` containers (running
  6+ hours) all had `TALIUM_DB_DSN` pointed at `econ_synth`, not `econ` -- exactly the
  configuration the README's own "Worker warning" names as unsafe.
- **Traced the exact mechanism, not just "the worker touched it."** The user pushed back:
  real jsonl-only sessions correctly get a token-based cost fallback when OTel is absent, so
  why would this synthetic data zero out instead of falling back the same way? Read
  `config/queries/otel.yaml`'s `jsonl_requests_for_session` query and found the real answer:
  the fallback reads individual per-request `assistant_message` events out of
  `sessions.signal_blob -> 'events'` -- the raw event-level structure a real transcript ingest
  produces -- not the aggregate fields (`session_info.tokens_total`/`cost_usd`) this package
  writes. Confirmed directly: every synthetic session's `signal_blob = {"events": []}`, exactly
  as expected, since simulating a full raw per-request transcript was never this package's
  job (plan §8's scope cut). The worker's fallback therefore always folds zero requests for a
  synthetic session and overwrites the loader's correct value with $0. `session_git_links`
  itself was unaffected (3202/4157 rows, matching the loader's own count exactly, both before
  and after) -- the "partial cost" the user saw was this same root cause (PR cost-to-produce
  sums its linked sessions' `cost_effective_usd_micros`), not a linkage gap.
- **Fixed on explicit direction** ("make an exception in the worker logic itself... if events
  are not there, it is a synthetic session which should be left as is"): rather than keying
  off "no events" directly (a real session that happens to have zero assistant_message events,
  e.g. one that errored before its first turn, would be a false positive and get silently
  skipped forever), used the existing, purpose-built `info_blob.synthetic = true` marker every
  synthetic session already carries (D12) -- `config/queries/otel.yaml`'s `touched_sessions`
  query now excludes any session_id whose `sessions` row has that flag, via a **LEFT JOIN**
  (critically not a plain JOIN: first attempt used JOIN and broke 21 pre-existing tests, because
  an OTel-only "stub" session -- PM.2's zero-install tier -- has no `sessions` row yet at the
  exact moment this query runs, and NULL there must still pass through untouched; fixed by
  switching to LEFT JOIN + `COALESCE(..., false)`).
- **Verified precisely**, not just "tests pass": added
  `tests/server/worker/test_otel_trueup.py::test_a_synthetic_session_is_never_touched`, which
  seeds a session shaped exactly like a loader-written one (`info_blob.synthetic=true`,
  `signal_blob.events=[]`, a pre-set `cost_effective_usd_micros`) and asserts a tick leaves
  every field on it byte-identical. Full `tests/server/worker` + `tests/server/storage` +
  `tests/server/test_otel_e2e.py` -> **290 passed, 1 failed** -- the failure
  (`test_both_signals_join_into_one_cost_block`) is pre-existing and unrelated: reproduced by
  temporarily reverting just this change (`git stash push -u -- config/queries/otel.yaml`,
  confirmed the same failure with the OLD query, restored via `git stash apply` + targeted
  `git stash drop stash@{0}` -- never touched the other, unrelated stash entry already on this
  shared stack). Full `econ-forecast/tests/synthetic` -> 198 passed (unaffected, as expected --
  this change is entirely mainline). Full mainline regression
  (`pytest tests -q -m "not slow"`, the plan's own §7 command) run once more ->
  **2093 passed, 561 skipped, 11 deselected, 0 failures** (561 vs. the earlier 560 is exactly
  the new `test_a_synthetic_session_is_never_touched`, correctly gated behind
  `TALIUM_TEST_DSN`) -- clean.
- **Important operational note for the user, not yet actioned**: `talium_stack_worker` (and
  `collector`/`ingest`/`api`) run from a built Docker image -- `config/queries` is not
  bind-mounted (confirmed via `docker inspect`'s `.Mounts`) -- so this fix will not take effect
  in the already-running containers until they are rebuilt. `utils/stack_up.sh` runs
  `docker compose -f docker/docker-compose.stack.yml up -d --build --wait`, which rebuilds and
  restarts everything in one step; offered to run it, awaiting confirmation since it restarts
  the user's whole local stack.
- Commit: `3c0aedd`.
- TODOs added: none.
- Open issues: the stack rebuild above, pending user confirmation.
