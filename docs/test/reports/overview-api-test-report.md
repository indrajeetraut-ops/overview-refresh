# Overview API (Track B) — Test Report

Run: `2026-10-14T16:54:31.070534+05:30`

- Total: 143  Passed: 143  Failed: 0  Skipped: 0
- Wall time (sum of cases): 13.29s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| tests.server.api.test_app_org_overview | 5 | 5 | 0 | 0 | 2.96 |
| tests.server.api.test_manager_aggregate | 106 | 106 | 0 | 0 | 0.02 |
| tests.server.api.test_manager_service_org_overview | 15 | 15 | 0 | 0 | 6.32 |
| tests.server.api.test_manager_service_org_usage | 6 | 6 | 0 | 0 | 2.41 |
| tests.server.api.test_openapi_contract | 3 | 3 | 0 | 0 | 1.57 |
| tests.server.api.test_schemas_contract | 8 | 8 | 0 | 0 | 0.01 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| tests.server.api.test_manager_service_org_overview.test_teams_over_budget_uses_spend_greater_than_budget | pass | 0.677 |
| tests.server.api.test_manager_service_org_overview.test_headline_budget_pct_is_window_scaled | pass | 0.424 |
| tests.server.api.test_manager_service_org_overview.test_the_same_spend_is_over_budget_only_on_the_shorter_window | pass | 0.433 |
| tests.server.api.test_manager_service_org_overview.test_org_overview_merged_prs_and_cost_per_pr | pass | 0.395 |
| tests.server.api.test_manager_service_org_overview.test_reasoning_share_rolls_up_from_the_fold | pass | 0.410 |
| tests.server.api.test_manager_service_org_overview.test_worth_a_look_flags_the_over_budget_team | pass | 0.396 |
| tests.server.api.test_manager_service_org_overview.test_org_overview_is_empty_scope_safe | pass | 0.365 |
| tests.server.api.test_manager_service_org_overview.test_agent_spend_is_on_the_precedence_chain_and_sums_to_the_headline | pass | 0.402 |
| tests.server.api.test_manager_service_org_overview.test_agent_spend_is_empty_for_an_empty_scope | pass | 0.367 |
| tests.server.api.test_manager_service_org_overview.test_an_agent_whose_rows_resolve_to_nothing_is_still_listed_with_no_money | pass | 0.422 |
| tests.server.api.test_manager_service_org_overview.test_daily_spend_sums_to_the_headline_on_mixed_cost_sources | pass | 0.431 |
| tests.server.api.test_manager_service_org_overview.test_daily_spend_stops_at_now_and_still_reconciles | pass | 0.396 |
| tests.server.api.test_manager_service_org_overview.test_daily_spend_is_zero_filled_for_an_empty_scope | pass | 0.384 |
| tests.server.api.test_manager_service_org_overview.test_agent_task_mix_matches_the_usage_breakdown_per_type | pass | 0.420 |
| tests.server.api.test_manager_service_org_overview.test_agent_task_mix_is_empty_with_no_typed_sessions | pass | 0.400 |
| tests.server.api.test_app_org_overview.test_org_overview_requires_a_viewer_header | pass | 0.544 |
| tests.server.api.test_app_org_overview.test_org_overview_rejects_a_plain_dev | pass | 0.521 |
| tests.server.api.test_app_org_overview.test_org_overview_as_admin_validates_against_the_envelope | pass | 0.772 |
| tests.server.api.test_app_org_overview.test_org_overview_rejects_an_unknown_period | pass | 0.568 |
| tests.server.api.test_app_org_overview.test_org_overview_rejects_no_timeframe_at_all | pass | 0.556 |
| tests.server.api.test_manager_aggregate.test_repo_rollup_two_users_local_repos_do_not_merge | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_repo_rollup_two_users_same_remote_do_merge | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_repo_rollup_counts_sessions_over_roots_only_sums_cost_over_every_row | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_repo_rollup_ignores_rows_with_no_repo | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_reasoning_share_is_none_cold_start_when_never_reported | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_reasoning_share_sums_and_computes_pct_once_reported | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_segment_has_beat_reads_the_real_kind_to_count_tally_shape | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_recoverable_sums_the_three_priced_buckets | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_never_counts_context_carry_or_ordinary_cache_writes | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_does_not_count_rework_segments | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_resume_bucket_is_the_write_minus_read_share_not_the_full_reload | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_resume_bucket_ignores_the_projected_subfield | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_skips_an_unpriced_request_bucket_rather_than_a_partial_sum | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_recoverable_is_capped_at_each_sessions_own_spend | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_is_zero_for_a_session_with_no_known_spend | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_recoverable_dominant_cause_matches_the_largest_bucket | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_is_none_when_nothing_recoverable | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_recoverable_pct_is_none_not_a_div_by_zero_with_no_spend | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_cost_by_repo_key_sums_priced_segments_per_repo | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_cost_by_repo_key_is_all_or_nothing_per_repo | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_cost_by_repo_key_skips_segments_with_no_repo_and_empty_when_no_segments | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_habits_reconcile_to_recoverable_spend | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_model_tier_reads_the_family_name_substring | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_dominant_model_picks_the_most_used_by_total_tokens | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_reasoning_demand_plan_with_no_rework_scores_high | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_reasoning_demand_comprehend_high_single_file_misc_low | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_fit_good_high_tier_on_high_demand_plan_segment | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_segment_fit_over_high_tier_on_a_low_demand_mechanical_segment | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_segment_fit_under_low_tier_on_a_revision_loop | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_segment_fit_good_mid_tier_regardless_of_demand | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_model_fit_is_none_cold_start_with_no_segments | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_model_fit_golden_session_s1_over_an_opus_session_on_mechanical_work | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_model_fit_savings_is_none_without_a_pricing_table | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_model_fit_savings_for_a_segment_dated_before_the_catalog_uses_the_earliest_row | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_model_fit_savings_is_none_when_the_reference_model_has_no_price | pass | 0.010 |
| tests.server.api.test_manager_aggregate.test_model_fit_golden_session_s2_under_a_haiku_session_driving_rework | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_model_fit_verdict_good_when_no_segment_is_flagged | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_pick_model_fit_verdict_under_wins_over_over_when_both_present | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_blended_cost_per_pr_is_none_with_no_merged_prs | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_blended_cost_per_pr_divides_scope_spend_by_merged_count | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_rework_rate_is_none_when_nothing_classified | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_rework_rate_averages_over_classified_prs_only | pass | 0.001 |
| tests.server.api.test_manager_aggregate.test_budget_pct_is_none_with_no_budget | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_budget_pct_divides_spend_by_window_budget | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_window_days_is_the_span_and_rejects_an_empty_window | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_budget_usd_micros_converts_decimal_and_string | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_window_budget_scales_by_window_days | pass | 0.000 |
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
| tests.server.api.test_manager_aggregate.test_cheapest_peer_requires_most_of_a_persons_work_to_be_linked | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_cheapest_peer_is_none_when_the_subject_is_not_comparable | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_cheapest_peer_respects_the_scale_band | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_group_rows_by_user | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_room_to_save_needs_both_the_share_and_the_floor | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_room_to_save_floor_scales_with_the_window | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_room_to_save_never_with_zero_spend | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_avg_time_to_merge_days | pass | 0.000 |
| tests.server.api.test_manager_aggregate.test_last_active_at_is_the_max_last_event_at | pass | 0.001 |
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
| tests.server.api.test_openapi_contract.test_committed_openapi_matches_the_live_schema | pass | 0.526 |
| tests.server.api.test_openapi_contract.test_pricing_reference_port_is_typed_in_the_schema | pass | 0.528 |
| tests.server.api.test_openapi_contract.test_forecast_routes_are_typed_in_the_schema | pass | 0.519 |
| tests.server.api.test_schemas_contract.test_envelope_carries_data_and_an_aggregate_mode_block | pass | 0.002 |
| tests.server.api.test_schemas_contract.test_envelope_null_data_with_unavailable_is_the_dark_whole_block_shape | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_money_reports_basis_and_completeness_not_a_bare_number | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_sourced_is_generic_over_its_value | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_unavailable_sub_block_shape | pass | 0.000 |
| tests.server.api.test_schemas_contract.test_models_forbid_unknown_fields | pass | 0.001 |
| tests.server.api.test_schemas_contract.test_forecast_models_forbid_unknown_fields_and_carry_no_presentation | pass | 0.001 |
| tests.server.api.test_schemas_contract.test_forecast_envelopes_wrap_the_models_like_every_manager_view | pass | 0.001 |
| tests.server.api.test_manager_service_org_usage.test_low_seat_flag_and_utilization_status | pass | 0.418 |
| tests.server.api.test_manager_service_org_usage.test_reasoning_share_rolls_up_from_the_fold | pass | 0.402 |
| tests.server.api.test_manager_service_org_usage.test_session_types_and_no_output_pct | pass | 0.400 |
| tests.server.api.test_manager_service_org_usage.test_org_usage_empty_scope_safe | pass | 0.375 |
| tests.server.api.test_manager_service_org_usage.test_spotlight_team_is_the_over_budget_one_expanded_to_people | pass | 0.428 |
| tests.server.api.test_manager_service_org_usage.test_idle_team_appears_as_under | pass | 0.389 |

Reproduce:

```bash
venv/bin/python -m pytest tests/server/api/test_manager_service_org_overview.py tests/server/api/test_app_org_overview.py tests/server/api/test_manager_aggregate.py tests/server/api/test_openapi_contract.py tests/server/api/test_schemas_contract.py tests/server/api/test_manager_service_org_usage.py --junitxml=docs/test/reports/overview-api-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Overview API (Track B)" \
    --junit docs/test/reports/overview-api-junit.xml \
    --out   docs/test/reports/overview-api-test-report.md \
    --tests 'tests/server/api/test_manager_service_org_overview.py tests/server/api/test_app_org_overview.py tests/server/api/test_manager_aggregate.py tests/server/api/test_openapi_contract.py tests/server/api/test_schemas_contract.py tests/server/api/test_manager_service_org_usage.py'
```

Needs TALIUM_TEST_DSN pointing at a disposable database (the fixture drops all tables).

Front end at the same commit: typecheck clean, vitest 113 passed, lexicon and build pass.
