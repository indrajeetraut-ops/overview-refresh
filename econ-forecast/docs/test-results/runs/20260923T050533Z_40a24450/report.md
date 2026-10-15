# Econ Forecast — Test Report

Run: `2026-09-23T10:35:33.696251+05:30`

- Total: 472  Passed: 471  Failed: 1  Skipped: 0
- Wall time (sum of cases): 60.22s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| econ-forecast.tests.acceptance.test_forecast_acceptance | 46 | 46 | 0 | 0 | 1.19 |
| econ-forecast.tests.acceptance.test_forecast_resources | 1 | 1 | 0 | 0 | 22.40 |
| econ-forecast.tests.acceptance.test_forecast_workflow | 3 | 2 | 1 | 0 | 1.74 |
| econ-forecast.tests.acceptance.test_history_allocation_contract | 19 | 19 | 0 | 0 | 0.04 |
| econ-forecast.tests.acceptance.test_input_failure_contract | 6 | 6 | 0 | 0 | 0.04 |
| econ-forecast.tests.acceptance.test_scope_contract | 20 | 20 | 0 | 0 | 2.19 |
| econ-forecast.tests.acceptance.test_user_reduction_contract | 4 | 4 | 0 | 0 | 0.00 |
| econ-forecast.tests.acceptance.test_weekday_config_contract | 18 | 18 | 0 | 0 | 0.33 |
| econ-forecast.tests.curve.test_artifacts | 26 | 26 | 0 | 0 | 1.14 |
| econ-forecast.tests.curve.test_cli | 10 | 10 | 0 | 0 | 11.03 |
| econ-forecast.tests.curve.test_config | 19 | 19 | 0 | 0 | 0.02 |
| econ-forecast.tests.curve.test_model | 29 | 29 | 0 | 0 | 0.01 |
| econ-forecast.tests.curve.test_preview | 6 | 6 | 0 | 0 | 0.27 |
| econ-forecast.tests.curve.test_runner | 14 | 14 | 0 | 0 | 0.69 |
| econ-forecast.tests.forecasting.test_core_numerics | 5 | 5 | 0 | 0 | 0.00 |
| econ-forecast.tests.forecasting.test_lightweight_import | 2 | 2 | 0 | 0 | 0.21 |
| econ-forecast.tests.forecasting.test_presentation | 11 | 11 | 0 | 0 | 0.29 |
| econ-forecast.tests.forecasting.test_request_config | 8 | 8 | 0 | 0 | 0.00 |
| econ-forecast.tests.forecasting.test_runner_integration | 7 | 7 | 0 | 0 | 0.88 |
| econ-forecast.tests.forecasting.test_runtime | 8 | 8 | 0 | 0 | 0.02 |
| econ-forecast.tests.forecasting.test_test_reporting | 4 | 4 | 0 | 0 | 0.62 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap | 4 | 4 | 0 | 0 | 0.00 |
| econ-forecast.tests.synthetic.test_behavior | 14 | 14 | 0 | 0 | 0.02 |
| econ-forecast.tests.synthetic.test_core | 5 | 5 | 0 | 0 | 1.85 |
| econ-forecast.tests.synthetic.test_curve_binding | 32 | 32 | 0 | 0 | 0.80 |
| econ-forecast.tests.synthetic.test_interfaces | 3 | 3 | 0 | 0 | 3.16 |
| econ-forecast.tests.synthetic.test_regressions | 93 | 93 | 0 | 0 | 2.28 |
| econ-forecast.tests.utils.test_actuals | 5 | 5 | 0 | 0 | 0.79 |
| econ-forecast.tests.utils.test_plot_config | 19 | 19 | 0 | 0 | 0.02 |
| econ-forecast.tests.utils.test_plot_forecast | 17 | 17 | 0 | 0 | 0.36 |
| econ-forecast.tests.utils.test_plot_forecast_cli | 14 | 14 | 0 | 0 | 7.81 |

## Failures

| Test | Message |
|---|---|
| econ-forecast.tests.acceptance.test_forecast_workflow.test_real_forecast_and_plot_wrappers_from_spaced_cwd | AssertionError: {"error_type": "input", "event": "plot.failed", "message": "unrecognized arguments: --run /private/var/folders/56/xt4mfvr93wx0v78tnxvrc8sc0000g… |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request0-start0-end0-8] | pass | 0.021 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request1-start1-end1-22] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request2-start2-end2-107] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[offset-0] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[offset-True] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[periods-0] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[periods-1.0] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-lookback_days-True] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-minimum_days-13] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-minimum_active_days-0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[allocation-lookback_days-False] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-paths-0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-paths-10001] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-seed--1] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_probability-nan] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_probability-1.1] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_factor-inf] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_factor-0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_factor-True] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[runtime-workers-True] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[runtime-workers-5] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_relative_dataset_path_and_unknown_key | pass | 0.003 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_run_forecast_hand_oracle_and_artifact_conservation | pass | 0.261 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_validate_only_preserves_inputs_and_writes_nothing | pass | 0.014 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_roundtrip_and_foreign_entry_rejection | pass | 0.014 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[checksum] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[traversal] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[nonfinite] | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[lying_scope] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[bool_version] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[bool_samples] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[unhashable_identity] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[start_date] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[end_date] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[timezone] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_impossible_team_attribution_is_rejected | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_future_rows_cannot_change_paths_or_allocation | pass | 0.215 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-inactive-inactive] | pass | 0.093 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-insufficient_startup-incomplete] | pass | 0.020 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-insufficient_history-incomplete] | pass | 0.020 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_future_startup_evidence_is_ignored_at_cutoff | pass | 0.108 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_single_path_preserves_two_dimensional_saved_axis[<lambda>0] | pass | 0.121 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_single_path_preserves_two_dimensional_saved_axis[<lambda>1] | pass | 0.117 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_nonzero_usage_on_outage_prevents_false_all_inactive | pass | 0.024 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_late_artifact_write_failure_leaves_no_published_run | pass | 0.102 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_shared_sampling_excludes_weekdays_outside_model_overlap | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_resources.test_frozen_reference_resource_benchmark | pass | 22.401 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_forecast_wrapper_help_outside_repo_is_side_effect_free | pass | 0.322 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_real_forecast_and_plot_wrappers_from_spaced_cwd | FAIL | 1.165 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_wrapper_invalid_input_has_machine_readable_channels | pass | 0.251 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist01_counts_distinct_dates_not_rows | pass | 0.005 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[10-21] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[25-6] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[31-0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist03_fourteen_days_cross_month_boundary | pass | 0.001 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_first_x3_streak_and_x1_x7_calendar_rules | pass | 0.004 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[True] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[91] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist05_daily_positivity_aggregates_split_agents | pass | 0.000 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist05_scope_models_are_checked_independently | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist06_all_zero_floor_is_inactive_but_subminimum_fails | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist07_qualification_persists_through_taper_retirement_and_restart | pass | 0.005 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist08_leading_zeros_then_seven_vs_fourteen_days_from_start | pass | 0.004 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist09_recent_zero_window_vs_one_positive | pass | 0.003 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc01_one_million_hand_oracle | pass | 0.000 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc02_token_weighted_not_daily_percentage_average | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc04_recent_window_excludes_old_outage_and_future | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc05_fixed_joint_ratios_conserve_every_day_path_including_zero | pass | 0.000 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[0] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[1] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[2] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_origin_outside_declared_dataset_interval_is_invalid[origin0] | pass | 0.014 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_origin_outside_declared_dataset_interval_is_invalid[origin1] | pass | 0.013 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_existing_output_directory_and_sentinel_are_byte_preserved | pass | 0.012 |
| econ-forecast.tests.acceptance.test_scope_contract.test_org_team_project_share_rows_without_double_counting | pass | 0.322 |
| econ-forecast.tests.acceptance.test_scope_contract.test_unattributed_usage_stays_only_in_organization | pass | 0.295 |
| econ-forecast.tests.acceptance.test_scope_contract.test_null_user_stream_uses_default_weekend_rule_as_one_contributor | pass | 0.197 |
| econ-forecast.tests.acceptance.test_scope_contract.test_two_teams_may_each_have_a_null_user_stream | pass | 0.209 |
| econ-forecast.tests.acceptance.test_scope_contract.test_default_org_override_and_same_team_id_do_not_mix | pass | 0.213 |
| econ-forecast.tests.acceptance.test_scope_contract.test_unsupported_billing_is_rejected_not_omitted | pass | 0.015 |
| econ-forecast.tests.acceptance.test_scope_contract.test_catalog_model_without_scope_population_is_explicitly_not_applicable | pass | 0.112 |
| econ-forecast.tests.acceptance.test_scope_contract.test_omitted_origin_uses_declared_covered_zero_and_explicit_cutoff | pass | 0.025 |
| econ-forecast.tests.acceptance.test_scope_contract.test_omitted_origin_keeps_final_outage_ineligible | pass | 0.014 |
| econ-forecast.tests.acceptance.test_scope_contract.test_names_resolve_saved_ids_and_reject_ambiguity_within_organization | pass | 0.038 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses0-expected0] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses1-expected1] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses2-expected2] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses3-expected3] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses4-expected4] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses5-expected5] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[conditional-conditional-True-complete] | pass | 0.232 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[inactive-inactive-False-complete] | pass | 0.188 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[insufficient-incomplete-False-partial] | pass | 0.105 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_period_quantiles_sum_matching_sample_columns | pass | 0.221 |
| econ-forecast.tests.acceptance.test_user_reduction_contract.test_probability_boundaries_apply_to_users_before_scope_sum | pass | 0.000 |
| econ-forecast.tests.acceptance.test_user_reduction_contract.test_partial_probability_has_independent_user_outcomes | pass | 0.000 |
| econ-forecast.tests.acceptance.test_user_reduction_contract.test_one_user_draw_scales_all_models_consistently | pass | 0.000 |
| econ-forecast.tests.acceptance.test_user_reduction_contract.test_known_user_draw_is_reused_in_overlapping_scope_views | pass | 0.000 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_old_ets_method_is_rejected | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_old_negative_correction_setting_is_rejected | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[weekend-reduction_probability] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[weekend-reduction_factor] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[weekend-team_overrides] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[history-minimum_active_days] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[uncertainty-paths] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_probability--0.01] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_probability-1.01] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_probability-nan] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_probability-True] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_factor-0] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_factor-1.01] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_factor-inf] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_factor-0.25] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_team_override_requires_explicit_probability_and_factor | pass | 0.006 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_unknown_team_override_fails_before_output | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_huge_numeric_weekend_value_has_structured_cli_input_error | pass | 0.270 |
| econ-forecast.tests.curve.test_artifacts.test_load_curve_artifact_returns_correct_fields | pass | 0.167 |
| econ-forecast.tests.curve.test_artifacts.test_missing_directory_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_missing_manifest_rejected | pass | 0.060 |
| econ-forecast.tests.curve.test_artifacts.test_manifest_replaced_by_symlink_rejected | pass | 0.057 |
| econ-forecast.tests.curve.test_artifacts.test_incomplete_status_rejected | pass | 0.056 |
| econ-forecast.tests.curve.test_artifacts.test_missing_config_sha256_rejected | pass | 0.057 |
| econ-forecast.tests.curve.test_artifacts.test_malformed_config_sha256_rejected[deadbeef] | pass | 0.059 |
| econ-forecast.tests.curve.test_artifacts.test_malformed_config_sha256_rejected[zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz] | pass | 0.056 |
| econ-forecast.tests.curve.test_artifacts.test_malformed_config_sha256_rejected[12345] | pass | 0.056 |
| econ-forecast.tests.curve.test_artifacts.test_tampered_config_yaml_rejected | pass | 0.056 |
| econ-forecast.tests.curve.test_artifacts.test_directory_id_mismatch_rejected | pass | 0.058 |
| econ-forecast.tests.curve.test_artifacts.test_directory_name_mismatch_rejected | pass | 0.060 |
| econ-forecast.tests.curve.test_artifacts.test_missing_config_yaml_rejected | pass | 0.056 |
| econ-forecast.tests.curve.test_artifacts.test_config_yaml_replaced_by_symlink_rejected | pass | 0.057 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_name_id_resolves | pass | 0.056 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bare_unique_name_resolves | pass | 0.062 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_missing_name_directory_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_zero_complete_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_ambiguous_rejected | pass | 0.109 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_ignores_incomplete_orphaned_sibling | pass | 0.057 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[Bad Name] | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[name:short] | pass | 0.000 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[name:0123456789ABCDEF] | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[] | pass | 0.000 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[:0123456789abcdef] | pass | 0.000 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[name:0123456789abcdef0] | pass | 0.000 |
| econ-forecast.tests.curve.test_cli.test_help_and_invalid_arguments_do_no_io | pass | 1.344 |
| econ-forecast.tests.curve.test_cli.test_validate_only_writes_nothing_and_hash_matches_a_real_run | pass | 0.978 |
| econ-forecast.tests.curve.test_cli.test_validate_only_with_preview_days_override_matches_real_run_hash | pass | 1.075 |
| econ-forecast.tests.curve.test_cli.test_exit_codes_success_config_error_and_artifact_error | pass | 1.988 |
| econ-forecast.tests.curve.test_cli.test_noop_path_reports_unchanged_and_exits_zero | pass | 0.972 |
| econ-forecast.tests.curve.test_cli.test_different_preview_days_create_different_directories_and_rerun_noops | pass | 1.655 |
| econ-forecast.tests.curve.test_cli.test_stdout_is_one_json_object_and_stderr_lines_are_all_json | pass | 0.678 |
| econ-forecast.tests.curve.test_cli.test_open_flag_exercises_the_browser_path_without_crashing | pass | 0.742 |
| econ-forecast.tests.curve.test_cli.test_wrapper_script_validate_only_smoke | pass | 0.922 |
| econ-forecast.tests.curve.test_cli.test_error_summary_shape_and_values | pass | 0.675 |
| econ-forecast.tests.curve.test_config.test_valid_config_round_trips_all_fields | pass | 0.002 |
| econ-forecast.tests.curve.test_config.test_default_preview_is_optional | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_bad_interp_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_fewer_than_two_anchors_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_non_increasing_positions_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_endpoint_violation_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_axis_bounds_violation_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_negative_level_walk_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_leading_delta_first_anchor_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_missing_name_rejected | pass | 0.002 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[] | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[ ] | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[has spaces] | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[has/slash] | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[has:colon] | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_duplicate_yaml_keys_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_unquoted_plus_yaml_trap_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_missing_config_file_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_config.test_unknown_field_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_absolute | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_delta_positive | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_delta_negative | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_the_yaml_unquoted_plus_trap | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[] | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[abc] | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[1.2.3] | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[++4] | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[4-] | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[1e5] | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[None] | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[4.0] | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_normalizes_positions_and_accumulates_deltas | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_leading_delta_as_first_anchor | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_delta_chain_that_walks_negative | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_allows_exact_zero_floor | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_non_increasing_positions | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_endpoints_not_at_axis_bounds | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_axis_end_not_greater_than_start | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_fewer_than_two_anchors | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_non_integer_or_boolean_position | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_endpoints_equal_first_and_last_resolved_level | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_no_overshoot_stays_within_anchor_range | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_nonnegative_when_anchors_touch_zero_floor | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_stretch_exactness_across_sample_counts | pass | 0.004 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_n_equals_one_returns_level_at_position_zero | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_rejects_n_less_than_one | pass | 0.003 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_rejects_fewer_than_two_anchors | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_rejects_non_increasing_positions | pass | 0.001 |
| econ-forecast.tests.curve.test_preview.test_render_html_writes_a_readable_file_with_0644_permissions | pass | 0.020 |
| econ-forecast.tests.curve.test_preview.test_render_png_writes_a_valid_png | pass | 0.058 |
| econ-forecast.tests.curve.test_preview.test_render_html_and_png_succeed_on_both_axis_modes | pass | 0.115 |
| econ-forecast.tests.curve.test_preview.test_no_baseline_tokens_uses_dimensionless_axis_label | pass | 0.012 |
| econ-forecast.tests.curve.test_preview.test_baseline_tokens_uses_scaled_axis_label | pass | 0.011 |
| econ-forecast.tests.curve.test_preview.test_single_sample_point_does_not_crash | pass | 0.056 |
| econ-forecast.tests.curve.test_runner.test_config_hash_is_deterministic | pass | 0.001 |
| econ-forecast.tests.curve.test_runner.test_config_hash_is_sensitive_to_preview_days | pass | 0.001 |
| econ-forecast.tests.curve.test_runner.test_config_hash_is_sensitive_to_anchors_and_name | pass | 0.001 |
| econ-forecast.tests.curve.test_runner.test_curve_jsonl_matches_evaluate_curve_exactly | pass | 0.057 |
| econ-forecast.tests.curve.test_runner.test_default_sample_count_is_two_hundred_when_no_preview_days_set | pass | 0.056 |
| econ-forecast.tests.curve.test_runner.test_all_six_artifact_files_present | pass | 0.057 |
| econ-forecast.tests.curve.test_runner.test_manifest_written_last_with_complete_status_and_matching_hashes | pass | 0.058 |
| econ-forecast.tests.curve.test_runner.test_config_yaml_persists_the_resolved_config | pass | 0.060 |
| econ-forecast.tests.curve.test_runner.test_identical_config_rerun_returns_none_and_writes_nothing | pass | 0.058 |
| econ-forecast.tests.curve.test_runner.test_different_preview_days_create_different_directories | pass | 0.111 |
| econ-forecast.tests.curve.test_runner.test_incomplete_directory_missing_manifest_raises_artifact_error | pass | 0.057 |
| econ-forecast.tests.curve.test_runner.test_incomplete_directory_status_not_complete_raises_artifact_error | pass | 0.061 |
| econ-forecast.tests.curve.test_runner.test_incomplete_directory_unparseable_manifest_raises_artifact_error | pass | 0.057 |
| econ-forecast.tests.curve.test_runner.test_incomplete_directory_is_not_overwritten | pass | 0.058 |
| econ-forecast.tests.forecasting.test_core_numerics.test_constant_source_preserves_single_path_axis_and_positive_reduction | pass | 0.000 |
| econ-forecast.tests.forecasting.test_core_numerics.test_zero_baseline_stays_zero_without_fabricated_activity | pass | 0.000 |
| econ-forecast.tests.forecasting.test_core_numerics.test_seed_replays_weekend_draws_and_different_seed_changes_them | pass | 0.000 |
| econ-forecast.tests.forecasting.test_core_numerics.test_overflow_rejects_nonfinite_result_without_clipping | pass | 0.000 |
| econ-forecast.tests.forecasting.test_core_numerics.test_allocation_is_token_weighted_joint_agent_category_share | pass | 0.000 |
| econ-forecast.tests.forecasting.test_lightweight_import.test_artifact_reader_import_is_independent_of_runner_and_statsmodels | pass | 0.061 |
| econ-forecast.tests.forecasting.test_lightweight_import.test_lazy_public_runner_api_remains_available | pass | 0.152 |
| econ-forecast.tests.forecasting.test_presentation.test_build_figure_artists_match_independent_oracle | pass | 0.005 |
| econ-forecast.tests.forecasting.test_presentation.test_date_locator_is_readable_for_real_horizons[30] | pass | 0.049 |
| econ-forecast.tests.forecasting.test_presentation.test_date_locator_is_readable_for_real_horizons[90] | pass | 0.039 |
| econ-forecast.tests.forecasting.test_presentation.test_inactive_title_and_invalid_arrays | pass | 0.046 |
| econ-forecast.tests.forecasting.test_presentation.test_write_charts_renders_png_and_skips_failures | pass | 0.071 |
| econ-forecast.tests.forecasting.test_presentation.test_thirty_day_chart_reserves_wrapped_readable_assumption_footer | pass | 0.057 |
| econ-forecast.tests.forecasting.test_presentation.test_report_uses_saved_totals_windows_failures_and_actual_metrics | pass | 0.001 |
| econ-forecast.tests.forecasting.test_presentation.test_report_labels_unavailable_output_metric | pass | 0.001 |
| econ-forecast.tests.forecasting.test_presentation.test_report_escapes_markdown_cells_and_keeps_inactive_aggregate_distinct | pass | 0.001 |
| econ-forecast.tests.forecasting.test_presentation.test_report_complete_qualified_scope_has_model_and_aggregate_totals | pass | 0.001 |
| econ-forecast.tests.forecasting.test_presentation.test_real_partial_run_reports_unavailable_totals_without_zero | pass | 0.023 |
| econ-forecast.tests.forecasting.test_request_config.test_month_offset_and_leap_year | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[True] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[1.0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request1] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request2] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_cli_help_explains_cutoff_calendar_horizon_and_validation_mode | pass | 0.001 |
| econ-forecast.tests.forecasting.test_runner_integration.test_scope_order_and_worker_count_preserve_actual_saved_arrays | pass | 0.387 |
| econ-forecast.tests.forecasting.test_runner_integration.test_end_to_end_single_path_axis_for_constant_and_varying[<lambda>0] | pass | 0.112 |
| econ-forecast.tests.forecasting.test_runner_integration.test_end_to_end_single_path_axis_for_constant_and_varying[<lambda>1] | pass | 0.118 |
| econ-forecast.tests.forecasting.test_runner_integration.test_late_report_failure_removes_owned_staging_directory | pass | 0.110 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-inactive] | pass | 0.110 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-insufficient_startup] | pass | 0.023 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-insufficient_history] | pass | 0.023 |
| econ-forecast.tests.forecasting.test_runtime.test_process_tree_peak_and_cpu_delta_survive_child_exit | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_failed_stage_is_timed_and_monitor_stops | pass | 0.010 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[-0.1] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[2] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[nan] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[inf] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_monitor_lifecycle_errors_are_explicit | pass | 0.010 |
| econ-forecast.tests.forecasting.test_test_reporting.test_junit_counts_and_index_preserve_pass_fail_skip_and_run_identity | pass | 0.001 |
| econ-forecast.tests.forecasting.test_test_reporting.test_index_can_record_a_report_failure_without_junit | pass | 0.000 |
| econ-forecast.tests.forecasting.test_test_reporting.test_failed_pytest_is_persisted_and_returned_to_shell | pass | 0.002 |
| econ-forecast.tests.forecasting.test_test_reporting.test_test_forecast_wrapper_help_is_side_effect_free | pass | 0.615 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap.test_team_override_and_unattributed_default_are_applied_per_contributor | pass | 0.001 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap.test_weekend_draws_are_independent_per_user_not_scope | pass | 0.000 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap.test_spike_and_model_mix_follow_same_sampled_weekday | pass | 0.000 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap.test_no_shared_weekday_is_reported_without_fabricated_paths | pass | 0.000 |
| econ-forecast.tests.synthetic.test_behavior.test_numerical_oracle_and_single_project_per_user_day | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_profile_largest_remainder_is_exact_and_seed_independent_of_usage | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_duration_extension_preserves_earlier_days_and_mapping_order | pass | 0.005 |
| econ-forecast.tests.synthetic.test_behavior.test_cache_boundaries_conserve_processed_input | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_weekend_reduction_preserves_activity_and_complete_coverage | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_weekend_reduction_is_drawn_per_user_before_project_aggregation | pass | 0.004 |
| econ-forecast.tests.synthetic.test_behavior.test_zero_period_keeps_zero_when_a_spike_rule_is_selected | pass | 0.000 |
| econ-forecast.tests.synthetic.test_behavior.test_overlapping_rules_are_additive_from_the_immutable_baseline | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_rules_never_apply_on_weekends | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_range_is_inclusive_and_still_excludes_weekends | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_team_and_technology_targets_intersect_while_agent_and_model_union | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_increment_uses_exact_baseline_proportions_with_integer_residue | pass | 0.000 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_rule_order_and_duration_extension_preserve_the_prefix | pass | 0.004 |
| econ-forecast.tests.synthetic.test_behavior.test_shared_contract_nulls_additive_fields_and_invalid_counts | pass | 0.000 |
| econ-forecast.tests.synthetic.test_core.test_allocation_exact_and_ties | pass | 0.000 |
| econ-forecast.tests.synthetic.test_core.test_shipped_configs_parse | pass | 0.004 |
| econ-forecast.tests.synthetic.test_core.test_boolean_count_and_duplicate_yaml_rejected | pass | 0.001 |
| econ-forecast.tests.synthetic.test_core.test_users_and_roster_are_deterministic | pass | 0.026 |
| econ-forecast.tests.synthetic.test_core.test_workflow_and_replay | pass | 1.818 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_identity_default_is_flat_everywhere | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_team_overrides_org_else_falls_back | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_flat_when_neither_org_nor_team_bound | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_agent_multiplies_or_flat | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_org_curve_scales_the_daily_baseline_exactly | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_team_curve_overrides_org_without_stacking | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_flat_agent_reproduces_the_configured_ratio_exactly | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_agent_curve_shifts_the_split_and_renormalizes | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_never_changes_coverage_or_activity_counts | pass | 0.011 |
| econ-forecast.tests.synthetic.test_curve_binding.test_all_bound_agents_at_level_zero_raises_clear_error | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_flat_curve_is_byte_identical_to_v1 | pass | 0.143 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name-2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name_x] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[n1:0123456789abcdef] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[Bad Name] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:short] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:0123456789ABCDEF] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[:0123456789abcdef] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:0123456789abcdef0] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_artifacts_path_resolves_relative_to_series_yaml | pass | 0.003 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_artifacts_path_absolute_stays_put | pass | 0.003 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_unknown_team | pass | 0.063 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_unknown_agent | pass | 0.062 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_non_directory_artifacts | pass | 0.007 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_missing_name_id | pass | 0.007 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_ambiguous_bare_name | pass | 0.124 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_accepts_bare_unique_name | pass | 0.064 |
| econ-forecast.tests.synthetic.test_curve_binding.test_bound_run_manifest_has_curve_section_and_snapshots_exist | pass | 0.094 |
| econ-forecast.tests.synthetic.test_curve_binding.test_series_days_equal_one_uses_the_first_anchor_level | pass | 0.086 |
| econ-forecast.tests.synthetic.test_curve_binding.test_bound_run_is_deterministic_on_repeat | pass | 0.132 |
| econ-forecast.tests.synthetic.test_interfaces.test_cli_help_and_invalid_arguments_do_no_io | pass | 1.189 |
| econ-forecast.tests.synthetic.test_interfaces.test_validate_only_emits_one_summary_and_writes_nothing | pass | 0.379 |
| econ-forecast.tests.synthetic.test_interfaces.test_reports_and_moved_series_replay | pass | 1.593 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path1] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path2] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path3] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path4] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path5] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path6] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path7] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path8] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_user_settings_are_rejected[count] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_user_settings_are_rejected[locale] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_team_activity_requires_complete_values | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_one_day_event_needs_explicit_end_date | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path0-True] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path1-1.0] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path2-1] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path3-True] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path4-nan] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path5-100] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path6-inf] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path7-.03] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path8-1.01] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path9-0] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path10-True] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path11-1.5] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path12-0] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path13-100000000000000000000000000000000000000000000000000000000… | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path14-0] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path15-2026-07-01T00:00:00] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path16-../zone] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path17-value17] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path18-value18] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path19-value19] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path20--0.4] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path21-0.600000005] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path22-subscription] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path23-value23] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path24-value24] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path25-value25] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path26-value26] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path27-value27] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path28-value28] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path29-value29] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_share_normalization_is_order_independent_and_zero_preserving | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_duplicate_projects_and_rule_ids_are_rejected_but_overlaps_are_allowed | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets0] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets1] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets2] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets3] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets4] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets5] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_unknown_spike_team_is_rejected_against_the_roster | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_all_inactive_report_and_manifest_are_complete | pass | 0.010 |
| econ-forecast.tests.synthetic.test_regressions.test_weekend_reduction_preserves_usage_but_excludes_spikes | pass | 0.012 |
| econ-forecast.tests.synthetic.test_regressions.test_streams_write_each_day_before_requesting_the_next | pass | 0.013 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[report] | pass | 0.012 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[manifest] | pass | 0.013 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[stream] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[snapshot] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_existing_output_and_dangling_symlink_are_not_modified | pass | 0.006 |
| econ-forecast.tests.synthetic.test_regressions.test_input_corruption_and_record_error_dont_leak_payload | pass | 0.006 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_roster_legacy_manual_id_rejected_during_validation | pass | 0.946 |
| econ-forecast.tests.synthetic.test_regressions.test_logging_has_consistent_run_ids_modules_and_no_payload | pass | 0.486 |
| econ-forecast.tests.synthetic.test_regressions.test_large_token_boundary_and_overflow_are_explicit | pass | 0.017 |
| econ-forecast.tests.synthetic.test_regressions.test_fake_email_collisions_empty_name_fallback_and_length | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_projects_random_mix_coverage_and_profile_independence | pass | 0.044 |
| econ-forecast.tests.synthetic.test_regressions.test_hash_seed_does_not_change_streams | pass | 0.056 |
| econ-forecast.tests.synthetic.test_regressions.test_project_probability_sanity | pass | 0.003 |
| econ-forecast.tests.synthetic.test_regressions.test_shared_record_rejects_numeric_or_timestamp_date | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_cli_config_errors_are_actionable_without_echoing_input | pass | 0.486 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0-1] | pass | 0.010 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0.8-1] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0.8-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-1-1] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-1-1000000] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0.8-1] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0.8-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-1-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-1-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0.8-1] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0.8-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-1-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-1-1000000] | pass | 0.008 |
| econ-forecast.tests.utils.test_actuals.test_auto_actuals_match_an_independent_build_histories_call | pass | 0.219 |
| econ-forecast.tests.utils.test_actuals.test_dataset_override_bypasses_the_recorded_location | pass | 0.187 |
| econ-forecast.tests.utils.test_actuals.test_hash_mismatch_is_noted_in_auto_mode_only | pass | 0.186 |
| econ-forecast.tests.utils.test_actuals.test_missing_dataset_directory_is_unavailable_not_raised | pass | 0.188 |
| econ-forecast.tests.utils.test_actuals.test_run_without_recorded_input_is_unavailable | pass | 0.008 |
| econ-forecast.tests.utils.test_plot_config.test_paths_resolve_relative_to_the_yaml_file | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_absolute_paths_are_kept | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_forecast_data_dataset_resolves_relative_to_yaml | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_config_default_applies_unless_entry_overrides | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc0] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc1] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc2] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc3] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc4] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc5] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc6] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc7] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc8] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc9] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc10] | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_duplicate_yaml_keys_are_rejected | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_slug_convention | pass | 0.000 |
| econ-forecast.tests.utils.test_plot_config.test_duplicate_slugs_are_rejected_before_anything_is_written | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_config.test_band_ordering_and_bounds | pass | 0.000 |
| econ-forecast.tests.utils.test_plot_forecast.test_hand_oracle_and_model_org_separation | pass | 0.029 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[0-0] | pass | 0.009 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[1-1] | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[10-3] | pass | 0.057 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[99-3] | pass | 0.011 |
| econ-forecast.tests.utils.test_plot_forecast.test_partial_and_inactive_are_explicit | pass | 0.014 |
| econ-forecast.tests.utils.test_plot_forecast.test_invalid_selections_and_axes | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_forecast.test_organization_cli_omission_resolves_to_org_id | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_name_selection_rejects_unknown_and_ambiguous_saved_names | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_band_is_configurable | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_actuals_overlay_draws_observed_line_and_seam | pass | 0.011 |
| econ-forecast.tests.utils.test_plot_forecast.test_actuals_source_without_matching_series_is_noted | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_omitted_project_name_plots_each_project_without_aggregation | pass | 0.044 |
| econ-forecast.tests.utils.test_plot_forecast.test_repeatable_and_source_preserved | pass | 0.047 |
| econ-forecast.tests.utils.test_plot_forecast.test_matplotlib_figure_matches_plotly_content | pass | 0.014 |
| econ-forecast.tests.utils.test_plot_forecast.test_matplotlib_figure_draws_actuals_and_seam | pass | 0.005 |
| econ-forecast.tests.utils.test_plot_forecast.test_export_png_writes_a_valid_file_and_refuses_overwrite | pass | 0.073 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_help_outside_repository_reads_no_run | pass | 0.592 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_missing_required_and_mutual_exclusion | pass | 0.813 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_bad_band_and_missing_team_scope_name_are_rejected_before_writing | pass | 0.790 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_single_plot_creates_output_dir_with_index_and_result | pass | 0.679 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_format_flag_produces_only_the_requested_file | pass | 0.363 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_output_directory_must_be_empty | pass | 0.252 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_corrupt_artifact_is_a_structural_failure | pass | 0.256 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_all_selected_entries_failing_is_partial_batch_not_a_crash | pass | 0.275 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_project_name_omission_plots_all_available_projects | pass | 1.292 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_project_duplicate_names_are_identifiable_and_all_failed_writes_no_files | pass | 1.009 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_hostile_scope_name_is_escaped_in_html_and_slugified_safely | pass | 0.618 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_config_mode_resilient_batch_and_output_contract | pass | 0.370 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_config_with_slug_collision_is_rejected_before_writing | pass | 0.250 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_unreadable_config_is_a_structural_failure | pass | 0.248 |

Reproduce:

```bash
venv/bin/python -m pytest econ-forecast/tests --ignore=econ-forecast/tests/utils/test_plot_forecast_browser.py --junitxml=/Users/sumandra/projects/talium/forecasting/code-econ.wt/plot-forecast-enhancements/econ-forecast/docs/test-results/runs/20260923T050533Z_40a24450/junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Econ Forecast" \
    --junit /Users/sumandra/projects/talium/forecasting/code-econ.wt/plot-forecast-enhancements/econ-forecast/docs/test-results/runs/20260923T050533Z_40a24450/junit.xml \
    --out   /Users/sumandra/projects/talium/forecasting/code-econ.wt/plot-forecast-enhancements/econ-forecast/docs/test-results/runs/20260923T050533Z_40a24450/report.md \
    --tests 'econ-forecast/tests --ignore=econ-forecast/tests/utils/test_plot_forecast_browser.py'
```

Run ID: `40a24450-cce8-4d45-8a63-d573fa893dc1`. Completed UTC: `2026-09-23T05:06:41.007034+00:00`.

Each test name states its purpose; bracketed suffixes record parameterized cases. The source test contains exact fixtures, inputs, and assertions.

Overall status is `failed`: 4 browser tests were excluded from this run and are unverified, not passed or skipped.
