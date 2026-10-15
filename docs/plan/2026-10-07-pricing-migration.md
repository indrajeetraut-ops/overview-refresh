# Pricing migration (#68) — implementation plan

## Context

Design: [2026-10-07-pricing-migration-design.md](../design/2026-10-07-pricing-migration-design.md) (`§` references are to it). Covers moving every `common.pricing` consumer to `common.model_pricing` via a new `Pricer`, retiring `common/pricing.py` and `config/pricing.yaml`, and repricing stored actuals. Process: `talium-impl-workflow` after the user approves this plan; branch `feat/issue-68-pricing-migration`; do not push unless asked.

Two definitions the tasks rely on (the context note lives on another branch):
- **Grep gates** (tracked files only, so gitignored output such as `econ-forecast/artifacts/` and `tests/graph_migration/.local-baseline/` is excluded):
  1. `git grep -n "common\.pricing\b\|common import pricing" -- src econ-forecast tests utils` returns nothing.
  2. `git grep -n "pricing\.yaml" -- src econ-forecast tests utils config docker webui` returns only `model_pricing.yaml` hits, or files on T10's historical list (the single source of truth for what may still mention the legacy file; those files are not edited).
  3. Comments naming the legacy file are scrubbed where code changes: `service.py:1535`, `graph_fold.py:1294`, and the test comments at `tests/server/api/conftest.py:26`, `test_manager_aggregate.py:22`, `test_service_pricing.py:35`, `tests/server/worker/test_resume_cost_pricing.py:14`, `test_graph_fold_pricing.py:14`, `test_segments_pricing.py:20`.
- **Table effect:** the cost ratio between the dashboard's and the forecast's pricing of the same tokens that is caused by different price tables alone (cache-write handling, #67, excluded). Measured on the synthetic org (28 days to 2026-09-30) by pricing each model's tokens once through the generator's pricing wrapper (named `OrgPricer` after T5) and once through `forecast.cost` with the cache-write categories merged. Before this plan: 1.13x overall, 1.50x on `claude-sonnet-5` over days on or after 2026-09-01 (1.00x before that date), 1.00x on Opus 5 and Haiku 4.5. (`2026-10-05-forecast-dashboard-integration.md` quotes 1.40x for the total effect including cache writes.)

## Deliverables

1. `common.model_pricing.Pricer`, `split_cache_write`, `price_to_micros`, `history.yaml` loader.
2. All consumers on `Pricer`; `common/pricing.py`, `config/pricing.yaml`, `tests/common/test_pricing.py` deleted.
3. Synthetic generator pricing with `Pricer` (wrapper renamed `OrgPricer`); seed dump regenerated.
4. Before/after cost report on an existing real database; refold run.
5. Updated `docs/modules/model-pricing.md` and `src/common/model_pricing/README.md`.

## Conventions

- Python 3.11+. Run everything from the repo root: `pytest.ini` already sets `pythonpath = . src tests econ-forecast/src` and `testpaths = tests econ-forecast/tests`. Use `venv/` with the root `requirements.txt` installed (the econ-forecast-only env lacks tree-sitter and 126 causal-graph tests fail).
- Structured JSON logging via `common.msging.log`. CLI entry points are `cli_{function}.py` inside a package module, with a `utils/*.sh` wrapper calling `python -m {module}.cli_{function}` (T7 follows this; `utils/fit_prices.py` is an existing bare script and keeps its name).
- Never `git -C`; no Claude/Codex attribution in commits or code; commits scoped, each ending `Refs #68`.
- Remove a `TODO` when done. The legacy `TODO(pricing)` block lives at the end of `config/pricing.yaml` (fast mode 2x on Opus 5 / 4.8 via the `speed` attribute of `api_request`; data residency 1.1x for `inference_geo: "us"`; batch 50% off). T1 adds those three rows, with their request-level signal, to the `model-pricing.md` §2.5 table (which already covers tier, long context, discounts, credits) so the text survives T8's deletion.
- DB-backed tests need `TALIUM_TEST_DSN` pointing at a scratch database whose name ends in `_synth` (`tests/server/conftest.py:11-14` enables them only then) — never the local `econ_synth` itself; e.g. `econ_68_synth`.
- Money stays `Decimal`; storage in integer micros.
- `config/model_pricing/channel_config.yaml` is untracked (only the `.example` is): absent means vendor channels, which is what every test and the default deployment use.
- The service configs (`ApiConfig`, `WorkerConfig`, `IngestConfig`) are plain pydantic models that ignore unknown keys; no `config/*.yaml` sets `pricing_config`. Do not add `extra="forbid"` here.

## Tasks

### T1 — `Pricer` core  [deps: none]
Files: `src/common/model_pricing/pricer.py` (incl. the history loader), `src/common/model_pricing/__init__.py`, `config/model_pricing/history.yaml` (`rows: []`), `docs/modules/model-pricing.md` §2.5, `tests/common/model_pricing/test_pricer.py`, `tests/common/model_pricing/helpers.py`
- `Pricer.load(org_id, version, root, config_root, history_path)`, `Pricer(prices, history, version)`, `price_tokens`, `rates`, `models(at)`, `fingerprint` (§2.2, §3.2). `load` passes `org_id or ""` to `for_org` (an unknown or empty org gets vendor channels; no annotation change). The history `model_id` check against the catalog registry (`ConfigError`) lives in `load`.
- `rates(model, at)` returns `dict[token_type, Decimal | None]` (USD per token) or `None` when the model does not resolve; `aggregate.py` changes its `block.input` attribute access to key access. `models(at)` returns `{model_id: rates dict}` for vendor `anthropic` models only, on the org's resolved channel for that vendor, keyed by registry id, excluding models retired on `at` or with no row on that channel (legacy served 7 Claude models; the catalog adds e.g. `claude-sonnet-4-5`).
- Fingerprint hash input: rows as canonical JSON (sorted keys), each rate rendered `format(d.normalize(), "f")` so YAML `3.0` and `3` hash alike, rows sorted by `(channel, model_id, start_date)`.
- Move `_as_date`, `split_cache_write`, `price_to_micros`, `TOKENS_PER_UNIT` semantics from `common/pricing.py` unchanged (copy; the legacy file stays until T8). `at` accepts `date | datetime | ISO str`; `None` raises `ValueError`.
- `rate_on`: history row only for days before the key's first catalog row; otherwise `OrgPrices.rate` (§2.2). `history.yaml` schema per §3.2.
- Fingerprint: `catalog.version` plus `+h<hash8>` when history has rows; hash input as in §2.2.
- Test helper `helpers.py`: builds a tiny catalog (index + version file) and, optionally, a channel config in `tmp_path`, so tests can create custom tables (a dated model with two rows, a partial-rate model, an empty or thinner catalog for a different fingerprint, a catalog 10x off for the drift test).
- Port `tests/common/test_pricing.py` onto the helper. Rewritten (decisions #2, #7): the 2025-date `None` case (lines ~85-89) now expects the earliest row's price; the Sonnet 12.00 (08-31) / 18.00 (09-01) split (lines ~165-175) now expects the same price on both days. Dropped (the surface no longer exists): `block_for`, `load_config`, env-ref tests. Kept as is: unknown model, partial rate, cache-write split, ISO-string `at`, fingerprint sensitivity.
- Accept: worked example §2.2 = exactly `Decimal("6.00")` with the real catalog (`'claude-sonnet-5'`, tokens `{"input": 1_000_000, "cache_write": 1_000_000}`, `at="2026-10-01"`); unknown, ambiguous, retired model, and a missing rate with tokens > 0 each → `None`; a history row dated before the catalog's first version wins for an earlier day and the catalog wins from its first row on; `claude-haiku-4-5-20251001` resolves to `claude-haiku-4-5`; `at` as `date`, `datetime`, and ISO string with `Z` all work; two different catalogs give different fingerprints; `at=None` raises.

### T2 — Service config fields  [deps: T1]
Files: `src/server/api/config.py`, `src/server/worker/config.py`, `src/server/ingest/config.py`, `tests/server/api/test_config.py` (and the worker/ingest config tests that exist)
- Add `pricing_org: str | None = None`, `pricing_version: str | None = None` beside `pricing_config`, which stays (marked `TODO(pricing)`) until T3/T4 migrate its readers and remove it; this keeps the suite green at the T2 commit.
- Accept: configs load with neither key; a yaml still carrying `pricing_config` loads without error (key ignored, per Conventions); defaults are `None`.

### T3 — Fold, loaders, segments, true-up  [deps: T1, T2]
Files: `src/server/worker/graph_fold.py` (`load_pricing_survivable(org, version)`), `worker/cli_work.py:25,64`, `worker/cli_refold.py:31,91`, `src/server/ingest/app.py:15,44`, `worker/segments/__init__.py` (`compute_segments`) and `segments/beats.py` (parameter `pricing_config` → `pricer`, through callers), `worker/otel_trueup.py` (comment at line 73, `_load_pricing`), `config/queries/otel.yaml` (comment ~line 79), tests `tests/server/conftest.py:70-76`, `tests/server/worker/{test_cache_busts,test_graph_fold_pricing,test_interrupted_pricing,test_resume_cost_pricing,test_segments_pricing,test_otel_trueup}.py`
- Keyword names stay, only types change: `GraphFold(pricing=Pricer)`, `compute_segments(pricing=...)`, `_estimate`/`_join_requests(pricing=...)`, `OtelTrueUp(pricing=Pricer)`. `OtelTrueUp`'s string ctor arg `pricing_config` is dropped; with no `pricing=` it loads `load_pricing_survivable(pricing_org, pricing_version)` from the worker config (same default as `GraphFold` call sites). `beats.py`'s parameter `pricing_config` is renamed `pricing`.
- `load_pricing_survivable` reads `pricing_org`/`pricing_version` from the service config; catalog absent (`CatalogError`) → `None` with the existing warning; a malformed config still raises.
- `otel_trueup`: `pricing_fingerprint = pricer.fingerprint`, `None` when no pricer (as today).
- Accept: `test_graph_fold_pricing.py`, `test_interrupted_pricing.py`, `test_resume_cost_pricing.py`, `test_segments_pricing.py`, `test_segments_beats.py`, `test_cache_busts.py`, `test_otel_trueup.py` pass. Allowed assertion edits, each with a comment citing the decision: early-date `None` cases (§4 #7: `test_pricing.py:88,174-175` equivalents and any `at=2025` fixture), the Sonnet 3.00/15.00 block (§4 #2), and tables built via the T1 helper in place of `PricingConfig.model_validate`. `test_otel_trueup.py` still shows: drift warning for a 10x-wrong table, a different fingerprint for a thinner table, recompute on fingerprint mismatch. Check that every `*_pricing.py` test's dates are valid under the earliest-row rule; list any that are not.
- Default-constructed tests (`tests/server/test_otel_e2e.py`, `test_ingest_to_fold.py`, `tests/server/ingest/test_fold_handler.py`, `tests/server/api/test_app_*.py`) now load the catalog instead of `config/pricing.yaml`; run them and list every changed cost or `cost_basis` with its decision number in the T3 commit message.

### T4 — API  [deps: T1, T2]
Files: `src/server/api/app.py`, `api/manager/aggregate.py` (rename `pricing_config` → `pricer` at lines ~487, 504, 520, 570, 595; keep the `is None` guard on rates), `api/single_user/service.py` (`pricing_models`), `tests/server/api/{conftest.py,test_app.py,test_service_pricing.py,test_manager_aggregate.py,test_service_session_detail.py}`, `src/webui/src/data/api-types.ts` only if the schema changes
- `Pricer.load(config.pricing_org, config.pricing_version)` at startup; survivable on `CatalogError`.
- `pricing_models`: per design §5 — numeric equality contract, `effective_from` = row `start_date`, two-decimal USD-per-million strings, model set and keys from `pricer.models(at)` (registry ids, anthropic vendor, org channel; retired models omitted).
- `aggregate.py` reads `rates(...)["input"]` / `["output"]`; keep the `is None` guards.
- `_blended_rate`/ratio helpers unchanged in arithmetic (per-token vs per-million cancels in `cheaper/actual`).
- Accept: the five test files pass; for `claude-opus-5` the five rates equal 5.00 / 25.00 / 0.50 / 6.25 / 10.00 numerically; the response contains `claude-haiku-4-5` not the dated id; with the catalog absent `/v1/pricing/models` returns an empty model map, not a 500.

### T5 — Synthetic org generator  [deps: T1]
Files: `econ-forecast/src/forecast/synthetic/org/{pricing,config,expand,content}.py` (`config.py:19,86,352,489` and `validate_org_inputs(..., pricing)` with callers `cli_synt_gen.py:73-77`, `expand.py:105`; `content.py:30,232,248,275,292,384,520` use the wrapper), `tests/server/conftest.py:70-76` (builds the org via `pricing_file`), `synthetic/cli_synt_gen.py`, `synthetic/org/cli_synth_org.py:108-116`, `forecast/testing/cli_cache_write_acceptance.py`, `econ-forecast/config/synthetic/org/{org,series}.yaml`, tests `econ-forecast/tests/synthetic/org/{conftest,test_content,test_expand_invariants,test_cli_org,test_determinism,test_parity_graph_fold}.py`, `econ-forecast/tests/synthetic/test_cache_write_acceptance.py`
- Rename the wrapper class `Pricer` → `OrgPricer`; it wraps a `common.model_pricing.Pricer`.
- The generator prices as org id `""` (vendor channels) unless `org.yaml` sets `pricing_org`. `OrgConfig.pricing_file` → `pricing_version`; `cli_synth_org.py` copies the pinned catalog file(s), `index.json`, `history.yaml`, and `channel_config.yaml`/`aliases.yaml` if present into the dataset snapshot and the snapshot loads from there (`expand.py:155` currently snapshots `pricing/pricing.yaml`). A version name alone does not reproduce.
- `config.py:489` load-time check (`price_tokens` of 1 input token per series model) keeps working; `series.yaml` keeps the dated Haiku id. Update the comment at `series.yaml:88`.
- Accept: `test_parity_graph_fold.py` byte-for-byte; `test_determinism.py` same version → same dataset; a dataset snapshot loads and prices with the live catalog directory renamed away; the tests above pass with fixtures built on the T1 helper where they used custom tables.

### T6 — Fit verifier  [deps: T1]
Files: `utils/fit_prices.py`, `tests/utils/test_fit_prices.py`
- Output a fit-vs-catalog comparison per model (fitted rate, catalog rate, ratio) instead of a `pricing.yaml` block; keep the exact-`Fraction` solver.
- Accept: unit test on `solve` plus a comparison function with synthetic rows at known rates → ratio 1.000 per kind; docstring no longer mentions `pricing.yaml`. No DB needed in the test; a manual run against a scratch DB is optional.

### T7 — Before/after report  [deps: T3, T4]
Files: `src/server/worker/cli_pricing_report.py` (run as `python -m server.worker.cli_pricing_report`), `utils/pricing_report.sh`, `tests/server/worker/test_pricing_report.py` (needs a `*_synth` scratch DB, Conventions)
- What "before" is. Not `otel_api_requests.cost_usd_micros`: that is the vendor's own cost, adopted as truth (`otel_trueup.py` `BASIS_VENDOR`), and comparing it to `Pricer` measures drift, not the migration. Our computed money is:
  1. the true-up's request ledger in `session_info.otel`, per-request `cost_est_usd_micros` (`otel_trueup.py:253,293`; only these depend on the price table when no vendor cost exists), with token counts from `otel_api_requests` (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`) or the JSONL record;
  2. the fold's `info.channel_cost`, `carry`, `resume_cost`, `cache_busts` priced from the session's per-model token map (the input of `graph_fold.channel_cost_of`).
  The report covers both; the implementer confirms the exact JSON paths against `otel_trueup.py:356-397` and `graph_fold.py:587`. A vendor-vs-`Pricer` drift column is shown for information only.
- Output per model and per source (1 or 2): stored sum (before), sum repriced with `Pricer` from the same tokens (after), ratio, count unpriced before / after, and a cause column per design §4.2. Markdown on stdout, JSON logs on stderr.
- Accept: seed a scratch DB with Sonnet 5 sessions dated 2026-08-15 and 2026-09-10 whose ledger estimates and fold costs are written at the old block → ratio 1.000 and 0.667; Opus 5 and Haiku 4.5 1.000; a seeded request with a dated model string (`claude-sonnet-4-5-20250929`) whose stored estimate is `null` (unpriced) appears priced after, counted in the unpriced column, cause #8; a seeded vendor-cost row never moves the ratios.

### T8 — Retire legacy  [deps: T3, T4, T5, T6]
Files: delete `src/common/pricing.py`, `config/pricing.yaml`, `tests/common/test_pricing.py`
- Accept: grep gates 1 and 3 (Context) are empty and gate 2 shows only T10's list; `pytest tests` and `pytest econ-forecast/tests` green from the repo root.

### T9 — Reprice and regenerate  [deps: T7, T8]
Files: `docs/design/notes/2026-10-pricing-migration-report.md` (new, holds both reports)
- Real database, on a scratch copy first (`docker exec econ_pg createdb -U postgres -T <db> <db>_68_synth`, name ends `_synth`): T7 report (before), `python -m server.worker.cli_refold`, run the true-up once, T7 report (after).
- Synthetic: regenerate the `econ_synth` seed dump with the pinned catalog; never refold `*_synth`.
- Accept: every ratio ≠ 1.000 is explained by §4 #2, #7 or #8; on the regenerated org the table effect (Context) is 1.00 for every model.
- Prod (`cc-econ-demo`) refold: separate step needing the user's go-ahead.

### T10 — Docs  [deps: T8]
Files: `docs/modules/model-pricing.md` (§9 `Pricer`, §10 layout, §12 mark #68 done, header "Replaces" → "Replaced"), `src/common/model_pricing/README.md`, `docs/modules/otel-intake.md`, `docs/design/{user-dashboard-design,dashboard-datasource-modes,2026-08-04-otel-intake-design}.md`, `econ-forecast/docs/design/{cost-module,cache-write-contract,synthetic-org-expansion}-design.md`, `econ-forecast/docs/arch/module-architecture.md`, `docs/plan/2026-10-05-forecast-dashboard-integration.md` (unblock note)
- Historical files (the single list; left unedited, and the only `pricing.yaml` hits gate 2 may show): `docs/plan/` entries dated 2026-08-01, 08-06, 09-12, 09-15, 09-18; `docs/design/2026-08-04-otel-intake-design.md`; `econ-forecast/docs/design/model-pricing-plan.md`, `model-pricing-impl-brief.md`, `model-pricing-retirement-brief.md`, `cache-write-contract-design.md`. Everything else in the Files list above is edited. Resolve any further hit by editing it or adding it here with a reason.
- Accept: gate 2 (Context) returns only `model_pricing.yaml` and the files listed here.

## Tests

| File | Covers |
|---|---|
| `tests/common/model_pricing/test_pricer.py`, `helpers.py` | T1: worked example, `None` cases, history precedence, `at` types, normalisation, fingerprint |
| `tests/server/worker/test_graph_fold_pricing.py`, `test_interrupted_pricing.py`, `test_resume_cost_pricing.py`, `test_segments_pricing.py`, `test_segments_beats.py`, `test_cache_busts.py` | T3 |
| `tests/server/worker/test_otel_trueup.py` | T3: fingerprint invalidation, drift |
| `tests/server/api/test_app.py`, `test_service_pricing.py`, `test_manager_aggregate.py`, `test_service_session_detail.py` | T4 |
| `econ-forecast/tests/synthetic/org/*`, `econ-forecast/tests/synthetic/test_cache_write_acceptance.py` | T5 |
| `tests/utils/test_fit_prices.py` | T6 |
| `tests/server/worker/test_pricing_report.py` | T7 |

## Out of scope

- Fingerprint on fold output → [#83](https://github.com/talium-ai/code-econ/issues/83).
- Fast mode, batch, data residency, negotiated discounts → #70 / design §8.
- Prod refold without explicit approval.
- #67 files (`forecast/cost/rates.py`, `synthetic/org/{sessions,requests,models}.py`).

## Verification (end to end)

1. `pytest tests` and `pytest econ-forecast/tests` green from the repo root (`TALIUM_TEST_DSN` set to a scratch DB).
2. Grep gates (Context) pass.
3. `python -c "from common.model_pricing import Pricer; print(Pricer.load(None, None).price_tokens('claude-sonnet-5', {'input': 1_000_000, 'cache_write': 1_000_000}, '2026-10-01'))"` prints `6.00`.
4. Before/after report (T9) saved and every difference explained.
5. Regenerated synthetic org: table effect 1.00 per model.
6. Start the API against the scratch DB; `/v1/pricing/models` lists the catalog's models; a session detail request prices without error.
