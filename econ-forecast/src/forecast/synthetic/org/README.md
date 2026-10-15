# Synthetic org data (Layer 2)

Deterministic, offline synthetic data for a whole engineering org, for exercising the
dashboard/manager API without a real GitHub-connected deployment. Built on top of Layer 1
(`forecast.synthetic`), which already generates a realistic daily token-usage series per user
(`usage.jsonl`) via `series-create`. This package expands that series into everything else a
real org produces: sessions, GitHub artifacts (PRs, issues, commits, reviews), the identity
directory, and session `info` content -- all attributable back to Layer 1's own totals exactly
(every dollar/token this package emits sums to Layer 1's numbers; see Invariants below).

Everything here is Claude Code only (no other coding agent), and every session this package
produces reports `datasource=jsonl_only` -- there is no OTel path in synthetic data.

## Token types

Layer 1's five token types (`input` uncached, `cache_write_5m`, `cache_write_1h`, `cache_read`,
`output`) are conserved exactly down to individual requests (I1 per type, checked in process over
the request ledgers). A request keeps its two writes separately and `Request.cache_write` is their
sum; `session_info.models` carries that sum as one `cache_write`, as real sessions do. The cache
lifetime `sessions.ttl_s` defaults to 3600 s, which makes resume requests rare; resume policy is
the owner's call. `expected_rollup.jsonl` carries the five fields and the dataset manifest is
`schema_version` 2 (see the [cache-write contract design](../../../../docs/design/cache-write-contract-design.md)).

## Pipeline

```
users-create -> roster-create -> series-create        (Layer 1, unmodified except identity_style)
                                        |
                                        v
                                 org-expand              (this package: org/expand.py)
                                        |
              +---------+---------+----+----+---------+----------+
              v         v         v    v     v         v          v
         directory   repos    sessions work   git    requests   content
        (users/teams) (repos) (session (items (PRs/   (per-      (session_info
                       /installs) plan) /features) issues/ session   JSON blobs)
                                              commits) ledger)
                                        |
                                        v
                              org-expand output dir
                        (17 JSONL files + manifest.json + report.md)
                                        |
                                        v
                       server.storage.synthetic_load.load_dataset
                        (mainline: loads into a *_synth Postgres db)
                                        |
                                        v
                          dashboard API + web UI, read normally
```

`cli_synth_org.py` (`utils/synth_org.sh`) chains all four Layer 1/Layer 2 steps in one
invocation, retiming the series window to end on `--end-date` (defaults to `today`) so a demo
dataset always looks current.

## Quick start

One command rebuilds the whole demo (generate, forecast, optionally load and dump):

```bash
utils/synth_demo_refresh.sh --end-date today                       # generate + forecast
utils/synth_demo_refresh.sh --dsn postgresql+psycopg://postgres@localhost:5433/econ_synth   # + load
utils/synth_demo_refresh.sh --dump                                  # + deploy/seed/econ_synth.sql.gz
utils/synth_demo_refresh.sh --dry-run --dsn ... --dump              # print the stages and commands only
```

It stops at the first failing stage, prints human progress on stderr and a single JSON line on
stdout, and refuses an existing `--output` unless you pass `--force`. With `--force` the old
output is moved aside while the generator runs and deleted only once it has succeeded (a failed
or interrupted generate puts the old demo back); only what a refresh creates is ever deleted:
`configs pricing org roster series users forecast`. `--dsn` has no default, its database name
must end in `_synth`, and **the database must already exist**: the loader does not create it,
and a missing one fails only after the generate and forecast stages, about 6 minutes in. See
the header of [`utils/synth_demo_refresh.sh`](../../../../../utils/synth_demo_refresh.sh) for every
option.

The same thing by hand:

```bash
# 1. Generate a dataset (users -> roster -> series -> org-expand, chained; about 5 min for the
#    example config's 100 users / 120 days, measured on a laptop)
econ-forecast/utils/synth_org.sh \
  --config-dir econ-forecast/config/synthetic/org \
  --output econ-forecast/artifacts/org-demo \
  --end-date today

# 2. Forecast it: one forecast and one cost run per scenario preset, and the export the
#    dashboard loads (about 40 s on the example org). Writes econ-forecast/artifacts/org-demo/forecast/.
econ-forecast/utils/dashboard_forecast.sh --org-demo econ-forecast/artifacts/org-demo

# 3. Create a database whose name ends in `_synth` (D13 -- the loader refuses anything else,
#    and does not create it).
#    On the local stack (Postgres 16, container talium_stack_pg, localhost:5433):
docker exec talium_stack_pg psql -U postgres -c "CREATE DATABASE econ_synth;"

# 4. Load it (--replace truncates every table this loader owns first). Leave out
#    --forecast-export and the dashboard shows the old flat-rate chart instead of the forecast.
utils/load_synthetic.sh \
  --dataset econ-forecast/artifacts/org-demo/org \
  --dsn postgresql+psycopg://postgres@localhost:5433/econ_synth \
  --replace \
  --forecast-export econ-forecast/artifacts/org-demo/forecast/forecast_export.json

# 5. Serve the API against it, on its own port so it can run alongside a real-data API
TALIUM_DB_DSN=postgresql+psycopg://postgres@localhost:5433/econ_synth \
  python -m server.api.cli_serve_api --port 8091

# 6. Run the web UI against that API (the load's own output prints the admin viewer id to use)
VITE_DATA_SOURCE=api VITE_API_TARGET=http://localhost:8091 \
  VITE_VIEWER_ID=github:<admin login> VITE_VIEWER_ROLE=admin \
  npm run dev
```

**The forecast and the dataset must match.** The export is built from `<output>/series` and
`<output>/org` of the same run. The loader refuses an export whose org, team or repository ids
are not in the dataset (exit 2, "forecast export does not belong to this dataset"). Generation
is deterministic, so an export from an equivalent dataset can still load; always forecast the
dataset you are about to load. The refresh script does both from one `--output`.

**The forecast is as of `data_through`**, the dataset's last day, which is the `--end-date`.
The run it fits ends at the previous month end and ignores the current month's actuals (plan
D1), so a demo generated a month ago shows an old "current period": the headline, the run-out date and the budget probabilities all describe that month.
Refresh it daily, or at least before showing it. When `--end-date` falls on a month end the
forecast has no current period (both periods are future months), which is correct but looks
different.

## Configuration reference

See the implementation plan, [`docs/plan/2026-09-18-synthetic-org-implementation.md`](../../../../../../docs/plan/2026-09-18-synthetic-org-implementation.md)
§3, for the full `org.yaml` schema (top level, `repos[]`, `defaults` blocks, per-team
overrides). A worked example set lives in `econ-forecast/config/synthetic/org/` (`users.yaml`,
`roster.yaml`, `series.yaml`, `org.yaml`).

`users.yaml` and `series.yaml` are Layer 1 configs and follow Layer 1's current schema
(`users.locale` required; `series.yaml` `schema_version: 3`). Two v1 behaviours have no v2
equivalent and are approximated in the example: weekend inactivity is a weekend reduction
(`weekend_reduction_probability` / `weekend_reduction_factor`, same average level), and a v1
per-user random spike is replaced by two things, the `power_profile`'s own personal spikes
(`spike_probability` etc. on a profile) and one org-wide `busy_days` spike rule (a v2 rule fires
for every targeted user on a date). `cli_synth_org.py` retimes `spike_rules[].date_range` and
`zero_periods` with the series window; the example's rule has no date range, so there is
nothing to retime.

### The example org

| | Development | Platform | QA | Org |
|---|---|---|---|---|
| People (budget at $200 each) | 20 ($4,000) | 10 ($2,000) | 5 ($1,000) | 35 ($7,000) |
| Profile counts | 2 power, 1 heavy, 3 mid, 13 light, 1 idle | 1 heavy, 2 mid, 5 light, 2 idle | 1 heavy, 2 mid, 1 light, 1 idle | 4 idle users |
| Baseline landing of the current month, 25 end dates swept 2026-09-28 to 2026-11-30 | 113-140% of budget | 53-61% (p90 at most 64%), the team with margin | 83-114% | 92-112% |
| Usage curve (`curves/`) | 1.8x for the first week, a 1.3x bump, a 1.5x release crunch about days 62-76 | calm: within +/-6% of 1.0 everywhere | 1.7x for the first week, a 1.15x bump, a 1.4x test cycle about days 79-86 | |

The two power users are fixed for the whole series, consume about 3x a heavy user and spike on
about three active weekdays in ten (1.5-2.5x). The `busy_days` rule adds an org-wide 1.3-1.6x
day on about one weekday in ten (a random draw per calendar day: 1-2 fell in the last 28 days
on the end dates measured). Development and QA have fewer daily absences than the Layer 1
default (4% and 3% against 10%), because two power users carry about half of Development's
tokens and QA has five people, so one absent person otherwise moves a whole day. `usage_seed` 41 was chosen out of 7 seeds as the one with the smallest swing of the
landings across the sweep; the draws are keyed by calendar date, so any other end date has its
own noise.

The example binds a usage curve to every team (`series.yaml` `curve.teams`, configs and built
artifacts under `config/synthetic/org/curves/`, axis 1..61, one anchor step about 2 days of the
120-day series). Without moving levels each day is an independent draw around a fixed level, and
a forecast fitted on the series has a monthly band of about +/-2% at org level, which makes a
budget probability read 0% or 100%. The forecaster fits the 90 days before its origin, so a
crunch inside that window moves the forecast and widens its band, and one before it is only
visible history. For an end date late in a month that window reaches back to day 0 of the series,
so **Platform has no crunch anywhere**: a 2x opening week there pushed its November landing to
65-69% and its p90 to 113-136% of budget. The Development and QA crunches sit in the last 60 days
of the series, which is what the dashboard's 60-day window shows; how large they look there
depends on the end date (the realised weekly weekday mean over a regular week was 1.3-1.7x for
Development and 1.2-1.5x for QA across the sweep, org 1.2-1.5x). The opening week (org weekdays
1.4-1.9x a regular week over the full series) is outside the window for an end date early in a
month and is not a feature of the 28/60-day views.

Band widths depend on the end date as well, because the forecaster re-selects its model on every
fit (plan F7): swept over the 25 end dates the org's October or next-month p10-p90 width was
3-119% of its expected landing (p90 at most 158% of budget), Development 4-181% (p90 at most
242%), QA 2-106% (p90 at most 146%) and Platform 2-13%. The widest values are mostly the next
month's horizon. After editing a curve
YAML rerun `utils/curve.sh` (the command is in the file) and update the `name:id` in
`series.yaml`; after changing a mix or a curve, re-sweep the end dates (series, then
`run_dashboard_forecast`), and do not trust one date. `cli_synth_org.py` rewrites
`curve.artifacts` to an absolute path; curves need no retiming because they are stretched over
the series. The structure is pinned by `tests/synthetic/org/test_example_demo_shape.py`, which
also regenerates a series for three end dates and checks the landing against the budgets.

## Invariants

Every `org-expand` run is checked against these, both by this package's own tests
(`econ-forecast/tests/synthetic/org/`) and, for I1, again by the mainline loader itself
(`synthetic_load.py`'s post-load `daily_rollup` check):

- **I1 rollup exactness** -- every session's tokens/cost sum, per user/day/repo/agent/model,
  to Layer 1's own `usage.jsonl` totals exactly (integer tokens, exact; cost within
  micro-rounding).
- **I2a determinism** -- the same config + seed reproduces byte-identical output, always.
- **I2b knob independence** -- changing one config knob (e.g. a habit rate) never changes
  anything not causally downstream of it (checked by re-running with one field changed and
  diffing everything else).
- **I3 referential integrity** -- every id a record references (`user_id`, `repo_id`,
  `parent_artifact_id`, `issue_artifact_id`, session parent/child links, ...) resolves to a
  real row inside the same dataset.
- **I4 temporal order** -- every session/event/artifact timestamp is internally consistent
  (children inside their parent's window, PR events in causal order, commits before their PR
  merges, ...).
- **I5 cost consistency** -- session costs, once expanded into per-channel figures, are the
  same ported channel-cost logic `graph_fold.py` uses on real data (parity-tested directly
  against the real functions in `test_parity_graph_fold.py`).
- **I6 calibration** -- the realized merged-PRs/dev/week should land within ±25% of
  `calibration.target_merged_prs_per_dev_week`. The shipped example config's org-wide ratio
  lands at about 0.82x target (1,469 merged PRs over 600 developer-weeks, 2026-10-09 dataset),
  inside the band; by team it is 3.36 (Development), 0.77 (Platform) and 2.17 (QA) merged PRs
  per developer-week. Platform alone is far below target: a structural session-granularity
  overshoot in FIFO item finalization makes big-PR teams finalize fewer, larger items -- see the
  progress log entry for P9 for the full root-cause and options; the plan's own pinned config
  values and algorithm were deliberately left unchanged rather than tuned to force compliance.
- **I7 Layer 1 untouched** -- this package only ever reads Layer 1's output; the full
  pre-existing `econ-forecast/tests` suite (Layer 1's own tests) stays green and unmodified.

## Worker warning

**Never point the mainline worker or `cli_refold` at a `*_synth` database.** This package's
output is loaded directly into its final table shape by `synthetic_load.py` -- it is not raw
ingest material, and the worker's fold/refold passes are not idempotent against data that did
not come through real ingest. The loader itself refuses to load anything into a database whose
name does not end in `_synth` (D13); that guard exists precisely so a synthetic load can never
land in `econ` or another real database, not so the worker can be safely pointed at a `*_synth`
one instead.

## Troubleshooting

- **`refusing to load into database ...: the name must end with '_synth'`** -- the D13 guard.
  Point `--dsn` at a database whose name literally ends with `_synth` (e.g. `econ_synth`, not
  `econ_synth_test` -- that ends with `_test`).
- **`manifest ... sha256 mismatch`** -- the dataset directory was edited or partially
  regenerated after `org-expand` wrote its manifest. Regenerate the whole dataset with
  `synth_org.sh` rather than hand-editing any `.jsonl` file.
  `directory_org_id` doesn't match `config/directory.yaml`'s own `default_org_id` -- pass
  `--directory-config` explicitly, or regenerate the dataset with the right `org.directory_org_id`.
- **`post-load rollup check failed`** -- the loaded database's `daily_rollup` no longer matches
  `expected_rollup.jsonl`. This should never happen against an unmodified dataset; if it does,
  it means either the dataset was partially loaded before (run with `--replace`) or there is a
  real bug -- do not ignore it.
- **A generated demo "looks stale"** -- the series window ends on the day it was generated,
  and the forecast is as of that day. Run `utils/synth_demo_refresh.sh --force --end-date today`
  (add `--dsn ...` to reload your database, `--dump` for the AWS seed) rather than reusing an old
  `artifacts/org-demo`; a loaded database needs the new load (`--replace`) too. `--replace`
  also empties `forecast_runs` and `forecast_scope_series`, so a load without a fresh
  `--forecast-export` leaves the dashboard on the flat-rate chart.
