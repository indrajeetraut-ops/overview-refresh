# Plot / pipeline (#78) — Test Report

Run: `2026-09-28T09:01:36.774950+05:30`

- Total: 655  Passed: 655  Failed: 0  Skipped: 0
- Wall time (sum of cases): 176.84s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| econ-forecast.tests.acceptance.test_force_recreate | 5 | 5 | 0 | 0 | 5.55 |
| econ-forecast.tests.acceptance.test_forecast_acceptance | 47 | 47 | 0 | 0 | 2.83 |
| econ-forecast.tests.acceptance.test_forecast_resources | 1 | 1 | 0 | 0 | 33.10 |
| econ-forecast.tests.acceptance.test_forecast_workflow | 3 | 3 | 0 | 0 | 11.73 |
| econ-forecast.tests.acceptance.test_history_allocation_contract | 19 | 19 | 0 | 0 | 0.13 |
| econ-forecast.tests.acceptance.test_input_failure_contract | 6 | 6 | 0 | 0 | 0.11 |
| econ-forecast.tests.acceptance.test_known_events_config_contract | 14 | 14 | 0 | 0 | 0.13 |
| econ-forecast.tests.acceptance.test_known_events_overlay | 4 | 4 | 0 | 0 | 1.77 |
| econ-forecast.tests.acceptance.test_scope_contract | 19 | 19 | 0 | 0 | 2.36 |
| econ-forecast.tests.cost.test_artifacts | 8 | 8 | 0 | 0 | 0.39 |
| econ-forecast.tests.cost.test_cli | 11 | 11 | 0 | 0 | 6.90 |
| econ-forecast.tests.cost.test_kernel | 6 | 6 | 0 | 0 | 0.01 |
| econ-forecast.tests.cost.test_mix | 9 | 9 | 0 | 0 | 0.01 |
| econ-forecast.tests.cost.test_presentation | 8 | 8 | 0 | 0 | 0.46 |
| econ-forecast.tests.cost.test_rates | 7 | 7 | 0 | 0 | 0.01 |
| econ-forecast.tests.cost.test_request | 15 | 15 | 0 | 0 | 7.68 |
| econ-forecast.tests.cost.test_scenario | 6 | 6 | 0 | 0 | 0.01 |
| econ-forecast.tests.cost.test_schemas | 12 | 12 | 0 | 0 | 0.00 |
| econ-forecast.tests.cost.test_summaries | 4 | 4 | 0 | 0 | 0.00 |
| econ-forecast.tests.curve.test_artifacts | 26 | 26 | 0 | 0 | 3.15 |
| econ-forecast.tests.curve.test_cli | 10 | 10 | 0 | 0 | 23.98 |
| econ-forecast.tests.curve.test_config | 19 | 19 | 0 | 0 | 0.18 |
| econ-forecast.tests.curve.test_force | 5 | 5 | 0 | 0 | 5.03 |
| econ-forecast.tests.curve.test_model | 29 | 29 | 0 | 0 | 0.03 |
| econ-forecast.tests.curve.test_preview | 6 | 6 | 0 | 0 | 0.64 |
| econ-forecast.tests.curve.test_runner | 14 | 14 | 0 | 0 | 1.79 |
| econ-forecast.tests.forecasting.test_core_numerics | 13 | 13 | 0 | 0 | 0.12 |
| econ-forecast.tests.forecasting.test_known_events | 29 | 29 | 0 | 0 | 0.01 |
| econ-forecast.tests.forecasting.test_lightweight_import | 2 | 2 | 0 | 0 | 2.01 |
| econ-forecast.tests.forecasting.test_presentation | 10 | 10 | 0 | 0 | 0.43 |
| econ-forecast.tests.forecasting.test_request_config | 8 | 8 | 0 | 0 | 0.00 |
| econ-forecast.tests.forecasting.test_runner_integration | 15 | 15 | 0 | 0 | 2.93 |
| econ-forecast.tests.forecasting.test_runtime | 8 | 8 | 0 | 0 | 0.06 |
| econ-forecast.tests.forecasting.test_test_reporting | 4 | 4 | 0 | 0 | 0.30 |
| econ-forecast.tests.pipeline.test_pipeline | 12 | 12 | 0 | 0 | 8.15 |
| econ-forecast.tests.synthetic.test_behavior | 14 | 14 | 0 | 0 | 0.06 |
| econ-forecast.tests.synthetic.test_core | 5 | 5 | 0 | 0 | 4.41 |
| econ-forecast.tests.synthetic.test_curve_binding | 32 | 32 | 0 | 0 | 2.01 |
| econ-forecast.tests.synthetic.test_force | 6 | 6 | 0 | 0 | 2.11 |
| econ-forecast.tests.synthetic.test_interfaces | 3 | 3 | 0 | 0 | 7.37 |
| econ-forecast.tests.synthetic.test_regressions | 92 | 92 | 0 | 0 | 4.59 |
| econ-forecast.tests.utils.test_actuals | 6 | 6 | 0 | 0 | 2.88 |
| econ-forecast.tests.utils.test_plot_config | 31 | 31 | 0 | 0 | 0.10 |
| econ-forecast.tests.utils.test_plot_cost | 12 | 12 | 0 | 0 | 5.46 |
| econ-forecast.tests.utils.test_plot_force | 5 | 5 | 0 | 0 | 5.57 |
| econ-forecast.tests.utils.test_plot_forecast | 18 | 18 | 0 | 0 | 0.80 |
| econ-forecast.tests.utils.test_plot_forecast_cli | 17 | 17 | 0 | 0 | 19.49 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| econ-forecast.tests.acceptance.test_force_recreate.test_force_recreates_existing_output_directory | pass | 0.352 |
| econ-forecast.tests.acceptance.test_force_recreate.test_force_refuses_a_non_directory_target | pass | 0.037 |
| econ-forecast.tests.acceptance.test_force_recreate.test_force_confines_deletion_to_the_resolved_target | pass | 0.197 |
| econ-forecast.tests.acceptance.test_force_recreate.test_force_refuses_an_output_that_would_delete_the_input_dataset | pass | 0.039 |
| econ-forecast.tests.acceptance.test_force_recreate.test_force_cli_wrapper_recreates_existing_output | pass | 4.930 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request0-start0-end0-8] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request1-start1-end1-22] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_calendar_oracles[forecast_request2-start2-end2-107] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[offset-0] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[offset-True] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[periods-0] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_request_rejects_non_strict_positive_integers[periods-1.0] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-lookback_days-True] | pass | 0.009 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-minimum_days-13] | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[history-minimum_active_days-0] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[allocation-lookback_days-False] | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-paths-0] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-paths-10001] | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-seed--1] | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-max_negative_correction_ratio-nan] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-max_negative_correction_ratio-inf] | pass | 0.006 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-max_negative_correction_ratio--0.1] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-max_negative_correction_ratio-1.1] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[uncertainty-max_negative_correction_ratio-True] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[model-seasonal-weekly] | pass | 0.006 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[model-trend-damped] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[runtime-workers-True] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_type_range_and_nonfinite_rejection[runtime-workers-5] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_config_relative_dataset_path_and_unknown_key | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_run_forecast_hand_oracle_and_artifact_conservation | pass | 0.212 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_validate_only_preserves_inputs_and_writes_nothing | pass | 0.048 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_roundtrip_and_foreign_entry_rejection | pass | 0.064 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[checksum] | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[traversal] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_corrupt_or_unsafe_runs[nonfinite] | pass | 0.029 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[lying_scope] | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[bool_version] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[bool_samples] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_public_reader_rejects_semantically_misleading_index[unhashable_identity] | pass | 0.008 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[start_date] | pass | 0.005 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[end_date] | pass | 0.005 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_neutral_metadata_missing_required_fields_is_input_error[timezone] | pass | 0.005 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_impossible_team_attribution_is_rejected | pass | 0.007 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_future_rows_cannot_change_paths_or_allocation | pass | 0.707 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-inactive-inactive] | pass | 0.148 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-insufficient_startup-incomplete] | pass | 0.272 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_history_status_oracles[<lambda>-<lambda>-insufficient_history-incomplete] | pass | 0.076 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_future_startup_evidence_is_ignored_at_cutoff | pass | 0.185 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_single_path_preserves_two_dimensional_saved_axis[<lambda>0] | pass | 0.276 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_single_path_preserves_two_dimensional_saved_axis[<lambda>1] | pass | 0.394 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_nonzero_usage_on_outage_prevents_false_all_inactive | pass | 0.079 |
| econ-forecast.tests.acceptance.test_forecast_acceptance.test_late_artifact_write_failure_leaves_no_published_run | pass | 0.146 |
| econ-forecast.tests.acceptance.test_forecast_resources.test_frozen_reference_resource_benchmark | pass | 33.100 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_forecast_wrapper_help_outside_repo_is_side_effect_free | pass | 2.560 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_real_forecast_and_plot_wrappers_from_spaced_cwd | pass | 6.827 |
| econ-forecast.tests.acceptance.test_forecast_workflow.test_wrapper_invalid_input_has_machine_readable_channels | pass | 2.346 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist01_counts_distinct_dates_not_rows | pass | 0.015 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[10-21] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[25-6] | pass | 0.007 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist02_outage_truncates_continuous_suffix[31-0] | pass | 0.006 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist03_fourteen_days_cross_month_boundary | pass | 0.005 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_first_x3_streak_and_x1_x7_calendar_rules | pass | 0.011 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[0] | pass | 0.006 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[True] | pass | 0.006 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist04_active_day_type_and_bounds_rejected[91] | pass | 0.006 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist05_daily_positivity_aggregates_split_agents | pass | 0.001 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist05_scope_models_are_checked_independently | pass | 0.006 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist06_all_zero_floor_is_inactive_but_subminimum_fails | pass | 0.006 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist07_qualification_persists_through_taper_retirement_and_restart | pass | 0.015 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist08_leading_zeros_then_seven_vs_fourteen_days_from_start | pass | 0.011 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_hist09_recent_zero_window_vs_one_positive | pass | 0.008 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc01_one_million_hand_oracle | pass | 0.000 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc02_token_weighted_not_daily_percentage_average | pass | 0.004 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc04_recent_window_excludes_old_outage_and_future | pass | 0.005 |
| econ-forecast.tests.acceptance.test_history_allocation_contract.test_alloc05_fixed_joint_ratios_conserve_every_day_path_including_zero | pass | 0.001 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[0] | pass | 0.005 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[1] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_null_token_category_is_invalid_instead_of_zero[2] | pass | 0.004 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_origin_outside_declared_dataset_interval_is_invalid[origin0] | pass | 0.035 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_origin_outside_declared_dataset_interval_is_invalid[origin1] | pass | 0.036 |
| econ-forecast.tests.acceptance.test_input_failure_contract.test_existing_output_directory_and_sentinel_are_byte_preserved | pass | 0.031 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_known_events_is_optional_and_defaults_empty | pass | 0.006 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_well_formed_known_events_round_trip | pass | 0.011 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_end_before_start_is_rejected[2026-08-05-2026-08-01] | pass | 0.010 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_bad_multiplier_or_spread_is_rejected[-0.1-0.0] | pass | 0.009 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_bad_multiplier_or_spread_is_rejected[1.0--0.1] | pass | 0.010 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_bad_multiplier_or_spread_is_rejected[1.0-1.5] | pass | 0.010 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_non_finite_or_non_numeric_multiplier_is_rejected[True] | pass | 0.010 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_non_finite_or_non_numeric_multiplier_is_rejected[1.0] | pass | 0.010 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_non_finite_or_non_numeric_multiplier_is_rejected[nan] | pass | 0.009 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_non_finite_or_non_numeric_multiplier_is_rejected[inf] | pass | 0.009 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_malformed_date_string_is_rejected | pass | 0.009 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_empty_team_or_project_or_agent_name_is_rejected | pass | 0.009 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_unknown_known_events_field_is_rejected | pass | 0.009 |
| econ-forecast.tests.acceptance.test_known_events_config_contract.test_default_org_id_required_when_known_events_is_set | pass | 0.010 |
| econ-forecast.tests.acceptance.test_known_events_overlay.test_scope_precedence_and_agent_axis_end_to_end | pass | 0.609 |
| econ-forecast.tests.acceptance.test_known_events_overlay.test_determinism_same_seed_and_data_yields_identical_paths | pass | 1.053 |
| econ-forecast.tests.acceptance.test_known_events_overlay.test_validate_only_rejects_unknown_team_name_before_any_output | pass | 0.056 |
| econ-forecast.tests.acceptance.test_known_events_overlay.test_validate_only_rejects_event_window_outside_horizon | pass | 0.051 |
| econ-forecast.tests.acceptance.test_scope_contract.test_org_team_project_share_rows_without_double_counting | pass | 0.376 |
| econ-forecast.tests.acceptance.test_scope_contract.test_unattributed_usage_stays_only_in_organization | pass | 0.244 |
| econ-forecast.tests.acceptance.test_scope_contract.test_two_teams_may_each_have_a_null_user_stream | pass | 0.224 |
| econ-forecast.tests.acceptance.test_scope_contract.test_default_org_override_and_same_team_id_do_not_mix | pass | 0.222 |
| econ-forecast.tests.acceptance.test_scope_contract.test_unsupported_billing_is_rejected_not_omitted | pass | 0.043 |
| econ-forecast.tests.acceptance.test_scope_contract.test_catalog_model_without_scope_population_is_explicitly_not_applicable | pass | 0.232 |
| econ-forecast.tests.acceptance.test_scope_contract.test_omitted_origin_uses_declared_covered_zero_and_explicit_cutoff | pass | 0.085 |
| econ-forecast.tests.acceptance.test_scope_contract.test_omitted_origin_keeps_final_outage_ineligible | pass | 0.043 |
| econ-forecast.tests.acceptance.test_scope_contract.test_names_resolve_saved_ids_and_reject_ambiguity_within_organization | pass | 0.122 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses0-expected0] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses1-expected1] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses2-expected2] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses3-expected3] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses4-expected4] | pass | 0.000 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_status_truth_table[statuses5-expected5] | pass | 0.001 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[conditional-conditional-True-complete] | pass | 0.208 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[inactive-inactive-False-complete] | pass | 0.207 |
| econ-forecast.tests.acceptance.test_scope_contract.test_runner_publishes_scope_readiness_from_every_component[insufficient-incomplete-False-partial] | pass | 0.153 |
| econ-forecast.tests.acceptance.test_scope_contract.test_scope_period_quantiles_sum_matching_sample_columns | pass | 0.203 |
| econ-forecast.tests.cost.test_artifacts.test_write_and_load_round_trip | pass | 0.054 |
| econ-forecast.tests.cost.test_artifacts.test_tampered_file_is_rejected | pass | 0.028 |
| econ-forecast.tests.cost.test_artifacts.test_unknown_minor_schema_is_accepted | pass | 0.054 |
| econ-forecast.tests.cost.test_artifacts.test_unknown_major_schema_is_rejected | pass | 0.030 |
| econ-forecast.tests.cost.test_artifacts.test_slug_collision_gets_a_numbered_suffix | pass | 0.054 |
| econ-forecast.tests.cost.test_artifacts.test_borrowed_mix_series_gets_a_mix_json_entry | pass | 0.050 |
| econ-forecast.tests.cost.test_artifacts.test_report_renderer_hook_runs_but_is_not_listed_in_the_manifest | pass | 0.051 |
| econ-forecast.tests.cost.test_artifacts.test_reader_never_checks_report_md_present_absent_or_tampered | pass | 0.072 |
| econ-forecast.tests.cost.test_cli.test_help_does_no_io | pass | 0.441 |
| econ-forecast.tests.cost.test_cli.test_missing_required_arguments_exit_2 | pass | 0.439 |
| econ-forecast.tests.cost.test_cli.test_validate_only_writes_nothing_and_exits_0 | pass | 0.686 |
| econ-forecast.tests.cost.test_cli.test_validate_only_with_unmatched_model_exits_2 | pass | 0.687 |
| econ-forecast.tests.cost.test_cli.test_missing_forecast_run_exits_2 | pass | 0.442 |
| econ-forecast.tests.cost.test_cli.test_real_run_writes_output_and_exits_0 | pass | 0.749 |
| econ-forecast.tests.cost.test_cli.test_existing_output_is_refused_without_force | pass | 0.688 |
| econ-forecast.tests.cost.test_cli.test_force_recreates_only_the_target | pass | 1.293 |
| econ-forecast.tests.cost.test_cli.test_wrapper_script_runs | pass | 0.826 |
| econ-forecast.tests.cost.test_cli.test_execution_error_exits_1 | pass | 0.003 |
| econ-forecast.tests.cost.test_cli.test_run_cost_prices_a_scenario_python_api | pass | 0.645 |
| econ-forecast.tests.cost.test_kernel.test_design_example_day_prices_to_1_41 | pass | 0.002 |
| econ-forecast.tests.cost.test_kernel.test_inactive_series_prices_to_zero | pass | 0.001 |
| econ-forecast.tests.cost.test_kernel.test_unusable_forecast_status_passes_through_with_no_path | pass | 0.001 |
| econ-forecast.tests.cost.test_kernel.test_unmatched_model_yields_unpriced_status | pass | 0.001 |
| econ-forecast.tests.cost.test_kernel.test_scope_status_complete_conditional_incomplete | pass | 0.001 |
| econ-forecast.tests.cost.test_kernel.test_mix_override_prices_with_the_borrowed_shares_and_reports_the_source_window | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_design_example_4_active_days_10_percent_3_percent_blends_to_5_5_percent | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_ratio_of_weighted_sums_not_mean_of_daily_ratios | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_outage_day_is_excluded_from_both_sums | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_organization_has_no_parent_and_uses_its_own_mix | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_attribution_share_matches_design_example | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_attribution_share_is_zero_when_parent_denominator_is_zero | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_team_of_project_finds_the_unique_superset_team | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_team_of_project_is_none_when_undetermined_non_strict_omits_it | pass | 0.001 |
| econ-forecast.tests.cost.test_mix.test_required_team_of_project_raises_naming_the_project_when_undetermined | pass | 0.001 |
| econ-forecast.tests.cost.test_presentation.test_report_sections_present_in_order | pass | 0.054 |
| econ-forecast.tests.cost.test_presentation.test_unpriced_model_listed_with_reason_and_candidates | pass | 0.053 |
| econ-forecast.tests.cost.test_presentation.test_thin_evidence_mix_lists_team_series | pass | 0.055 |
| econ-forecast.tests.cost.test_presentation.test_render_report_no_longer_draws_charts_itself | pass | 0.054 |
| econ-forecast.tests.cost.test_presentation.test_charts_section_lists_exactly_the_charts_index_json_entries | pass | 0.056 |
| econ-forecast.tests.cost.test_presentation.test_scenario_section_only_for_scenario_runs | pass | 0.109 |
| econ-forecast.tests.cost.test_presentation.test_render_report_output_matches_output_dir_argument | pass | 0.074 |
| econ-forecast.tests.cost.test_presentation.test_scope_order_follows_the_scope_tree_and_status_is_derived_from_the_index | pass | 0.001 |
| econ-forecast.tests.cost.test_rates.test_design_example_blended_rate_is_1_281_per_million | pass | 0.001 |
| econ-forecast.tests.cost.test_rates.test_null_rate_with_nonzero_mix_share_is_unpriced | pass | 0.001 |
| econ-forecast.tests.cost.test_rates.test_null_rate_with_zero_mix_share_is_still_priced | pass | 0.001 |
| econ-forecast.tests.cost.test_rates.test_unmatched_model_is_unpriced_with_reason | pass | 0.001 |
| econ-forecast.tests.cost.test_rates.test_model_absent_from_sheet_is_unpriced | pass | 0.001 |
| econ-forecast.tests.cost.test_rates.test_price_change_mid_range_uses_the_segment_in_effect | pass | 0.001 |
| econ-forecast.tests.cost.test_rates.test_missing_catalog_is_execution_error | pass | 0.006 |
| econ-forecast.tests.cost.test_request.test_baseline_resolves | pass | 0.545 |
| econ-forecast.tests.cost.test_request.test_scenario_resolves | pass | 0.522 |
| econ-forecast.tests.cost.test_request.test_pre_t2_run_raises_input_error | pass | 0.029 |
| econ-forecast.tests.cost.test_request.test_dataset_hash_mismatch_raises_input_error | pass | 0.721 |
| econ-forecast.tests.cost.test_request.test_scenario_name_must_match_file_name | pass | 0.549 |
| econ-forecast.tests.cost.test_request.test_scenario_name_baseline_is_reserved | pass | 0.524 |
| econ-forecast.tests.cost.test_request.test_scenario_scope_must_exist_in_forecast_run | pass | 0.469 |
| econ-forecast.tests.cost.test_request.test_model_transfer_from_must_be_a_series_in_scope | pass | 0.518 |
| econ-forecast.tests.cost.test_request.test_model_transfer_to_must_be_matched_in_the_rate_sheet | pass | 0.523 |
| econ-forecast.tests.cost.test_request.test_model_transfer_fraction_out_of_range | pass | 0.526 |
| econ-forecast.tests.cost.test_request.test_agent_transfer_from_must_have_usage_in_scope | pass | 0.514 |
| econ-forecast.tests.cost.test_request.test_agent_transfer_to_must_have_usage_in_org | pass | 0.510 |
| econ-forecast.tests.cost.test_request.test_growth_factor_must_be_nonnegative | pass | 0.510 |
| econ-forecast.tests.cost.test_request.test_token_factor_must_be_positive | pass | 0.494 |
| econ-forecast.tests.cost.test_request.test_effective_from_must_fall_within_forecast_dates | pass | 0.724 |
| econ-forecast.tests.cost.test_scenario.test_design_example_40_percent_times_30_percent_equals_12_percent_at_org | pass | 0.002 |
| econ-forecast.tests.cost.test_scenario.test_token_factor_scales_destination_and_breaks_conservation | pass | 0.001 |
| econ-forecast.tests.cost.test_scenario.test_growth_propagates_by_attribution_share | pass | 0.001 |
| econ-forecast.tests.cost.test_scenario.test_agent_transfer_moves_between_models_via_org_level_split | pass | 0.001 |
| econ-forecast.tests.cost.test_scenario.test_declared_project_scope_requires_a_uniquely_determined_team | pass | 0.001 |
| econ-forecast.tests.cost.test_scenario.test_ambiguous_project_sub_scope_raises_during_team_declared_scenario | pass | 0.001 |
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
| econ-forecast.tests.cost.test_summaries.test_scope_p90_is_not_the_sum_of_series_p90s | pass | 0.001 |
| econ-forecast.tests.cost.test_summaries.test_incomplete_scope_gets_no_period_summary_entry | pass | 0.001 |
| econ-forecast.tests.cost.test_summaries.test_daily_expected_rows_split_tokens_and_usd_by_mix | pass | 0.000 |
| econ-forecast.tests.cost.test_summaries.test_daily_expected_rows_for_inactive_series_are_all_zero | pass | 0.000 |
| econ-forecast.tests.curve.test_artifacts.test_load_curve_artifact_returns_correct_fields | pass | 0.500 |
| econ-forecast.tests.curve.test_artifacts.test_missing_directory_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_missing_manifest_rejected | pass | 0.185 |
| econ-forecast.tests.curve.test_artifacts.test_manifest_replaced_by_symlink_rejected | pass | 0.157 |
| econ-forecast.tests.curve.test_artifacts.test_incomplete_status_rejected | pass | 0.154 |
| econ-forecast.tests.curve.test_artifacts.test_missing_config_sha256_rejected | pass | 0.145 |
| econ-forecast.tests.curve.test_artifacts.test_malformed_config_sha256_rejected[deadbeef] | pass | 0.149 |
| econ-forecast.tests.curve.test_artifacts.test_malformed_config_sha256_rejected[zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz] | pass | 0.146 |
| econ-forecast.tests.curve.test_artifacts.test_malformed_config_sha256_rejected[12345] | pass | 0.145 |
| econ-forecast.tests.curve.test_artifacts.test_tampered_config_yaml_rejected | pass | 0.155 |
| econ-forecast.tests.curve.test_artifacts.test_directory_id_mismatch_rejected | pass | 0.148 |
| econ-forecast.tests.curve.test_artifacts.test_directory_name_mismatch_rejected | pass | 0.164 |
| econ-forecast.tests.curve.test_artifacts.test_missing_config_yaml_rejected | pass | 0.159 |
| econ-forecast.tests.curve.test_artifacts.test_config_yaml_replaced_by_symlink_rejected | pass | 0.146 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_name_id_resolves | pass | 0.148 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bare_unique_name_resolves | pass | 0.155 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_missing_name_directory_rejected | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_zero_complete_rejected | pass | 0.002 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_ambiguous_rejected | pass | 0.331 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_ignores_incomplete_orphaned_sibling | pass | 0.149 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[Bad Name] | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[name:short] | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[name:0123456789ABCDEF] | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[] | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[:0123456789abcdef] | pass | 0.001 |
| econ-forecast.tests.curve.test_artifacts.test_resolve_curve_ref_bad_format_rejected[name:0123456789abcdef0] | pass | 0.001 |
| econ-forecast.tests.curve.test_cli.test_help_and_invalid_arguments_do_no_io | pass | 3.314 |
| econ-forecast.tests.curve.test_cli.test_validate_only_writes_nothing_and_hash_matches_a_real_run | pass | 2.302 |
| econ-forecast.tests.curve.test_cli.test_validate_only_with_preview_days_override_matches_real_run_hash | pass | 2.275 |
| econ-forecast.tests.curve.test_cli.test_exit_codes_success_config_error_and_artifact_error | pass | 4.546 |
| econ-forecast.tests.curve.test_cli.test_noop_path_reports_unchanged_and_exits_zero | pass | 2.277 |
| econ-forecast.tests.curve.test_cli.test_different_preview_days_create_different_directories_and_rerun_noops | pass | 3.830 |
| econ-forecast.tests.curve.test_cli.test_stdout_is_one_json_object_and_stderr_lines_are_all_json | pass | 1.504 |
| econ-forecast.tests.curve.test_cli.test_open_flag_exercises_the_browser_path_without_crashing | pass | 1.539 |
| econ-forecast.tests.curve.test_cli.test_wrapper_script_validate_only_smoke | pass | 0.854 |
| econ-forecast.tests.curve.test_cli.test_error_summary_shape_and_values | pass | 1.540 |
| econ-forecast.tests.curve.test_config.test_valid_config_round_trips_all_fields | pass | 0.007 |
| econ-forecast.tests.curve.test_config.test_default_preview_is_optional | pass | 0.004 |
| econ-forecast.tests.curve.test_config.test_bad_interp_rejected | pass | 0.004 |
| econ-forecast.tests.curve.test_config.test_fewer_than_two_anchors_rejected | pass | 0.003 |
| econ-forecast.tests.curve.test_config.test_non_increasing_positions_rejected | pass | 0.008 |
| econ-forecast.tests.curve.test_config.test_endpoint_violation_rejected | pass | 0.004 |
| econ-forecast.tests.curve.test_config.test_axis_bounds_violation_rejected | pass | 0.004 |
| econ-forecast.tests.curve.test_config.test_negative_level_walk_rejected | pass | 0.003 |
| econ-forecast.tests.curve.test_config.test_leading_delta_first_anchor_rejected | pass | 0.003 |
| econ-forecast.tests.curve.test_config.test_missing_name_rejected | pass | 0.003 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[] | pass | 0.003 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[ ] | pass | 0.110 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[has spaces] | pass | 0.005 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[has/slash] | pass | 0.004 |
| econ-forecast.tests.curve.test_config.test_invalid_name_characters_rejected[has:colon] | pass | 0.004 |
| econ-forecast.tests.curve.test_config.test_duplicate_yaml_keys_rejected | pass | 0.003 |
| econ-forecast.tests.curve.test_config.test_unquoted_plus_yaml_trap_rejected | pass | 0.003 |
| econ-forecast.tests.curve.test_config.test_missing_config_file_rejected | pass | 0.002 |
| econ-forecast.tests.curve.test_config.test_unknown_field_rejected | pass | 0.004 |
| econ-forecast.tests.curve.test_force.test_force_rebuilds_a_complete_matching_artifact | pass | 0.292 |
| econ-forecast.tests.curve.test_force.test_force_rebuilds_an_incomplete_orphaned_artifact | pass | 0.312 |
| econ-forecast.tests.curve.test_force.test_force_refuses_a_non_directory_target | pass | 0.004 |
| econ-forecast.tests.curve.test_force.test_force_confines_deletion_to_the_single_leaf | pass | 0.440 |
| econ-forecast.tests.curve.test_force.test_force_cli_flag_rebuilds_an_incomplete_artifact | pass | 3.981 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_absolute | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_delta_positive | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_delta_negative | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_the_yaml_unquoted_plus_trap | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[] | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[abc] | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[1.2.3] | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[++4] | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[4-] | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[1e5] | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[None] | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_parse_anchor_value_rejects_malformed_or_non_string[4.0] | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_normalizes_positions_and_accumulates_deltas | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_leading_delta_as_first_anchor | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_delta_chain_that_walks_negative | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_allows_exact_zero_floor | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_non_increasing_positions | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_endpoints_not_at_axis_bounds | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_axis_end_not_greater_than_start | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_fewer_than_two_anchors | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_resolve_anchors_rejects_non_integer_or_boolean_position | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_endpoints_equal_first_and_last_resolved_level | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_no_overshoot_stays_within_anchor_range | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_nonnegative_when_anchors_touch_zero_floor | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_stretch_exactness_across_sample_counts | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_n_equals_one_returns_level_at_position_zero | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_rejects_n_less_than_one | pass | 0.001 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_rejects_fewer_than_two_anchors | pass | 0.000 |
| econ-forecast.tests.curve.test_model.test_evaluate_curve_rejects_non_increasing_positions | pass | 0.000 |
| econ-forecast.tests.curve.test_preview.test_render_html_writes_a_readable_file_with_0644_permissions | pass | 0.045 |
| econ-forecast.tests.curve.test_preview.test_render_png_writes_a_valid_png | pass | 0.117 |
| econ-forecast.tests.curve.test_preview.test_render_html_and_png_succeed_on_both_axis_modes | pass | 0.275 |
| econ-forecast.tests.curve.test_preview.test_no_baseline_tokens_uses_dimensionless_axis_label | pass | 0.033 |
| econ-forecast.tests.curve.test_preview.test_baseline_tokens_uses_scaled_axis_label | pass | 0.034 |
| econ-forecast.tests.curve.test_preview.test_single_sample_point_does_not_crash | pass | 0.137 |
| econ-forecast.tests.curve.test_runner.test_config_hash_is_deterministic | pass | 0.003 |
| econ-forecast.tests.curve.test_runner.test_config_hash_is_sensitive_to_preview_days | pass | 0.001 |
| econ-forecast.tests.curve.test_runner.test_config_hash_is_sensitive_to_anchors_and_name | pass | 0.002 |
| econ-forecast.tests.curve.test_runner.test_curve_jsonl_matches_evaluate_curve_exactly | pass | 0.150 |
| econ-forecast.tests.curve.test_runner.test_default_sample_count_is_two_hundred_when_no_preview_days_set | pass | 0.154 |
| econ-forecast.tests.curve.test_runner.test_all_six_artifact_files_present | pass | 0.146 |
| econ-forecast.tests.curve.test_runner.test_manifest_written_last_with_complete_status_and_matching_hashes | pass | 0.145 |
| econ-forecast.tests.curve.test_runner.test_config_yaml_persists_the_resolved_config | pass | 0.155 |
| econ-forecast.tests.curve.test_runner.test_identical_config_rerun_returns_none_and_writes_nothing | pass | 0.148 |
| econ-forecast.tests.curve.test_runner.test_different_preview_days_create_different_directories | pass | 0.295 |
| econ-forecast.tests.curve.test_runner.test_incomplete_directory_missing_manifest_raises_artifact_error | pass | 0.150 |
| econ-forecast.tests.curve.test_runner.test_incomplete_directory_status_not_complete_raises_artifact_error | pass | 0.153 |
| econ-forecast.tests.curve.test_runner.test_incomplete_directory_unparseable_manifest_raises_artifact_error | pass | 0.142 |
| econ-forecast.tests.curve.test_runner.test_incomplete_directory_is_not_overwritten | pass | 0.145 |
| econ-forecast.tests.forecasting.test_core_numerics.test_short_history_forces_level_only_tier | pass | 0.004 |
| econ-forecast.tests.forecasting.test_core_numerics.test_long_history_prefers_seasonal_damped_trend_when_it_converges | pass | 0.030 |
| econ-forecast.tests.forecasting.test_core_numerics.test_seasonal_none_never_attempts_a_seasonal_configuration | pass | 0.004 |
| econ-forecast.tests.forecasting.test_core_numerics.test_trend_none_keeps_seasonal_only_tier_and_never_attempts_trend | pass | 0.016 |
| econ-forecast.tests.forecasting.test_core_numerics.test_seasonal_and_trend_none_always_level_only_regardless_of_length | pass | 0.005 |
| econ-forecast.tests.forecasting.test_core_numerics.test_exact_constant_series_uses_analytic_branch_with_zero_residuals | pass | 0.001 |
| econ-forecast.tests.forecasting.test_core_numerics.test_simulate_shape_and_bitwise_replay_with_explicit_errors | pass | 0.032 |
| econ-forecast.tests.forecasting.test_core_numerics.test_simulate_zero_errors_reproduces_forecast_exactly | pass | 0.030 |
| econ-forecast.tests.forecasting.test_core_numerics.test_correction_within_tolerance_stays_qualified_with_exact_stats | pass | 0.001 |
| econ-forecast.tests.forecasting.test_core_numerics.test_correction_exceeding_tolerance_stays_qualified_but_flagged_not_a_new_status | pass | 0.001 |
| econ-forecast.tests.forecasting.test_core_numerics.test_nonfinite_simulation_result_fails_sampling | pass | 0.001 |
| econ-forecast.tests.forecasting.test_core_numerics.test_fewer_than_fourteen_shared_dates_fails_sampling | pass | 0.000 |
| econ-forecast.tests.forecasting.test_core_numerics.test_allocation_is_token_weighted_joint_agent_category_share | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_project_resolves_to_its_single_team | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_project_spanning_two_teams_skips_the_middle_step | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_unattributed_null_user_does_not_count_toward_team_resolution | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_team_series_uses_its_own_team_event_not_org | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_project_prefers_its_own_event_over_team_fallback | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_project_falls_back_to_its_unambiguous_team | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_project_with_ambiguous_team_falls_back_to_org | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_team_series_with_no_declared_team_event_falls_back_to_org | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_organization_series_only_ever_uses_the_org_event | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_no_declared_events_match_anything | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_agent_for_model_resolves_unique_agent | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_agent_for_model_is_none_when_dataset_leaves_it_ambiguous | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_zero_spread_is_deterministic_full_multiplier | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_positive_spread_fans_within_the_declared_band | pass | 0.001 |
| econ-forecast.tests.forecasting.test_known_events.test_same_seed_and_key_reproduce_identical_factor | pass | 0.001 |
| econ-forecast.tests.forecasting.test_known_events.test_different_keys_draw_independently | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_scope_overlay_is_coherent_per_path_not_washed_out | pass | 0.002 |
| econ-forecast.tests.forecasting.test_known_events.test_deterministic_spread_zero_shifts_the_band_without_fanning_it | pass | 0.001 |
| econ-forecast.tests.forecasting.test_known_events.test_zero_inactive_paths_stay_zero_under_any_multiplier | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_window_outside_event_dates_is_left_unmodified | pass | 0.001 |
| econ-forecast.tests.forecasting.test_known_events.test_agent_axis_composes_multiplicatively_with_scope_and_only_touches_its_agent | pass | 0.001 |
| econ-forecast.tests.forecasting.test_known_events.test_no_declared_events_is_a_no_op_and_returns_no_provenance | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_validate_known_events_accepts_well_formed_declarations | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_validate_known_events_rejects_unknown_team_name | pass | 0.001 |
| econ-forecast.tests.forecasting.test_known_events.test_validate_known_events_rejects_unknown_project_name | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_validate_known_events_rejects_unknown_agent_name | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_validate_known_events_rejects_window_outside_horizon | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_validate_known_events_rejects_ambiguous_team_name | pass | 0.000 |
| econ-forecast.tests.forecasting.test_known_events.test_validate_known_events_is_a_no_op_when_empty | pass | 0.000 |
| econ-forecast.tests.forecasting.test_lightweight_import.test_artifact_reader_import_is_independent_of_runner_and_statsmodels | pass | 0.134 |
| econ-forecast.tests.forecasting.test_lightweight_import.test_lazy_public_runner_api_remains_available | pass | 1.878 |
| econ-forecast.tests.forecasting.test_presentation.test_build_figure_artists_match_independent_oracle | pass | 0.016 |
| econ-forecast.tests.forecasting.test_presentation.test_date_locator_is_readable_for_real_horizons[30] | pass | 0.120 |
| econ-forecast.tests.forecasting.test_presentation.test_date_locator_is_readable_for_real_horizons[90] | pass | 0.080 |
| econ-forecast.tests.forecasting.test_presentation.test_inactive_title_and_invalid_arrays | pass | 0.007 |
| econ-forecast.tests.forecasting.test_presentation.test_thirty_day_chart_reserves_wrapped_readable_assumption_footer | pass | 0.137 |
| econ-forecast.tests.forecasting.test_presentation.test_report_uses_saved_totals_windows_failures_and_actual_metrics | pass | 0.002 |
| econ-forecast.tests.forecasting.test_presentation.test_report_labels_unavailable_output_metric | pass | 0.002 |
| econ-forecast.tests.forecasting.test_presentation.test_report_escapes_markdown_cells_and_keeps_inactive_aggregate_distinct | pass | 0.002 |
| econ-forecast.tests.forecasting.test_presentation.test_report_complete_qualified_scope_has_model_and_aggregate_totals | pass | 0.002 |
| econ-forecast.tests.forecasting.test_presentation.test_real_partial_run_reports_unavailable_totals_without_zero | pass | 0.059 |
| econ-forecast.tests.forecasting.test_request_config.test_month_offset_and_leap_year | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[True] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_request_strict_positive[1.0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request0] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request1] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_unbounded_or_overflowing_windows_reject_before_enumeration[forecast_request2] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_request_config.test_cli_help_explains_cutoff_calendar_horizon_and_validation_mode | pass | 0.002 |
| econ-forecast.tests.forecasting.test_runner_integration.test_scope_order_and_worker_count_preserve_actual_saved_arrays | pass | 1.012 |
| econ-forecast.tests.forecasting.test_runner_integration.test_end_to_end_single_path_axis_for_constant_and_varying[<lambda>0] | pass | 0.180 |
| econ-forecast.tests.forecasting.test_runner_integration.test_end_to_end_single_path_axis_for_constant_and_varying[<lambda>1] | pass | 0.220 |
| econ-forecast.tests.forecasting.test_runner_integration.test_late_report_failure_removes_owned_staging_directory | pass | 0.130 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-inactive] | pass | 0.151 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-insufficient_startup] | pass | 0.067 |
| econ-forecast.tests.forecasting.test_runner_integration.test_history_boundary_statuses[<lambda>-<lambda>-insufficient_history] | pass | 0.057 |
| econ-forecast.tests.forecasting.test_runner_integration.test_run_records_dataset_hash_and_cost_facts | pass | 0.207 |
| econ-forecast.tests.forecasting.test_runner_integration.test_load_run_tolerates_missing_run_facts | pass | 0.028 |
| econ-forecast.tests.forecasting.test_runner_integration.test_load_run_rejects_malformed_dataset_fact | pass | 0.009 |
| econ-forecast.tests.forecasting.test_runner_integration.test_load_run_tolerates_extra_index_field | pass | 0.223 |
| econ-forecast.tests.forecasting.test_runner_integration.test_charts_are_not_written_and_manifest_omits_them | pass | 0.199 |
| econ-forecast.tests.forecasting.test_runner_integration.test_dataset_links_are_recorded_relative_to_the_run | pass | 0.198 |
| econ-forecast.tests.forecasting.test_runner_integration.test_load_run_resolves_dataset_path_after_moving_workspace_and_dataset_together | pass | 0.223 |
| econ-forecast.tests.forecasting.test_runner_integration.test_load_run_still_loads_an_old_run_with_an_absolute_dataset_path | pass | 0.031 |
| econ-forecast.tests.forecasting.test_runtime.test_process_tree_peak_and_cpu_delta_survive_child_exit | pass | 0.001 |
| econ-forecast.tests.forecasting.test_runtime.test_failed_stage_is_timed_and_monitor_stops | pass | 0.032 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[0] | pass | 0.001 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[-0.1] | pass | 0.001 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[2] | pass | 0.001 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[nan] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_invalid_sampling_interval[inf] | pass | 0.000 |
| econ-forecast.tests.forecasting.test_runtime.test_monitor_lifecycle_errors_are_explicit | pass | 0.024 |
| econ-forecast.tests.forecasting.test_test_reporting.test_junit_counts_and_index_preserve_pass_fail_skip_and_run_identity | pass | 0.004 |
| econ-forecast.tests.forecasting.test_test_reporting.test_index_can_record_a_report_failure_without_junit | pass | 0.000 |
| econ-forecast.tests.forecasting.test_test_reporting.test_failed_pytest_is_persisted_and_returned_to_shell | pass | 0.005 |
| econ-forecast.tests.forecasting.test_test_reporting.test_test_forecast_wrapper_help_is_side_effect_free | pass | 0.291 |
| econ-forecast.tests.pipeline.test_pipeline.test_all_stages_run_in_order_with_the_design_commands | pass | 1.133 |
| econ-forecast.tests.pipeline.test_pipeline.test_failing_cost_scenario_stops_and_skips_the_rest | pass | 0.539 |
| econ-forecast.tests.pipeline.test_pipeline.test_config_error_exits_1_with_top_level_error_and_runs_nothing[pipeline0-stages] | pass | 0.006 |
| econ-forecast.tests.pipeline.test_pipeline.test_config_error_exits_1_with_top_level_error_and_runs_nothing[pipeline1-stages] | pass | 0.005 |
| econ-forecast.tests.pipeline.test_pipeline.test_config_error_exits_1_with_top_level_error_and_runs_nothing[pipeline2---output] | pass | 0.005 |
| econ-forecast.tests.pipeline.test_pipeline.test_config_error_exits_1_with_top_level_error_and_runs_nothing[pipeline3-forecast] | pass | 0.005 |
| econ-forecast.tests.pipeline.test_pipeline.test_config_error_exits_1_with_top_level_error_and_runs_nothing[pipeline4-extra] | pass | 0.005 |
| econ-forecast.tests.pipeline.test_pipeline.test_reserved_scenario_stem_is_a_config_error[baseline] | pass | 0.005 |
| econ-forecast.tests.pipeline.test_pipeline.test_reserved_scenario_stem_is_a_config_error[scenarios] | pass | 0.005 |
| econ-forecast.tests.pipeline.test_pipeline.test_unreadable_pipeline_yaml_is_a_config_error | pass | 0.005 |
| econ-forecast.tests.pipeline.test_pipeline.test_help_and_missing_config | pass | 0.293 |
| econ-forecast.tests.pipeline.test_pipeline.test_real_pipeline_end_to_end | pass | 6.145 |
| econ-forecast.tests.synthetic.test_behavior.test_numerical_oracle_and_single_project_per_user_day | pass | 0.003 |
| econ-forecast.tests.synthetic.test_behavior.test_profile_largest_remainder_is_exact_and_seed_independent_of_usage | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_duration_extension_preserves_earlier_days_and_mapping_order | pass | 0.016 |
| econ-forecast.tests.synthetic.test_behavior.test_cache_boundaries_conserve_processed_input | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_weekend_reduction_preserves_activity_and_complete_coverage | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_weekend_reduction_is_drawn_per_user_before_project_aggregation | pass | 0.010 |
| econ-forecast.tests.synthetic.test_behavior.test_zero_period_keeps_zero_when_a_spike_rule_is_selected | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_overlapping_rules_are_additive_from_the_immutable_baseline | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_rules_never_apply_on_weekends | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_range_is_inclusive_and_still_excludes_weekends | pass | 0.004 |
| econ-forecast.tests.synthetic.test_behavior.test_team_and_technology_targets_intersect_while_agent_and_model_union | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_increment_uses_exact_baseline_proportions_with_integer_residue | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_rule_order_and_duration_extension_preserve_the_prefix | pass | 0.010 |
| econ-forecast.tests.synthetic.test_behavior.test_shared_contract_nulls_additive_fields_and_invalid_counts | pass | 0.000 |
| econ-forecast.tests.synthetic.test_core.test_allocation_exact_and_ties | pass | 0.000 |
| econ-forecast.tests.synthetic.test_core.test_shipped_configs_parse | pass | 0.012 |
| econ-forecast.tests.synthetic.test_core.test_boolean_count_and_duplicate_yaml_rejected | pass | 0.003 |
| econ-forecast.tests.synthetic.test_core.test_users_and_roster_are_deterministic | pass | 0.055 |
| econ-forecast.tests.synthetic.test_core.test_workflow_and_replay | pass | 4.337 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_identity_default_is_flat_everywhere | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_team_overrides_org_else_falls_back | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_flat_when_neither_org_nor_team_bound | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_agent_multiplies_or_flat | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_org_curve_scales_the_daily_baseline_exactly | pass | 0.004 |
| econ-forecast.tests.synthetic.test_curve_binding.test_team_curve_overrides_org_without_stacking | pass | 0.002 |
| econ-forecast.tests.synthetic.test_curve_binding.test_flat_agent_reproduces_the_configured_ratio_exactly | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_agent_curve_shifts_the_split_and_renormalizes | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_never_changes_coverage_or_activity_counts | pass | 0.028 |
| econ-forecast.tests.synthetic.test_curve_binding.test_all_bound_agents_at_level_zero_raises_clear_error | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_flat_curve_is_byte_identical_to_v1 | pass | 0.350 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name-2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name_x] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[n1:0123456789abcdef] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[Bad Name] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:short] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:0123456789ABCDEF] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[:0123456789abcdef] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:0123456789abcdef0] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_artifacts_path_resolves_relative_to_series_yaml | pass | 0.009 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_artifacts_path_absolute_stays_put | pass | 0.008 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_unknown_team | pass | 0.169 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_unknown_agent | pass | 0.153 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_non_directory_artifacts | pass | 0.016 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_missing_name_id | pass | 0.016 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_ambiguous_bare_name | pass | 0.291 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_accepts_bare_unique_name | pass | 0.157 |
| econ-forecast.tests.synthetic.test_curve_binding.test_bound_run_manifest_has_curve_section_and_snapshots_exist | pass | 0.283 |
| econ-forecast.tests.synthetic.test_curve_binding.test_series_days_equal_one_uses_the_first_anchor_level | pass | 0.200 |
| econ-forecast.tests.synthetic.test_curve_binding.test_bound_run_is_deterministic_on_repeat | pass | 0.307 |
| econ-forecast.tests.synthetic.test_force.test_refuse_by_default_is_preserved | pass | 0.002 |
| econ-forecast.tests.synthetic.test_force.test_force_recreates_an_existing_output_directory | pass | 0.008 |
| econ-forecast.tests.synthetic.test_force.test_force_refuses_a_non_directory_target | pass | 0.002 |
| econ-forecast.tests.synthetic.test_force.test_force_confines_deletion_to_the_resolved_target | pass | 0.008 |
| econ-forecast.tests.synthetic.test_force.test_force_refuses_an_output_that_would_delete_a_required_input | pass | 0.007 |
| econ-forecast.tests.synthetic.test_force.test_force_cli_flag_recreates_existing_output | pass | 2.083 |
| econ-forecast.tests.synthetic.test_interfaces.test_cli_help_and_invalid_arguments_do_no_io | pass | 2.742 |
| econ-forecast.tests.synthetic.test_interfaces.test_validate_only_emits_one_summary_and_writes_nothing | pass | 0.937 |
| econ-forecast.tests.synthetic.test_interfaces.test_reports_and_moved_series_replay | pass | 3.693 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path6] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_weekend_reduction_defaults_to_off | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_user_settings_are_rejected[count] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_user_settings_are_rejected[locale] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_team_activity_requires_complete_values | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_one_day_event_needs_explicit_end_date | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path0-True] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path1-1.0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path2-1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path3-True] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path4-nan] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path5-100] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path6-inf] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path7-.03] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path8-1.01] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path9-0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path10-True] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path11-1.5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path12-0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path13-100000000000000000000000000000000000000000000000000000000… | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path14-0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path15-2026-07-01T00:00:00] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path16-../zone] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path17-value17] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path18-value18] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path19-value19] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path20--0.4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path21-0.600000005] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path22-subscription] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path23-value23] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path24-value24] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path25-value25] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path26-value26] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path27-value27] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path28-value28] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_config_is_rejected_before_coercion[path29-value29] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_share_normalization_is_order_independent_and_zero_preserving | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_duplicate_projects_and_rule_ids_are_rejected_but_overlaps_are_allowed | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_invalid_spike_targets_are_rejected[targets5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_unknown_spike_team_is_rejected_against_the_roster | pass | 0.015 |
| econ-forecast.tests.synthetic.test_regressions.test_all_inactive_report_and_manifest_are_complete | pass | 0.022 |
| econ-forecast.tests.synthetic.test_regressions.test_weekend_reduction_preserves_usage_but_excludes_spikes | pass | 0.025 |
| econ-forecast.tests.synthetic.test_regressions.test_streams_write_each_day_before_requesting_the_next | pass | 0.028 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[report] | pass | 0.025 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[manifest] | pass | 0.031 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[stream] | pass | 0.017 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[snapshot] | pass | 0.014 |
| econ-forecast.tests.synthetic.test_regressions.test_existing_output_and_dangling_symlink_are_not_modified | pass | 0.014 |
| econ-forecast.tests.synthetic.test_regressions.test_input_corruption_and_record_error_dont_leak_payload | pass | 0.014 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest0] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest1] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest2] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest3] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest4] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_regressions.test_roster_legacy_manual_id_rejected_during_validation | pass | 1.003 |
| econ-forecast.tests.synthetic.test_regressions.test_logging_has_consistent_run_ids_modules_and_no_payload | pass | 1.014 |
| econ-forecast.tests.synthetic.test_regressions.test_large_token_boundary_and_overflow_are_explicit | pass | 0.046 |
| econ-forecast.tests.synthetic.test_regressions.test_fake_email_collisions_empty_name_fallback_and_length | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_projects_random_mix_coverage_and_profile_independence | pass | 0.170 |
| econ-forecast.tests.synthetic.test_regressions.test_hash_seed_does_not_change_streams | pass | 0.184 |
| econ-forecast.tests.synthetic.test_regressions.test_project_probability_sanity | pass | 0.014 |
| econ-forecast.tests.synthetic.test_regressions.test_shared_record_rejects_numeric_or_timestamp_date | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_cli_config_errors_are_actionable_without_echoing_input | pass | 1.445 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0-1] | pass | 0.030 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0-1000000] | pass | 0.027 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0.8-1] | pass | 0.021 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0.8-1000000] | pass | 0.026 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-1-1] | pass | 0.024 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-1-1000000] | pass | 0.028 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0-1] | pass | 0.023 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0-1000000] | pass | 0.023 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0.8-1] | pass | 0.030 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0.8-1000000] | pass | 0.029 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-1-1] | pass | 0.023 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-1-1000000] | pass | 0.025 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0-1] | pass | 0.023 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0-1000000] | pass | 0.022 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0.8-1] | pass | 0.021 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0.8-1000000] | pass | 0.023 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-1-1] | pass | 0.022 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-1-1000000] | pass | 0.023 |
| econ-forecast.tests.utils.test_actuals.test_auto_actuals_match_an_independent_build_histories_call | pass | 0.736 |
| econ-forecast.tests.utils.test_actuals.test_dataset_override_bypasses_the_recorded_location | pass | 0.650 |
| econ-forecast.tests.utils.test_actuals.test_hash_mismatch_is_noted_in_auto_mode_only | pass | 0.584 |
| econ-forecast.tests.utils.test_actuals.test_missing_dataset_directory_is_unavailable_not_raised | pass | 0.542 |
| econ-forecast.tests.utils.test_actuals.test_run_without_recorded_input_is_unavailable | pass | 0.031 |
| econ-forecast.tests.utils.test_actuals.test_moved_workspace_actuals_still_resolve_via_saved_run_dataset | pass | 0.340 |
| econ-forecast.tests.utils.test_plot_config.test_design_example_validates | pass | 0.007 |
| econ-forecast.tests.utils.test_plot_config.test_plotly_js_omitted_is_none | pass | 0.005 |
| econ-forecast.tests.utils.test_plot_config.test_plotly_js_absolute_path_is_kept | pass | 0.006 |
| econ-forecast.tests.utils.test_plot_config.test_scope_name_omitted_for_team_or_project_is_legal | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_old_revision_1_keys_are_rejected[bad_doc0] | pass | 0.005 |
| econ-forecast.tests.utils.test_plot_config.test_old_revision_1_keys_are_rejected[bad_doc1] | pass | 0.005 |
| econ-forecast.tests.utils.test_plot_config.test_old_revision_1_keys_are_rejected[bad_doc2] | pass | 0.005 |
| econ-forecast.tests.utils.test_plot_config.test_old_revision_1_keys_are_rejected[bad_doc3] | pass | 0.005 |
| econ-forecast.tests.utils.test_plot_config.test_old_revision_1_keys_are_rejected[bad_doc4] | pass | 0.005 |
| econ-forecast.tests.utils.test_plot_config.test_schema_version_must_be_1 | pass | 0.004 |
| econ-forecast.tests.utils.test_plot_config.test_at_least_one_section_required | pass | 0.002 |
| econ-forecast.tests.utils.test_plot_config.test_models_under_cost_is_rejected | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_views_under_forecast_is_rejected | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_scope_name_on_organization_is_rejected[forecast] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_scope_name_on_organization_is_rejected[cost] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc0] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc1] | pass | 0.004 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc2] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc3] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc4] | pass | 0.004 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc5] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc6] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc7] | pass | 0.004 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc8] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc9] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_invalid_configs_are_rejected[bad_doc10] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_config.test_duplicate_yaml_keys_are_rejected | pass | 0.002 |
| econ-forecast.tests.utils.test_plot_config.test_defaults_apply_unless_entry_overrides | pass | 0.000 |
| econ-forecast.tests.utils.test_plot_config.test_cost_views_default_when_omitted | pass | 0.000 |
| econ-forecast.tests.utils.test_plot_config.test_band_ordering_and_bounds | pass | 0.000 |
| econ-forecast.tests.utils.test_plot_config.test_stem_naming_examples_from_design_section_7 | pass | 0.000 |
| econ-forecast.tests.utils.test_plot_cost.test_help_reads_no_data | pass | 0.525 |
| econ-forecast.tests.utils.test_plot_cost.test_missing_required_argument_exits_1[args0] | pass | 0.004 |
| econ-forecast.tests.utils.test_plot_cost.test_missing_required_argument_exits_1[args1] | pass | 0.003 |
| econ-forecast.tests.utils.test_plot_cost.test_default_views_on_nine_scopes_give_two_stems_per_scope | pass | 2.691 |
| econ-forecast.tests.utils.test_plot_cost.test_per_model_on_organization_one_file_per_model | pass | 0.176 |
| econ-forecast.tests.utils.test_plot_cost.test_breakdown_ignores_band_and_samples | pass | 0.184 |
| econ-forecast.tests.utils.test_plot_cost.test_total_honors_band_and_samples | pass | 0.127 |
| econ-forecast.tests.utils.test_plot_cost.test_existing_charts_refused_without_force_and_replaced_with_it | pass | 0.612 |
| econ-forecast.tests.utils.test_plot_cost.test_shared_plotly_js_is_referenced_relatively | pass | 0.120 |
| econ-forecast.tests.utils.test_plot_cost.test_absent_cost_section_leaves_charts_untouched | pass | 0.054 |
| econ-forecast.tests.utils.test_plot_cost.test_unknown_scope_is_a_per_entry_failure_exit_2 | pass | 0.208 |
| econ-forecast.tests.utils.test_plot_cost.test_wrapper_runs_end_to_end | pass | 0.757 |
| econ-forecast.tests.utils.test_plot_force.test_force_replaces_an_existing_charts_dir | pass | 1.359 |
| econ-forecast.tests.utils.test_plot_force.test_force_refuses_a_charts_path_that_is_not_a_directory | pass | 0.527 |
| econ-forecast.tests.utils.test_plot_force.test_force_confines_deletion_to_charts_and_leaves_the_rest_of_the_run | pass | 1.318 |
| econ-forecast.tests.utils.test_plot_force.test_force_refuses_a_charts_symlink | pass | 0.541 |
| econ-forecast.tests.utils.test_plot_force.test_config_mode_honors_force | pass | 1.827 |
| econ-forecast.tests.utils.test_plot_forecast.test_hand_oracle_and_model_org_separation | pass | 0.079 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[0-0] | pass | 0.025 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[1-1] | pass | 0.025 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[10-3] | pass | 0.027 |
| econ-forecast.tests.utils.test_plot_forecast.test_display_count_does_not_change_all_sample_statistics[99-3] | pass | 0.028 |
| econ-forecast.tests.utils.test_plot_forecast.test_partial_and_inactive_are_explicit | pass | 0.037 |
| econ-forecast.tests.utils.test_plot_forecast.test_invalid_selections_and_axes | pass | 0.002 |
| econ-forecast.tests.utils.test_plot_forecast.test_organization_cli_omission_resolves_to_org_id | pass | 0.024 |
| econ-forecast.tests.utils.test_plot_forecast.test_name_selection_rejects_unknown_and_ambiguous_saved_names | pass | 0.025 |
| econ-forecast.tests.utils.test_plot_forecast.test_band_is_configurable | pass | 0.026 |
| econ-forecast.tests.utils.test_plot_forecast.test_actuals_overlay_draws_observed_line_and_seam | pass | 0.028 |
| econ-forecast.tests.utils.test_plot_forecast.test_actuals_source_without_matching_series_is_noted | pass | 0.025 |
| econ-forecast.tests.utils.test_plot_forecast.test_project_scopes_are_drawn_one_at_a_time_by_name | pass | 0.098 |
| econ-forecast.tests.utils.test_plot_forecast.test_repeatable_and_source_preserved | pass | 0.132 |
| econ-forecast.tests.utils.test_plot_forecast.test_export_html_references_a_shared_plotly_js_by_relative_path | pass | 0.033 |
| econ-forecast.tests.utils.test_plot_forecast.test_matplotlib_figure_matches_plotly_content | pass | 0.040 |
| econ-forecast.tests.utils.test_plot_forecast.test_matplotlib_figure_draws_actuals_and_seam | pass | 0.010 |
| econ-forecast.tests.utils.test_plot_forecast.test_export_png_writes_a_valid_file_and_refuses_overwrite | pass | 0.131 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_help_outside_repository_reads_no_run | pass | 0.998 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_missing_required_and_mutual_exclusion | pass | 1.996 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_bad_band_and_scope_name_on_organization_are_rejected_before_writing | pass | 0.966 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_single_plot_creates_charts_dir_with_index_and_result | pass | 1.309 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_format_flag_produces_only_the_requested_file | pass | 0.734 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_existing_charts_dir_is_refused_without_force | pass | 1.872 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_corrupt_artifact_is_a_structural_failure | pass | 0.501 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_all_selected_entries_failing_is_partial_batch_not_a_crash | pass | 0.555 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_project_name_omission_plots_one_file_per_project | pass | 1.537 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_hostile_scope_name_is_escaped_in_html_and_slugified_safely | pass | 1.432 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_config_mode_resilient_batch_and_output_contract | pass | 0.727 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_single_plot_and_config_mode_write_the_same_file_for_the_same_entry | pass | 2.715 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_config_with_stem_collision_is_rejected_before_writing | pass | 0.542 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_unreadable_config_is_a_structural_failure | pass | 0.492 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_config_without_forecast_section_leaves_charts_untouched | pass | 0.499 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_configured_plotly_js_is_written_once_when_missing | pass | 1.314 |
| econ-forecast.tests.utils.test_plot_forecast_cli.test_configured_plotly_js_is_not_rewritten_when_it_already_exists | pass | 1.297 |

Reproduce:

```bash
venv/bin/python -m pytest econ-forecast/tests/ --junitxml=../docs/test/reports/plot-pipeline-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Plot / pipeline (#78)" \
    --junit ../docs/test/reports/plot-pipeline-junit.xml \
    --out   ../docs/test/reports/plot-pipeline-test-report.md \
    --tests econ-forecast/tests/
```

tests/utils/test_plot_forecast_browser.py excluded: needs a provisioned Playwright Chromium (4 tests; environment, not code).
