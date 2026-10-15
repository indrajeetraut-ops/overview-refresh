# Handoff: operator guide for synthetic data, forecasts, and plotting

Completed on 2026-09-17 at the user's selected destination: [operator guide](../operations/README-forecast.md). See [execution and parent-review evidence](operator-guide-validation.md). The instructions below preserve the original handoff; its suggested `docs/operators/` destination was superseded by `docs/operations/README-forecast.md`.

## Assignment

Write an operator document for someone trying the implemented modules locally. Cover configuration, generating a daily dataset, executing forecasts, understanding outputs, and using the Plotly CLI to inspect saved forecasts. The reader should not need to navigate research and design history to complete a first run.

The user is switching from Astra for this documentation work. Do not begin another implementation or redesign the model. No new query API, reporting module, or budget calculator is requested. If coding becomes necessary, the user's standing preference is GPT-5.6-sol for major implementation work with independent review and parent ownership of fixes.

## Repository and starting state

- Work directory: `/Users/sumandra/projects/talium/forecasting/code-econ`.
- Branch at handoff: `forecast`; verify before editing.
- Latest implementation commit: `3d1cb45`, `feat(forecast): implement daily token forecasting and offline plots`.
- Synthetic implementation commit: `4318d0a`.
- Working tree was clean before this handoff was added. This handoff is not yet committed.
- Read root `AGENTS.md` and check for more specific instructions. Use the repository `venv` and root `requirements.txt`; there is no separately installed forecast package. Never use `git -C`.
- Suggested deliverable: `econ-forecast/docs/operators/forecast-workflow.md`, with discovery links from the three module READMEs. Keep module-specific READMEs in their modules.

All paths below are relative to the repository root.

## Read these first

1. `econ-forecast/src/forecast/synthetic/README.md` — three independent generation stages, stored token meanings, events, reports, and replay.
2. `econ-forecast/src/forecast/forecasting/README.md` — execution, eligibility, output schemas, samples, limitations, and the future budget consumer.
3. `econ-forecast/src/forecast/utils/README.md` — implemented Plotly command, selection flags, graph interpretation, exit statuses.
4. Maintained templates:
   - `econ-forecast/config/synthetic/users.yaml`: fake user pool, organization domain, locale, identity seed.
   - `econ-forecast/config/synthetic/roster.yaml`: saved users, membership seed, team names/counts, optional explicit team IDs.
   - `econ-forecast/config/synthetic/series.yaml`: saved users/roster, dates, duration, profile/usage seeds, profiles, team project/profile mixtures, agent/model mixtures, activity, cache ratios, optional spikes/zero periods.
   - `econ-forecast/config/forecasting/forecast.yaml`: dataset path, default organization, scope selection, history gates, ETS policy, allocation window, uncertainty samples/seed/tolerance, workers. Horizon is a CLI input.
5. Executable sources of truth: `econ-forecast/utils/{synt_gen,forecast,plot_forecast}.sh` and their `cli_synt_gen.py`, `cli_forecast.py`, `cli_plot_forecast.py` modules. Execute their help; do not invent flags.

Consult specifications only to resolve details: `econ-forecast/docs/design/synthetic-data-generation.md`, `synthetic-data-generation-design.md`, `forecasting.md`, `forecasting-design.md`, and `forecasting-release-contract.md`. The release contract supplies exact forecast artifact schemas. Architecture is in `econ-forecast/docs/arch/module-architecture.md`.

## Required operator content

1. A short description of what works today and a copyable end-to-end first-run walkthrough. Include repository location, environment setup, prerequisites, and expected duration/resources with measured evidence distinguished from estimates. No GPU, database, provider API key, or browser server is needed for this synthetic workflow.
2. A small configuration table explaining each template, its inputs and outputs, essential edits, units, and path resolution. YAML input paths resolve relative to that YAML file; CLI paths resolve relative to the caller's working directory. Explain how to copy configs without breaking references. Preserve existing operator data and use fresh output destinations.
3. Generate users once, create a fixed roster once, then generate multiple series from those saved identities. Explain manual `team_id`, pool size versus rostered population, seeds, series length, zero days, weekends, optional spikes, and one project per active user-day. Distinguish team display names used in generation profiles from team IDs used in forecast selection.
4. Execute a forecast with validation first, a historical origin, and explicit calendar horizon. Explain next week/month and `--offset 3 --periods 1`. Changing origin must match available coverage. One dataset supports organization, team, and project selections; no separate generated dataset per scope is needed.
5. An output directory map for each generation stage and forecast. Explain reports, resolved configs, completion manifests, usage/coverage JSONL, path index and NumPy arrays, daily expectations, period summaries, allocations, diagnostics, and static charts. Derive actual names from a fresh run. Include short readable JSON examples copied from that run, explaining that JSONL stores one object per physical line.
6. Run Plotly directly through `plot_forecast.sh`, selecting organization/team/project and optional repeated model filters. Show all models, one model, and zero displayed sample paths. Explain how to open the HTML on macOS, and how a remote Linux operator can copy the HTML locally. Embedded Plotly works offline; opening a browser is a separate action.
7. Explain today's inspection workflow: use reports/JSON outputs and Plotly scope/model selection. No general question/query CLI or interactive query API exists. Do not present future budget examples as executable commands. If showing programmatic inspection, use the verified `load_run(...)` API after checking its actual signature; keep it optional.
8. Troubleshooting and exit statuses, separately for each CLI: existing output, wrong path/ID, insufficient history, outage at origin, partial results, numerical rejection, invalid config, and missing dependencies. Explain stdout JSON versus stderr structured logs and completion manifests. `--validate-only` is not a promise that fitting will succeed.

## Facts that must remain consistent

- The checked-in example has 100 users, 50 rostered across three teams, and 60 daily dates starting `2026-07-01`, ending `2026-08-29`. Organization ID is `talium.ai`; the first team's explicit ID is `payments-team`, display name `team1`. The example forecast origin is `2026-08-29`, month offset 1, periods 1, forecasting September. Treat configured model IDs as dataset labels, not a claim of live provider availability.
- Synthetic `daily_input_tokens` means processed input: stored `input + cache_read`. Stored `input` already includes cache writes; cache reads are separate. Forecast total is `input + cache_read + output`. Never require cache reads to be smaller than stored uncached input; at 80% cache hits they normally exceed it. Synthetic output bounds refer to processed input. Do not apply synthetic ratio assumptions as validation rules to real usage.
- Synthetic coverage is complete, including zero-consumption days. Covered absence of usage means zero; absent/partial/outage coverage is unknown. The current synthetic generator does not generate collection outages through its zero-day controls.
- Default history lookback is 90 days, with at least 14 consecutive covered days ending on origin, bounded by established start. Startup requires a configurable consecutive positive-day streak (default 4); its first day is the start. Later zeros do not restart an established series. Explain inactive and insufficient outcomes without reopening agreed policy.
- A path array is dates by samples. Each column is one possible daily future. Corresponding columns align across models within a scope. Do not sum overlapping scopes or add per-model/daily percentiles to get period percentiles.
- `--sample-paths` controls displayed traces only. Mean and p10–p90 bands use all saved samples. Daily bands are pointwise and expressed in tokens, not dollars or whole-period survival probability.
- Saved paths and allocations support future budget work, but price conversion, $40K survival/overrun calculations, scenarios, and the general reporting/query module are not implemented. Minimal Markdown/PNG presentation and Plotly are implemented. Subscription handling remains TODO.

## Validation and completion criteria

- Execute the guide's exact fresh-workspace workflow: user creation, roster creation, series creation, forecast validation and execution, then plotting. Use an isolated temporary directory with fresh output names; document config copies/path edits completely. Do not overwrite existing datasets or quietly depend on files from prior acceptance runs.
- Verify stage reports/manifests and their declared artifacts. Independently inspect basic dataset counts, dates, coverage, IDs, and token invariants; verify forecast index, actual array shapes, requested dates, statuses, and summary meanings. Execute documented selection examples against real IDs.
- Open at least one generated HTML chart in a browser and inspect readable labels, hover, zoom, and legend visibility. Verify its offline behavior if claiming that verification; distinguish source-supported capability from checks actually executed in this documentation task.
- Record exact commands, exit results, artifact locations, and any blocked checks in a short evidence note. Fix documentation errors found by execution. If a production defect is discovered, report it clearly and follow repository implementation/review rules rather than disguising it in the guide.
- Existing implementation acceptance recorded 251 distinct tests passing on macOS and Linux emulation. This is historical evidence, not validation of newly written commands. See `econ-forecast/docs/notes/forecast-parent-validation.md` and `forecasting-acceptance-matrix.md`. Native AWS performance has not been measured; the prior temporary Linux validation container was removed.
- No full test-suite rerun is necessary for prose-only changes. Run relevant checks if executable files or examples are changed. Finish with the guide link, the workflow actually verified, and material limitations. Do not commit or push unless requested.

## Suggested next-model instruction

Read `econ-forecast/docs/notes/operator-guide-handoff.md` and root `AGENTS.md`. Create the operator guide described there using the implemented CLIs and maintained configs. Execute the documented workflow and inspect its outputs before declaring completion. Keep the guide practical and self-contained; do not implement the deferred budget, reporting, or query modules.
