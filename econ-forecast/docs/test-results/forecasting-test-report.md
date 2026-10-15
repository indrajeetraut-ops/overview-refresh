# Econ Forecast — Test Report

Run: `2026-09-19T16:30:51.212128+05:30`

- Total: 285  Passed: 285  Failed: 0  Skipped: 0
- Wall time (sum of cases): 31.91s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| econ-forecast.tests.acceptance.test_forecast_acceptance | 46 | 46 | 0 | 0 | 1.33 |
| econ-forecast.tests.acceptance.test_forecast_resources | 1 | 1 | 0 | 0 | 19.16 |
| econ-forecast.tests.acceptance.test_forecast_workflow | 3 | 3 | 0 | 0 | 1.27 |
| econ-forecast.tests.acceptance.test_history_allocation_contract | 19 | 19 | 0 | 0 | 0.04 |
| econ-forecast.tests.acceptance.test_input_failure_contract | 6 | 6 | 0 | 0 | 0.06 |
| econ-forecast.tests.acceptance.test_scope_contract | 20 | 20 | 0 | 0 | 2.07 |
| econ-forecast.tests.acceptance.test_user_reduction_contract | 4 | 4 | 0 | 0 | 0.00 |
| econ-forecast.tests.acceptance.test_weekday_config_contract | 18 | 18 | 0 | 0 | 0.27 |
| econ-forecast.tests.forecasting.test_core_numerics | 5 | 5 | 0 | 0 | 0.01 |
| econ-forecast.tests.forecasting.test_lightweight_import | 2 | 2 | 0 | 0 | 0.23 |
| econ-forecast.tests.forecasting.test_presentation | 11 | 11 | 0 | 0 | 0.24 |
| econ-forecast.tests.forecasting.test_request_config | 8 | 8 | 0 | 0 | 0.00 |
| econ-forecast.tests.forecasting.test_runner_integration | 7 | 7 | 0 | 0 | 0.93 |
| econ-forecast.tests.forecasting.test_runtime | 8 | 8 | 0 | 0 | 0.03 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap | 4 | 4 | 0 | 0 | 0.00 |
| econ-forecast.tests.synthetic.test_behavior | 9 | 9 | 0 | 0 | 0.01 |
| econ-forecast.tests.synthetic.test_core | 5 | 5 | 0 | 0 | 1.81 |
| econ-forecast.tests.synthetic.test_interfaces | 3 | 3 | 0 | 0 | 1.19 |
| econ-forecast.tests.synthetic.test_regressions | 88 | 88 | 0 | 0 | 0.88 |
| econ-forecast.tests.utils.test_plot_forecast | 11 | 11 | 0 | 0 | 0.23 |
| econ-forecast.tests.utils.test_plot_forecast_cli | 7 | 7 | 0 | 0 | 2.15 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request0-start0-end0-8] | pass | 0.016 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request1-start1-end1-22] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request2-start2-end2-107] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[offset-0] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[offset-True] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[periods-0] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[periods-1.0] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-lookback_days-True] | pass | 0.018 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-minimum_days-13] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-minimum_active_days-0] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[allocation-lookback_days-False] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-paths-0] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-paths-10001] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-seed--1] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_probability-nan] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_probability-1.1] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_factor-inf] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_factor-0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[weekend-reduction_factor-True] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[runtime-workers-True] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[runtime-workers-5] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_relative_dataset_path_and_unknown_key | pass | 0.003 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_run_forecast_hand_oracle_and_artifact_conservation | pass | 0.313 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_validate_only_preserves_inputs_and_writes_nothing | pass | 0.020 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_roundtrip_and_foreign_entry_rejection | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[checksum] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[traversal] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[nonfinite] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[lying_scope] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[bool_version] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[bool_samples] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[unhashable_identity] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[start_date] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[end_date] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[timezone] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_impossible_team_attribution_is_rejected | pass | 0.002 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_future_rows_cannot_change_paths_or_allocation | pass | 0.228 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-inactive-inactive] | pass | 0.108 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-insufficient_startup-incomplete] | pass | 0.030 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-insufficient_history-incomplete] | pass | 0.031 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_future_startup_evidence_is_ignored_at_cutoff | pass | 0.114 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_single_path_preserves_two_dimensional_saved_axis[<lambda>0] | pass | 0.138 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_single_path_preserves_two_dimensional_saved_axis[<lambda>1] | pass | 0.115 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_nonzero_usage_on_outage_prevents_false_all_inactive | pass | 0.027 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_late_artifact_write_failure_leaves_no_published_run | pass | 0.104 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_shared_sampling_excludes_weekdays_outside_model_overlap | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_resources.test_frozen_reference_resource_benchmark | pass | 19.156 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_forecast_wrapper_help_outside_repo_is_side_effect_free | pass | 0.175 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_real_forecast_and_plot_wrappers_from_spaced_cwd | pass | 0.942 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_wrapper_invalid_input_has_machine_readable_channels | pass | 0.158 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist01_counts_distinct_dates_not_rows | pass | 0.005 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[10-21] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[25-6] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[31-0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist03_fourteen_days_cross_month_boundary | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_first_x3_streak_and_x1_x7_calendar_rules | pass | 0.004 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[True] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[91] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist05_daily_positivity_aggregates_split_agents | pass | 0.000 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist05_scope_models_are_checked_independently | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist06_all_zero_floor_is_inactive_but_subminimum_fails | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist07_qualification_persists_through_taper_retirement_and_restart | pass | 0.005 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist08_leading_zeros_then_seven_vs_fourteen_days_from_start | pass | 0.003 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist09_recent_zero_window_vs_one_positive | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc01_one_million_hand_oracle | pass | 0.000 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc02_token_weighted_not_daily_percentage_average | pass | 0.001 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc04_recent_window_excludes_old_outage_and_future | pass | 0.002 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc05_fixed_joint_ratios_conserve_every_day_path_including_zero | pass | 0.000 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[0] | pass | 0.002 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[1] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[2] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_origin_outside_declared_dataset_interval_is_invalid[origin0] | pass | 0.020 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_origin_outside_declared_dataset_interval_is_invalid[origin1] | pass | 0.019 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_existing_output_directory_and_sentinel_are_byte_preserved | pass | 0.018 |
| econ-forecast.tests.acceptance.test_scope_contract.test_org_team_project_share_rows_without_double_counting | pass | 0.266 |
| econ-forecast.tests.acceptance.test_scope_contract.test_unattributed_usage_stays_only_in_organization | pass | 0.255 |
| econ-forecast.tests.acceptance.test_scope_contract.test_null_user_stream_uses_default_weekend_rule_as_one_contributor | pass | 0.212 |
| econ-forecast.tests.acceptance.test_scope_contract.test_two_teams_may_each_have_a_null_user_stream | pass | 0.197 |
| econ-forecast.tests.acceptance.test_scope_contract.test_default_org_override_and_same_team_id_do_not_mix | pass | 0.195 |
| econ-forecast.tests.acceptance.test_scope_contract.test_unsupported_billing_is_rejected_not_omitted | pass | 0.022 |
| econ-forecast.tests.acceptance.test_scope_contract.test_catalog_model_without_scope_population_is_explicitly_not_applicable | pass | 0.121 |
| econ-forecast.tests.acceptance.test_scope_contract.test_omitted_origin_uses_declared_covered_zero_and_explicit_cutoff | pass | 0.040 |
| econ-forecast.tests.acceptance.test_scope_contract.test_omitted_origin_keeps_final_outage_ineligible | pass | 0.032 |
| econ-forecast.tests.acceptance.test_scope_contract.test_names_resolve_saved_ids_and_reject_ambiguity_within_organization | pass | 0.061 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses0-expected0] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses1-expected1] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses2-expected2] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses3-expected3] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses4-expected4] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses5-expected5] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[conditional-conditional-True-complete] | pass | 0.190 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[inactive-inactive-False-complete] | pass | 0.176 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[insufficient-incomplete-False-partial] | pass | 0.109 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_period_quantiles_sum_matching_sample_columns | pass | 0.198 |
| econ-forecast.tests.acceptance.test_user_reduction_contract.test_probability_boundaries_apply_to_users_before_scope_sum | pass | 0.000 |
| econ-forecast.tests.acceptance.test_user_reduction_contract.test_partial_probability_has_independent_user_outcomes | pass | 0.000 |
| econ-forecast.tests.acceptance.test_user_reduction_contract.test_one_user_draw_scales_all_models_consistently | pass | 0.000 |
| econ-forecast.tests.acceptance.test_user_reduction_contract.test_known_user_draw_is_reused_in_overlapping_scope_views | pass | 0.000 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_old_ets_method_is_rejected | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_old_negative_correction_setting_is_rejected | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[weekend-reduction_probability] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[weekend-reduction_factor] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[weekend-team_overrides] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[history-minimum_active_days] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_missing_forecast_setting_is_rejected[uncertainty-paths] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_probability--0.01] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_probability-1.01] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_probability-nan] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_probability-True] | pass | 0.005 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_factor-0] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_factor-1.01] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_factor-inf] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_weekend_rule_rejects_invalid_values[reduction_factor-0.25] | pass | 0.003 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_team_override_requires_explicit_probability_and_factor | pass | 0.005 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_unknown_team_override_fails_before_output | pass | 0.004 |
| econ-forecast.tests.acceptance.test_weekday_config_contract.test_huge_numeric_weekend_value_has_structured_cli_input_error | pass | 0.200 |
| econ-forecast.tests.forecasting.test_core_numerics.test_constant_source_preserves_single_path_axis_and_positive_reduction | pass | 0.001 |
| econ-forecast.tests.forecasting.test_core_numerics.test_zero_baseline_stays_zero_without_fabricated_activity | pass | 0.000 |
| econ-forecast.tests.forecasting.test_core_numerics.test_seed_replays_weekend_draws_and_different_seed_changes_them | pass | 0.004 |
| econ-forecast.tests.forecasting.test_core_numerics.test_overflow_rejects_nonfinite_result_without_clipping | pass | 0.001 |
| econ-forecast.tests.forecasting.test_core_numerics.test_allocation_is_token_weighted_joint_agent_category_share | pass | 0.001 |
| econ-forecast.tests.forecasting.test_lightweight_import.test_artifact_reader_import_is_independent_of_runner_and_statsmodels | pass | 0.077 |
| econ-forecast.tests.forecasting.test_lightweight_import.test_lazy_public_runner_api_remains_available | pass | 0.156 |
| econ-forecast.tests.forecasting.test_presentation.test_build_figure_artists_match_independent_oracle | pass | 0.005 |
| econ-forecast.tests.forecasting.test_presentation.test_date_locator_is_readable_for_real_horizons[30] | pass | 0.045 |
| econ-forecast.tests.forecasting.test_presentation.test_date_locator_is_readable_for_real_horizons[90] | pass | 0.037 |
| econ-forecast.tests.forecasting.test_presentation.test_inactive_title_and_invalid_arrays | pass | 0.003 |
| econ-forecast.tests.forecasting.test_presentation.test_write_charts_renders_png_and_skips_failures | pass | 0.054 |
| econ-forecast.tests.forecasting.test_presentation.test_thirty_day_chart_reserves_wrapped_readable_assumption_footer | pass | 0.061 |
| econ-forecast.tests.forecasting.test_presentation.test_report_uses_saved_totals_windows_failures_and_actual_metrics | pass | 0.001 |
| econ-forecast.tests.forecasting.test_presentation.test_report_labels_unavailable_output_metric | pass | 0.001 |
| econ-forecast.tests.forecasting.test_presentation.test_report_escapes_markdown_cells_and_keeps_inactive_aggregate_distinct | pass | 0.001 |
| econ-forecast.tests.forecasting.test_presentation.test_report_complete_qualified_scope_has_model_and_aggregate_totals | pass | 0.001 |
| econ-forecast.tests.forecasting.test_presentation.test_real_partial_run_reports_unavailable_totals_without_zero | pass | 0.030 |
| econ-forecast.tests.forecasting.test_request_config.test_month_offset_and_leap_year | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[True] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[1.0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request1] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request2] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_cli_help_explains_cutoff_calendar_horizon_and_validation_mode | pass | 0.001 |
| econ-forecast.tests.forecasting.test_runner_integration.test_scope_order_and_worker_count_preserve_actual_saved_arrays | pass | 0.381 |
| econ-forecast.tests.forecasting.test_runner_integration.test_end_to_end_single_path_axis_for_constant_and_varying[<lambda>0] | pass | 0.119 |
| econ-forecast.tests.forecasting.test_runner_integration.test_end_to_end_single_path_axis_for_constant_and_varying[<lambda>1] | pass | 0.120 |
| econ-forecast.tests.forecasting.test_runner_integration.test_late_report_failure_removes_owned_staging_directory | pass | 0.112 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-inactive] | pass | 0.126 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-insufficient_startup] | pass | 0.036 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-insufficient_history] | pass | 0.033 |
| econ-forecast.tests.forecasting.test_runtime.test_process_tree_peak_and_cpu_delta_survive_child_exit | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_failed_stage_is_timed_and_monitor_stops | pass | 0.017 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[-0.1] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[2] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[nan] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[inf] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_monitor_lifecycle_errors_are_explicit | pass | 0.017 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap.test_team_override_and_unattributed_default_are_applied_per_contributor | pass | 0.000 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap.test_weekend_draws_are_independent_per_user_not_scope | pass | 0.000 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap.test_spike_and_model_mix_follow_same_sampled_weekday | pass | 0.000 |
| econ-forecast.tests.forecasting.test_weekday_bootstrap.test_no_shared_weekday_is_reported_without_fabricated_paths | pass | 0.000 |
| econ-forecast.tests.synthetic.test_behavior.test_numerical_oracle_and_single_project_per_user_day | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_profile_largest_remainder_is_exact_and_seed_independent_of_usage | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_duration_extension_preserves_earlier_days_and_mapping_order | pass | 0.005 |
| econ-forecast.tests.synthetic.test_behavior.test_cache_boundaries_conserve_processed_input | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_weekend_reduction_preserves_activity_and_complete_coverage | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_weekend_reduction_is_drawn_per_user_before_project_aggregation | pass | 0.004 |
| econ-forecast.tests.synthetic.test_behavior.test_zero_period_dominates_dated_spike | pass | 0.000 |
| econ-forecast.tests.synthetic.test_behavior.test_user_spike_precedes_team_and_org_and_replaces_random | pass | 0.000 |
| econ-forecast.tests.synthetic.test_behavior.test_shared_contract_nulls_additive_fields_and_invalid_counts | pass | 0.000 |
| econ-forecast.tests.synthetic.test_core.test_allocation_exact_and_ties | pass | 0.000 |
| econ-forecast.tests.synthetic.test_core.test_shipped_configs_parse | pass | 0.004 |
| econ-forecast.tests.synthetic.test_core.test_boolean_count_and_duplicate_yaml_rejected | pass | 0.001 |
| econ-forecast.tests.synthetic.test_core.test_users_and_roster_are_deterministic | pass | 0.024 |
| econ-forecast.tests.synthetic.test_core.test_workflow_and_replay | pass | 1.784 |
| econ-forecast.tests.synthetic.test_interfaces.test_cli_help_and_invalid_arguments_do_no_io | pass | 0.429 |
| econ-forecast.tests.synthetic.test_interfaces.test_validate_only_emits_one_summary_and_writes_nothing | pass | 0.140 |
| econ-forecast.tests.synthetic.test_interfaces.test_reports_and_moved_series_replay | pass | 0.616 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path0] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path1] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path2] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path3] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path4] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path5] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path6] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path7] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path8] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path9] | pass | 0.000 |
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
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path30-value30] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_share_normalization_is_order_independent_and_zero_preserving | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_normalized_duplicate_projects_and_overlapping_events | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_all_inactive_report_and_manifest_are_complete | pass | 0.011 |
| econ-forecast.tests.synthetic.test_regressions.test_weekend_reduction_preserves_spikes_and_usage | pass | 0.012 |
| econ-forecast.tests.synthetic.test_regressions.test_streams_write_each_day_before_requesting_the_next | pass | 0.012 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[report] | pass | 0.012 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[manifest] | pass | 0.013 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[stream] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[snapshot] | pass | 0.006 |
| econ-forecast.tests.synthetic.test_regressions.test_existing_output_and_dangling_symlink_are_not_modified | pass | 0.006 |
| econ-forecast.tests.synthetic.test_regressions.test_input_corruption_and_record_error_dont_leak_payload | pass | 0.006 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_roster_legacy_manual_id_rejected_during_validation | pass | 0.177 |
| econ-forecast.tests.synthetic.test_regressions.test_logging_has_consistent_run_ids_modules_and_no_payload | pass | 0.185 |
| econ-forecast.tests.synthetic.test_regressions.test_large_token_boundary_and_overflow_are_explicit | pass | 0.013 |
| econ-forecast.tests.synthetic.test_regressions.test_fake_email_collisions_empty_name_fallback_and_length | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_projects_random_mix_coverage_and_profile_independence | pass | 0.046 |
| econ-forecast.tests.synthetic.test_regressions.test_hash_seed_does_not_change_streams | pass | 0.052 |
| econ-forecast.tests.synthetic.test_regressions.test_project_probability_sanity | pass | 0.003 |
| econ-forecast.tests.synthetic.test_regressions.test_shared_record_rejects_numeric_or_timestamp_date | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_cli_config_errors_are_actionable_without_echoing_input | pass | 0.171 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0-1] | pass | 0.010 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0.8-1] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0.8-1000000] | pass | 0.009 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-1-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-1-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0-1] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0.8-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0.8-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-1-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-1-1000000] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0.8-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0.8-1000000] | pass | 0.008 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-1-1] | pass | 0.007 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-1-1000000] | pass | 0.008 |
| econ-forecast.tests.utils.test_plot_forecast.test_hand_oracle_and_model_org_separation | pass | 0.074 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[0-0] | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[1-1] | pass | 0.009 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[10-3] | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[99-3] | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_partial_and_inactive_are_explicit | pass | 0.013 |
| econ-forecast.tests.utils.test_plot_forecast.test_invalid_selections_and_axes | pass | 0.001 |
| econ-forecast.tests.utils.test_plot_forecast.test_organization_cli_omission_resolves_to_org_id | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_name_selection_rejects_unknown_and_ambiguous_saved_names | pass | 0.009 |
| econ-forecast.tests.utils.test_plot_forecast.test_omitted_project_name_plots_each_project_without_aggregation | pass | 0.040 |
| econ-forecast.tests.utils.test_plot_forecast.test_repeatable_and_source_preserved | pass | 0.040 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_help_outside_repository_reads_no_run | pass | 0.188 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_real_wrapper_json_channels_and_relative_paths | pass | 0.217 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_partial_exit_invalid_and_no_overwrite | pass | 0.379 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_project_name_omission_plots_all_available_projects | pass | 0.430 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_project_duplicate_names_are_identifiable_and_all_failed_writes_nothing | pass | 0.326 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_no_chart_when_unavailable_only_and_write_failure | pass | 0.329 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_corrupt_artifact_and_output_inside_run_are_rejected | pass | 0.284 |

Reproduce:

```bash
venv/bin/python -m pytest econ-forecast/tests --ignore=econ-forecast/tests/utils/test_plot_forecast_browser.py --junitxml=econ-forecast/docs/test-results/forecasting-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Econ Forecast" \
    --junit econ-forecast/docs/test-results/forecasting-junit.xml \
    --out   econ-forecast/docs/test-results/forecasting-test-report.md \
    --tests 'econ-forecast/tests --ignore=econ-forecast/tests/utils/test_plot_forecast_browser.py'
```

Each test name states its purpose; bracketed suffixes record parameterized cases. The source test contains the exact fixtures, inputs, and assertions.

Overall result applies to the 285 executed non-browser tests. Four Plotly browser interaction tests were not executed because Playwright Chromium is unavailable; they are unverified, not passed or skipped.
