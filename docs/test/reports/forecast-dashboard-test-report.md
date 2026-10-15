# Forecast dashboard batch (forecast.dashboard) — Test Report

Run: `2026-10-08T16:19:31.858539+05:30`

- Total: 260  Passed: 260  Failed: 0  Skipped: 0
- Wall time (sum of cases): 59.33s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast | 21 | 21 | 0 | 0 | 31.12 |
| econ-forecast.tests.dashboard.test_export | 95 | 95 | 0 | 0 | 12.16 |
| econ-forecast.tests.dashboard.test_export_runway | 11 | 11 | 0 | 0 | 7.08 |
| econ-forecast.tests.dashboard.test_presets | 44 | 44 | 0 | 0 | 0.16 |
| econ-forecast.tests.dashboard.test_runner | 52 | 52 | 0 | 0 | 5.89 |
| econ-forecast.tests.dashboard.test_scopes | 37 | 37 | 0 | 0 | 2.93 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_success_exits_zero_with_the_summary_and_the_file | pass | 6.315 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_the_default_output_is_forecast_inside_the_org_demo | pass | 0.001 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_stdout_is_one_json_line_and_logs_are_json_on_stderr | pass | 0.001 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_command_line_errors_exit_2[argv0] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_command_line_errors_exit_2[argv1] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_command_line_errors_exit_2[argv2] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_command_line_errors_exit_2[argv3] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_a_missing_org_demo_exits_2 | pass | 0.002 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_an_org_demo_without_a_series_dataset_exits_2 | pass | 0.001 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_a_missing_presets_file_exits_2_and_leaves_the_output_alone | pass | 0.012 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_a_non_empty_output_without_force_exits_2_and_changes_nothing | pass | 0.022 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_an_unmappable_scope_stops_before_the_output_is_touched | pass | 0.011 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_an_unmappable_scope_with_force_keeps_the_previous_run | pass | 0.071 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_force_replaces_the_previous_run_and_its_stale_export | pass | 5.497 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_an_execution_error_in_the_run_exits_1 | pass | 0.013 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_an_execution_error_in_the_export_exits_1 | pass | 4.592 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_a_run_over_other_scopes_than_planned_exits_1_without_an_export | pass | 0.013 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_the_wrapper_is_executable_and_runs_the_cli_module | pass | 0.001 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_help_does_no_io | pass | 2.747 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_the_real_entry_point_on_the_fixture_org | pass | 9.409 |
| econ-forecast.tests.dashboard.test_cli_dashboard_forecast.test_the_real_entry_point_reports_a_missing_org_demo | pass | 2.403 |
| econ-forecast.tests.dashboard.test_export.test_percentiles_are_taken_after_summing_the_series_per_sample | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_daily_values_are_the_mean_and_the_band_percentiles | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_money_is_rounded_once_from_the_exact_sum | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_the_total_is_the_last_cumulative_entry | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_tokens_by_model_divide_each_series_by_its_own_daily_rate | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_tokens_are_rounded_to_the_nearest_integer | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_an_amount_beyond_the_integer_range_is_an_error | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export.test_tokens_are_zero_where_there_is_nothing_to_price | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_usd_without_tokens_is_an_error_not_a_zero | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export.test_tokens_at_a_zero_rate_are_an_error | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export.test_an_incomplete_scope_carries_only_its_status | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_the_cost_scope_status_is_passed_through[complete] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_the_cost_scope_status_is_passed_through[conditional] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_the_cost_scope_status_is_passed_through[inactive] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_an_unknown_scope_status_is_refused | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export.test_presets_follow_the_file_order_with_labels_groups_and_assumptions | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_from_is_the_day_after_the_data_when_it_lies_inside_the_period[data_through0-origin0-firsts0-lengths0] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_from_is_the_day_after_the_data_when_it_lies_inside_the_period[data_through1-origin1-firsts1-lengths1] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_from_is_the_day_after_the_data_when_it_lies_inside_the_period[data_through2-origin2-firsts2-lengths2] | pass | 0.004 |
| econ-forecast.tests.dashboard.test_export.test_from_is_the_day_after_the_data_when_it_lies_inside_the_period[data_through3-origin3-firsts3-lengths3] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export.test_the_timezone_comes_from_the_series_manifest | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_a_series_manifest_without_a_timezone_is_an_error[None] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export.test_a_series_manifest_without_a_timezone_is_an_error[] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export.test_now_must_be_timezone_aware | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export.test_generated_at_is_utc_seconds | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_read_errors_become_export_errors | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export.test_a_cost_run_for_another_scenario_is_refused | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export.test_cost_runs_over_different_dates_are_refused | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export.test_a_cost_run_priced_from_another_forecast_run_is_refused | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export.test_the_forecast_must_have_two_calendar_periods | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export.test_the_forecast_origin_must_match_the_run | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export.test_the_cost_scopes_must_be_the_runs_scopes | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export.test_two_builds_with_a_pinned_clock_are_byte_identical | pass | 0.005 |
| econ-forecast.tests.dashboard.test_export.test_builds_at_different_times_differ_only_in_generated_at | pass | 0.004 |
| econ-forecast.tests.dashboard.test_export.test_the_default_clock_is_the_current_utc_time | pass | 0.004 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[schema id] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[extra top-level key] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[missing key] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[generated_at format] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[empty timezone] | pass | 0.008 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[origin not a date] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[origin after data_through] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[band low off the grid] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[band high off the grid] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[band reversed] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[grid not increasing] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[grid not ending at 100] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[dates not consecutive] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[date not a date] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[one period] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[from before start] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[period outside the dates] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[periods overlap] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[duplicate preset ids] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[unsorted assumptions] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[empty group] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[series keys differ from the presets] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[duplicate scope] | pass | 0.008 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[unknown scope type] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[empty scope id] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[unknown status] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[incomplete with numbers] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[daily too short] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[daily low above high] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[float in daily] | pass | 0.008 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[bool in daily] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[negative daily] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[one period only] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[cumulative expected decreasing] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[cumulative expected outside its quantiles] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[quantile row decreasing across the grid] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[quantile column decreasing across dates] | pass | 0.010 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[quantile row of the wrong width] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[too few quantile rows] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[total expected differs] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[total quantile differs] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[float in total] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[tokens unsorted] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[tokens negative] | pass | 0.041 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[tokens float] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[tokens extra key] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[period block missing a key] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_fires_on_each_corruption[a float anywhere] | pass | 0.006 |
| econ-forecast.tests.dashboard.test_export.test_validate_export_rejects_a_non_object | pass | 0.000 |
| econ-forecast.tests.dashboard.test_export.test_canonical_bytes_are_sorted_compact_utf8_with_a_newline | pass | 0.000 |
| econ-forecast.tests.dashboard.test_export.test_write_export_leaves_exactly_the_export | pass | 0.004 |
| econ-forecast.tests.dashboard.test_export.test_the_export_gets_ordinary_file_permissions | pass | 0.004 |
| econ-forecast.tests.dashboard.test_export.test_a_failed_write_keeps_the_old_file_and_leaves_no_temporary | pass | 0.004 |
| econ-forecast.tests.dashboard.test_export.test_a_missing_output_directory_is_an_execution_error | pass | 0.003 |
| econ-forecast.tests.dashboard.test_export.test_real_export_shape | pass | 5.325 |
| econ-forecast.tests.dashboard.test_export.test_real_export_has_no_floats | pass | 0.018 |
| econ-forecast.tests.dashboard.test_export.test_real_presets_assumptions_are_the_union_over_the_cost_runs_scopes | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export.test_a_future_period_total_matches_the_cost_runs_own_period_summary | pass | 0.182 |
| econ-forecast.tests.dashboard.test_export.test_data_ending_on_a_month_end_makes_both_periods_future | pass | 5.385 |
| econ-forecast.tests.dashboard.test_export.test_export_forecast_writes_the_file_and_logs_its_events | pass | 0.796 |
| econ-forecast.tests.dashboard.test_export_runway.test_grids_never_decrease_across_quantiles_or_across_dates | pass | 6.473 |
| econ-forecast.tests.dashboard.test_export_runway.test_grid_values_equal_the_quantiles_of_the_summed_raw_paths | pass | 0.168 |
| econ-forecast.tests.dashboard.test_export_runway.test_the_plan_worked_example_of_the_budget_probability | pass | 0.002 |
| econ-forecast.tests.dashboard.test_export_runway.test_probability_of_running_out_by_each_date_matches_the_paths | pass | 0.174 |
| econ-forecast.tests.dashboard.test_export_runway.test_tokens_by_model_equal_the_usd_paths_divided_by_the_daily_rate | pass | 0.170 |
| econ-forecast.tests.dashboard.test_export_runway.test_baseline_tokens_equal_the_forecast_runs_own_token_paths | pass | 0.088 |
| econ-forecast.tests.dashboard.test_export_runway.test_a_constant_token_model_move_keeps_the_token_total_of_baseline[opus-to-sonnet-30] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export_runway.test_a_constant_token_model_move_keeps_the_token_total_of_baseline[opus-to-sonnet-60] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_export_runway.test_a_constant_token_model_move_keeps_the_token_total_of_baseline[sonnet-to-haiku-30] | pass | 0.000 |
| econ-forecast.tests.dashboard.test_export_runway.test_growth_scales_the_token_total_of_a_future_period | pass | 0.000 |
| econ-forecast.tests.dashboard.test_export_runway.test_presets_order_the_totals_the_expected_way | pass | 0.001 |
| econ-forecast.tests.dashboard.test_presets.test_shipped_presets_match_the_plan_table | pass | 0.006 |
| econ-forecast.tests.dashboard.test_presets.test_shipped_presets_use_no_banned_tone_words | pass | 0.001 |
| econ-forecast.tests.dashboard.test_presets.test_schema_must_match | pass | 0.005 |
| econ-forecast.tests.dashboard.test_presets.test_unknown_keys_are_rejected | pass | 0.005 |
| econ-forecast.tests.dashboard.test_presets.test_ids_must_be_unique | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_ids_must_be_directory_safe[Grow] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_presets.test_ids_must_be_directory_safe[grow_up] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_presets.test_ids_must_be_directory_safe[-grow] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_presets.test_ids_must_be_directory_safe[grow up] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_presets.test_ids_must_be_directory_safe[gr\xf6w] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_ids_must_be_directory_safe[] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_presets.test_exactly_one_baseline | pass | 0.004 |
| econ-forecast.tests.dashboard.test_presets.test_baseline_must_be_first | pass | 0.002 |
| econ-forecast.tests.dashboard.test_presets.test_baseline_has_no_changes_or_group | pass | 0.004 |
| econ-forecast.tests.dashboard.test_presets.test_other_presets_need_a_change | pass | 0.004 |
| econ-forecast.tests.dashboard.test_presets.test_other_presets_need_a_group[None] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_presets.test_other_presets_need_a_group[] | pass | 0.002 |
| econ-forecast.tests.dashboard.test_presets.test_other_presets_need_a_group[ ] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_changes_use_the_cost_schema | pass | 0.008 |
| econ-forecast.tests.dashboard.test_presets.test_agent_transfer_is_allowed | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_unknown_outer_change_key_is_rejected | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_unknown_inner_change_key_is_rejected | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_effective_from_is_never_set_in_a_preset[value0] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_effective_from_is_never_set_in_a_preset[None] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_label_and_description_are_not_empty[-label] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_label_and_description_are_not_empty[-description] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_label_and_description_are_not_empty[ -label] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_label_and_description_are_not_empty[ -description] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_missing_or_malformed_file_is_an_input_error | pass | 0.001 |
| econ-forecast.tests.dashboard.test_presets.test_only_the_schema_alias_is_accepted | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_transfer_uses_the_alias_not_the_python_field_name | pass | 0.005 |
| econ-forecast.tests.dashboard.test_presets.test_duplicate_yaml_keys_are_rejected | pass | 0.007 |
| econ-forecast.tests.dashboard.test_presets.test_an_unhashable_yaml_key_is_an_input_error | pass | 0.001 |
| econ-forecast.tests.dashboard.test_presets.test_non_finite_numbers_are_rejected[.inf] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_non_finite_numbers_are_rejected[-.inf] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_non_finite_numbers_are_rejected[.nan] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_non_finite_numbers_are_rejected['inf'] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_non_finite_numbers_are_rejected['Infinity'] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_presets.test_non_finite_numbers_are_rejected[1e999] | pass | 0.004 |
| econ-forecast.tests.dashboard.test_presets.test_unknown_keys_of_mixed_types_are_an_input_error_not_a_type_error | pass | 0.004 |
| econ-forecast.tests.dashboard.test_presets.test_every_shipped_preset_renders_a_valid_scenario | pass | 0.015 |
| econ-forecast.tests.dashboard.test_presets.test_rendering_keeps_change_order_and_parameters | pass | 0.006 |
| econ-forecast.tests.dashboard.test_presets.test_an_unwritable_scenario_path_is_an_execution_error | pass | 0.006 |
| econ-forecast.tests.dashboard.test_presets.test_baseline_has_no_scenario | pass | 0.006 |
| econ-forecast.tests.dashboard.test_runner.test_resolve_origin[data_through0-origin0] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_runner.test_resolve_origin[data_through1-origin1] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_runner.test_resolve_origin[data_through2-origin2] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_runner.test_resolve_origin[data_through3-origin3] | pass | 0.000 |
| econ-forecast.tests.dashboard.test_runner.test_resolve_origin[data_through4-origin4] | pass | 0.000 |
| econ-forecast.tests.dashboard.test_runner.test_resolve_origin[data_through5-origin5] | pass | 0.001 |
| econ-forecast.tests.dashboard.test_runner.test_resolve_origin[data_through6-origin6] | pass | 0.000 |
| econ-forecast.tests.dashboard.test_runner.test_resolve_origin[data_through7-origin7] | pass | 0.000 |
| econ-forecast.tests.dashboard.test_runner.test_missing_manifest_is_an_input_error | pass | 0.007 |
| econ-forecast.tests.dashboard.test_runner.test_unusable_manifest_is_an_input_error[kwargs0-not complete] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_runner.test_unusable_manifest_is_an_input_error[kwargs1-operation 'series'] | pass | 0.007 |
| econ-forecast.tests.dashboard.test_runner.test_fewer_than_28_days_before_the_origin_is_an_input_error | pass | 0.008 |
| econ-forecast.tests.dashboard.test_runner.test_exactly_28_days_before_the_origin_is_enough | pass | 0.015 |
| econ-forecast.tests.dashboard.test_runner.test_data_starting_after_the_origin_is_an_input_error | pass | 0.008 |
| econ-forecast.tests.dashboard.test_runner.test_series_scopes_lists_the_scopes_a_run_will_forecast | pass | 0.003 |
| econ-forecast.tests.dashboard.test_runner.test_forecast_yaml_has_every_scope_and_keeps_the_template | pass | 0.020 |
| econ-forecast.tests.dashboard.test_runner.test_default_template_is_found_from_the_package_not_the_cwd | pass | 0.022 |
| econ-forecast.tests.dashboard.test_runner.test_existing_empty_output_dir_is_fine | pass | 0.015 |
| econ-forecast.tests.dashboard.test_runner.test_non_empty_output_dir_is_refused_without_force | pass | 0.013 |
| econ-forecast.tests.dashboard.test_runner.test_output_path_that_is_a_file_is_refused | pass | 0.013 |
| econ-forecast.tests.dashboard.test_runner.test_force_removes_only_the_known_entries | pass | 0.017 |
| econ-forecast.tests.dashboard.test_runner.test_force_removes_symlinked_entries_without_following_them | pass | 0.015 |
| econ-forecast.tests.dashboard.test_runner.test_output_must_be_separate_from_the_series_dir[equal-False] | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_output_must_be_separate_from_the_series_dir[equal-True] | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_output_must_be_separate_from_the_series_dir[contains-False] | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_output_must_be_separate_from_the_series_dir[contains-True] | pass | 0.016 |
| econ-forecast.tests.dashboard.test_runner.test_output_must_be_separate_from_the_series_dir[inside-False] | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_output_must_be_separate_from_the_series_dir[inside-True] | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_output_under_a_file_is_an_input_error | pass | 0.025 |
| econ-forecast.tests.dashboard.test_runner.test_output_under_a_read_only_directory_is_an_execution_error | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_an_unreadable_existing_output_dir_is_an_input_error | pass | 0.025 |
| econ-forecast.tests.dashboard.test_runner.test_force_on_a_read_only_output_dir_is_an_input_error | pass | 0.015 |
| econ-forecast.tests.dashboard.test_runner.test_output_is_compared_to_the_series_dir_by_identity[equal] | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_output_is_compared_to_the_series_dir_by_identity[inside] | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_a_bad_template_never_costs_the_previous_run | pass | 0.008 |
| econ-forecast.tests.dashboard.test_runner.test_the_previous_runs_own_forecast_yaml_can_be_the_template | pass | 0.017 |
| econ-forecast.tests.dashboard.test_runner.test_forecast_errors_keep_their_category_and_name_the_stage | pass | 0.027 |
| econ-forecast.tests.dashboard.test_runner.test_a_filesystem_error_inside_a_stage_is_an_execution_error | pass | 0.028 |
| econ-forecast.tests.dashboard.test_runner.test_a_partial_forecast_is_an_execution_error | pass | 0.014 |
| econ-forecast.tests.dashboard.test_runner.test_cost_errors_name_the_preset_and_stage | pass | 0.029 |
| econ-forecast.tests.dashboard.test_runner.test_a_cost_run_that_is_not_fully_complete_is_an_execution_error[result0-incomplete] | pass | 0.016 |
| econ-forecast.tests.dashboard.test_runner.test_a_cost_run_that_is_not_fully_complete_is_an_execution_error[result1-m-x] | pass | 0.016 |
| econ-forecast.tests.dashboard.test_runner.test_a_cost_run_that_is_not_fully_complete_is_an_execution_error[result2-QA] | pass | 0.015 |
| econ-forecast.tests.dashboard.test_runner.test_end_to_end_result | pass | 0.009 |
| econ-forecast.tests.dashboard.test_runner.test_end_to_end_forecast_is_complete | pass | 0.001 |
| econ-forecast.tests.dashboard.test_runner.test_end_to_end_every_preset_has_a_complete_cost_run | pass | 0.227 |
| econ-forecast.tests.dashboard.test_runner.test_end_to_end_scenario_files_take_effect_the_day_after_the_data_ends | pass | 0.007 |
| econ-forecast.tests.dashboard.test_runner.test_end_to_end_scenarios_move_the_org_total_the_expected_way | pass | 0.183 |
| econ-forecast.tests.dashboard.test_runner.test_end_to_end_november_figures_follow_from_the_prices | pass | 0.275 |
| econ-forecast.tests.dashboard.test_runner.test_end_to_end_rerun_needs_force | pass | 0.011 |
| econ-forecast.tests.dashboard.test_runner.test_a_partial_forecast_names_the_rejected_scopes_and_their_reasons | pass | 0.211 |
| econ-forecast.tests.dashboard.test_runner.test_end_to_end_with_data_ending_on_a_month_end | pass | 4.424 |
| econ-forecast.tests.dashboard.test_scopes.test_maps_every_scope_in_run_order | pass | 0.005 |
| econ-forecast.tests.dashboard.test_scopes.test_order_follows_the_run_not_the_files | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_team_whose_directory_name_differs_from_the_layer_1_name | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_a_team_name_with_spaces_and_underscores_is_slugged | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_the_parent_directory_team_is_not_exported | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_an_empty_budgets_file_is_valid | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_blank_lines_in_jsonl_files_are_ignored | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_a_repository_label_is_matched_exactly | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_a_team_without_a_directory_row | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_project_without_a_repo_entry | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_a_repo_entry_without_a_repository_row | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_an_org_dataset_of_another_org | pass | 0.002 |
| econ-forecast.tests.dashboard.test_scopes.test_a_budgets_org_row_that_disagrees | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_directory_team_of_another_org | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_two_teams_with_one_slug | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_two_projects_in_one_repository | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_repository_label_that_appears_twice | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_a_project_with_two_repo_entries | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_an_unknown_forecast_scope_type | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_a_missing_org_file[budgets.jsonl] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_missing_org_file[directory_teams.jsonl] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_missing_org_file[repositories.jsonl] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_missing_org_file[manifest.json] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_missing_org_config | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_garbage_jsonl_line_names_the_file_and_line[budgets.jsonl] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_garbage_jsonl_line_names_the_file_and_line[directory_teams.jsonl] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_garbage_jsonl_line_names_the_file_and_line[repositories.jsonl] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_jsonl_line_that_is_not_an_object | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_non_object_manifest | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_malformed_org_config[[]] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_malformed_org_config[org: {}\nrepos: []\n] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_malformed_org_config[org: {github_owner: acme}\n] | pass | 0.004 |
| econ-forecast.tests.dashboard.test_scopes.test_a_malformed_org_config[{] | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_repos_entry_without_a_name | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_a_manifest_without_a_directory_org_id | pass | 0.003 |
| econ-forecast.tests.dashboard.test_scopes.test_only_scopes_imports_from_forecast_synthetic | pass | 0.040 |
| econ-forecast.tests.dashboard.test_scopes.test_importing_the_package_does_not_load_the_synthetic_org_package | pass | 2.775 |

Reproduce:

```bash
venv/bin/python -m pytest econ-forecast/tests/dashboard/ --junitxml=docs/test/reports/forecast-dashboard-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Forecast dashboard batch (forecast.dashboard)" \
    --junit docs/test/reports/forecast-dashboard-junit.xml \
    --out   docs/test/reports/forecast-dashboard-test-report.md \
    --tests econ-forecast/tests/dashboard/
```

T3 and T4 of docs/plan/2026-10-05-forecast-dashboard-integration.md: presets and runs (T3); scope mapping, the dashboard export and its command line (T4). The end-to-end tests run real forecasts and cost runs on small generated Layer 1 datasets with a pinned, tiered price catalog; the export tests also build documents from hand-made runs, and test_export_runway.py checks the export against the raw .npy files of a 1000-path run.
