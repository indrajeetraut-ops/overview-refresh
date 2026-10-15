# Model pricing (`common.model_pricing`) — implementation plan

## Context
Cost calculation needs per-token prices for the channel an org actually buys through (vendor API,
AWS Bedrock, Azure, GCP Vertex), dated so that each usage or forecast day gets the price in effect that
day. The design is approved: `docs/modules/model-pricing.md` (cited as §N). This plan covers **this
module only**. No consumer (cost/budget) integration, no dashboard migration (#68), no service tiers (#70).

**Process:** start only after explicit approval of this plan; then run the `talium-impl-workflow`
skill (sonnet subagent, handshake, worktree, tests + persisted report, Major/Minor review).

## Deliverables
- `src/common/model_pricing/` package (§10) with a module `README.md`.
- `config/model_pricing/` templates (§4.2–4.3, §5.3).
- `utils/model_pricing.sh` wrapper with `update`, `match`, `show`, `versions` (§8).
- `tests/common/model_pricing/` with offline fixtures (§10.1).
- `src/common/pricing.py` and `config/pricing.yaml` untouched.

## Conventions (repo)
- Python 3.11; deps already in `requirements.txt`: `httpx`, `pydantic`, `PyYAML`, `structlog`. No new deps.
- Logging: `common.msging.log.get_logger("model_pricing")`, `configure_logging()` in each CLI (§8).
- CLI entry points `cli_<function>.py`; wrapper follows `utils/ingest.sh` (`exec python -m …`).
- Money: `Decimal` in files and API (§2.2); rates stored as decimal strings.
- `GCP_BILLING_API_KEY` read from the environment (`.env` loaded by the shell), never committed.
- Anything deferred in code gets a `TODO(<issue or §>)` comment (CLAUDE.md).

## Tasks

Dependencies in brackets. T2–T5 can run in parallel after T1.

### T0 — Package skeleton, errors, config loading
Files: `__init__.py`, `errors.py`, `config.py`.
- `ConfigError`, `CatalogError`, `SourceError` (§9).
- Pydantic models + loaders, each file optional with defaults (§4.4):
  `model_pricing.yaml` (channels to price, geo map, sources), `channel_config.yaml` (org → vendor →
  channel; channel must be priced, else `ConfigError`), `aliases.yaml`.
- Channel ID parsing/validation: `anthropic|openai|gemini`, `aws_bedrock:<region>:<regional|global>`,
  `azure:<region>:<global|data_zone>`, `gcp_vertex:<region>:<regional|global>` (§4.1).
- `TOKEN_TYPES` constant (§2.2).

Accept: absent files → defaults; invalid channel / unknown channel in channel config → `ConfigError`.

### T1 — Model name normalization [T0]
File: `normalize.py`.
- `norm`, `strip_date`, `canon` (§5.1).
- Registry type: `{model_id: {vendor, names_seen}}` + ambiguous set (§5.2).
- `normalize(x, registry, aliases)` → `model_id` + step used (`alias` / `normalized` / `canonical`) or
  `ambiguous` / `unmatched` (§5.3).

Accept: §5.1 table as tests; `gpt-4o` family conflict → four models, undated unknown date → ambiguous.

### T2 — LiteLLM source [T1]
File: `sources/litellm.py`.
- `fetch(commit=None)`: resolve latest commit SHA via GitHub commits API; download raw file at SHA
  (`httpx`). Returns payload + provenance.
- `parse(payload, cfg)`: keep `mode ∈ {chat, responses}`; field → `token_type` (§6.3); ignore tier/
  context fields; build **registry** from vendor-channel keys (§5.2); emit source records for vendor
  channels and, via key prefixes + geo map, for configured cloud channels (§6.4); record
  `deprecation_date` as `retired_on` (§3.1).

Accept: fixture subset produces expected registry, vendor rows, Bedrock `us.`/`global.` rows, Azure
`azure/`, `azure/eu/`, `azure_ai/` rows, Vertex rows; unmapped listed.

### T3 — AWS Price List source [T1]
File: `sources/aws_price_list.py`.
- `fetch(region)`: `AmazonBedrockFoundationModels/current/<region>/index.json`.
- `parse(payload, region, registry)`: servicename → model string (§5.3 note) → `normalize`;
  `usagetype` newer + older schemes → `token_type` and scope; ignore `Batch`, `Reserved_*`; USD per 1M
  ÷ 1,000,000; both schemes differ → newer wins + warning (§6.3).

Accept: fixture reproduces §3.2 rows (Sonnet 5 regional/global, Opus 4.8, Haiku 4.5); non-vendor
products (Cohere, …) → unmapped.

### T4 — Azure Retail Prices source [T1]
File: `sources/azure_retail.py`.
- `fetch(region)`: filter `serviceName eq 'Foundry Models'` and `armRegionName`; follow `NextPageLink`.
- `parse(items, registry)`: `productName` `GPT<n>` + meter version tokens → model string; meter tokens
  case-insensitive; keep `ShortCo` + standard tier; `Gl` → global, `DZ` → data_zone; unit `1M` only.

Accept: fixture with mixed-case meters, `LongCo`/`Batch`/`PP` ignored; `gpt-5.6-sol` data_zone
4.40 / 22.00 / cache_write 5.50; paging followed.

### T5 — GCP Billing source (skip path only) [T0]
File: `sources/gcp_billing.py`.
- Enabled only when `GCP_BILLING_API_KEY` is set (`auto`); otherwise returns `status: skipped` with reason.
- SKU mapping is **[open]** (§6.3, §12): implement the skip path and a `TODO(§12 GCP)` stub
  that raises `SourceError("gcp_billing mapping not implemented")` if explicitly enabled.

Accept: no key → skipped, Vertex channels priced from LiteLLM.

### T6 — Build pipeline [T2, T3, T4, T5]
File: `build.py`.
- Steps §6.1: acquire (any enabled source fails → `SourceError`, nothing written) → registry →
  normalize → merge per `(channel, model_id)` with source precedence and whole-row rule (§6.4) →
  derive cache-write (§6.5) → validate (§6.6; change check skipped on first build) → stamp
  `start_date = built_on` → `hash8` over rates only → no-op if unchanged → atomic write of
  `<version>.json`, `<version>.refresh.md`, `index.json`.
- Fetchers injectable (tests pass fixture payloads).

Accept: same fixtures twice → same `hash8` and second run no-op; one changed rate → new version;
`litellm_rates` only when a non-LiteLLM source won; excluded / unmapped / `no_longer_published` recorded.

### T7 — Catalog read and lookup [T6]
File: `catalog.py`.
- `load_catalog(version=None, root=…)`: read `index.json`; load the bound version **and earlier
  versions**; verify each `hash8`; missing/unknown/corrupt → `CatalogError`.
- `Catalog.for_org(org_id)` → `OrgPrices` (channel config applied, default vendor channel).
- `OrgPrices.resolve(x)` → `MatchResult` (§2.4 steps 1–3; never raises for unknown models).
- `OrgPrices.rate(match, token_type, date)` → forward / backward / retired rules (§3.4).
- `OrgPrices.mapping_report(ids)`.

Accept: §2.4 example rows; §3.4 lookup table (backward, forward, no longer published, retired); no
fallback across channels; version binding ignores later versions.

### T8 — Reports [T6, T7]
File: `reports.py`.
- Refresh report (§7): provenance per source; per channel rows by source, added / changed / no longer
  published, `source_diff`, excluded, unmapped, warnings.
- `render_mapping(results, fmt="md"|"json")` (§7, §9).

Accept: golden Markdown/JSON for fixture build and a mapping of matched + unmatched + ambiguous IDs.

### T9 — CLIs and wrapper [T6, T7, T8]
Files: `cli_update.py`, `cli_match.py`, `cli_show.py`, `cli_versions.py`, `utils/model_pricing.sh`.
- Options and exit codes per §8; `--dry-run` writes nothing; JSON logs on stderr, results on stdout.
- Wrapper dispatches `utils/model_pricing.sh <command> …` → `python -m common.model_pricing.cli_<command>`.

Accept: `--help` works without catalog/network; exit codes 0/1/2 per §8; `update` with injected
failing fetcher → exit 1, no files.

### T10 — Config templates and README [T0–T9]
- `config/model_pricing/model_pricing.yaml` (commented; default channels list empty or the channels we
  need), `channel_config.example.yaml`, `aliases.example.yaml`.
- `src/common/model_pricing/README.md`: purpose; cost key `(date, channel, model_id)`; channel config
  (optional, default vendor); how prices are acquired and merged; prices over time (forward/backward/
  retired); CLI usage with examples; API usage; required vs optional inputs; troubleshooting
  (unmatched / ambiguous / retired / skipped source). Link to the design doc.

Accept: README examples runnable against a fixture-built catalog.

### T11 — First live catalog (needs approval: network + commit)
- Run `./utils/model_pricing.sh update` against live sources; review refresh report; commit
  `config/model_pricing/catalog/` first version.
- Capture fixture subsets used in T2–T4 from real responses (already partly available: LiteLLM commit
  `d3462c65`, AWS us-east-1 publication 2026-09-22).

## Tests (`tests/common/model_pricing/`)
| File | Covers |
|---|---|
| `test_config.py` | T0 defaults, channel validation, `ConfigError` |
| `test_normalize.py` | T1 §5.1 table, registry grouping, aliases, ambiguous |
| `test_source_litellm.py` / `_aws.py` / `_azure.py` / `_gcp.py` | T2–T5 parse on fixtures |
| `test_build.py` | T6 merge precedence, derive, validate, hash/no-op, atomic write, source failure |
| `test_catalog.py` | T7 lookup rules, version binding, channel config default |
| `test_reports.py` | T8 golden outputs |
| `test_cli.py` | T9 exit codes, `--help`, `--dry-run` |
| `fixtures/` | Small captured payloads per source (§10.1 list) |

No network in tests. Run: `venv/bin/python -m pytest tests/common/model_pricing -q`.

## Out of scope
- Cost/budget integration, dashboard/ingest migration (#68), service tiers and credits (#70),
  cache-write tokens in usage (#67), GCP SKU mapping (T5 stub only), scheduled `update`.

## Verification (end to end)
1. `pytest tests/common/model_pricing -q` green.
2. Fixture-driven `update` → version file matches §3.3 shape; second run no-op.
3. `match --org talium.ai --models claude-sonnet-5,gpt-5.6-sol,claude-sonnet` with a channel config
   (Claude on `aws_bedrock:us-east-1:regional`) → 2.20 / 4.00 / unmatched.
4. T11 live run reviewed before commit.
