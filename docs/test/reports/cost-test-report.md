# Cost (forecast.cost) — Test Report

Run: `2026-09-27T19:33:44.476555+05:30`

- Total: 85  Passed: 85  Failed: 0  Skipped: 0
- Wall time (sum of cases): 12.13s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| econ-forecast.tests.cost.test_artifacts | 8 | 8 | 0 | 0 | 0.13 |
| econ-forecast.tests.cost.test_cli | 11 | 11 | 0 | 0 | 5.07 |
| econ-forecast.tests.cost.test_kernel | 6 | 6 | 0 | 0 | 0.00 |
| econ-forecast.tests.cost.test_mix | 9 | 9 | 0 | 0 | 0.00 |
| econ-forecast.tests.cost.test_presentation | 7 | 7 | 0 | 0 | 2.17 |
| econ-forecast.tests.cost.test_rates | 7 | 7 | 0 | 0 | 0.00 |
| econ-forecast.tests.cost.test_request | 15 | 15 | 0 | 0 | 4.75 |
| econ-forecast.tests.cost.test_scenario | 6 | 6 | 0 | 0 | 0.00 |
| econ-forecast.tests.cost.test_schemas | 12 | 12 | 0 | 0 | 0.00 |
| econ-forecast.tests.cost.test_summaries | 4 | 4 | 0 | 0 | 0.00 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| econ-forecast.tests.cost.test_artifacts.test_write_and_load_round_trip | pass | 0.043 |
| econ-forecast.tests.cost.test_artifacts.test_tampered_file_is_rejected | pass | 0.008 |
| econ-forecast.tests.cost.test_artifacts.test_unknown_minor_schema_is_accepted | pass | 0.013 |
| econ-forecast.tests.cost.test_artifacts.test_unknown_major_schema_is_rejected | pass | 0.007 |
| econ-forecast.tests.cost.test_artifacts.test_slug_collision_gets_a_numbered_suffix | pass | 0.014 |
| econ-forecast.tests.cost.test_artifacts.test_borrowed_mix_series_gets_a_mix_json_entry | pass | 0.015 |
| econ-forecast.tests.cost.test_artifacts.test_report_renderer_hook_is_included_in_the_manifest | pass | 0.013 |
| econ-forecast.tests.cost.test_artifacts.test_reader_tolerates_absent_presentation_files_but_verifies_present_ones | pass | 0.019 |
| econ-forecast.tests.cost.test_cli.test_help_does_no_io | pass | 0.138 |
| econ-forecast.tests.cost.test_cli.test_missing_required_arguments_exit_2 | pass | 0.136 |
| econ-forecast.tests.cost.test_cli.test_validate_only_writes_nothing_and_exits_0 | pass | 0.498 |
| econ-forecast.tests.cost.test_cli.test_validate_only_with_unmatched_model_exits_2 | pass | 0.263 |
| econ-forecast.tests.cost.test_cli.test_missing_forecast_run_exits_2 | pass | 0.140 |
| econ-forecast.tests.cost.test_cli.test_real_run_writes_output_and_exits_0 | pass | 0.727 |
| econ-forecast.tests.cost.test_cli.test_existing_output_is_refused_without_force | pass | 0.263 |
| econ-forecast.tests.cost.test_cli.test_force_recreates_only_the_target | pass | 1.260 |
| econ-forecast.tests.cost.test_cli.test_wrapper_script_runs | pass | 0.785 |
| econ-forecast.tests.cost.test_cli.test_execution_error_exits_1 | pass | 0.001 |
| econ-forecast.tests.cost.test_cli.test_run_cost_prices_a_scenario_python_api | pass | 0.862 |
| econ-forecast.tests.cost.test_kernel.test_design_example_day_prices_to_1_41 | pass | 0.001 |
| econ-forecast.tests.cost.test_kernel.test_inactive_series_prices_to_zero | pass | 0.000 |
| econ-forecast.tests.cost.test_kernel.test_unusable_forecast_status_passes_through_with_no_path | pass | 0.000 |
| econ-forecast.tests.cost.test_kernel.test_unmatched_model_yields_unpriced_status | pass | 0.000 |
| econ-forecast.tests.cost.test_kernel.test_scope_status_complete_conditional_incomplete | pass | 0.000 |
| econ-forecast.tests.cost.test_kernel.test_mix_override_prices_with_the_borrowed_shares_and_reports_the_source_window | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_design_example_4_active_days_10_percent_3_percent_blends_to_5_5_percent | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_ratio_of_weighted_sums_not_mean_of_daily_ratios | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_outage_day_is_excluded_from_both_sums | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_organization_has_no_parent_and_uses_its_own_mix | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_attribution_share_matches_design_example | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_attribution_share_is_zero_when_parent_denominator_is_zero | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_team_of_project_finds_the_unique_superset_team | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_team_of_project_is_none_when_undetermined_non_strict_omits_it | pass | 0.000 |
| econ-forecast.tests.cost.test_mix.test_required_team_of_project_raises_naming_the_project_when_undetermined | pass | 0.000 |
| econ-forecast.tests.cost.test_presentation.test_report_sections_present_in_order | pass | 0.330 |
| econ-forecast.tests.cost.test_presentation.test_unpriced_model_listed_with_reason_and_candidates | pass | 0.304 |
| econ-forecast.tests.cost.test_presentation.test_thin_evidence_mix_lists_team_series | pass | 0.298 |
| econ-forecast.tests.cost.test_presentation.test_charts_written_as_png_and_html_with_no_network_references | pass | 0.352 |
| econ-forecast.tests.cost.test_presentation.test_scenario_section_only_for_scenario_runs | pass | 0.590 |
| econ-forecast.tests.cost.test_presentation.test_render_report_output_matches_output_dir_argument | pass | 0.296 |
| econ-forecast.tests.cost.test_presentation.test_scope_order_follows_the_scope_tree_and_status_is_derived_from_the_index | pass | 0.000 |
| econ-forecast.tests.cost.test_rates.test_design_example_blended_rate_is_1_281_per_million | pass | 0.000 |
| econ-forecast.tests.cost.test_rates.test_null_rate_with_nonzero_mix_share_is_unpriced | pass | 0.000 |
| econ-forecast.tests.cost.test_rates.test_null_rate_with_zero_mix_share_is_still_priced | pass | 0.000 |
| econ-forecast.tests.cost.test_rates.test_unmatched_model_is_unpriced_with_reason | pass | 0.000 |
| econ-forecast.tests.cost.test_rates.test_model_absent_from_sheet_is_unpriced | pass | 0.000 |
| econ-forecast.tests.cost.test_rates.test_price_change_mid_range_uses_the_segment_in_effect | pass | 0.000 |
| econ-forecast.tests.cost.test_rates.test_missing_catalog_is_execution_error | pass | 0.001 |
| econ-forecast.tests.cost.test_request.test_baseline_resolves | pass | 0.339 |
| econ-forecast.tests.cost.test_request.test_scenario_resolves | pass | 0.344 |
| econ-forecast.tests.cost.test_request.test_pre_t2_run_raises_input_error | pass | 0.008 |
| econ-forecast.tests.cost.test_request.test_dataset_hash_mismatch_raises_input_error | pass | 0.335 |
| econ-forecast.tests.cost.test_request.test_scenario_name_must_match_file_name | pass | 0.347 |
| econ-forecast.tests.cost.test_request.test_scenario_name_baseline_is_reserved | pass | 0.333 |
| econ-forecast.tests.cost.test_request.test_scenario_scope_must_exist_in_forecast_run | pass | 0.338 |
| econ-forecast.tests.cost.test_request.test_model_transfer_from_must_be_a_series_in_scope | pass | 0.338 |
| econ-forecast.tests.cost.test_request.test_model_transfer_to_must_be_matched_in_the_rate_sheet | pass | 0.338 |
| econ-forecast.tests.cost.test_request.test_model_transfer_fraction_out_of_range | pass | 0.339 |
| econ-forecast.tests.cost.test_request.test_agent_transfer_from_must_have_usage_in_scope | pass | 0.339 |
| econ-forecast.tests.cost.test_request.test_agent_transfer_to_must_have_usage_in_org | pass | 0.339 |
| econ-forecast.tests.cost.test_request.test_growth_factor_must_be_nonnegative | pass | 0.338 |
| econ-forecast.tests.cost.test_request.test_token_factor_must_be_positive | pass | 0.343 |
| econ-forecast.tests.cost.test_request.test_effective_from_must_fall_within_forecast_dates | pass | 0.337 |
| econ-forecast.tests.cost.test_scenario.test_design_example_40_percent_times_30_percent_equals_12_percent_at_org | pass | 0.001 |
| econ-forecast.tests.cost.test_scenario.test_token_factor_scales_destination_and_breaks_conservation | pass | 0.000 |
| econ-forecast.tests.cost.test_scenario.test_growth_propagates_by_attribution_share | pass | 0.000 |
| econ-forecast.tests.cost.test_scenario.test_agent_transfer_moves_between_models_via_org_level_split | pass | 0.000 |
| econ-forecast.tests.cost.test_scenario.test_declared_project_scope_requires_a_uniquely_determined_team | pass | 0.000 |
| econ-forecast.tests.cost.test_scenario.test_ambiguous_project_sub_scope_raises_during_team_declared_scenario | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_manifest_example_validates | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_request_example_validates | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_index_example_validates | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_daily_expected_row_example_validates | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_period_summary_example_validates | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_mix_example_validates | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_scenario_config_example_validates | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_readers_ignore_unknown_fields | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_schema_major_reads_the_major_component | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_scenario_name_baseline_is_rejected | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_scenario_change_requires_exactly_one_type | pass | 0.000 |
| econ-forecast.tests.cost.test_schemas.test_scenario_organization_scope_defaults_and_rejects_a_name | pass | 0.000 |
| econ-forecast.tests.cost.test_summaries.test_scope_p90_is_not_the_sum_of_series_p90s | pass | 0.000 |
| econ-forecast.tests.cost.test_summaries.test_incomplete_scope_gets_no_period_summary_entry | pass | 0.000 |
| econ-forecast.tests.cost.test_summaries.test_daily_expected_rows_split_tokens_and_usd_by_mix | pass | 0.000 |
| econ-forecast.tests.cost.test_summaries.test_daily_expected_rows_for_inactive_series_are_all_zero | pass | 0.000 |

Reproduce:

```bash
venv/bin/python -m pytest econ-forecast/tests/cost/ --junitxml=docs/test/reports/cost-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Cost (forecast.cost)" \
    --junit docs/test/reports/cost-junit.xml \
    --out   docs/test/reports/cost-test-report.md \
    --tests econ-forecast/tests/cost/
```
