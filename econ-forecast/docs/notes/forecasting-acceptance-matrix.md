# Forecasting acceptance matrix

> Historical acceptance record for the earlier ETS implementation. Current model and checks are in [the weekday-resampling design](../design/forecasting-weekday-bootstrap.md) and current tests.

**Accepted by parent after independent review, 2026-09-14.** Parent-owned mapping against the functional specification, design section 10, and frozen release contract. Implementation A, utility B, independent review C. Final consolidated execution and review status are recorded in [parent validation](forecast-parent-validation.md). No test count substitutes for the per-criterion oracles below.

## Test references and execution

All test paths below are relative to `econ-forecast/tests`. Production paths are relative to `src/forecast/forecasting` unless stated otherwise.

| Alias | Exact file |
|---|---|
| H | acceptance/test_history_allocation_contract.py |
| F | acceptance/test_fit_selection_contract.py |
| S | acceptance/test_scope_contract.py |
| A | acceptance/test_forecast_acceptance.py |
| W | acceptance/test_forecast_workflow.py |
| R | acceptance/test_forecast_resources.py |
| N | forecasting/test_core_numerics.py |
| I | forecasting/test_runner_integration.py |
| P | forecasting/test_presentation.py |
| U | utils/test_plot_forecast.py |
| V | utils/test_plot_forecast_cli.py |
| B | utils/test_plot_forecast_browser.py |
| X | acceptance/test_input_failure_contract.py |
| E | acceptance/test_ets_recurrence_contract.py |

The final Mac command is `PLAYWRIGHT_BROWSERS_PATH=/private/tmp/forecast-playwright venv/bin/python -m pytest econ-forecast/tests -q -s`. The same complete test directory runs under `/workspace/venv/bin/python` with `-p no:cacheprovider` in the constrained Linux Python 3.11 container. Browser and process-memory checks run with the permissions they require; a blocked run is not treated as passing. See parent evidence for final counts, exact artifact locations, platform versions, and rechecks after fixes.

## Named design requirements

| ID | Implementing source | Test / inspection | Independent checking method |
|---|---|---|---|
| HIST-01 | `history.py` | H::test_hist01_counts_distinct_dates_not_rows | 13/14 dates, four users per day |
| HIST-02 | `history.py` | H::test_hist02_outage_truncates_continuous_suffix | Aug 10/25/origin outages retain 21/6/0 days |
| HIST-03 | `history.py` | H::test_hist03_fourteen_days_cross_month_boundary | Literal Aug 25–Sep 7 dates |
| HIST-04 | `history.py; config.py` | H::test_hist04_first_x3_streak_and_x1_x7_calendar_rules; H::test_hist04_active_day_type_and_bounds_rejected | First X-day streak; weekends; strict bounds |
| HIST-05 | `history.py` | H::test_hist05_daily_positivity_aggregates_split_agents; H::test_hist05_scope_models_are_checked_independently | Daily aggregation and different model outcomes |
| HIST-06 | `history.py` | H::test_hist06_all_zero_floor_is_inactive_but_subminimum_fails | Zero histories still require 14 covered days |
| HIST-07 | `history.py` | H::test_hist07_qualification_persists_through_taper_retirement_and_restart | Established startup is not reset |
| HIST-08 | `history.py` | H::test_hist08_leading_zeros_then_seven_vs_fourteen_days_from_start | First streak sets start; no future evidence leakage |
| HIST-09 | `history.py` | H::test_hist09_recent_zero_window_vs_one_positive | Zero allocation versus one token |
| ETS-01 | `ets.py` | N::test_weekly_fit_replay_and_zero_error_identity; N::test_single_path_keeps_two_dimensional_shape | Actual statsmodels simulation; zero errors reproduce forecast |
| ETS-02 | `ets.py` | F (all 9 cases) | 14/27/28 boundary, ordered fallback, convergence/warnflag, 1000-iteration cap |
| PATH-01 | `paths.py` | N::test_joint_models_receive_same_centered_historical_vector_indices | Opposing paired residual vectors |
| PATH-02 | `paths.py; ets.py; request.py` | E::test_fit_simulate_uses_end_anchor_and_recursive_weekly_state_updates; N::test_weekly_fit_replay_and_zero_error_identity; N::test_zero_all_negative_nonfinite_and_internal_offset_correction_cases; A::test_calendar_oracles | Recursive library state; internal offset excluded from totals |
| PATH-05 | `paths.py` | A::test_shared_sampling_keeps_full_fits_and_excludes_nonoverlap_errors | 90/21 full fits, shared 21 samples, excluded huge old residuals and inactive component |
| PATH-03 | `paths.py` | N::test_negative_correction_exact_ratio_boundary | Hand [-5,40,60] ratio = 0.05 |
| PATH-04 | `paths.py; config.py` | N::test_negative_correction_exact_ratio_boundary; N::test_zero_all_negative_nonfinite_and_internal_offset_correction_cases | Equality passes; above threshold/all-negative/nonfinite fail |
| ALLOC-01 | `allocation.py` | H::test_alloc01_one_million_hand_oracle | 1M history allocates 500K to 50K/400K/50K |
| ALLOC-02 | `history.py; allocation.py` | H::test_alloc02_token_weighted_not_daily_percentage_average | Unequal daily volumes through wire loader/history; weighted fractions |
| ALLOC-03 | `allocation.py` | N::test_allocation_is_token_weighted_joint_agent_category_share | Joint agent/category hand fractions |
| ALLOC-04 | `history.py` | H::test_alloc04_recent_window_excludes_old_outage_and_future | Literal recent-window totals exclude old/outage/future evidence |
| ALLOC-05 | `allocation.py; runner.py` | H::test_alloc05_fixed_joint_ratios_conserve_every_day_path_including_zero; A::test_run_forecast_hand_oracle_and_artifact_conservation | Explicit six shares; array conservation; produced daily rows |
| ALLOC-06 | `runner.py; presentation.py; README.md` | P::test_report_uses_saved_totals_windows_failures_and_actual_metrics; A::test_run_forecast_hand_oracle_and_artifact_conservation | Saved evidence windows, historical sums and assumptions |
| RESULT-01 | `runner.py:_scope_status` | S::test_scope_status_truth_table; S::test_runner_publishes_scope_readiness_from_every_component; S::test_catalog_model_without_scope_population_is_explicitly_not_applicable | Complete/conditional/inactive/incomplete and all numerical failure statuses |
| STORE-01 | `schemas.py; artifacts.py` | A::test_public_reader_roundtrip_and_foreign_entry_rejection; A::test_public_reader_rejects_semantically_misleading_index; S::test_default_org_override_and_same_team_id_do_not_mix | Read-only float64 mmap, strict identity/status axes, collision separation |
| STORE-02 | `runner.py; allocation.py` | S::test_scope_period_quantiles_sum_matching_sample_columns; A::test_run_forecast_hand_oracle_and_artifact_conservation | Anti-correlated model paths; aggregate columns before quantiles |
| STORE-03 | `runner.py; artifacts.py` | X::test_existing_output_directory_and_sentinel_are_byte_preserved; A::test_late_artifact_write_failure_leaves_no_published_run; A::test_public_reader_rejects_corrupt_or_unsafe_runs; I::test_scope_order_and_worker_count_preserve_actual_saved_arrays | Exclusive output, failure cleanup, manifest/checksum lifecycle, replay |
| DOC-01 | `README.md; ../utils/README.md` | Parent and C document inspection; W::test_real_forecast_and_plot_wrappers_from_spaced_cwd | Actual JSON schemas, Python/shell use, scope/sample meaning, limitations |
| PLOT-01 | `../utils/plot_forecast.py` | U::test_hand_oracle_and_model_org_separation | Independent mean/linear percentile/selected model values |
| PLOT-02 | `../utils/plot_forecast.py` | U::test_display_count_does_not_change_all_sample_statistics | Displayed path count does not affect all-sample statistics |
| PLOT-03 | `../utils/plot_forecast.py` | U::test_partial_and_inactive_are_explicit; U::test_invalid_selections_and_axes | Units/dates/readiness/continued-inactivity labels |
| PLOT-04 | `../utils/plot_forecast.py` | U::test_repeatable_and_source_preserved | Semantic replay and unchanged input hashes |
| PLOT-05 | `../utils/cli_plot_forecast.py; ../../../../utils/plot_forecast.sh` | V (all cases); W::test_real_forecast_and_plot_wrappers_from_spaced_cwd | Real shell outside repo, spaced paths, JSON channels, no overwrite |
| PLOT-06 | `../utils/plot_forecast.py` | B::test_offline_render_hover_zoom_reset_legend_and_hostile_text | Actual offline browser; HTTP(S) blocked; no errors |
| PLOT-07 | `../utils/plot_forecast.py` | B::test_offline_render_hover_zoom_reset_legend_and_hostile_text | Hover, zoom/reset, legend visibility, escaped hostile text |
| PLOT-08 | `../utils/plot_forecast.py` | B::test_reference_plot_workload_four_models_90_days_1000_samples | Measured generation/load/interaction time, memory and HTML size |
| PLOT-09 | `../utils/plot_forecast.py; presentation.py` | U; B; P; parent inspected PNG/browser screenshots | Numerical oracles and actual rendered views both required |
| BUDGET-CONTRACT-01 | `README.md` | Parent and C document inspection | 40K pricing/retained sample identity/strict overrun/conditional crossing date; budget runtime deferred |

## Functional acceptance scenarios

| ID | Test / inspection | Required outcome checked |
|---|---|---|
| FS-A01 | S::test_org_team_project_share_rows_without_double_counting | Three scopes from one dataset |
| FS-A02 | S::test_default_org_override_and_same_team_id_do_not_mix | Default org and explicit org override |
| FS-A03 | S::test_default_org_override_and_same_team_id_do_not_mix | Same team ID in two orgs remains distinct |
| FS-A04 | S::test_unattributed_usage_stays_only_in_organization | Unattributed usage excluded from team/project |
| FS-A05 | S::test_org_team_project_share_rows_without_double_counting | Shared source records independently aggregated |
| FS-A06 | S::test_org_team_project_share_rows_without_double_counting; README target contract | Combined input includes writes exactly once; no second cache-write category |
| FS-A07 | H::test_hist01_counts_distinct_dates_not_rows | 13 distinct dates rejected regardless of row count |
| FS-A08 | F | 14/27/28 model-selection boundary |
| FS-A09 | H::test_hist07_qualification_persists_through_taper_retirement_and_restart | Covered absence is zero |
| FS-A10 | H::test_hist04_first_x3_streak_and_x1_x7_calendar_rules; H::test_hist07_qualification_persists_through_taper_retirement_and_restart | Startup and later decline |
| FS-A11 | H::test_hist09_recent_zero_window_vs_one_positive | Empty recent allocation stays inactive |
| FS-A12 | S::test_scope_status_truth_table; S::test_runner_publishes_scope_readiness_from_every_component | Qualified+inactive versus insufficient readiness |
| FS-A13 | X::test_null_token_category_is_invalid_instead_of_zero; X::test_origin_outside_declared_dataset_interval_is_invalid; A::test_history_status_oracles; A::test_nonzero_usage_on_outage_prevents_false_all_inactive; S::test_catalog_model_without_scope_population_is_explicitly_not_applicable; H | Outage/null/stale/all-zero/new component checks |
| FS-A14 | S::test_unsupported_billing_is_rejected_not_omitted; S::test_catalog_model_without_scope_population_is_explicitly_not_applicable | Unsupported API billing rejected; not-applicable explicitly reported |
| FS-A15 | A::test_calendar_oracles | Offset3/period1 literal dates |
| FS-A16 | A::test_calendar_oracles; test_request_config.py::test_month_offset_and_leap_year | Month length/leap/year boundaries |
| FS-A17 | I::test_scope_order_and_worker_count_preserve_actual_saved_arrays; U::test_repeatable_and_source_preserved | Stable frozen seed/input across worker/scope order |
| FS-A18 | A::test_future_rows_cannot_change_paths_or_allocation; A::test_future_startup_evidence_is_ignored_at_cutoff | Origin excludes future observations/evidence |
| FS-A19 | README budget consumer contract; U::test_repeatable_and_source_preserved | Saved paths reusable without refitting; budget/scenario runtime outside this implementation |
| FS-A20 | N::test_zero_all_negative_nonfinite_and_internal_offset_correction_cases | Negative/nonfinite handling |
| FS-A21 | N::test_negative_correction_exact_ratio_boundary; A::test_config_type_range_and_nonfinite_rejection | Configurable tolerance and equality |
| FS-A22 | S::test_scope_period_quantiles_sum_matching_sample_columns | Aggregate anti-correlated columns before quantiles |
| FS-A23 | W; A::test_run_forecast_hand_oracle_and_artifact_conservation; test_request_config.py::test_cli_help_explains_cutoff_calendar_horizon_and_validation_mode | Library/CLI/shell/help exercised |
| FS-A24 | R::test_frozen_reference_resource_benchmark | Mac and Linux emulation reference benchmarks; no native AWS measurement |

## Additional release checks

- Strict reader corruption/unsafe-path/version/type/identity/status tests: A. Lightweight import boundary: `forecasting/test_lightweight_import.py` in a fresh Python process.
- Configuration hard limits/calendar overflow: A plus `forecasting/test_request_config.py`. Resource lifecycle/CPU and child-exit accounting: `forecasting/test_runtime.py`.
- Minimal PNG numerical/date/footer/report checks: P; parent inspected the corrected image.
- Dependency pins and Python 3.11 compatibility: [amendment](forecasting-release-amendment-1.json), `pip check`, full Mac/Linux execution. Frozen sources were hash-verified before implementation; later README updates are intentional delivered documentation changes.
- Synthetic regression: complete existing synthetic test suite included in final `econ-forecast/tests` run.
- Principal workflow: actual user-create → roster-create → series-create → forecast → plot shell commands outside repo with spaced paths, independent NumPy/JSON/hash inspection; see parent validation.
- Scope paths are persisted within each bounded worker before returning mmap handles; summary aggregation is released per scope. Manifest is published once after stable metadata/report byte accounting. Parent inspected runner code; lifecycle/failure tests execute it.
- Native AWS deployment/benchmark, budget/scenario runtime, full reporting module, and real-data calibration are not implemented by this release. Documentation explicitly describes these boundaries.

Review evidence: [core](forecast-core-implementation-evidence.md), [Plotly and gap tests](forecast-plotly-implementation-evidence.md), [independent review](forecast-independent-review.md), [parent final validation](forecast-parent-validation.md). All mapped in-scope criteria passed their cited tests/inspection. Final verification is 244 full-suite plus 7 supplemental cases on each of Mac and Linux emulation; C confirmed matrix closure and parent accepted the implementation. Native AWS performance and downstream budget/scenario runtime remain explicitly outside this acceptance.
