# Forecast API (server.api) — Test Report

Run: `2026-10-09T02:23:33.534734+05:30`

- Total: 250  Passed: 250  Failed: 0  Skipped: 0
- Wall time (sum of cases): 53.87s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| tests.server.api.test_forecast_endpoint | 33 | 33 | 0 | 0 | 20.06 |
| tests.server.api.test_forecast_helpers | 34 | 34 | 0 | 0 | 0.03 |
| tests.server.api.test_forecast_plan_endpoint | 17 | 17 | 0 | 0 | 11.66 |
| tests.server.api.test_forecast_runway | 36 | 36 | 0 | 0 | 0.11 |
| tests.server.api.test_forecast_store | 87 | 87 | 0 | 0 | 0.70 |
| tests.server.api.test_forecast_unreadable | 32 | 32 | 0 | 0 | 19.67 |
| tests.server.api.test_openapi_contract | 3 | 3 | 0 | 0 | 1.63 |
| tests.server.api.test_schemas_contract | 8 | 8 | 0 | 0 | 0.01 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| tests.server.api.test_forecast_endpoint.test_response_shape_presets_and_series_order | pass | 0.915 |
| tests.server.api.test_forecast_endpoint.test_actual_daily_is_zero_filled_from_28_days_before_the_first_period | pass | 0.615 |
| tests.server.api.test_forecast_endpoint.test_forecast_is_null_when_no_run_is_loaded | pass | 0.573 |
| tests.server.api.test_forecast_endpoint.test_forecast_is_null_for_a_scope_outside_the_run | pass | 0.615 |
| tests.server.api.test_forecast_endpoint.test_the_latest_loaded_run_wins | pass | 0.621 |
| tests.server.api.test_forecast_endpoint.test_gate_behaves_as_economics | pass | 0.679 |
| tests.server.api.test_forecast_endpoint.test_unknown_scope_type_is_a_bad_request | pass | 0.585 |
| tests.server.api.test_forecast_endpoint.test_actual_daily_buckets_by_the_run_timezone | pass | 0.614 |
| tests.server.api.test_forecast_endpoint.test_actual_daily_equals_the_economics_fetch_for_the_same_window | pass | 0.443 |
| tests.server.api.test_forecast_endpoint.test_actual_daily_matches_economics_budget_burn_in_a_utc_run | pass | 0.439 |
| tests.server.api.test_forecast_endpoint.test_mode_counts_the_sessions_the_actuals_came_from | pass | 0.604 |
| tests.server.api.test_forecast_endpoint.test_team_and_repo_scopes_use_their_own_session_sets | pass | 0.714 |
| tests.server.api.test_forecast_endpoint.test_current_and_future_period_numbers | pass | 0.589 |
| tests.server.api.test_forecast_endpoint.test_baseline_delta_is_zero_and_presets_move_the_landing | pass | 0.589 |
| tests.server.api.test_forecast_endpoint.test_month_end_data_has_no_current_period | pass | 0.646 |
| tests.server.api.test_forecast_endpoint.test_incomplete_status_has_no_numbers | pass | 0.624 |
| tests.server.api.test_forecast_endpoint.test_budget_edit_changes_probability_and_runway_on_the_next_request | pass | 0.680 |
| tests.server.api.test_forecast_endpoint.test_runway_is_exhausted_when_the_budget_is_already_spent | pass | 0.693 |
| tests.server.api.test_forecast_endpoint.test_non_finite_budget_is_no_budget_and_negative_is_exhausted | pass | 0.614 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[bad_timezone] | pass | 0.558 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[long_timezone_name] | pass | 0.575 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[meta_not_an_object] | pass | 0.574 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[missing_band] | pass | 0.590 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[missing_series_row] | pass | 0.580 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[missing_tokens_by_model] | pass | 0.571 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[non_numeric_total] | pass | 0.596 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[non_string_timezone] | pass | 0.601 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[periods_not_an_array] | pass | 0.585 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[preset_without_description] | pass | 0.585 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[short_daily_array] | pass | 0.612 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[short_quantile_row] | pass | 0.604 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[unknown_status] | pass | 0.571 |
| tests.server.api.test_forecast_endpoint.test_a_damaged_row_of_another_scope_does_not_break_this_one | pass | 0.609 |
| tests.server.api.test_forecast_helpers.test_cdf_plan_worked_example | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_cdf_at_the_ends_and_on_grid_values | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_cdf_flat_run_jumps_to_its_top_percentile | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_cdf_single_valued_distribution | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_cdf_two_point_grid | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_cdf_is_non_decreasing_in_the_threshold | pass | 0.002 |
| tests.server.api.test_forecast_helpers.test_quantile_value_interpolates_and_clamps | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_quantile_value_inverts_the_cdf_on_a_strictly_increasing_row | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_compose_landing_and_projected_are_actual_plus_forecast | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_compose_uses_the_configured_band_not_10_90 | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_compose_within_budget_probability | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_compose_spent_budget_is_zero_probability | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_compose_delta_vs_baseline | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_compose_future_period_has_no_actuals | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_compose_returns_plain_integers | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_compose_rounds_interpolated_landing_percentiles | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_forecast_config_defaults | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs0] | pass | 0.003 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs1] | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs2] | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs3] | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs4] | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs5] | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs6] | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs7] | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs8] | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs9] | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_forecast_config_rejects_invalid_values[kwargs10] | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_forecast_config_accepts_the_boundaries[kwargs0] | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_forecast_config_accepts_the_boundaries[kwargs1] | pass | 0.000 |
| tests.server.api.test_forecast_helpers.test_forecast_config_accepts_the_boundaries[kwargs2] | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_forecast_config_accepts_the_boundaries[kwargs3] | pass | 0.001 |
| tests.server.api.test_forecast_helpers.test_api_yaml_forecast_block_matches_the_defaults | pass | 0.008 |
| tests.server.api.test_forecast_helpers.test_within_probability_is_exactly_zero_once_nothing_is_left | pass | 0.001 |
| tests.server.api.test_forecast_plan_endpoint.test_rows_are_exactly_the_runs_teams_then_repos_with_export_names | pass | 0.549 |
| tests.server.api.test_forecast_plan_endpoint.test_needed_and_gap_at_the_configured_confidence | pass | 2.046 |
| tests.server.api.test_forecast_plan_endpoint.test_gap_is_null_without_a_budget_and_a_budget_edit_moves_it | pass | 0.658 |
| tests.server.api.test_forecast_plan_endpoint.test_presets_change_the_figures | pass | 0.581 |
| tests.server.api.test_forecast_plan_endpoint.test_tokens_are_passed_through_from_the_export | pass | 0.565 |
| tests.server.api.test_forecast_plan_endpoint.test_the_planner_reads_only_total_and_tokens | pass | 0.626 |
| tests.server.api.test_forecast_plan_endpoint.test_current_period_uses_the_scopes_own_session_set | pass | 0.579 |
| tests.server.api.test_forecast_plan_endpoint.test_month_end_data_has_no_current_period | pass | 0.592 |
| tests.server.api.test_forecast_plan_endpoint.test_incomplete_scope_has_no_period_figures | pass | 0.632 |
| tests.server.api.test_forecast_plan_endpoint.test_gate_matches_the_teams_table | pass | 0.661 |
| tests.server.api.test_forecast_plan_endpoint.test_no_run_is_null_data_with_unavailable | pass | 0.554 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_runs SET meta = jsonb_set(meta, '{timezone}', '"… | pass | 0.564 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{… | pass | 0.612 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{… | pass | 0.598 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_scope_series SET payload = payload #- '{periods,… | pass | 0.596 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{… | pass | 0.638 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[DELETE FROM forecast_scope_series WHERE scope_id = 'platform' AN… | pass | 0.608 |
| tests.server.api.test_forecast_runway.test_run_out_dates_for_a_hand_built_set_of_rows | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_a_certain_overrun_runs_out_from_the_first_forecast_day | pass | 0.000 |
| tests.server.api.test_forecast_runway.test_actual_spend_shifts_the_dates_earlier | pass | 0.000 |
| tests.server.api.test_forecast_runway.test_p_out_by_d_is_non_decreasing_in_d_for_every_budget | pass | 0.003 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[0.81-lasts] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[0.8-at_risk] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[0.79-at_risk] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[0.5-at_risk] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[0.21-at_risk] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[0.2-at_risk] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[0.19-runs_out] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[0.0-runs_out] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_boundaries_are_exact[1.0-lasts] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_follows_the_configured_thresholds | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_run_out_dates_are_null_below_the_minimum_probability[0.95-True] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_run_out_dates_are_null_below_the_minimum_probability[0.949-True] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_run_out_dates_are_null_below_the_minimum_probability[0.96-False] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_run_out_dates_are_null_below_the_minimum_probability[1.0-False] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_run_out_dates_are_null_below_the_minimum_probability[0.0-True] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_nothing_runs_out_when_the_budget_exceeds_every_outcome | pass | 0.000 |
| tests.server.api.test_forecast_runway.test_probabilities_sum_to_exactly_one | pass | 0.010 |
| tests.server.api.test_forecast_runway.test_exhausted_reports_the_day_actual_spend_crossed_the_budget | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_exhausted_exactly_at_the_budget_and_on_the_first_day | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_a_budget_of_zero_or_less_is_exhausted_on_the_period_start[0] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_a_budget_of_zero_or_less_is_exhausted_on_the_period_start[-50] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_future_period_and_no_budget_have_no_runway | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_raising_the_budget_never_raises_the_chance_of_running_out | pass | 0.014 |
| tests.server.api.test_forecast_runway.test_run_out_dates_are_ordered_p10_p50_p90 | pass | 0.023 |
| tests.server.api.test_forecast_runway.test_p50_never_moves_earlier_when_the_budget_rises_for_a_scaled_distribution | pass | 0.036 |
| tests.server.api.test_forecast_runway.test_p50_can_move_earlier_when_the_budget_rises_documented_exception | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_cut_offs_are_compared_to_twelve_digits[0.8004-lasts] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_cut_offs_are_compared_to_twelve_digits[0.7996-at_risk] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_cut_offs_are_compared_to_twelve_digits[0.1996-runs_out] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_status_cut_offs_are_compared_to_twelve_digits[0.2004-at_risk] | pass | 0.001 |
| tests.server.api.test_forecast_runway.test_the_minimum_probability_is_compared_to_twelve_digits | pass | 0.000 |
| tests.server.api.test_forecast_runway.test_the_date_search_is_compared_to_twelve_digits | pass | 0.001 |
| tests.server.api.test_forecast_store.test_a_valid_row_reads | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_valid_month_end_row_reads_with_no_current_period | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[assumption_int] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[band_not_in_grid] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[band_reversed] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[bool_band_high] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[bool_in_grid] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_2000] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_3000] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_9999] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_before_current_from] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_garbage] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_infinity] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_minus_infinity] | pass | 0.028 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_none] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[data_through_off_by_one] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[date_garbage] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[dates_empty] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[dates_not_a_list] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[end_not_in_dates] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[float_band_low] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[float_in_grid] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[from_after_end] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[from_before_start] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[from_not_in_dates] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[grid_duplicate] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[grid_one_entry] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[grid_unordered] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[meta_is_a_list] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_band] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_band_low] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_dates] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_grid] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_period_end] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_period_from] | pass | 0.007 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_periods] | pass | 0.008 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_preset_assumptions] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_preset_description] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_preset_group] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_preset_id] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_presets] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_scope_name] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_scopes] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[no_timezone] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[origin_after_data_through] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[origin_garbage] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[origin_infinity] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[periods_empty] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[preset_group_int] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[presets_empty] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[string_in_grid] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[timezone_empty] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[timezone_int] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[timezone_long] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[timezone_path] | pass | 0.005 |
| tests.server.api.test_forecast_store.test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id[timezone_unknown] | pass | 0.006 |
| tests.server.api.test_forecast_store.test_month_end_data_through_must_match_the_first_period | pass | 0.007 |
| tests.server.api.test_forecast_store.test_two_current_periods_are_unreadable | pass | 0.006 |
| tests.server.api.test_forecast_store.test_an_actuals_window_longer_than_the_cap_is_unreadable | pass | 0.008 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[cumulative_expected_float] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[cumulative_expected_short] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[daily_bool] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[daily_float] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[daily_short] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[daily_string] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[extra_period] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[no_cumulative] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[no_daily] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[no_periods] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[no_tokens] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[no_total] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[one_period_missing] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[payload_not_an_object] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[quantile_row_bool] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[quantile_row_float] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[quantile_row_short] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[quantiles_missing_rows] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[token_expected_float] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[token_model_missing] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[token_p90_bool] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[tokens_not_a_list] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[total_expected_bool] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[total_expected_float] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[total_expected_missing] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[total_quantiles_float] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_damaged_series_is_unreadable[total_quantiles_short] | pass | 0.011 |
| tests.server.api.test_forecast_store.test_a_valid_series_reads_and_unknown_status_does_not | pass | 0.032 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args0-UPDATE forecast_runs SET data_through = '9… | pass | 0.647 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args1-UPDATE forecast_runs SET data_through = '3… | pass | 0.592 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args2-UPDATE forecast_runs SET data_through = '2… | pass | 0.600 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args3-UPDATE forecast_runs SET data_through = '2… | pass | 0.608 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args4-UPDATE forecast_runs SET data_through = '2… | pass | 0.595 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args5-UPDATE forecast_runs SET data_through = 'i… | pass | 0.600 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args6-UPDATE forecast_runs SET data_through = '-… | pass | 0.627 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args7-UPDATE forecast_runs SET origin = 'infinit… | pass | 0.603 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args8-UPDATE forecast_runs SET origin = '-infini… | pass | 0.596 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args9-UPDATE forecast_runs SET origin = '2026-10… | pass | 0.581 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args10-UPDATE forecast_runs SET data_through = '… | pass | 0.665 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args11-UPDATE forecast_runs SET data_through = '… | pass | 0.593 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args12-UPDATE forecast_runs SET data_through = '… | pass | 0.614 |
| tests.server.api.test_forecast_unreadable.test_inconsistent_or_extreme_dates_are_unreadable_never_a_500[export_args13-UPDATE forecast_runs SET data_through = '… | pass | 0.598 |
| tests.server.api.test_forecast_unreadable.test_an_intact_run_is_not_reported | pass | 0.615 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[bool_total] | pass | 0.627 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[float_band] | pass | 0.602 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[float_total] | pass | 0.638 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[missing_key] | pass | 0.603 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[period_from_not_in_dates] | pass | 0.596 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[plan_no_periods_but_complete] | pass | 0.662 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[plan_one_period_extra] | pass | 0.742 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[plan_one_period_short] | pass | 0.604 |
| tests.server.api.test_forecast_unreadable.test_strictness_through_the_routes[unordered_grid] | pass | 0.587 |
| tests.server.api.test_forecast_unreadable.test_date_arithmetic_failing_after_the_run_is_read_is_unreadable | pass | 0.593 |
| tests.server.api.test_forecast_unreadable.test_a_database_error_while_fetching_actuals_still_propagates | pass | 0.579 |
| tests.server.api.test_forecast_unreadable.test_a_failure_while_building_the_view_is_unreadable | pass | 0.626 |
| tests.server.api.test_forecast_unreadable.test_a_scope_that_is_not_in_the_run_is_not_logged_as_damage | pass | 0.606 |
| tests.server.api.test_forecast_unreadable.test_no_run_at_all_is_not_logged_as_damage | pass | 0.575 |
| tests.server.api.test_forecast_unreadable.test_latest_loaded_at_wins_and_run_id_breaks_the_tie | pass | 0.641 |
| tests.server.api.test_forecast_unreadable.test_only_the_rows_of_the_latest_run_are_read | pass | 0.644 |
| tests.server.api.test_forecast_unreadable.test_a_far_data_through_with_sessions_is_still_bounded | pass | 0.613 |
| tests.server.api.test_openapi_contract.test_committed_openapi_matches_the_live_schema | pass | 0.575 |
| tests.server.api.test_openapi_contract.test_pricing_reference_port_is_typed_in_the_schema | pass | 0.526 |
| tests.server.api.test_openapi_contract.test_forecast_routes_are_typed_in_the_schema | pass | 0.532 |
| tests.server.api.test_schemas_contract.test_envelope_carries_data_and_an_aggregate_mode_block | pass | 0.002 |
| tests.server.api.test_schemas_contract.test_envelope_null_data_with_unavailable_is_the_dark_whole_block_shape | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_money_reports_basis_and_completeness_not_a_bare_number | pass | 0.001 |
| tests.server.api.test_schemas_contract.test_sourced_is_generic_over_its_value | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_unavailable_sub_block_shape | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_models_forbid_unknown_fields | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_forecast_models_forbid_unknown_fields_and_carry_no_presentation | pass | 0.001 |
| tests.server.api.test_schemas_contract.test_forecast_envelopes_wrap_the_models_like_every_manager_view | pass | 0.001 |

Reproduce:

```bash
venv/bin/python -m pytest tests/server/api/ --junitxml=docs/test/reports/forecast-api-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Forecast API (server.api)" \
    --junit docs/test/reports/forecast-api-junit.xml \
    --out   docs/test/reports/forecast-api-test-report.md \
    --tests tests/server/api/
```

T6: GET /v1/forecast and GET /v1/forecast/plan; helpers, runway, stored-run reader, endpoint, unreadable-run and contract tests run against a scratch _synth database; the wider tests/server/api run is reported in the T6 handoff. T7 addendum: planning_confidence on BudgetPlan and plan_for_usd_micros on PlanPeriodFigures, asserted in test_forecast_plan_endpoint.
