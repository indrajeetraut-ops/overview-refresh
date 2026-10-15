# Budget cycles (Overview refresh, Track A backend) — Test Report

Run: `2026-10-14T22:04:20.727811+05:30`

- Total: 687  Passed: 666  Failed: 0  Skipped: 21
- Wall time (sum of cases): 148.78s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| tests.server.api.test_app_budget_cycle | 15 | 15 | 0 | 0 | 12.49 |
| tests.server.api.test_budget_cycle_migration | 1 | 1 | 0 | 0 | 1.84 |
| tests.server.api.test_budget_cycles | 56 | 56 | 0 | 0 | 6.31 |
| tests.server.api.test_forecast_endpoint | 37 | 37 | 0 | 0 | 30.55 |
| tests.server.api.test_forecast_plan_endpoint | 18 | 18 | 0 | 0 | 15.35 |
| tests.server.api.test_manager_aggregate | 116 | 116 | 0 | 0 | 0.03 |
| tests.server.api.test_manager_budgets | 17 | 17 | 0 | 0 | 6.11 |
| tests.server.api.test_manager_service_economics | 7 | 7 | 0 | 0 | 4.31 |
| tests.server.api.test_manager_service_org_overview | 21 | 21 | 0 | 0 | 12.30 |
| tests.server.api.test_manager_service_tables | 10 | 10 | 0 | 0 | 5.61 |
| tests.server.api.test_manager_service_team_detail | 11 | 11 | 0 | 0 | 6.02 |
| tests.server.api.test_openapi_contract | 3 | 3 | 0 | 0 | 1.68 |
| tests.server.api.test_schemas_contract | 8 | 8 | 0 | 0 | 0.01 |
| tests.server.api.test_service_overview | 14 | 14 | 0 | 0 | 7.92 |
| tests.server.storage.test_cli_db | 7 | 7 | 0 | 0 | 1.83 |
| tests.server.storage.test_cli_load_forecast | 17 | 14 | 0 | 3 | 1.04 |
| tests.server.storage.test_config | 2 | 2 | 0 | 0 | 0.00 |
| tests.server.storage.test_engine | 2 | 2 | 0 | 0 | 0.04 |
| tests.server.storage.test_er | 1 | 1 | 0 | 0 | 0.00 |
| tests.server.storage.test_errors | 1 | 1 | 0 | 0 | 0.00 |
| tests.server.storage.test_forecast_load | 183 | 177 | 0 | 6 | 6.64 |
| tests.server.storage.test_forecast_schema | 8 | 8 | 0 | 0 | 3.70 |
| tests.server.storage.test_git_queries | 7 | 7 | 0 | 0 | 2.83 |
| tests.server.storage.test_git_schema | 5 | 5 | 0 | 0 | 1.31 |
| tests.server.storage.test_integration | 3 | 3 | 0 | 0 | 1.14 |
| tests.server.storage.test_issue_links_schema | 5 | 5 | 0 | 0 | 1.37 |
| tests.server.storage.test_operations | 3 | 3 | 0 | 0 | 1.41 |
| tests.server.storage.test_otel_schema | 6 | 6 | 0 | 0 | 2.45 |
| tests.server.storage.test_pr_class_queries | 8 | 8 | 0 | 0 | 3.56 |
| tests.server.storage.test_pr_class_schema | 5 | 5 | 0 | 0 | 1.18 |
| tests.server.storage.test_producer_store | 3 | 3 | 0 | 0 | 1.29 |
| tests.server.storage.test_queries | 5 | 5 | 0 | 0 | 0.01 |
| tests.server.storage.test_queue_queries | 4 | 4 | 0 | 0 | 1.34 |
| tests.server.storage.test_queue_schema | 4 | 4 | 0 | 0 | 0.75 |
| tests.server.storage.test_readme | 1 | 1 | 0 | 0 | 0.00 |
| tests.server.storage.test_schema_dump | 3 | 3 | 0 | 0 | 0.02 |
| tests.server.storage.test_schema_metadata | 4 | 4 | 0 | 0 | 0.00 |
| tests.server.storage.test_session_info_scalar_columns_migration | 1 | 1 | 0 | 0 | 1.25 |
| tests.server.storage.test_session_scalars | 12 | 12 | 0 | 0 | 2.41 |
| tests.server.storage.test_synthetic_load | 12 | 0 | 0 | 12 | 0.00 |
| tests.server.storage.test_synthetic_load_unit | 41 | 41 | 0 | 0 | 2.70 |

## Skipped

| Test | Reason |
|---|---|
| tests.server.storage.test_cli_load_forecast.test_success_prints_one_json_summary_line_and_loads_the_rows | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_cli_load_forecast.test_loading_twice_keeps_one_run_and_the_same_id | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_leaves_the_database_unchanged | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_forecast_load.test_load_forecast_export_loads_and_returns_the_summary | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_forecast_load.test_load_forecast_export_replaces_the_previous_run | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[truncated] | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[bad-version] | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[bad-grid] | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_forecast_load.test_load_forecast_export_on_a_fresh_database_migrates_first | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_load_with_replace_matches_file_counts | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_reload_without_replace_is_idempotent | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_pr_classification_one_row_per_pr_and_rework_set_on_merged | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_admin_viewer_id_resolves | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_load_with_a_forecast_export_puts_the_rows_in | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_load_without_an_export_reports_zero_forecast_rows_and_adds_none | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_a_load_without_an_export_leaves_an_existing_run_alone_and_replace_clears_it | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_reloading_with_a_second_export_leaves_exactly_one_run | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_a_foreign_export_is_refused_and_the_database_is_untouched | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_a_rollup_failure_rolls_the_forecast_rows_back_with_the_rest_of_the_load | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_the_forecast_is_written_before_the_rollup_check | set TALIUM_TEST_DSN to a *_synth database to run this test |
| tests.server.storage.test_synthetic_load.test_a_rollup_failure_keeps_the_previous_forecast_run | set TALIUM_TEST_DSN to a *_synth database to run this test |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| tests.server.api.test_budget_cycles.test_sprints_are_numbered_from_the_anchor_and_expose_the_next_one | pass | 0.075 |
| tests.server.api.test_budget_cycles.test_calendar_quarters_and_a_one_time_custom_cycle | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_sprint_history_extends_backwards_from_the_anchor | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_sprint_numbers_stop_at_one | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_sprint_before_its_anchor_has_no_past_periods_and_next_is_the_anchor_sprint | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_sprint_rolls_over_on_its_boundary_day | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_one_week_and_three_week_sprints | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_one_time_sprint_is_only_the_anchor_sprint | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_quarter_rolls_over_the_year | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_month_rolls_over_december_and_keys_are_stable | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_half_and_year_periods | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_calendar_history_defaults_to_six_and_is_oldest_first | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_leap_years | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_recurring_custom_repeats_its_length_from_the_start | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_recurring_custom_before_its_start_has_only_a_next_period | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_one_time_custom_before_during_and_after | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_single_day_custom_period | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_period_containing_for_calendar_and_sprint | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_period_containing_extends_before_the_anchor_and_start | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_period_containing_a_date_outside_a_one_time_cycle_returns_its_single_period | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs0-unknown cycle kind] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs1-unknown recurrence] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs2-always recurring] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs3-always recurring] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs4-anchor_date] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs5-sprint_weeks] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs6-sprint_weeks] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs7-sprint_weeks] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs8-sprint_weeks] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs9-anchor_number] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs10-calendar date] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs11-calendar date] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs12-custom_end] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs13-anchor_date] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_rejects[kwargs14-before anchor_date] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_validate_normalises_and_drops_fields_the_kind_does_not_use | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_label_map_is_one_to_one | pass | 0.002 |
| tests.server.api.test_budget_cycles.test_periods_rejects_a_bad_history[0] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_periods_rejects_a_bad_history[-1] | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_periods_rejects_a_bad_history[25] | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_periods_rejects_a_bad_history[True] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_periods_rejects_a_bad_history[6] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_periods_rejects_a_bad_history[2.0] | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_periods_accepts_the_history_bounds | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_no_saved_cycle_is_the_default_monthly_cycle | pass | 1.575 |
| tests.server.api.test_budget_cycles.test_a_default_label_other_than_monthly_with_no_saved_cycle_is_rejected | pass | 0.682 |
| tests.server.api.test_budget_cycles.test_put_cycle_round_trips_and_a_second_put_replaces | pass | 0.555 |
| tests.server.api.test_budget_cycles.test_cycles_are_per_org | pass | 0.552 |
| tests.server.api.test_budget_cycles.test_a_datetime_today_is_reduced_to_its_utc_date | pass | 0.001 |
| tests.server.api.test_budget_cycles.test_anchor_number_and_dates_are_bounded | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_history_larger_than_the_available_past_returns_what_exists | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_periods_view_reports_exact_instants_and_the_current_to_is_now | pass | 0.000 |
| tests.server.api.test_budget_cycles.test_a_corrupt_saved_row_falls_back_to_the_monthly_default_and_logs[sprint-recurring-None] | pass | 0.753 |
| tests.server.api.test_budget_cycles.test_a_corrupt_saved_row_falls_back_to_the_monthly_default_and_logs[weekly-recurring-None] | pass | 0.598 |
| tests.server.api.test_budget_cycles.test_a_corrupt_saved_row_falls_back_to_the_monthly_default_and_logs[month-one-time-None] | pass | 0.918 |
| tests.server.api.test_budget_cycles.test_put_cycle_reports_what_a_save_did_to_budget_amounts | pass | 0.573 |
| tests.server.api.test_budget_cycle_migration.test_budget_cycle_migration_up_down | pass | 1.842 |
| tests.server.api.test_app_budget_cycle.test_every_route_without_a_viewer_header_is_401 | pass | 0.925 |
| tests.server.api.test_app_budget_cycle.test_an_unresolvable_viewer_is_401 | pass | 0.769 |
| tests.server.api.test_app_budget_cycle.test_a_plain_dev_cannot_read_or_write_the_cycle_but_can_read_the_periods | pass | 0.697 |
| tests.server.api.test_app_budget_cycle.test_get_with_no_saved_cycle_is_the_monthly_default | pass | 0.673 |
| tests.server.api.test_app_budget_cycle.test_put_a_sprint_cycle_round_trips_and_reports_the_label_change | pass | 0.738 |
| tests.server.api.test_app_budget_cycle.test_a_same_kind_edit_keeps_the_label_and_says_amounts_now_mean_the_new_period | pass | 0.796 |
| tests.server.api.test_app_budget_cycle.test_put_is_a_full_replace_and_drops_fields_the_kind_does_not_use | pass | 0.702 |
| tests.server.api.test_app_budget_cycle.test_put_rejects_an_invalid_cycle_with_a_400_and_keeps_the_saved_one | pass | 0.889 |
| tests.server.api.test_app_budget_cycle.test_put_rejects_a_malformed_body_with_a_422 | pass | 0.667 |
| tests.server.api.test_app_budget_cycle.test_budgets_follow_the_active_cycle_label | pass | 1.006 |
| tests.server.api.test_app_budget_cycle.test_periods_default_is_six_calendar_months_ending_with_the_current_one | pass | 0.945 |
| tests.server.api.test_app_budget_cycle.test_periods_under_a_sprint_cycle_and_the_history_param | pass | 0.794 |
| tests.server.api.test_app_budget_cycle.test_periods_before_a_future_anchor_are_empty_with_the_anchor_as_next | pass | 0.943 |
| tests.server.api.test_app_budget_cycle.test_periods_rejects_a_bad_history | pass | 0.780 |
| tests.server.api.test_app_budget_cycle.test_periods_of_a_finished_one_time_cycle_have_no_next | pass | 1.165 |
| tests.server.api.test_manager_budgets.test_period_bounds_monthly_returns_the_containing_calendar_month | pass | 0.001 |
| tests.server.api.test_manager_budgets.test_period_bounds_monthly_rolls_the_year_over_at_december | pass | 0.000 |
| tests.server.api.test_manager_budgets.test_period_bounds_resolves_the_other_calendar_labels | pass | 0.001 |
| tests.server.api.test_manager_budgets.test_period_bounds_takes_a_cycle_or_an_active_cycle_for_any_kind | pass | 0.001 |
| tests.server.api.test_manager_budgets.test_period_bounds_rejects_a_bare_sprint_or_custom_label | pass | 0.001 |
| tests.server.api.test_manager_budgets.test_period_bounds_rejects_an_unsupported_period_rather_than_guessing | pass | 0.000 |
| tests.server.api.test_manager_budgets.test_get_budget_is_none_when_unset_not_zero | pass | 0.690 |
| tests.server.api.test_manager_budgets.test_budget_round_trips | pass | 0.583 |
| tests.server.api.test_manager_budgets.test_budget_on_unknown_team_scope_is_rejected | pass | 0.554 |
| tests.server.api.test_manager_budgets.test_budget_on_unknown_repo_scope_is_rejected | pass | 0.545 |
| tests.server.api.test_manager_budgets.test_budget_on_a_real_team_scope_succeeds | pass | 0.602 |
| tests.server.api.test_manager_budgets.test_budget_on_a_real_repo_scope_succeeds | pass | 0.641 |
| tests.server.api.test_manager_budgets.test_unknown_scope_type_is_a_bad_request | pass | 0.508 |
| tests.server.api.test_manager_budgets.test_cli_refuses_a_file_that_still_has_an_allowances_section | pass | 0.012 |
| tests.server.api.test_manager_budgets.test_budgets_for_scopes_omits_scopes_with_no_row | pass | 0.515 |
| tests.server.api.test_manager_budgets.test_cli_import_writes_under_the_active_cycle_label[None-monthly] | pass | 0.739 |
| tests.server.api.test_manager_budgets.test_cli_import_writes_under_the_active_cycle_label[quarter-quarterly] | pass | 0.716 |
| tests.server.api.test_manager_aggregate.test_repo_rollup_two_users_local_repos_do_not_merge | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_repo_rollup_two_users_same_remote_do_merge | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_repo_rollup_counts_sessions_over_roots_only_sums_cost_over_every_row | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_repo_rollup_ignores_rows_with_no_repo | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_reasoning_share_is_none_cold_start_when_never_reported | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_reasoning_share_sums_and_computes_pct_once_reported | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_has_beat_reads_the_real_kind_to_count_tally_shape | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_sums_the_three_priced_buckets | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_never_counts_context_carry_or_ordinary_cache_writes | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_does_not_count_rework_segments | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_resume_bucket_is_the_write_minus_read_share_not_the_full_reload | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_resume_bucket_ignores_the_projected_subfield | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_skips_an_unpriced_request_bucket_rather_than_a_partial_sum | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_recoverable_is_capped_at_each_sessions_own_spend | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_recoverable_is_zero_for_a_session_with_no_known_spend | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_dominant_cause_matches_the_largest_bucket | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_recoverable_is_none_when_nothing_recoverable | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_recoverable_pct_is_none_not_a_div_by_zero_with_no_spend | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_cost_by_repo_key_sums_priced_segments_per_repo | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_cost_by_repo_key_is_all_or_nothing_per_repo | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_cost_by_repo_key_skips_segments_with_no_repo_and_empty_when_no_segments | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_habits_reconcile_to_recoverable_spend | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_model_tier_reads_the_family_name_substring | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_dominant_model_picks_the_most_used_by_total_tokens | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_reasoning_demand_plan_with_no_rework_scores_high | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_reasoning_demand_comprehend_high_single_file_misc_low | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_fit_good_high_tier_on_high_demand_plan_segment | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_segment_fit_over_high_tier_on_a_low_demand_mechanical_segment | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_fit_under_low_tier_on_a_revision_loop | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_segment_fit_good_mid_tier_regardless_of_demand | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_model_fit_is_none_cold_start_with_no_segments | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_model_fit_golden_session_s1_over_an_opus_session_on_mechanical_work | pass | 0.002 |
| tests.server.api.test_manager_aggregate.test_model_fit_savings_is_none_without_a_pricing_table | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_model_fit_savings_for_a_segment_dated_before_the_catalog_uses_the_earliest_row | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_model_fit_savings_is_none_when_the_reference_model_has_no_price | pass | 0.005 |
| tests.server.api.test_manager_aggregate.test_model_fit_golden_session_s2_under_a_haiku_session_driving_rework | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_model_fit_verdict_good_when_no_segment_is_flagged | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_pick_model_fit_verdict_under_wins_over_over_when_both_present | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_blended_cost_per_pr_is_none_with_no_merged_prs | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_blended_cost_per_pr_divides_scope_spend_by_merged_count | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_rework_rate_is_none_when_nothing_classified | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_rework_rate_averages_over_classified_prs_only | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_budget_pct_is_none_with_no_budget | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_budget_pct_divides_spend_by_window_budget | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_window_days_is_the_span_and_rejects_an_empty_window | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_budget_usd_micros_converts_decimal_and_string | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_window_budget_scales_by_window_days | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_window_budget_none_without_budget | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seat_allowance_full_window_when_seat_predates_it | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seat_allowance_prorates_from_seat_start | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seat_allowance_none_when_seat_starts_after_window_or_no_budget | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_in_grace_boundary | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_is_low_use_needs_allowance_band_and_no_grace | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_usage_pct_none_without_allowance | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_usage_status_bands | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seat_usage_status_turns_under_into_new_inside_grace_only | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_person_headline_status_is_on_track_only_when_nothing_flags | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_avg_session_minutes_root_only | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_sessions_with_no_output_pct | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_session_type_counts_root_only_and_omits_unset | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_dominant_segment_label_picks_most_turns | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seats_under_threshold_is_none_with_no_allowance | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seats_under_counts_members_below_the_band_including_idle_ones | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seats_under_respects_the_new_seat_grace_period | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_agent_task_mix_counts_root_sessions_per_agent_and_type | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_agent_task_mix_summed_per_type_equals_session_type_counts | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_velocity_weekly | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_linked_work_volume_counts_only_prs_with_linked_sessions | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_linked_work_volume_costs_a_session_shared_by_two_prs_once | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_linked_work_volume_sizes_every_statted_pr_by_default | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_linked_work_volume_can_still_cap_pr_size_when_configured | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_linked_work_volume_has_no_rate_without_linked_cost | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_cost_per_kloc_is_none_with_no_lines | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_cost_per_kloc_divides_cost_by_thousands_of_lines | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_cheapest_peer_returns_the_genuinely_lower_cost_per_kloc | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_cheapest_peer_skips_a_teammate_with_no_session_telemetry | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_cheapest_peer_requires_enough_linked_prs | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_cheapest_peer_requires_most_of_a_persons_work_to_be_linked | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_cheapest_peer_is_none_when_the_subject_is_not_comparable | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_cheapest_peer_respects_the_scale_band | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_group_rows_by_user | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_room_to_save_needs_both_the_share_and_the_floor | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_room_to_save_floor_scales_with_the_window | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_room_to_save_never_with_zero_spend | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_avg_time_to_merge_days | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_last_active_at_is_the_max_last_event_at | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_repo_contributors_and_merged_prs_by_repo | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_sort_desc_none_last | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_paginate_and_search_by_name | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_elapsed_utc_days_stop_at_now_or_the_exclusive_end | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_daily_spend_zero_fills_and_buckets_on_the_utc_day | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_daily_spend_counts_a_session_that_began_before_the_window_on_its_first_day | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_daily_spend_clamps_out_of_range_days_so_the_sum_still_reconciles | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_daily_spend_skips_unresolvable_rows_like_resolve_aggregate | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_daily_spend_empty_and_not_yet_started_windows | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_group_rows_by_agent_partitions_every_row_once | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_per_agent_resolved_cost_sums_to_the_whole_scope_cost | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_channel_totals_sums_across_sessions | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_channel_totals_incomplete_when_any_row_is_unpriced | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_channel_totals_is_none_cold_start_with_no_priced_rows_at_all | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_pre_post_open_cost_split_partitions_at_the_opened_timestamp | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_pre_post_open_cost_split_buckets_unknown_start_as_pre_open | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_feature_benchmark_ranks_subject_among_priced_peers | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_feature_benchmark_cold_start_below_min_peers | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_feature_benchmark_ignores_zero_spend_peers_and_the_subject_itself | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_feature_benchmark_none_when_subject_absent | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_a_whole_period_and_a_period_to_date_get_the_full_budget | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_a_window_that_only_resembles_a_period_is_rolling | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_rolling_windows_use_the_actual_length_of_the_period_containing_the_end | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_quarter_and_sprint_cycles_use_their_own_period | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_a_window_outside_a_one_time_cycle_is_prorated_over_the_cycle_length | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_window_budget_still_none_without_a_budget_for_any_cycle | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_a_bare_sprint_or_custom_label_cannot_size_a_window | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seat_allowance_in_a_full_period_window | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seat_allowance_in_a_rolling_window_uses_the_period_containing_the_end | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_seat_allowance_none_without_a_seat_budget_for_any_cycle | pass | 0.000 |
| tests.server.api.test_manager_service_org_overview.test_teams_over_budget_uses_spend_greater_than_budget | pass | 0.522 |
| tests.server.api.test_manager_service_org_overview.test_headline_budget_pct_is_window_scaled | pass | 0.536 |
| tests.server.api.test_manager_service_org_overview.test_the_same_spend_is_over_budget_only_on_the_shorter_window | pass | 0.532 |
| tests.server.api.test_manager_service_org_overview.test_org_overview_merged_prs_and_cost_per_pr | pass | 0.463 |
| tests.server.api.test_manager_service_org_overview.test_reasoning_share_rolls_up_from_the_fold | pass | 0.611 |
| tests.server.api.test_manager_service_org_overview.test_worth_a_look_flags_the_over_budget_team | pass | 0.559 |
| tests.server.api.test_manager_service_org_overview.test_org_overview_is_empty_scope_safe | pass | 0.566 |
| tests.server.api.test_manager_service_org_overview.test_agent_spend_is_on_the_precedence_chain_and_sums_to_the_headline | pass | 0.556 |
| tests.server.api.test_manager_service_org_overview.test_agent_spend_is_empty_for_an_empty_scope | pass | 0.896 |
| tests.server.api.test_manager_service_org_overview.test_an_agent_whose_rows_resolve_to_nothing_is_still_listed_with_no_money | pass | 0.565 |
| tests.server.api.test_manager_service_org_overview.test_daily_spend_sums_to_the_headline_on_mixed_cost_sources | pass | 0.537 |
| tests.server.api.test_manager_service_org_overview.test_daily_spend_stops_at_now_and_still_reconciles | pass | 0.545 |
| tests.server.api.test_manager_service_org_overview.test_daily_spend_is_zero_filled_for_an_empty_scope | pass | 0.525 |
| tests.server.api.test_manager_service_org_overview.test_agent_task_mix_matches_the_usage_breakdown_per_type | pass | 0.652 |
| tests.server.api.test_manager_service_org_overview.test_agent_task_mix_is_empty_with_no_typed_sessions | pass | 0.608 |
| tests.server.api.test_manager_service_org_overview.test_whole_period_and_period_to_date_windows_compare_with_the_full_budget | pass | 0.634 |
| tests.server.api.test_manager_service_org_overview.test_a_window_ending_at_midnight_on_the_first_is_measured_in_the_month_it_ended_in | pass | 0.633 |
| tests.server.api.test_manager_service_org_overview.test_no_budget_cycle_row_is_the_monthly_default | pass | 0.517 |
| tests.server.api.test_manager_service_org_overview.test_a_quarterly_cycle_reads_quarterly_budgets_and_ignores_monthly_ones | pass | 0.695 |
| tests.server.api.test_manager_service_org_overview.test_a_sprint_cycle_measures_against_the_sprint_budget | pass | 0.577 |
| tests.server.api.test_manager_service_org_overview.test_a_corrupt_saved_cycle_falls_back_to_the_monthly_default | pass | 0.569 |
| tests.server.api.test_manager_service_tables.test_teams_table_sorts_by_spend_descending_by_default | pass | 0.511 |
| tests.server.api.test_manager_service_tables.test_teams_table_paginates | pass | 0.541 |
| tests.server.api.test_manager_service_tables.test_teams_table_rejects_an_unknown_sort_key | pass | 0.596 |
| tests.server.api.test_manager_service_tables.test_teams_table_rows_carry_budget_and_member_fields | pass | 0.731 |
| tests.server.api.test_manager_service_tables.test_teams_table_budget_pct_is_window_scaled | pass | 0.587 |
| tests.server.api.test_manager_service_tables.test_teams_table_no_longer_sorts_by_utilization | pass | 0.551 |
| tests.server.api.test_manager_service_tables.test_teams_table_search_by_name | pass | 0.543 |
| tests.server.api.test_manager_service_tables.test_projects_table_one_row_per_canonical_repo | pass | 0.471 |
| tests.server.api.test_manager_service_tables.test_teams_table_to_date_and_whole_period_windows_use_the_full_budget | pass | 0.556 |
| tests.server.api.test_manager_service_tables.test_teams_table_under_a_quarterly_cycle_has_no_budget_until_entered | pass | 0.525 |
| tests.server.api.test_manager_service_team_detail.test_room_to_save_uses_recoverable_share_and_scaled_floor | pass | 0.506 |
| tests.server.api.test_manager_service_team_detail.test_seat_usage_is_spend_over_the_window_scaled_seat_allowance | pass | 0.512 |
| tests.server.api.test_manager_service_team_detail.test_a_seat_over_its_allowance_is_never_on_track | pass | 0.565 |
| tests.server.api.test_manager_service_team_detail.test_idle_member_is_listed_and_counts_as_low_use | pass | 0.519 |
| tests.server.api.test_manager_service_team_detail.test_new_member_in_grace_is_not_low_use_and_is_prorated | pass | 0.506 |
| tests.server.api.test_manager_service_team_detail.test_no_team_budget_means_no_seat_usage | pass | 0.505 |
| tests.server.api.test_manager_service_team_detail.test_team_budget_pct_is_window_scaled | pass | 0.676 |
| tests.server.api.test_manager_service_team_detail.test_worth_a_look_names_the_room_to_save_person | pass | 0.633 |
| tests.server.api.test_manager_service_team_detail.test_team_budget_pct_for_a_period_to_date_window_uses_the_full_budget | pass | 0.528 |
| tests.server.api.test_manager_service_team_detail.test_seat_allowance_in_a_to_date_window_is_prorated_to_the_period_end | pass | 0.524 |
| tests.server.api.test_manager_service_team_detail.test_sprint_cycle_team_detail_seat_and_team_budgets | pass | 0.548 |
| tests.server.api.test_service_overview.test_overview_mixed_mode_range | pass | 0.481 |
| tests.server.api.test_service_overview.test_overview_repos_group_by_repo_id_not_the_fragile_repo_key | pass | 0.497 |
| tests.server.api.test_service_overview.test_overview_subagent_fanout_counts_one_session_but_sums_all_tokens | pass | 0.537 |
| tests.server.api.test_service_overview.test_overview_otel_only_range_is_structurally_unavailable_not_zero | pass | 0.471 |
| tests.server.api.test_service_overview.test_overview_folded_jsonl_only_session_counts_as_interactive | pass | 0.460 |
| tests.server.api.test_service_overview.test_overview_jsonl_only_range_has_no_by_query_source | pass | 0.496 |
| tests.server.api.test_service_overview.test_overview_pre_tick_session_is_jsonl_capable_not_unavailable | pass | 0.461 |
| tests.server.api.test_service_overview.test_overview_empty_range | pass | 0.552 |
| tests.server.api.test_service_overview.test_overview_merged_prs_and_cost_per_pr | pass | 0.629 |
| tests.server.api.test_service_overview.test_overview_recoverable_spend | pass | 0.573 |
| tests.server.api.test_service_overview.test_overview_seat_is_team_budget_per_member_window_scaled | pass | 0.756 |
| tests.server.api.test_service_overview.test_overview_seat_none_when_team_has_no_budget | pass | 0.683 |
| tests.server.api.test_service_overview.test_overview_seat_for_a_period_to_date_window_is_the_full_period_allowance | pass | 0.648 |
| tests.server.api.test_service_overview.test_overview_seat_reads_the_budget_of_the_active_cycle | pass | 0.677 |
| tests.server.api.test_manager_service_economics.test_unknown_scope_type_is_a_bad_request | pass | 0.626 |
| tests.server.api.test_manager_service_economics.test_team_scope_computes_dist_envelope_and_rework_ratio | pass | 0.604 |
| tests.server.api.test_manager_service_economics.test_team_scope_with_no_activity_is_all_cold_start_not_zero | pass | 0.573 |
| tests.server.api.test_manager_service_economics.test_repo_scope_only_counts_that_repos_own_activity | pass | 0.602 |
| tests.server.api.test_manager_service_economics.test_budget_burn_forecasts_to_the_month_end_not_the_window_end | pass | 0.620 |
| tests.server.api.test_manager_service_economics.test_org_scope_reconciles_with_a1_headline | pass | 0.680 |
| tests.server.api.test_manager_service_economics.test_budget_burn_stays_on_the_calendar_month_under_every_budget_cycle | pass | 0.603 |
| tests.server.api.test_forecast_endpoint.test_response_shape_presets_and_series_order | pass | 0.732 |
| tests.server.api.test_forecast_endpoint.test_actual_daily_is_zero_filled_from_28_days_before_the_first_period | pass | 0.764 |
| tests.server.api.test_forecast_endpoint.test_forecast_is_null_when_no_run_is_loaded | pass | 0.764 |
| tests.server.api.test_forecast_endpoint.test_forecast_is_null_for_a_scope_outside_the_run | pass | 0.768 |
| tests.server.api.test_forecast_endpoint.test_the_latest_loaded_run_wins | pass | 0.908 |
| tests.server.api.test_forecast_endpoint.test_gate_behaves_as_economics | pass | 0.907 |
| tests.server.api.test_forecast_endpoint.test_unknown_scope_type_is_a_bad_request | pass | 0.726 |
| tests.server.api.test_forecast_endpoint.test_actual_daily_buckets_by_the_run_timezone | pass | 0.774 |
| tests.server.api.test_forecast_endpoint.test_actual_daily_equals_the_economics_fetch_for_the_same_window | pass | 0.595 |
| tests.server.api.test_forecast_endpoint.test_actual_daily_matches_economics_budget_burn_in_a_utc_run | pass | 0.615 |
| tests.server.api.test_forecast_endpoint.test_mode_counts_the_sessions_the_actuals_came_from | pass | 0.742 |
| tests.server.api.test_forecast_endpoint.test_team_and_repo_scopes_use_their_own_session_sets | pass | 0.834 |
| tests.server.api.test_forecast_endpoint.test_current_and_future_period_numbers | pass | 1.652 |
| tests.server.api.test_forecast_endpoint.test_baseline_delta_is_zero_and_presets_move_the_landing | pass | 0.840 |
| tests.server.api.test_forecast_endpoint.test_month_end_data_has_no_current_period | pass | 0.740 |
| tests.server.api.test_forecast_endpoint.test_incomplete_status_has_no_numbers | pass | 0.911 |
| tests.server.api.test_forecast_endpoint.test_budget_edit_changes_probability_and_runway_on_the_next_request | pass | 0.962 |
| tests.server.api.test_forecast_endpoint.test_runway_is_exhausted_when_the_budget_is_already_spent | pass | 0.844 |
| tests.server.api.test_forecast_endpoint.test_non_finite_budget_is_no_budget_and_negative_is_exhausted | pass | 0.756 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[bad_timezone] | pass | 0.739 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[long_timezone_name] | pass | 0.774 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[meta_not_an_object] | pass | 0.740 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[missing_band] | pass | 0.866 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[missing_series_row] | pass | 0.829 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[missing_tokens_by_model] | pass | 0.785 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[non_numeric_total] | pass | 0.722 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[non_string_timezone] | pass | 0.706 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[periods_not_an_array] | pass | 0.712 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[preset_without_description] | pass | 0.690 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[short_daily_array] | pass | 0.738 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[short_quantile_row] | pass | 0.747 |
| tests.server.api.test_forecast_endpoint.test_an_unreadable_stored_run_is_null_never_a_500[unknown_status] | pass | 0.721 |
| tests.server.api.test_forecast_endpoint.test_a_damaged_row_of_another_scope_does_not_break_this_one | pass | 0.868 |
| tests.server.api.test_forecast_endpoint.test_the_forecast_budget_is_per_calendar_month_under_every_cycle[cycle0-1000000000] | pass | 1.172 |
| tests.server.api.test_forecast_endpoint.test_the_forecast_budget_is_per_calendar_month_under_every_cycle[cycle1-336956522] | pass | 0.951 |
| tests.server.api.test_forecast_endpoint.test_the_forecast_budget_is_per_calendar_month_under_every_cycle[cycle2-2214285714] | pass | 0.809 |
| tests.server.api.test_forecast_endpoint.test_the_forecast_has_no_budget_until_one_is_entered_for_the_new_cadence | pass | 1.147 |
| tests.server.api.test_forecast_plan_endpoint.test_rows_are_exactly_the_runs_teams_then_repos_with_export_names | pass | 0.954 |
| tests.server.api.test_forecast_plan_endpoint.test_needed_and_gap_at_the_configured_confidence | pass | 2.281 |
| tests.server.api.test_forecast_plan_endpoint.test_gap_is_null_without_a_budget_and_a_budget_edit_moves_it | pass | 0.826 |
| tests.server.api.test_forecast_plan_endpoint.test_presets_change_the_figures | pass | 0.712 |
| tests.server.api.test_forecast_plan_endpoint.test_tokens_are_passed_through_from_the_export | pass | 0.743 |
| tests.server.api.test_forecast_plan_endpoint.test_the_planner_reads_only_total_and_tokens | pass | 0.795 |
| tests.server.api.test_forecast_plan_endpoint.test_current_period_uses_the_scopes_own_session_set | pass | 0.836 |
| tests.server.api.test_forecast_plan_endpoint.test_month_end_data_has_no_current_period | pass | 0.802 |
| tests.server.api.test_forecast_plan_endpoint.test_incomplete_scope_has_no_period_figures | pass | 0.692 |
| tests.server.api.test_forecast_plan_endpoint.test_gate_matches_the_teams_table | pass | 0.875 |
| tests.server.api.test_forecast_plan_endpoint.test_no_run_is_null_data_with_unavailable | pass | 0.732 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_runs SET meta = jsonb_set(meta, '{timezone}', '"… | pass | 0.658 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{… | pass | 0.692 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{… | pass | 0.822 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_scope_series SET payload = payload #- '{periods,… | pass | 0.760 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{… | pass | 0.688 |
| tests.server.api.test_forecast_plan_endpoint.test_an_unreadable_run_is_unavailable_never_a_500[DELETE FROM forecast_scope_series WHERE scope_id = 'platform' AN… | pass | 0.710 |
| tests.server.api.test_forecast_plan_endpoint.test_plan_budgets_are_per_calendar_month_under_a_non_monthly_cycle | pass | 0.770 |
| tests.server.api.test_openapi_contract.test_committed_openapi_matches_the_live_schema | pass | 0.591 |
| tests.server.api.test_openapi_contract.test_pricing_reference_port_is_typed_in_the_schema | pass | 0.539 |
| tests.server.api.test_openapi_contract.test_forecast_routes_are_typed_in_the_schema | pass | 0.545 |
| tests.server.api.test_schemas_contract.test_envelope_carries_data_and_an_aggregate_mode_block | pass | 0.002 |
| tests.server.api.test_schemas_contract.test_envelope_null_data_with_unavailable_is_the_dark_whole_block_shape | pass | 0.001 |
| tests.server.api.test_schemas_contract.test_money_reports_basis_and_completeness_not_a_bare_number | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_sourced_is_generic_over_its_value | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_unavailable_sub_block_shape | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_models_forbid_unknown_fields | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_forecast_models_forbid_unknown_fields_and_carry_no_presentation | pass | 0.001 |
| tests.server.api.test_schemas_contract.test_forecast_envelopes_wrap_the_models_like_every_manager_view | pass | 0.001 |
| tests.server.storage.test_cli_db.test_help_exits_zero | pass | 0.003 |
| tests.server.storage.test_cli_db.test_unknown_verb_returns_error_code | pass | 0.009 |
| tests.server.storage.test_cli_db.test_import_reports_deferred | pass | 0.008 |
| tests.server.storage.test_cli_db.test_org_token_issue_prints_the_key_once_and_stores_only_its_hash | pass | 0.445 |
| tests.server.storage.test_cli_db.test_org_token_revoke_by_hash_prefix | pass | 0.448 |
| tests.server.storage.test_cli_db.test_org_token_revoke_refuses_an_unknown_prefix | pass | 0.464 |
| tests.server.storage.test_cli_db.test_org_token_list_shows_state | pass | 0.450 |
| tests.server.storage.test_cli_load_forecast.test_success_prints_one_json_summary_line_and_loads_the_rows | skip | 0.000 |
| tests.server.storage.test_cli_load_forecast.test_loading_twice_keeps_one_run_and_the_same_id | skip | 0.000 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_exits_2_with_nothing_on_stdout[missing-cannot read forecast export] | pass | 0.003 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_exits_2_with_nothing_on_stdout[truncated-not valid JSON] | pass | 0.003 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_exits_2_with_nothing_on_stdout[bad-version-unsupported schema version 'dashboard-forecast/2.0'] | pass | 0.003 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_exits_2_with_nothing_on_stdout[not-object-JSON object] | pass | 0.002 |
| tests.server.storage.test_cli_load_forecast.test_a_non_synth_dsn_exits_2_without_connecting | pass | 0.002 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_leaves_the_database_unchanged | skip | 0.000 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv0] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv1] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv2] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv3] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv4] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_help_exits_zero | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_an_unexpected_failure_exits_1_with_a_traceback_on_stderr | pass | 0.004 |
| tests.server.storage.test_cli_load_forecast.test_the_wrapper_script_is_executable_and_runs_the_right_module | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_the_wrapper_runs_the_cli_end_to_end | pass | 1.012 |
| tests.server.storage.test_config.test_defaults | pass | 0.001 |
| tests.server.storage.test_config.test_load_interpolates_env | pass | 0.002 |
| tests.server.storage.test_engine.test_get_engine_requires_cfg_first | pass | 0.000 |
| tests.server.storage.test_engine.test_session_scope_commits | pass | 0.038 |
| tests.server.storage.test_er.test_er_lists_tables_and_columns | pass | 0.001 |
| tests.server.storage.test_errors.test_errors_are_exceptions | pass | 0.000 |
| tests.server.storage.test_forecast_load.test_valid_document_gives_the_run_id_rows_and_meta | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_run_id_is_stable_and_changes_with_the_bytes | pass | 0.008 |
| tests.server.storage.test_forecast_load.test_incomplete_entries_have_an_empty_payload_and_other_statuses_keep_numbers | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_a_higher_minor_with_extra_keys_is_accepted_and_the_keys_are_kept | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_scope_names_and_order_are_preserved_in_meta | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_zero_org_scopes_are_accepted | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_a_missing_file_is_refused | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_a_directory_is_refused | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[-not valid JSON] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{-not valid JSON] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"schema": "dashboard-forecast/1.0"-not valid JSON] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[\xff\xfe\x00-not valid JSON] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"a": NaN}-not valid JSON] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"a": Infinity}-not valid JSON] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"a": -Infinity}-not valid JSON] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"a": 1e999}-outside the representable range] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"name": "a\u0000b"}-NUL character] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"a\u0000": 1}-NUL character] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"name": "\ud800"}-unpaired surrogate] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"a": [{"b": "x\u0000"}]}-NUL character] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"a": [1, [2, 1e999]]}-outside the representable range] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"a": ["ok", ["\ud800"]]}-unpaired surrogate] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[… | pass | 0.065 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[[]-JSON object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused["dashboard-forecast/1.0"-JSON object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[null-JSON object] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[42-JSON object] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_a_truncated_real_document_is_refused | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema not a string] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema null] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema no minor] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema three parts] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema other family] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema leading zero] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema non ascii digit] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema major 2] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema major 10] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema major 0] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing generated_at] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing timezone] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing origin] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing data_through] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing band] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing quantile_grid] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing dates] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing periods] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing presets] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing scopes] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at out of range] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema with a trailing newline] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[date with a trailing newline] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at garbage] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at naive] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at date only] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[timezone empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[timezone not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin not a string] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin wrong format] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin basic format] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin week date] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin not a calendar date] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[data_through feb 30] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin after data_through] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band not an object] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band high missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low a float] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low a bool] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low not in grid] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band high not in grid] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low equals high] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low above high] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid too short] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid has a float] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid has a bool] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid has a string] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid does not start at 0] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid does not end at 100] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid not increasing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid decreasing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[dates not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[dates empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[dates entry not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[dates entry malformed] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[dates not consecutive] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[dates descending] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[dates repeated] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[periods not a list0] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[periods empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period start missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period end missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period from missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period from before start] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period from after end] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period from outside start..end] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period from not a date] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period from not on the date axis] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period end not on the date axis] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[presets not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[presets empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset id missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset id empty] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset id not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset id duplicated] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset label missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset label not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scopes not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scopes empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope type missing] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope type unknown] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope type not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope id missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope id empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope id not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope name missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope name empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope duplicated] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[two org scopes] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series lacks a preset] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series has an extra preset] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series key renamed] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series entry not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[status missing] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[status unknown] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[status not a string] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[status case differs] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily low missing] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily expected not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily expected too short] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily high too long] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[periods missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[periods not a list1] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period count too small] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period count too large] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period entry not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative expected missing] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative expected wrong length] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative expected for the wrong period] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative quantiles missing] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative quantiles one row short] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative quantile row too short] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative quantile row not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[total missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[total quantiles missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[total quantiles wrong length] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_the_unmodified_document_passes_the_same_harness | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_refused_version_names_the_version_found | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_an_incomplete_entry_needs_no_numbers | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_check_against_dataset_accepts_a_matching_export | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_check_against_dataset_names_every_unknown_id | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_check_against_dataset_without_an_org_scope_only_checks_teams_and_repos | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_the_synth_guard_refuses_a_non_synth_database_before_any_connection | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_an_unparseable_dsn_is_refused | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_a_malformed_file_never_reaches_the_database | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_replace_run_rejects_a_zero_batch_size | pass | 0.000 |
| tests.server.storage.test_forecast_load.test_replace_run_stores_exactly_the_expected_rows | pass | 0.648 |
| tests.server.storage.test_forecast_load.test_replace_run_stores_incomplete_as_an_empty_payload | pass | 0.684 |
| tests.server.storage.test_forecast_load.test_an_extra_series_key_survives_the_database | pass | 0.549 |
| tests.server.storage.test_forecast_load.test_reloading_the_identical_file_is_idempotent_and_loaded_at_moves | pass | 0.585 |
| tests.server.storage.test_forecast_load.test_loading_a_second_different_export_leaves_exactly_one_run | pass | 0.545 |
| tests.server.storage.test_forecast_load.test_replace_run_never_commits_the_caller_owns_the_transaction | pass | 0.486 |
| tests.server.storage.test_forecast_load.test_a_failure_in_a_later_batch_rolls_everything_back_and_keeps_the_previous_run | pass | 0.535 |
| tests.server.storage.test_forecast_load.test_a_failure_while_inserting_the_run_row_keeps_the_previous_run | pass | 0.614 |
| tests.server.storage.test_forecast_load.test_two_concurrent_replaces_are_serialised_and_leave_one_run | pass | 1.581 |
| tests.server.storage.test_forecast_load.test_load_forecast_export_loads_and_returns_the_summary | skip | 0.000 |
| tests.server.storage.test_forecast_load.test_load_forecast_export_replaces_the_previous_run | skip | 0.000 |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[truncated] | skip | 0.000 |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[bad-version] | skip | 0.000 |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[bad-grid] | skip | 0.000 |
| tests.server.storage.test_forecast_load.test_load_forecast_export_on_a_fresh_database_migrates_first | skip | 0.000 |
| tests.server.storage.test_forecast_schema.test_models_are_registered_on_the_shared_base | pass | 0.000 |
| tests.server.storage.test_forecast_schema.test_forecast_runs_columns_types_and_key | pass | 0.000 |
| tests.server.storage.test_forecast_schema.test_forecast_scope_series_columns_types_and_composite_key | pass | 0.000 |
| tests.server.storage.test_forecast_schema.test_forecast_tables_have_no_foreign_keys_or_extra_indexes | pass | 0.000 |
| tests.server.storage.test_forecast_schema.test_migration_follows_the_previous_head_and_the_history_has_one_head | pass | 0.015 |
| tests.server.storage.test_forecast_schema.test_migration_applies_from_the_previous_head_and_downgrades_cleanly | pass | 1.251 |
| tests.server.storage.test_forecast_schema.test_no_model_drift_after_migrate | pass | 1.335 |
| tests.server.storage.test_forecast_schema.test_cli_db_status_reports_no_drift_after_migrate | pass | 1.100 |
| tests.server.storage.test_git_queries.test_catalog_loads_git_queries | pass | 0.078 |
| tests.server.storage.test_git_queries.test_land_delivery_dedups_on_delivery_id | pass | 0.479 |
| tests.server.storage.test_git_queries.test_upsert_artifact_idempotent_same_payload_twice | pass | 0.456 |
| tests.server.storage.test_git_queries.test_upsert_artifact_out_of_order_older_update_never_wins | pass | 0.445 |
| tests.server.storage.test_git_queries.test_upsert_repo_and_installation_upsert_on_conflict | pass | 0.460 |
| tests.server.storage.test_git_queries.test_prune_deliveries_only_removes_done_and_unmapped | pass | 0.477 |
| tests.server.storage.test_git_queries.test_coverage_counts_reports_attributed_and_bot_prs | pass | 0.433 |
| tests.server.storage.test_git_schema.test_git_tables_declared_with_expected_columns | pass | 0.001 |
| tests.server.storage.test_git_schema.test_git_installations_id_is_not_autoincrement | pass | 0.000 |
| tests.server.storage.test_git_schema.test_git_pending_installs_declared_with_expected_columns | pass | 0.000 |
| tests.server.storage.test_git_schema.test_git_artifacts_and_deliveries_indexes_per_spec | pass | 0.000 |
| tests.server.storage.test_git_schema.test_migration_up_down_creates_and_drops_exactly_the_git_tables | pass | 1.310 |
| tests.server.storage.test_integration.test_public_api_imports | pass | 0.001 |
| tests.server.storage.test_integration.test_init_then_producer_roundtrip_is_idempotent | pass | 0.657 |
| tests.server.storage.test_integration.test_subagent_linkage_roundtrips_both_directions | pass | 0.480 |
| tests.server.storage.test_issue_links_schema.test_pr_issue_links_declared_with_expected_columns | pass | 0.001 |
| tests.server.storage.test_issue_links_schema.test_pr_issue_links_composite_primary_key_is_the_tracker_agnostic_issue_key_plus_pr | pass | 0.000 |
| tests.server.storage.test_issue_links_schema.test_pr_issue_links_reverse_index_per_spec | pass | 0.000 |
| tests.server.storage.test_issue_links_schema.test_no_foreign_keys | pass | 0.000 |
| tests.server.storage.test_issue_links_schema.test_migration_up_down_creates_and_drops_pr_issue_links | pass | 1.367 |
| tests.server.storage.test_operations.test_init_creates_tables_then_noops | pass | 0.482 |
| tests.server.storage.test_operations.test_init_force_recreates | pass | 0.387 |
| tests.server.storage.test_operations.test_status_reports | pass | 0.536 |
| tests.server.storage.test_otel_schema.test_migration_from_empty_builds_the_otel_tables | pass | 0.342 |
| tests.server.storage.test_otel_schema.test_series_and_point_roundtrip_with_jsonb_attrs | pass | 0.407 |
| tests.server.storage.test_otel_schema.test_replayed_datapoint_inserts_zero_rows | pass | 0.430 |
| tests.server.storage.test_otel_schema.test_replayed_api_request_inserts_zero_rows | pass | 0.442 |
| tests.server.storage.test_otel_schema.test_api_request_tolerates_a_sparse_record | pass | 0.444 |
| tests.server.storage.test_otel_schema.test_org_token_stores_only_the_hash | pass | 0.386 |
| tests.server.storage.test_pr_class_queries.test_catalog_loads_pr_class_queries | pass | 0.079 |
| tests.server.storage.test_pr_class_queries.test_upsert_session_git_link_idempotent_on_replay | pass | 0.414 |
| tests.server.storage.test_pr_class_queries.test_upsert_session_git_link_reclassifies_method_on_a_later_stronger_match | pass | 0.572 |
| tests.server.storage.test_pr_class_queries.test_sessions_for_artifact_and_links_for_session_are_reverse_views | pass | 0.578 |
| tests.server.storage.test_pr_class_queries.test_owning_prs_for_shas_finds_prs_by_commit_membership | pass | 0.544 |
| tests.server.storage.test_pr_class_queries.test_owning_prs_for_shas_excludes_unmerged_prs | pass | 0.474 |
| tests.server.storage.test_pr_class_queries.test_upsert_pr_classification_preserves_rework_ratio_set_by_sibling_module | pass | 0.452 |
| tests.server.storage.test_pr_class_queries.test_upsert_pr_classification_idempotent_on_replay | pass | 0.445 |
| tests.server.storage.test_pr_class_schema.test_tables_declared_with_expected_columns | pass | 0.001 |
| tests.server.storage.test_pr_class_schema.test_session_git_links_composite_primary_key | pass | 0.000 |
| tests.server.storage.test_pr_class_schema.test_indexes_per_spec | pass | 0.000 |
| tests.server.storage.test_pr_class_schema.test_no_foreign_keys | pass | 0.001 |
| tests.server.storage.test_pr_class_schema.test_migration_up_down_creates_and_drops_exactly_these_tables | pass | 1.180 |
| tests.server.storage.test_producer_store.test_upsert_get_roundtrip | pass | 0.412 |
| tests.server.storage.test_producer_store.test_upsert_is_idempotent | pass | 0.531 |
| tests.server.storage.test_producer_store.test_touch_and_revoke | pass | 0.347 |
| tests.server.storage.test_queries.test_load_and_get | pass | 0.003 |
| tests.server.storage.test_queries.test_missing_query_raises_with_listing | pass | 0.002 |
| tests.server.storage.test_queries.test_empty_statement_rejected | pass | 0.002 |
| tests.server.storage.test_queries.test_duplicate_rejected | pass | 0.002 |
| tests.server.storage.test_queries.test_non_mapping_yaml_rejected | pass | 0.002 |
| tests.server.storage.test_queue_queries.test_catalog_loads_ingest_and_fold_queries | pass | 0.083 |
| tests.server.storage.test_queue_queries.test_ensure_session_creates_once_and_dedups | pass | 0.393 |
| tests.server.storage.test_queue_queries.test_ring_rollup_twice_coalesces_to_one_pending_job | pass | 0.412 |
| tests.server.storage.test_queue_queries.test_claim_job_skip_locked_hands_to_exactly_one | pass | 0.451 |
| tests.server.storage.test_queue_schema.test_no_event_queue_table | pass | 0.000 |
| tests.server.storage.test_queue_schema.test_queue_jobs_columns_and_indexes | pass | 0.000 |
| tests.server.storage.test_queue_schema.test_partial_unique_index_rejects_second_pending | pass | 0.353 |
| tests.server.storage.test_queue_schema.test_partial_unique_index_allows_done_plus_new_pending | pass | 0.400 |
| tests.server.storage.test_readme.test_readme_has_required_sections | pass | 0.001 |
| tests.server.storage.test_schema_dump.test_ddl_contains_all_tables | pass | 0.006 |
| tests.server.storage.test_schema_dump.test_ddl_has_generated_columns | pass | 0.005 |
| tests.server.storage.test_schema_dump.test_ddl_matches_golden | pass | 0.007 |
| tests.server.storage.test_schema_metadata.test_all_tables_present | pass | 0.001 |
| tests.server.storage.test_schema_metadata.test_producers_has_generated_key_columns | pass | 0.000 |
| tests.server.storage.test_schema_metadata.test_sessions_has_subagent_linkage | pass | 0.000 |
| tests.server.storage.test_schema_metadata.test_no_foreign_keys | pass | 0.000 |
| tests.server.storage.test_session_info_scalar_columns_migration.test_migration_backfills_matching_session_scalars | pass | 1.254 |
| tests.server.storage.test_session_scalars.test_priced_total_reads_the_key_missing_null_or_unpriced_is_zero | pass | 0.001 |
| tests.server.storage.test_session_scalars.test_recoverable_buckets_are_uncapped_and_default_to_zero | pass | 0.000 |
| tests.server.storage.test_session_scalars.test_reasoning_tokens_none_means_never_estimated_not_zero | pass | 0.000 |
| tests.server.storage.test_session_scalars.test_has_files | pass | 0.000 |
| tests.server.storage.test_session_scalars.test_segment_turns_number_list_and_missing | pass | 0.000 |
| tests.server.storage.test_session_scalars.test_dominant_segment_label_first_max_wins_on_a_tie | pass | 0.000 |
| tests.server.storage.test_session_scalars.test_dominant_segment_label_handles_list_turns_and_missing_label | pass | 0.000 |
| tests.server.storage.test_session_scalars.test_otel_vendor_cost_none_on_estimated_unknown_or_empty_basis | pass | 0.000 |
| tests.server.storage.test_session_scalars.test_fold_scalars_bundles_every_fold_owned_column | pass | 0.000 |
| tests.server.storage.test_session_scalars.test_scope_session_facts_matches_session_scalars_on_every_shape | pass | 0.520 |
| tests.server.storage.test_session_scalars.test_person_session_facts_channel_fragments | pass | 0.929 |
| tests.server.storage.test_session_scalars.test_scope_model_split_matches_model_split_over_the_full_rows | pass | 0.960 |
| tests.server.storage.test_synthetic_load.test_load_with_replace_matches_file_counts | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_reload_without_replace_is_idempotent | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_pr_classification_one_row_per_pr_and_rework_set_on_merged | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_admin_viewer_id_resolves | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_load_with_a_forecast_export_puts_the_rows_in | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_load_without_an_export_reports_zero_forecast_rows_and_adds_none | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_a_load_without_an_export_leaves_an_existing_run_alone_and_replace_clears_it | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_reloading_with_a_second_export_leaves_exactly_one_run | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_a_foreign_export_is_refused_and_the_database_is_untouched | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_a_rollup_failure_rolls_the_forecast_rows_back_with_the_rest_of_the_load | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_the_forecast_is_written_before_the_rollup_check | skip | 0.000 |
| tests.server.storage.test_synthetic_load.test_a_rollup_failure_keeps_the_previous_forecast_run | skip | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_guard_rejects_non_synth_names[postgresql+psycopg://user@localhost:5433/econ] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_guard_rejects_non_synth_names[postgresql+psycopg://user@localhost:5433/econ_synth_x] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_guard_rejects_non_synth_names[postgresql+psycopg://user@localhost:5433/] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_guard_accepts_synth_names[postgresql+psycopg://user@localhost:5433/econ_synth] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_guard_accepts_synth_names[postgresql+psycopg://user@localhost:5433/talium_test_synth] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_guard_rejects_unparseable_dsn | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_tamper_detected | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_schema_1_is_rejected_with_a_regenerate_message | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_unknown_schema_is_rejected | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_wrong_operation_detected | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_incomplete_status_detected | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_missing_file_detected | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_org_id_mismatch_detected | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_org_id_match_passes_through_to_dry_run | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_dry_run_never_calls_get_engine | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_list_turns_normalizes_to_its_length | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_normalize_segment_turns_tolerates_missing_shapes | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_a_pre_removal_dataset_listing_allowances_still_verifies_and_is_not_an_input | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_generated_dataset_is_schema_2_and_rollup_rows_feed_the_per_type_check | pass | 2.567 |
| tests.server.storage.test_synthetic_load_unit.test_daily_rollup_query_selects_input_and_cache_write_separately | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_match_when_the_writes_sum_to_the_database_cache_write | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes0] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes1] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes2] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes3] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes4] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes5] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_dry_run_with_an_export_plans_the_forecast_rows_and_never_connects | pass | 0.009 |
| tests.server.storage.test_synthetic_load_unit.test_dry_run_without_an_export_has_no_forecast_counts | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_an_export_for_another_org_is_refused_before_any_database_work | pass | 0.004 |
| tests.server.storage.test_synthetic_load_unit.test_unknown_team_and_repo_ids_are_named_in_the_refusal | pass | 0.005 |
| tests.server.storage.test_synthetic_load_unit.test_a_dry_run_also_refuses_a_foreign_export | pass | 0.004 |
| tests.server.storage.test_synthetic_load_unit.test_a_malformed_export_is_a_synthetic_load_error_and_touches_nothing[{-not valid JSON] | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_a_malformed_export_is_a_synthetic_load_error_and_touches_nothing[{"schema": "dashboard-forecast/2.0"}-unsupp… | pass | 0.021 |
| tests.server.storage.test_synthetic_load_unit.test_a_malformed_export_is_a_synthetic_load_error_and_touches_nothing[[]-JSON object] | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_a_missing_export_file_is_a_synthetic_load_error | pass | 0.024 |
| tests.server.storage.test_synthetic_load_unit.test_the_guard_still_comes_first | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_cli_dry_run_with_forecast_export_prints_the_planned_counts | pass | 0.008 |
| tests.server.storage.test_synthetic_load_unit.test_cli_exits_2_for_a_foreign_export | pass | 0.005 |
| tests.server.storage.test_synthetic_load_unit.test_cli_forecast_export_option_defaults_to_none | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_load_synthetic_row_counts_and_truncate_cover_the_forecast_tables | pass | 0.005 |

Reproduce:

```bash
venv/bin/python -m pytest tests/server/api/test_budget_cycles.py tests/server/api/test_budget_cycle_migration.py tests/server/api/test_app_budget_cycle.py tests/server/api/test_manager_budgets.py tests/server/api/test_manager_aggregate.py tests/server/api/test_manager_service_org_overview.py tests/server/api/test_manager_service_tables.py tests/server/api/test_manager_service_team_detail.py tests/server/api/test_service_overview.py tests/server/api/test_manager_service_economics.py tests/server/api/test_forecast_endpoint.py tests/server/api/test_forecast_plan_endpoint.py tests/server/api/test_openapi_contract.py tests/server/api/test_schemas_contract.py tests/server/storage --junitxml=docs/test/reports/overview-budget-cycles-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Budget cycles (Overview refresh, Track A backend)" \
    --junit docs/test/reports/overview-budget-cycles-junit.xml \
    --out   docs/test/reports/overview-budget-cycles-test-report.md \
    --tests 'tests/server/api/test_budget_cycles.py tests/server/api/test_budget_cycle_migration.py tests/server/api/test_app_budget_cycle.py tests/server/api/test_manager_budgets.py tests/server/api/test_manager_aggregate.py tests/server/api/test_manager_service_org_overview.py tests/server/api/test_manager_service_tables.py tests/server/api/test_manager_service_team_detail.py tests/server/api/test_service_overview.py tests/server/api/test_manager_service_economics.py tests/server/api/test_forecast_endpoint.py tests/server/api/test_forecast_plan_endpoint.py tests/server/api/test_openapi_contract.py tests/server/api/test_schemas_contract.py tests/server/storage'
```

Needs TALIUM_TEST_DSN pointing at a disposable database (the fixture drops all tables).

Front end at the same commit: vitest 168 passed, typecheck, lexicon and build pass.

Synth-stack check (clone of econ_synth, API + UI): passed. Budget figures matched by hand for whole-period, period-to-date and rolling windows under month, quarter, sprint and custom cycles.
