# Pricing migration (#68): synthetic reprice report

| | |
|---|---|
| **Scope** | Synthetic half of plan T9: regenerate the synthetic org with the catalog and reload `econ_synth`; measure the table effect. The real-database reprice is deferred (last section). |
| **Branch** | `feat/issue-68-impl` (worktree `code-econ-68`) |
| **Catalog** | `2026-09-25-d3e5c412` (current; `pricing_version: null`) |
| **Run on** | 2026-10-08, local Postgres container `econ_pg`; only `econ_synth` (throwaway, user-approved) and the scratch `econ_68_synth` were touched |
| **Design** | [pricing migration design](../2026-10-07-pricing-migration-design.md) §4, §4.2 |

## 1. What was found in `econ_synth` before

`econ_synth` did not hold the 100-user demo org. It held a small leftover org:

| Measure | Before |
|---|---|
| `sessions` / `session_info` | 170 / 170 |
| sessions with a non-empty `otel` block | 0 |
| session dates | 2026-01-05 to 2026-01-18 |
| `users` | 9 |
| models in `session_info.models` | `claude-opus-5` (170 sessions), `claude-sonnet-5` (170) |
| `sum(cost_effective_usd_micros)` | 150,987,336 ($150.99) |
| `sum(info.channel_cost.*.usd_micros)` | 129,001,350 |

`cost_effective_usd_micros` is per session, so a per-model sum is not derivable from it. The per-model view comes from the pricing report (read-only), run before touching the database:

```text
python -m server.worker.cli_pricing_report --dsn postgresql+psycopg://postgres@localhost:5432/econ_synth
```

| source | model | month | items | before (micros) | after (micros) | ratio | cause |
|---|---|---|---|---|---|---|---|
| fold:channel_cost | (multi-model) | 2026-01 | 170 | 129,001,350 | 129,001,350 | 1.000 | - |
| fold:carry | (multi-model) | 2026-01 | 170 | 21,985,984 | 21,985,984 | 1.000 | - |
| fold:cache_busts | claude-sonnet-5 | 2026-01 | 4 | 830,775 | 882,272 | 1.062 | see 3.2 |

Dates in January 2026 sit before the Sonnet 5 price change (2026-09-01), so no decision-#2 effect applies to this data. This is why the leftover org could not demonstrate the Sonnet change; the regenerated org does (section 2).

## 2. Regeneration and load

Commands, run from the worktree root with the root venv (`venv/` symlinked to the main checkout's venv):

```text
# 1. users -> roster -> series -> org-expand; end date pinned so the series ends 2026-09-30
econ-forecast/utils/synth_org.sh --config-dir econ-forecast/config/synthetic/org \
    --output <scratch>/org-demo --end-date 2026-09-30            # 1m41s

# 2. load (guard: database name must end in _synth)
utils/load_synthetic.sh --dataset <scratch>/org-demo/org \
    --dsn postgresql+psycopg://postgres@localhost:5432/econ_synth --dry-run
utils/load_synthetic.sh --dataset <scratch>/org-demo/org \
    --dsn postgresql+psycopg://postgres@localhost:5432/econ_synth --replace   # 55s, rollup_ok true
```

`econ_synth` was not refolded. The generated dataset is outside the repo and is not committed. `deploy/seed/econ_synth.sql.gz` is gitignored, and `utils/synth_dump.sh` was not run, so no seed dump exists to commit or ship.

The dataset is self-contained: `org/pricing/` holds `catalog/index.json` (trimmed), `catalog/2026-09-25-d3e5c412.json` and `history.yaml`, and the manifest records the resolved version (T5).

| Measure | After |
|---|---|
| `sessions` / `session_info` | 15,289 / 15,289 (9,369 root, 5,920 child per the generator summary) |
| `users` | 36 (35 rostered + admin) |
| session dates | 2026-08-02 to 2026-09-30 |
| models | `claude-haiku-4-5-20251001`, `claude-opus-5`, `claude-sonnet-5`, 15,289 sessions each |
| `sum(cost_effective_usd_micros)` | 18,392,747,051 ($18,392.75) |
| `sum(info.channel_cost.*.usd_micros)` | 13,315,656,967 |
| sessions with a non-empty `otel` block | 0 |

The before and after rows describe two different orgs, so their totals are not comparable. The comparable quantity is the effect of the price table on the same tokens, below.

### 2.1 Effect of the migration on the regenerated org's tokens (decisions)

The org's daily usage tokens (`series/usage.jsonl`, all five token types) priced at the retired table (Sonnet 5 at $3/$15, cache rates scaled the same way, from 2026-09-01; every other model and date identical to the catalog) against the catalog:

| Model | Month | Retired table (USD) | Catalog (USD) | Ratio | Decision |
|---|---|---:|---:|---:|---|
| claude-haiku-4-5-20251001 | 2026-08 | 606.10 | 606.10 | 1.000 | - |
| claude-haiku-4-5-20251001 | 2026-09 | 615.81 | 615.81 | 1.000 | - |
| claude-opus-5 | 2026-08 | 6,027.55 | 6,027.55 | 1.000 | - |
| claude-opus-5 | 2026-09 | 6,184.72 | 6,184.72 | 1.000 | - |
| claude-sonnet-5 | 2026-08 | 2,413.62 | 2,413.62 | 1.000 | - |
| claude-sonnet-5 | 2026-09 | 3,728.07 | 2,485.38 | **0.667** | #2: LiteLLM's $2/$10 stands, the retired $3/$15 block is not carried over |

This is arithmetic on the usage tokens, not a database reprice. The retired file no longer exists, so the Sonnet 5 block is reconstructed from the design (§4 #2).

## 3. Table effect

Definition (plan Context): the cost ratio between the dashboard's and the forecast's pricing of the same tokens that is caused by different price tables alone. Measured on the regenerated org, 28 days to 2026-09-30 (2026-09-03 to 2026-09-30), by pricing each model's per-request tokens through the generator's `OrgPricer` and through the cost kernel's rates from a `rate_sheet` of the same catalog:

```text
python -c '... rate_sheet("", [3 models], 2026-09-03, 2026-09-30).to_json()' > rate_sheet.json
econ-forecast/utils/cache_write_acceptance.sh --org-run <scratch>/org-demo \
    --rate-sheet rate_sheet.json --origin 2026-09-30 --days 28 --json
```

| Model | Request-level USD (`OrgPricer`) | Cost kernel USD | Table effect (A1) |
|---|---:|---:|---:|
| claude-haiku-4-5-20251001 | 565.929426 | 565.929451 | 1.0000 |
| claude-opus-5 | 5,703.154762 | 5,703.154737 | 1.0000 |
| claude-sonnet-5 | 2,295.111777 | 2,295.111805 | 1.0000 |

Before the migration: 1.13x overall and 1.50x on Sonnet 5 over this window (plan Context). After: 1.00 per model; the differences are at the 8th decimal place (rounding to micros).

The same harness also reports A2, dashboard (combined cache write at the 1h rate) over request level (per-lifetime 5m/1h split): 1.0032 for Haiku, 1.0033 for Opus, 1.0033 for Sonnet, 1.0033 overall. That is the cache-write-contract residual (#67, 5m writes priced at the 1h rate), not a table effect.

### 3.1 The pricing report on the regenerated org

`python -m server.worker.cli_pricing_report` against the reloaded `econ_synth` (read-only):

| source | model | month | items | before (micros) | after (micros) | ratio |
|---|---|---|---:|---:|---:|---:|
| fold:channel_cost | (multi-model) | 2026-08 | 7,602 | 6,562,229,375 | 6,562,229,375 | 1.000 |
| fold:channel_cost | (multi-model) | 2026-09 | 7,687 | 6,753,427,592 | 6,753,427,592 | 1.000 |
| fold:carry | (multi-model) | 2026-08 | 7,602 | 2,514,206,397 | 2,514,206,397 | 1.000 |
| fold:carry | (multi-model) | 2026-09 | 7,687 | 2,562,883,694 | 2,562,883,694 | 1.000 |
| fold:cache_busts | haiku / opus / sonnet | 2026-08, 2026-09 | 83 to 210 each | see 3.2 | see 3.2 | 1.074 to 1.200 |

`channel_cost` and `carry` agree to the micro because the generator now prices with the same `Pricer` the report reprices with, which is a consistency check, not an independent one. The report has no true-up ledger rows for this data: the OTel true-up skips synthetic sessions by design (`info_blob.synthetic`), so every synthetic `session_info.otel` block is empty and only the `fold:*` sources apply. For the same reason, 307 multi-model `resume_cost` totals were skipped (they carry no model).

### 3.2 Cache-bust ratios above 1.000 are not a price-table effect

`fold:cache_busts` items repriced by the report come out 6% to 20% higher than stored. The stored amounts are about $3.80 per million tokens for Sonnet 5, between its 5m ($2.50) and 1h ($4.00) rates: the generator prices a bust's rewritten tokens per cache lifetime (5m/1h split, `OrgPricer.request_micros`), while the report reprices `rewrite_tokens` as one combined `cache_write` at the 1h rate, like the real fold does. So the gap is the cache-write modelling difference (#67), present in the leftover January org too (1.062). The report's first version labelled these `#7` / `#2` / `unexplained` by a date-and-model heuristic, which was wrong for this case; the numbers in this section were produced by that version. The report now assigns a cause only when its mechanism accounts for the move (`#2` only for Sonnet 5 items from 2026-09-01 whose ratio is about 2/3; `#7` only for items unpriced before on a date preceding the catalog's first row; `#8` only for newly matched or ambiguous model strings), so every one of these `cache_busts` rows is now reported `unexplained`, with a footer and module docstring stating that busts are repriced combined at 1h while synthetic data prices them per lifetime (#67). The `cause` values for `fold:cache_busts` in the tables above should be read that way.

## 4. Accepted limit of the report

The fold persists money as session totals: `channel_cost`, `carry` and `resume_cost` have no per-model split, and `resume_cost` events carry no model. The report therefore attributes them to a model only for single-model sessions. Multi-model sessions appear under `(multi-model)` (all synthetic sessions here), and their `resume_cost` is skipped and counted in the log (`multi_model_resume_cost_skipped`). `cache_busts` events and true-up ledger requests carry their own model and are exact per model. Decision (coordinator, 2026-10-07): accept this limit; the fold is not changed.

## 5. Real database reprice: deferred (user decision 2026-10-08)

Not done and not to be done without the user's go-ahead: no real database (`econ`, a prod or `cc-econ-demo` database, or a copy) was touched. Runbook for when it is approved. Always on a scratch copy first (the name must end in `_synth` for the DB-backed tests, and a copy is made so the original stays intact):

```text
# 0. scratch copy
docker exec econ_pg createdb -U postgres -T <db> <db>_68_synth

# 1. before: stored computed costs vs Pricer (read-only)
utils/pricing_report.sh --dsn postgresql+psycopg://postgres@localhost:5432/<db>_68_synth > before.md

# 2. refold the fold-owned costs (never against a *_synth database in production use;
#    here the scratch copy is the only target)
python -m server.worker.cli_refold --config config/worker.yaml      # needs TALIUM_DB_DSN pointing at the copy

# 3. one true-up tick reprices ledger rows whose pricing_fingerprint differs
python -m server.worker.cli_work --config config/worker.yaml         # run until the first otel_trueup tick completes

# 4. after
utils/pricing_report.sh --dsn postgresql+psycopg://postgres@localhost:5432/<db>_68_synth > after.md
```

Expected: Sonnet 5 from 2026-09-01 drops to ratio 0.667 (decision #2), Opus 5 and Haiku 4.5 stay 1.000, rows dated before the catalog's first row price at the first row (#7), and requests whose model string was unpriced before and now matches appear in the "unpriced before" column (#8). Every ratio other than 1.000 should be explained by those decisions; the multi-model limit (section 4) and the cache-bust modelling difference (section 3.2) are known exceptions. Production (`cc-econ-demo`) needs a separate go-ahead; after it, run `cli_refold` once per catalog version that changes a served price.
