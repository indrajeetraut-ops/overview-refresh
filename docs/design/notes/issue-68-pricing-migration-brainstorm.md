# Issue #68 pricing migration — brainstorm scratchpad

Context note: [2026-10-06-issue-68-pricing-migration-context.md](2026-10-06-issue-68-pricing-migration-context.md). Design: [../2026-10-07-pricing-migration-design.md](../2026-10-07-pricing-migration-design.md).

## Decisions

| # | Decision | Notes |
|---|---|---|
| D1 | LiteLLM is the price authority; Sonnet 5 = catalog ($2.00 / $10.00); the 2026-09-01 $3.00 block is dropped. | User asked to check vendor-reported costs first; local DBs (`econ_test`, `econ_synth`) hold no `otel_api_requests`, so the fit runs against prod (T0). Catalog (LiteLLM) and AWS Price List both say $2.00 on 2026-09-25; `pricing.yaml`'s 2026-09-01 block was entered ahead of time (table read 2026-08-05). 2026-10-07 |
| D2 | History = curated dated rows in `config/model_pricing/history.yaml`, read under the catalog. | User chose curated rows. Contents decided by the T0 fit: if Sonnet really billed $3.00 from 2026-09-01, the file carries that row; otherwise it ships empty-but-supported. 2026-10-07 |
| D3 | Fingerprint = catalog `version` (+ history hash when the file has rows). Column name `pricing_fingerprint` unchanged. | Any version bump invalidates all computed costs; repricing is idempotent. 2026-10-07 |
| D4 | `source: measured` / `ratio` / `fit:` dropped. Measured agreement lives on as the true-up's `pricing_drift` check; `utils/fit_prices.py` becomes a verifier (fit vs catalog). | Table has no `ratio` entries today; only two `published+measured`. |
| D5 | Channel: `pricing_org` setting in each service config; unset → vendor channels (`for_org` of an unknown org). | No org id exists in worker/api config today. |
| D6 | One adapter, `common.model_pricing.Pricer`, replaces `PricingConfig`; consumers keep their call shapes (`price_tokens`, `fingerprint`, `rates`). | Fewest consumer edits. |
| D7 | Date before a model's first row now prices at the first row (new module rule); legacy returned `None`. | Recorded change; history file covers real dated changes. |

## Open questions

| # | Question | Status |
|---|---|---|
| ~~Q1~~ | ~~Fit Sonnet 5 on prod~~ | Withdrawn: LiteLLM accepted as authority (2026-10-07) |
| ~~Q2~~ | Orphan `2026-09-25-9dbc1746*` | Deleted (2026-10-07) |
| ~~Q3~~ | JSONL fold fingerprint? | Only the true-up stores one; fold costs refresh via `cli_refold` (2026-10-07) |

## External-module items

| Module | Change |
|---|---|
| `common/model_pricing` | add `Pricer`, `history.yaml` loader |
| ingest, api, worker configs | `pricing_config` → `pricing_org`, `pricing_version` |
| econ-forecast synthetic org | `Pricer` over a pinned version; `pricing_file` removed from `org.yaml`; seed dump regenerated |
