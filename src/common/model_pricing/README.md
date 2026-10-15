# `common.model_pricing`

One master rate table for per-token model prices, keyed by `(date, channel, model_id)`.
Every cost calculation and price report reads only this table.

Full design: [`docs/modules/model-pricing.md`](../../../docs/modules/model-pricing.md)
(cited below as `§N`). This README is the quick-reference for using the module; the
design doc is authoritative on *why*.

## 1. Purpose

- Prices for Anthropic/OpenAI/Gemini's own APIs and for the cloud channels an org
  actually deploys through (AWS Bedrock, Azure Foundry, GCP Vertex) live in one
  versioned table, built from several price sources.
- `update` builds a new table version from LiteLLM, AWS Price List, Azure Retail
  Prices, and (optionally) GCP Billing Catalog.
- The table answers one question: **what is the USD/token rate for this model, on
  this channel, on this date?**

## 2. Cost key

```text
cost key = (date, channel, model_id)
```

| Part | Where it comes from |
|---|---|
| `date` | Caller-supplied: day tokens were (or will be) consumed. |
| `channel` | Channel config for the org + the model's vendor (§4.3); default = the vendor channel. |
| `model_id` | Observed model string, normalized (§5). |

Rates are per **normalized token type** — every source's own price names are mapped to
these five (§2.2, §6.3):

| `token_type` | Meaning |
|---|---|
| `input` | Uncached input tokens. |
| `output` | Output tokens, incl. reasoning tokens. |
| `cache_read` | Input tokens served from the prompt cache. |
| `cache_write_5m` | Input tokens written to the cache, 5-minute TTL. |
| `cache_write_1h` | Input tokens written to the cache, 1-hour TTL. |

A rate is `Decimal` USD per **one** token; a missing rate is `None`, **never** `0`.

## 3. Channels

| Channel ID | Meaning |
|---|---|
| `anthropic` / `openai` / `gemini` | Vendor's own API. Always priced. |
| `aws_bedrock:<region>:<regional\|global>` | AWS Bedrock. Priced only if listed in `model_pricing.yaml`. |
| `azure:<region>:<global\|data_zone>` | Azure Foundry Models. Priced only if listed. |
| `gcp_vertex:<region>:<regional\|global>` | Google Vertex. Priced only if listed; LiteLLM-only until the GCP adapter ships (§12). |

### Channel config — which channel an org buys each vendor through

Optional file, `config/model_pricing/channel_config.yaml` (template:
[`channel_config.example.yaml`](../../../config/model_pricing/channel_config.example.yaml)).
Absent, org not listed, or vendor not listed → the vendor channel.

```yaml
schema_version: 1
orgs:
  talium.ai:
    anthropic: aws_bedrock:us-east-1:regional    # Claude via Bedrock
    # openai not listed -> openai (OpenAI API)
```

The named channel must be priced (§4.2) or `Catalog.for_org` raises `ConfigError` —
checked once, at load time, not on the first lookup that hits it.

## 4. Required vs optional inputs

| Input | Required? | Absent → |
|---|---|---|
| Network access to LiteLLM (GitHub) | **Required** for `update` | `update` fails (exit 1). |
| A catalog version (`catalog/*.json` + `index.json`) | **Required** for lookup | `load_catalog` raises `CatalogError`; run `update` first. |
| `model_pricing.yaml` | Optional | Vendor channels only. |
| `channel_config.yaml` | Optional | Every org uses vendor channels. |
| `aliases.yaml` | Optional | Only `norm`/`canon` matching (§5.3). |
| `GCP_BILLING_API_KEY` (`.env`) | Optional | `gcp_vertex` channels priced from LiteLLM. |
| AWS / Azure network access | Required only if that cloud's channel is listed | `update` fails (exit 1). |

## 5. How prices are acquired and merged (`update`, §6)

1. **Acquire** every enabled source. Any enabled source's fetch fails → whole update
   fails (exit 1); the current catalog version is left untouched. A failed source
   never silently falls back.
2. **Registry**: build the model registry from LiteLLM's vendor-channel entries first
   — every other source maps its own model names against this registry (§5.2). Then
   extend it with **official-source models** (§5.2): an AWS/Azure record whose reduced
   model string doesn't match anything LiteLLM has, but has a recognizable vendor
   prefix (`vendor_prefixes` in `model_pricing.yaml`, default `claude`→anthropic,
   `gpt`/`o<digit>`→openai, `gemini`→gemini), still becomes a real registry entry and a
   priced row — this is how AWS's older Claude models (dropped from LiteLLM's vendor
   list) get priced at all. No recognizable prefix (e.g. Cohere) → stays `unmapped`,
   same as before this existed.
3. **Normalize** each source record: model name → `model_id`, price name →
   `token_type`, listing → `channel`.
4. **Merge**, one row per `(channel, model_id)` — source precedence:

   | Channel family | First (official) source | Fallback |
   |---|---|---|
   | `anthropic` / `openai` / `gemini` | — | `litellm` (its only source) |
   | `aws_bedrock:*` | `aws_price_list` | `litellm` |
   | `azure:*` | `azure_retail` | `litellm` |
   | `gcp_vertex:*` | `gcp_billing` | `litellm` (only source until §12) |

   A row's rates **all come from one source** — never mixed. The first source wins if
   it has both `input` and `output`; else the fallback; else no row at all.
5. **Derive** missing cache-write rates (§6.5): no cache-write rate published at all →
   both `cache_write_5m` and `cache_write_1h` = `input`, flagged
   `cache_write_billed_as_input` in `derived`; only `cache_write_5m` published →
   `cache_write_1h` = `cache_write_5m`, flagged `cache_write_1h_billed_as_5m` (the reverse,
   only `cache_write_1h` published, is not derived). **Validate** (§6.6): missing/negative
   `input`/`output` → row excluded; other bad rates → dropped to `None` with a warning;
   >50% change vs. previous version → warning only, never blocking.
6. **Retire** (§3.5): a `(channel, model_id)` priced in the previous version but not
   published by any source now gets a **retirement row** — last known rates,
   `source = "retired"`, `retired_on = built_on - 1 day` — so lookups price it `None`
   from that date on, instead of silently keeping its old price forever. Already-
   retired keys still unpublished are carried forward unchanged (same `retired_on`,
   keeps the version hash stable); a retired key published again just becomes a normal
   row again.
7. **Stamp**: every (non-carried) row gets `start_date = built_on` (§3.4).
8. **Version**: `hash8` over served prices only. Unchanged → no-op (exit 0, nothing
   written). Changed → new `<version>.json` + refresh report + `index.json` append.

   Catalog version `2026-09-25-9dbc1746` is intentionally superseded by `2026-09-25-d3e5c412` (the second §6.5 derive rule, #67); its files are retained but unreferenced.

## 6. Prices over time (§3.4) and retirement (§3.5)

Every `update` stamps **every** row with the build date — no per-row diff against the
previous version. A new version exists only when some served price changed (the
`hash8` check). The price history is simply the union of every version's rows.

Lookup of `(date, channel, model_id)` uses the bound catalog version and every earlier
one:

| Rule | Case | Result |
|---|---|---|
| Forward | Rows with `start_date ≤ date` exist | Latest such row. |
| Backward | `date` before the key's first row | The key's earliest row. |
| Retired | Latest row has `retired_on` and `date > retired_on` | No rate (`None`). |
| None | No row for the key in any loaded version | `unmatched`. |

Example (`anthropic` / `claude-x`, two versions built 2026-09-25 and 2026-12-01):

| `start_date` | input $/M | output $/M |
|---|---|---|
| 2026-09-25 | 3.00 | 15.00 |
| 2026-12-01 | 2.00 | 10.00 |

| Lookup date | Rule | input / output $/M |
|---|---|---|
| 2026-07-15 | backward | 3.00 / 15.00 |
| 2026-10-20 | forward | 3.00 / 15.00 |
| 2027-01-10 | forward | 2.00 / 10.00 |

**Retirement (§3.5)** is what actually gives `retired_on` its value once a source
stops publishing a model: when `update` sees a `(channel, model_id)` that was priced
in the previous version but that no enabled source publishes any more, it writes a
retirement row (last known rates, `source = "retired"`, `retired_on = built_on - 1
day`) instead of just dropping the key. From then on, lookups on that key price
`None` past `retired_on`; dates up to and including `retired_on` still get the last
known price. If the model comes back later (a source starts publishing it again), the
key simply gets a normal row again and is live from that build's `start_date` on.

## 7. CLI

```bash
./utils/model_pricing.sh <update|match|show|versions> [options]
# -> python -m common.model_pricing.cli_<command>
```

| Command | Key options | Exit codes |
|---|---|---|
| `update` | `[--dry-run] [--json] [--litellm-commit SHA] [--config-root DIR] [--catalog-root DIR]` | `0` new version or no-op; `1` a source (or its config) failed |
| `match` | `--org ORG --models a,b,… [--catalog VERSION] [--format md\|json] [--date YYYY-MM-DD]` | `0` every model matched; `2` any did not (unmatched/ambiguous/unpriced on the org's channel); `1` catalog/config failed to load |
| `show` | `--model M [--org ORG] [--catalog VERSION]` | `0` model has at least one priced row; `2` unmatched/ambiguous/unpriced everywhere; `1` catalog/config failed to load |
| `versions` | `[--limit N] [--catalog-root DIR]` | always `0` |

All results print to stdout; structured JSON logs go to stderr
(`common.msging.log`, component `model_pricing`).

### Examples

```bash
$ ./utils/model_pricing.sh update
# builds a new version from every enabled source, or reports "no changes"; prints the
# short Markdown refresh summary (§9) -- add --json for the full refresh detail instead

$ ./utils/model_pricing.sh update --dry-run
# example shape (illustrative counts, not the literal fixture-built numbers below):
#
# Refresh report — 2026-09-25-a950f989
#
# - built_on: 2026-09-25
# - run time (UTC): 2026-09-25T09:00:03Z
# - previous version: first version
# - result: new version
#
# ## Summary
#
# ### Sources
# | source | status | provenance | rows won | unmapped |
# |---|---|---|---|---|
# | `aws_price_list` | ok | publication_date: 2026-09-22T16:44:18Z, regions: ['us-east-1'], version: 1 | 6 | 1 |
# | `azure_retail` | skipped | reason: no channels configured | 0 | 0 |
# | `gcp_billing` | skipped | reason: GCP_BILLING_API_KEY not set | 0 | 0 |
# | `litellm` | ok | commit_date: 2026-09-24T18:08:09Z, commit_sha: d3462c65 | 10 | 1 |
#
# ### Channels
# | channel | rows | first source | LiteLLM fallback |
# |---|---|---|---|
# | `anthropic` | 3 | 3 | 0 |
# | `aws_bedrock:us-east-1:global` | 4 | 2 | 2 |
# | `aws_bedrock:us-east-1:regional` | 4 | 4 | 0 |
# | `openai` | 2 | 2 | 0 |
#
# - first version (no previous catalog to diff against)
# - unmapped, by source and reason:
#   - `aws_price_list`: unknown vendor prefix (1)
#   - `litellm`: model ambiguous (1)
# - source diffs (winner != litellm): 1
#
# ## Errors
# - None

$ ./utils/model_pricing.sh match --org talium.ai \
    --models claude-sonnet-5,us.anthropic.claude-sonnet-5,gpt-5.6-sol
| usage_model_id | model_id | vendor | channel | source | source_key | input | output | cache_read | cache_write_5m | cache_write_1h | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5 | claude-sonnet-5 (normalized) | anthropic | aws_bedrock:us-east-1:regional | aws_price_list | Claude Sonnet 5 (Amazon Bedrock Edition) | 2.2 | 11 | 0.22 | 2.75 | 4.4 | - |
| us.anthropic.claude-sonnet-5 | claude-sonnet-5 (normalized) | anthropic | aws_bedrock:us-east-1:regional | aws_price_list | Claude Sonnet 5 (Amazon Bedrock Edition) | 2.2 | 11 | 0.22 | 2.75 | 4.4 | - |
| gpt-5.6-sol | gpt-5-6-sol (normalized) | openai | openai | litellm | gpt-5.6-sol | 4 | 20 | 0.4 | 5 | - | - |
$ echo $?
0

$ ./utils/model_pricing.sh match --org talium.ai \
    --models claude-sonnet-5,totally-unknown,gpt-4o-2025-06-01
# unmatched / ambiguous rows sort first; exit 2

$ ./utils/model_pricing.sh show --model claude-sonnet-5 --org talium.ai
# claude-sonnet-5 (anthropic) -- matched via normalized -- catalog 2026-09-25-a950f989

| channel | source | source_key | input | output | cache_read | cache_write_5m | cache_write_1h | start_date | retired_on | org |
|---|---|---|---|---|---|---|---|---|---|---|
| `anthropic` | litellm | claude-sonnet-5 | 2 | 10 | 0.2 | 2.5 | 4 | 2026-09-25 | - |  |
| `aws_bedrock:us-east-1:global` | aws_price_list | Claude Sonnet 5 (Amazon Bedrock Edition) | 2 | 10 | 0.2 | 2.5 | 4 | 2026-09-25 | - |  |
| `aws_bedrock:us-east-1:regional` | aws_price_list | Claude Sonnet 5 (Amazon Bedrock Edition) | 2.2 | 11 | 0.22 | 2.75 | 4.4 | 2026-09-25 | - | yes |

$ ./utils/model_pricing.sh versions
2026-09-25-a950f989  built_on=2026-09-25  rows=16  excluded=0  unmapped=2  sources: aws_price_list=pub 2026-09-22T16:44:18Z, azure_retail=skipped (no channels configured), gcp_billing=skipped (GCP_BILLING_API_KEY not set), litellm=commit d3462c65
```

(Sample output above is from a fixture-built catalog — see
`tests/common/model_pricing/test_cli.py` for the reproducible build.)

## 8. Python API

```python
from datetime import date
from common.model_pricing import load_catalog, TOKEN_TYPES

catalog = load_catalog(version=None)          # None = current version
prices = catalog.for_org("talium.ai")          # applies channel config (§4.3)

match = prices.resolve("us.anthropic.claude-sonnet-5")
# MatchResult(model_id='claude-sonnet-5', vendor='anthropic',
#             channel='aws_bedrock:us-east-1:regional', status='matched', ...)

prices.rate(match, "output", date(2026, 10, 1))
# Decimal('0.000011') or None (unmatched, retired, or that rate absent)

prices.rates(match, date(2026, 10, 1))         # dict, every token_type at once

results = prices.mapping_report(["claude-sonnet-5", "claude-sonnet"])
from common.model_pricing import render_mapping
render_mapping(results, fmt="md")               # or fmt="json"
```

| Call | Contract |
|---|---|
| `load_catalog(version, root, config_root)` | Reads one version + every earlier one; verifies `hash8`. |
| `for_org(org_id)` | Unknown org → vendor channels. Raises `ConfigError` if the org's configured channel isn't priced. |
| `resolve(x)` | Never raises. `status` = `matched` \| `ambiguous` \| `unmatched`. |
| `rate(match, token_type, date)` | `None` if not matched, retired as of `date`, or the rate is absent. |
| `mapping_report(ids)` | `resolve` for each id, failures first. |

**Version binding**: a loaded `Catalog` (and every `OrgPrices`/`MatchResult` derived
from it) sees its bound version and every earlier one, never later ones. To reproduce
a past calculation, pass the `version` string it recorded.

### 8.0 `Pricer` — per-request pricing for services

Services price requests through `common.model_pricing.Pricer` (`Pricer.load(org_id, version)`;
`price_tokens`, `rates`, `models`, `resolve`, `effective_from`, `fingerprint`), which adds curated
dated rows from `config/model_pricing/history.yaml` beneath the catalog. See design §9.2.

### 8.1 Rate sheet — a date-range extract (`rate-sheet/1.0`, design §9.1)

One call for a caller that wants every model's price over a date range at once (a cost
run, a price report) instead of resolving and looking up rate-by-rate itself:

```python
from datetime import date
from common.model_pricing import rate_sheet

sheet = rate_sheet("talium.ai", ["claude-sonnet-5", "claude-sonnet"],
                    date(2026, 10, 1), date(2026, 10, 31))
sheet.to_json()   # exact §9.1 shape: by_alias + exclude_none baked in
```

| Field | Rule |
|---|---|
| `status` | `matched` \| `ambiguous` \| `unmatched`. Non-`matched` → `reason`, `candidates` (`[]` for `unmatched`), no `model_id`/`vendor`/`channel`/`match_step`/`segments` keys. |
| `segments` | Cover `start…end` inclusive, no gaps; consecutive dates with an identical rate dict merge — a price change is two segments, a retirement is a segment with every rate `null`. |
| `rates` | Exact decimal strings, all five `TOKEN_TYPES` keys always present; `null` = no price. |

Built entirely on `load_catalog`/`for_org`/`resolve`/`rates` above — no separate matching
or date logic. `catalog=None` (default) calls `load_catalog()` for the current version;
pass a `Catalog` to pin a version, same as any other version-bound call. `start > end`
raises `ValueError`.

## 9. Outputs

| File | Content |
|---|---|
| `config/model_pricing/catalog/<version>.json` | One immutable version: rates, registry, provenance, excluded/unmapped records (§3.3). Committed to git. |
| `config/model_pricing/catalog/<version>.refresh.md` | Refresh **summary** (§7), for people: header (version, build date, run time, previous version, result) + a `## Summary` of tables and counts only (sources, channels, added/changed/retired counts, unmapped counts grouped by source+reason, source-diff count) + a `## Errors` section (excluded rows and warnings, one line each, or "None"). No per-record lists — `update` prints this by default. |
| `config/model_pricing/catalog/<version>.refresh.json` | Refresh **detail** (§7), for machines: everything the summary counts but doesn't name — full `added`/`changed`/`retired`/`source_diff`/`unmapped` lists, provenance, channel counts, excluded, warnings. `update --json` prints this instead of the summary. |
| `config/model_pricing/catalog/index.json` | Version list, in order; the last entry is current. |

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `match`/lookup: `status=unmatched`, "model id not recognized" | No alias/`norm`/`canon` hit for the observed string. | Check spelling; if the string has no readable model name (e.g. an ARN), add it to `aliases.yaml`. |
| `status=ambiguous`, "matches several dated variants that price differently" | The string's `canon` group has diverging rates (e.g. the `gpt-4o` family). | Use the exact observed model string, not the bare family name. |
| `status=unmatched`, "no price on channel X" | Model resolved, but no row on the org's configured channel — no cross-channel fallback (§2.4 step 4). | Add the channel to `model_pricing.yaml`'s `channels` and rerun `update`, or fix `channel_config.yaml`. |
| `rate()` returns `None` for a model you know is priced | Model retired (`retired_on`) and the lookup date is after it — either a source's own `deprecation_date`, or §3.5's own retirement row (no source publishes it any more). | Expected (§3.4/§3.5) — retired models price `None` from that date on; earlier dates still get the last known price. |
| `versions` shows `source=skipped (...)` | `aws_price_list`/`azure_retail`: no channel of that cloud configured. `gcp_billing` (`auto`): `GCP_BILLING_API_KEY` unset. | Expected if you don't price that cloud; otherwise check `model_pricing.yaml`/`.env`. |
| `update` exits 1, "update failed: ..." | An enabled source's fetch/parse failed. | Nothing was written; current version unchanged. Fix source access (network, region, credentials) and rerun. |
| AWS/Azure-only model (e.g. an older Claude version) shows `unmapped`, reason "unknown vendor prefix" | Its reduced model string (§5.3) doesn't start with a name `vendor_prefixes` recognizes (§5.2) — could be a genuinely non-vendor product (Cohere, ...) or a vendor naming this module doesn't know about yet. | If it's a real model from a known vendor, add its prefix to `vendor_prefixes` in `model_pricing.yaml` and rerun `update`. |
| Two AWS/Azure-only records for what looks like one model both show up as separate registry entries | Their reduced model strings genuinely differ (e.g. two different dated snapshots) and share a `canon` — §5.2 treats that the same way LiteLLM's own dated-variant families are treated: each gets its own `model_id`, the canon is recorded ambiguous. | Use the exact observed model string in `match`/usage data, not the bare family name (same as any other ambiguous family, §5.3). |

## 11. Known limitations

- **Standard tier only** — no fast/batch/flex/priority pricing; all rows are the
  standard-tier rate (§2.5, deferred to #70).
- **Base context only** — long-context tiers (e.g. >200K tokens) are not read.
- **Whole-row source precedence** — a row's rates all come from one winning source; a
  token type that source lacks stays `None` even if LiteLLM has it (§6.4).
- **Azure meter grammar** — only `ShortCo` + standard tier (`Std` or no tier token)
  meters, scoped `Gl`/`DZ`, are parsed; anything else falls back to LiteLLM.
- **GCP adapter is skip-only** — no key → skipped; a key with SKU mapping unimplemented
  raises `SourceError` if explicitly enabled (§12, tracked as `TODO(§12 GCP)` in
  `sources/gcp_billing.py`). `gcp_vertex` channels price from LiteLLM until then.
- **Official-source models are AWS/Azure only** (§5.2) — the promotion pass only reads
  candidates from `aws_price_list`/`azure_retail`; GCP Billing Catalog records are not
  considered (no adapter reads GCP yet, previous point).
- **Vendor inference is prefix-based, not authoritative** — a model whose reduced
  string happens to start with a recognized prefix (`claude`, `gpt`, `o<digit>`,
  `gemini`) but isn't actually that vendor's model would be mis-promoted; in practice
  this only matters for a source with adversarial or coincidental naming, which none of
  today's sources have.

## 12. Layout

```text
src/common/model_pricing/
├── config.py         # model_pricing.yaml, channel_config.yaml, aliases.yaml
├── normalize.py       # norm, strip_date, canon, normalize (§5)
├── sources/            # one adapter per source
├── build.py            # registry, merge, derive, validate, version (§6)
├── catalog.py          # read versions, OrgPrices, resolve, rate
├── rate_sheet.py       # rate_sheet, RateSheet (§8.1)
├── pricer.py           # Pricer, split_cache_write, price_to_micros, history loader (§8.0)
├── reports.py
├── errors.py
└── cli_{update,match,show,versions}.py

config/model_pricing/
├── model_pricing.yaml            # cloud channels to price, geo map, sources (§4.2)
├── channel_config.example.yaml   # copy to channel_config.yaml to use (§4.3)
├── aliases.example.yaml          # copy to aliases.yaml to use (§5.3)
├── history.yaml                  # curated dated rows before the catalog begins (may be empty)
└── catalog/                       # versions + index.json + refresh reports
```

See [`docs/modules/model-pricing.md`](../../../docs/modules/model-pricing.md) for the
full design, including adding a new channel (§2.6), the exact normalization rules
(§5.1), and open/deferred items (§12).
