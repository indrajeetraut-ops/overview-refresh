# Forecasting design decision checkpoint

> Historical decision checkpoint for the earlier ETS implementation. The current model is documented in [the weekday-resampling design](../design/forecasting-weekday-bootstrap.md).

Updated 2026-09-13 from the user/assistant conversation. This is the durable discussion checkpoint; the [design](../design/forecasting-design.md), [functional spec](../design/forecasting.md), [template](../../config/forecasting/forecast.yaml), and [module README](../../src/forecast/forecasting/README.md) carry the maintained details. No forecasting implementation or executable benchmark is claimed. Continue material decisions one at a time; parent owns recommendations and verification.

## Agreed decisions and rationale

| Decision | Agreement and reason | Design section |
|---|---|---|
| Architecture | One CPU batch operation; separate history, fitting, paths, allocation, artifacts, and reporting. Budget/scenarios reuse outputs. | 1 |
| Library | statsmodels ETSModel, 0.15.0 a compatibility candidate; documented explicit-error trajectory API. No custom ETS, no assumed AutoETS simulation support. | 2 |
| Supporting packages | NumPy, SciPy, pandas; psutil for measurements and threadpoolctl for numerical thread limits; reuse Pydantic/PyYAML/structlog/pytest and argparse. Matplotlib for reporting file charts, Plotly for local interactive experiments. Exact tested pins pending. | 2 |
| Fitting | 14–27 days level-only; 28+ weekly seasonal+damped trend, then seasonal/no-trend, then level-only on numerical failure. 1,000 iterations per attempt, convergence/finite checks, report every fallback. | 2 |
| Scope granularity | Organization/model, Team/model, Project/model, always Organization-qualified. Agent is a joint allocation dimension, not an independent fit/gate. | 3 |
| Coverage | Covered absence is zero; declared outage breaks fitting history; malformed tokens are validation errors. At least 14 consecutive covered days ending on origin; do not move origin or bridge gaps. | 3 |
| Revised startup gate | `history.minimum_active_days` is X CONSECUTIVE positive calendar days, default 4. Start is first day of qualifying streak; those days count toward 14. Once established, zeros/low usage do not reset the gate. X=7 requires weekends too. | 3 |
| Inactivity/retirement | Qualified series can taper normally; preserve any positive usage. Entirely zero recent allocation window means currently inactive/assumed zero, not formal retirement/completion. All-zero covered history has a valid non-probabilistic inactivity outcome. | 3–4 |
| Token meaning | Forecast input + cache_read + output. Input already includes cache writes. Never double count; budget needs an effective pricing assumption for that merged category. | 4 |
| Allocation | Last 28 calendar days intersected with usable history; ratios of summed joint agent/category tokens, held fixed over paths/horizon. Preserves totals and makes pricing possible; no separate category forecast or claim to predict changing cache efficiency. | 4 |
| Empty recent allocation | No older-share fallback. Established series with zero recent consumption is currently inactive; older active-period data is separately labeled context. | 4 |
| Uncertainty | Joint historical residual-vector bootstrap within scope; center per model, select aligned date vectors together, simulate recursively. 1,000 equally weighted futures default; day-vector independence and fixed parameters/shares are limitations. | 5 |
| Different history lengths | Retain each model's full fit; use common valid error dates, centered on that shared window, with at least 14 dates. 90/21-day fits use 21 shared sampling dates. Keeps older fitting evidence and observed paired errors; excludes older unusual events from sampling. Inactive models do not shorten overlap. Report both windows. | 5 |
| Negative values | Post-generation max(0, value); summaries use corrected paths. Correction amount / corrected total per model over requested dates/all samples. Configurable tolerance `uncertainty.max_negative_correction_ratio`, default .05 in [0,1]; above fails, equality passes. Positive correction/zero denominator always fails. | 6 |
| Scope readiness | Qualified+inactive allows conditional risk; insufficient/failed nonzero models cannot be omitted. All-inactive has no empirically established 100% confidence. Save useful valid scopes, partial exit 2. Retirement alone never means insufficient. | 7 |
| Storage | New directory/run, no forecast DB. JSONL daily expectations; JSON summaries/shares/metadata; numeric float64 .npy per scope/model. Explanatory scope-name/model filenames, IDs resolve collisions, index maps exact identities and axes. | 8 |
| Reporting | Separate module with its own functional spec and detailed design; default report AND charts. Saved output contract belongs in module README. Specific-question/interactive API deferred, no new forecast pricing endpoint. | 9 |
| Local Plotly utility | Early heavily used experimentation utility in forecast.utils; standalone embedded/offline HTML, CLI plus shell wrapper, saved artifacts only, no refitting. Numerical trace assertions, real shell workflow, offline browser rendering/interactions and actual artifact review required. Utility README added. | 9–10 |
| Samples and $40K | Row=date, column=one possible future, cell=total tokens. Price matching columns across models, count <=budget versus >budget. Expected crossing date and range conditional on crossing; noncrossing paths have no date. Requires prices/shares and complete/conditional scope readiness. | 9 |
| Validation | Explicit what/how test matrix, actual shell execution and output inspection, independent reviewer for subagent implementation. CPU/memory/runtime/storage need measured evidence; not yet executed. | 10–11 |

## Superseded proposals — do not restore

- Counting any X positive days in the lookback. The user changed this to a consecutive startup streak, not a recurring quota.
- Starting eligibility at the first isolated positive day. The final rule starts at the first day of a qualifying streak.
- Treating once-weekly activity as automatically startup-eligible under X=4. It needs prior qualification or a suitable configured X.
- Expanding an empty allocation window into older active history. User rejected it; recent inactivity is meaningful evidence.
- Treating all inactive components as failures that block an otherwise qualified scope. They contribute explicitly assumed zero; a wholly inactive scope still cannot claim established certainty.
- Independent per-model error sampling as the v1 uncertainty method. Joint aligned error vectors are agreed.
- Opaque `series-001.npy` output names. Use explanatory scope/model names and authoritative index metadata.
- A text-only report or embedding all presentation in fitting. Reporting and graphics are separate consumers; interactive API deferred.
- Rejecting low/zero observed variation or injecting arbitrary noise to manufacture uncertainty. The user rejected making normal consistent workloads another eligibility discussion. Handle them normally in numerical implementation; do not reintroduce that proposed gate.
- Deferring all Plotly work until the reporting module is designed. The local experimentation utility is explicitly requested now, with tests; the reporting module still receives separate specifications.

## Remaining engineering work

Use design section 11 as the single open-work list: residual scaling and deterministic streams; tested dependency pins and exact fit internals; coverage/population/startup-evidence mapping; never-qualified older sparse usage with a recent zero window; final schemas/statuses/naming and file lifecycle; batch interfaces/layout and Plotly reader integration; workload/resource limits and benchmarks; complete acceptance commands/fixtures/tolerances. Reporting gets its own spec/design. Full-fitting/common-sampling windows and their rationale are settled, not open.

Do not list settled fitting, bootstrap, negative correction, inactivity, or storage-format decisions as open again. Do not describe this checkpoint as an implementation-ready design until the remaining details are resolved. Full forecasting code, reporting/budget implementations, and interactive query APIs are not part of this documentation checkpoint.

## Implementation preparation

The user requested GPT-5.6-sol subagents for implementation with independent validation and parent-owned code/test quality and final fixes. The [implementation handoff](forecasting-implementation-handoff.md) defines sources, roles/ownership, self-contained prompts, required real command/browser workflows, evidence and review closure. The [acceptance matrix](forecasting-acceptance-matrix.md) seeds all 37 current design criteria and 24 FS acceptance scenarios; runtime results remain unverified. Parent contract freeze is required before coding agents are released, including precise shared schemas and resource acceptance targets. Preparing/reviewing instructions is not implementation dispatch or acceptance.

## Implementation release — 2026-09-13

The user approved subagent dispatch. Parent resolved the engineering checklist in [release contract](../design/forecasting-release-contract.md). Read it for final input adapters, artifact schemas, interface/file ownership, numerical policies and resource/test targets; prior pending-engineering wording is historical. Sol numerical preflight verified Mac library compatibility and affine normalization, including exact zero-innovation ETS without noise. An independent Sol reviewer checked the interfaces; parent resolved count/tuple/nullability/selection ambiguities. Linux/runtime acceptance remains unverified. Frozen sources are recorded in forecasting-release-snapshot.json.
