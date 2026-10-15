# Synthetic daily usage generator

For a complete first run through generation, forecasting, and plotting, see the [operator guide](../../../docs/operations/README-forecast.md).

This module creates reusable fake users, a fixed mutually exclusive Team roster, and dated daily usage. Each operation is independent: series runs reuse saved identities and membership, so another duration does not alter them. Synthetic baselines are configurable assumptions and do not establish forecasting accuracy.

Use Python 3.11+ and the repository environment. From the repository root, create `venv` if needed, activate it, and install root requirements:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

No separate module packaging step is needed. Shell wrappers set the import path automatically. For direct Python commands, first run:

```bash
export PYTHONPATH="$PWD/econ-forecast/src:$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
```
 `Faker==40.38.0` is pinned because seeded identity output is version-dependent.

```bash
./econ-forecast/utils/synt_gen.sh --op user-create --config econ-forecast/config/synthetic/users.yaml --output econ-forecast/artifacts/users
./econ-forecast/utils/synt_gen.sh --op roster-create --config econ-forecast/config/synthetic/roster.yaml --output econ-forecast/artifacts/roster
./econ-forecast/utils/synt_gen.sh --op series-create --config econ-forecast/config/synthetic/series.yaml --output econ-forecast/artifacts/series-60d
./econ-forecast/utils/synt_gen.sh --op series-create --config econ-forecast/artifacts/series-60d/config.yaml --output econ-forecast/artifacts/series-60d-replay
# Re-run into an existing directory: add --force (deletes and recreates only that --output directory).
./econ-forecast/utils/synt_gen.sh --op series-create --config econ-forecast/config/synthetic/series.yaml --output econ-forecast/artifacts/series-60d --force
python -m forecast.synthetic.cli_synt_gen --help
./econ-forecast/utils/synt_gen.sh --help
```

User and roster YAML files use `schema_version: 1`; series YAML uses `schema_version: 2`. User settings are `count` (positive integer), `org_domain`, `locale`, and a nonnegative `seed`; each must be present. The maintained example sets `locale: en_US`. Roster settings reference `users_file`, use their own `seed`, and list positive Team counts. Each Team is defined by a unique `team_name`; stored IDs are generated internally.

Series settings reference saved users and roster and define `start_date`, calendar `days`, `timezone`, `profile_seed`, and `usage_seed`. Profiles define active-weekday processed input tokens, and may opt in to personal spikes (below). Every Team defines exact profile proportions and Project selection probabilities. Projects have unique names; `project_mix` keys are those names. **One team per project:** the forecasting model treats Org → Team → Project as a strict tree ([design §1](../../../docs/design/forecasting-model-design.md#1-structural-model-authoritative)), so every maintained config and example lists each Project in exactly one Team's `project_mix`. The generator does not enforce this; listing a Project under several Teams still works but produces a multi-team Project the forecaster's scope model does not expect. Agent shares and per-agent model shares allocate one user daily total and must each sum to one. API billing is the only generator mode; subscription generation and equivalent-cost handling remain future work.

The maintained YAML sets:

```yaml
defaults:
  daily_variation_pct: 30        # required
  output_ratio: 0.03             # required
  cache_hit_ratio: 0.95          # required
  cache_hit_variation_pp: 5      # required
  mix_variation_pct: 10          # required
  daily_zero_probability: 0.10   # required
  # weekend_reduction_probability: 0.0   # optional, default 0 = no weekend drop
  # weekend_reduction_factor: 0.25       # optional, used only when probability > 0
```

The six fields shown uncommented are required. **Weekend reduction is off by default**: omit it (as the maintained config does) for no weekly pattern, or set `weekend_reduction_probability > 0` to opt in — see [`examples/synthetic/weekend-drop.yaml`](../../../examples/synthetic/weekend-drop.yaml). Ratios are fractions; variation fields are percent or percentage points as named. At bounded cache extremes the sampled interval is truncated, which changes its realized mean.

Profile assignment uses `profile_seed` and stays fixed when duration or `usage_seed` changes. `usage_seed` independently drives activity, Project, volume, spikes, allocation jitter, and cache ratio. Extending a series with the same start preserves earlier record bytes.

Stored `input` is uncached input only. Cache writes are separate (`cache_write_5m`, `cache_write_1h`), as are `cache_read` and `output` (additional). `cache_write_ratio` (default 0.997) is the share of non-cache-read input that is a cache write, and `cache_write_1h_share` (default 0.97) is the share of writes at the 1-hour lifetime; both apply to every row. With 1,000,000 processed input at an illustrative 80% cache hit ratio (the shipped config uses 0.95) and the write defaults, a record totals 600 `input`, 5,982 `cache_write_5m`, 193,418 `cache_write_1h`, 800,000 `cache_read`, and 30,000 `output`. The series manifest is `schema_version` 2 and the config is `schema_version` 3; a version-1 dataset is rejected with a regenerate message.

Coverage is emitted for every rostered user/date/configured agent, including inactive days and zero-share agents. An absent usage row under complete coverage means zero activity. A forced-zero period prevents baseline usage. Ordinary daily inactivity is sampled on every day. When weekend reduction is enabled (probability > 0), each otherwise active user on a weekend date gets an independent draw that multiplies usage by the configured factor; it never forces zero usage. With the default probability 0 weekends are ordinary days. Spike rules are evaluated once per rule/date on weekdays after baseline allocation. They add load only to matching positive baseline rows, so zero remains zero.

Each output directory contains resolved `config.yaml`, `report.md`, and a completion `manifest.json`. Users add `users.jsonl`; rosters add `teams.jsonl` and user snapshots; series add `usage.jsonl`, `coverage.jsonl`, `user_profiles.jsonl`, `generation_events.jsonl`, and user/roster snapshots. The manifest records hashes, row counts, versions, provenance, and totals. Saved series configuration points to snapshots and supports replay after moving the directory. An existing/non-empty output is refused unless `--force` is given, which deletes and recreates only the resolved `--output` directory.

Every report includes stage settings, artifact counts, and warnings. Series reports include profile, activity, event, coverage, and token breakdowns by Team, Project, and agent/model. CLI success writes exactly one JSON summary to stdout; structured logs go to stderr. Exit 0 means success/valid/help, 2 means arguments/config/input error, and 1 means artifact or unexpected failure. `--validate-only` validates references and writes nothing. Common failures include bad share sums, unknown Team/Profile/Project/event targets, duplicate names, unsupported billing modes, corrupt input hashes, and an existing output path without `--force`.

Run tests with `venv/bin/python -m pytest econ-forecast/tests/synthetic -q`, collect all tests with `venv/bin/python -m pytest --collect-only -q`, and syntax-check both scripts with `bash -n`.

## Configuration examples and units

The maintained examples are in [config/synthetic](../../../config/synthetic). Input paths in YAML are relative to that YAML file; CLI output paths are relative to your shell directory. Team and Project names are validated before output creation. Shares must sum to one (absolute tolerance 1e-9), and booleans or numeric strings cannot stand in for numeric controls.

To keep Teams fixed and generate another history, reuse the saved users and roster. Change `series.days` to 30 or 60 and choose a new output path. Keep the start date and seeds unchanged to preserve the shared date prefix. Change `profile_seed` to reassign users to profiles; change only `usage_seed` to vary daily consumption while retaining profiles. Event ranges must remain within the new series dates.

A Team `activity` block **replaces** the activity values from `defaults` for that Team (no field-by-field inheritance). `daily_zero_probability` is required; the weekend fields are optional and default to 0 / 0.25 — so a Team block that omits them has no weekend drop even if `defaults` enables one. A Team without `activity` uses `defaults`:

```yaml
team_profiles:
  team1:
    profile_mix: {light_profile: 0.80, heavy_profile: 0.20}
    project_mix: {payment_service_improvement: 0.70, auth_service_rewrite: 0.30}
    activity:
      daily_zero_probability: 0.10
      weekend_reduction_probability: 0.80   # optional opt-in for this Team only
      weekend_reduction_factor: 0.25
```

A profile may add personal spikes: `spike_probability` (0..1), `spike_multiplier_min` and `spike_multiplier_max` (finite, `1 <= min <= max`), all three or none. On each active weekday (never a weekend, forced-zero or inactive day) a user on that profile spikes with that probability, and the user's whole day is multiplied by a uniform draw in `[min, max]` before the agent/model split. Draws depend only on `usage_seed`, the user and the date, so other config changes do not move them. Rules in `events.spike_rules` then act on the scaled baseline. Each spiked user-day is a `personal_spike` record in `generation_events.jsonl`, and the report gains a per-profile table. Omitting the fields leaves every artifact byte-identical to before the feature ([design §3.4.1](../../../docs/design/synthetic-data-generation-design.md#341-personal-spikes-per-profile)).

```yaml
profiles:
  power_profile:
    daily_input_tokens: 45000000
    spike_probability: 0.15
    spike_multiplier_min: 1.5
    spike_multiplier_max: 2.5
```

Profile proportions allocate exact user counts once. Project proportions are probabilities per active user-day, so small samples need not match them exactly. Each active user may change Project between dates, with one Project across all that day's agent/model records.

Daily volume variation is relative to the profile baseline: 30% samples 70–130%. Cache variation uses percentage points: 95% with 5 points samples 90–100%. Agent/model mix variation perturbs each weight relatively and renormalizes: 10% on a 60/40 mix perturbs weights within 54–66 and 36–44 before normalization. Zero-share agents/models stay zero; the processed-input total is conserved. Output is a fixed ratio of processed input, rounded separately per model record.

## Consumption events

```yaml
events:
  spike_rules:
    - id: release
      probability: 1.0
      multiplier_min: 3.0
      multiplier_max: 5.0
      date_range:
        start_date: "2026-07-20"
        end_date: "2026-07-22"
      targets:
        teams: [team1]
        agents: [codex]
        models:
          - {agent: claude, model: claude-opus-5}
  zero_periods:
    - id: break
      target_type: org
      target: talium.ai
      start_date: "2026-07-27"
      end_date: "2026-07-28"
```

The `events`, `spike_rules`, and `zero_periods` fields are required; use empty lists to disable them. Each spike rule has a unique ID, probability, multiplier bounds, and explicit target lists. Omit `date_range` to cover the full series; otherwise both inclusive dates are required. Spikes never run on weekends.

An empty Team list means all Teams. Agent targets include every model under those agents; exact model pairs are unioned with agent targets. Team and technology selections intersect. If all target lists are empty, the rule is Organization-wide. A selected rule distributes its incremental processed input across matching positive baseline rows in baseline proportion. Overlapping rules add independent increments calculated from the same baseline; they do not compound. A multiplier of one or a target slice with no positive baseline emits no spike event. Zero periods keep their existing user, Team, and Organization targets and always leave affected consumption at zero. Collection coverage remains complete.

## Reports, failures, and replay

Every stage saves `report.md` before publishing `manifest.json`. Reports show seeds, saved input/config references, artifact counts, and stage-specific results. Roster reports show Team names and internally generated stored IDs. Series reports verify coverage counts and show token breakdowns by Team, Project, and agent/model. Empty series remain valid: token totals are zero and shares read `N/A`.

For an interrupted or failed run, a directory without a complete manifest is not a valid dataset. Handled failures remove only the directory created by that invocation; existing directories and input files are never overwritten unless `--force` targets that output directory. The CLI reports whether cleanup succeeded. Choose a new output path when replaying, or pass `--force` to recreate the same one. Record files reproduce with the same inputs and versions; report timestamps and run IDs intentionally differ.

```bash
./econ-forecast/utils/synt_gen.sh --op series-create \
  --config econ-forecast/config/synthetic/series.yaml --validate-only
```

`--validate-only` checks saved inputs and cross-file references, including duplicate Team names in roster creation, without creating a dataset or report file. It does not sample an entire series; generated-volume overflow is detected during generation. No external API, database, or runtime download is used.
