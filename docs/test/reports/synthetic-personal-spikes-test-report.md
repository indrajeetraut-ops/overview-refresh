# Synthetic generator: personal spikes (Layer 1) — Test Report

Run: `2026-10-10T15:51:08.391333+05:30`

- Total: 378  Passed: 378  Failed: 0  Skipped: 0
- Wall time (sum of cases): 131.62s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| econ-forecast.tests.synthetic.org.test_cli_org | 11 | 11 | 0 | 0 | 15.26 |
| econ-forecast.tests.synthetic.org.test_content | 8 | 8 | 0 | 0 | 19.86 |
| econ-forecast.tests.synthetic.org.test_determinism | 3 | 3 | 0 | 0 | 1.84 |
| econ-forecast.tests.synthetic.org.test_expand_invariants | 13 | 13 | 0 | 0 | 21.86 |
| econ-forecast.tests.synthetic.org.test_git | 12 | 12 | 0 | 0 | 3.98 |
| econ-forecast.tests.synthetic.org.test_identity | 5 | 5 | 0 | 0 | 0.13 |
| econ-forecast.tests.synthetic.org.test_ids | 11 | 11 | 0 | 0 | 0.12 |
| econ-forecast.tests.synthetic.org.test_org_config | 25 | 25 | 0 | 0 | 2.55 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold | 8 | 8 | 0 | 0 | 22.61 |
| econ-forecast.tests.synthetic.org.test_personal_spikes_org | 1 | 1 | 0 | 0 | 1.00 |
| econ-forecast.tests.synthetic.org.test_repos_directory | 10 | 10 | 0 | 0 | 0.99 |
| econ-forecast.tests.synthetic.org.test_requests | 9 | 9 | 0 | 0 | 2.43 |
| econ-forecast.tests.synthetic.org.test_sessions | 10 | 10 | 0 | 0 | 1.51 |
| econ-forecast.tests.synthetic.org.test_work | 8 | 8 | 0 | 0 | 3.10 |
| econ-forecast.tests.synthetic.test_behavior | 18 | 18 | 0 | 0 | 0.07 |
| econ-forecast.tests.synthetic.test_cache_write_acceptance | 4 | 4 | 0 | 0 | 0.01 |
| econ-forecast.tests.synthetic.test_core | 5 | 5 | 0 | 0 | 6.23 |
| econ-forecast.tests.synthetic.test_curve_binding | 32 | 32 | 0 | 0 | 2.31 |
| econ-forecast.tests.synthetic.test_force | 6 | 6 | 0 | 0 | 3.17 |
| econ-forecast.tests.synthetic.test_interfaces | 3 | 3 | 0 | 0 | 9.96 |
| econ-forecast.tests.synthetic.test_personal_spikes | 81 | 81 | 0 | 0 | 7.24 |
| econ-forecast.tests.synthetic.test_regressions | 95 | 95 | 0 | 0 | 5.38 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| econ-forecast.tests.synthetic.org.test_cli_org.test_validate_only_writes_nothing | pass | 1.425 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_successful_generation_run | pass | 1.974 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_existing_output_exits_1 | pass | 1.304 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_bad_config_exits_2 | pass | 1.217 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_stderr_lines_are_json | pass | 1.328 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_retiming_shift_is_correct | pass | 1.749 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_end_date_gives_matching_series_end | pass | 1.751 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_chain_produces_all_four_datasets | pass | 1.725 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_curve_binding_resolves_against_the_config_dir | pass | 2.744 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_pricing_snapshot_is_self_contained | pass | 0.037 |
| econ-forecast.tests.synthetic.org.test_cli_org.test_pricing_files_rejects_an_unknown_version | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_content.test_schema_present_with_types | pass | 19.726 |
| econ-forecast.tests.synthetic.org.test_content.test_hit_ratio_formula | pass | 0.018 |
| econ-forecast.tests.synthetic.org.test_content.test_cost_consistency_i5 | pass | 0.012 |
| econ-forecast.tests.synthetic.org.test_content.test_segments_sum_to_session | pass | 0.016 |
| econ-forecast.tests.synthetic.org.test_content.test_counters_consistent_with_lists | pass | 0.008 |
| econ-forecast.tests.synthetic.org.test_content.test_request_micros_prices_the_lifetimes_separately | pass | 0.014 |
| econ-forecast.tests.synthetic.org.test_content.test_interrupted_events_price_from_the_split_and_keep_the_combined_tokens | pass | 0.013 |
| econ-forecast.tests.synthetic.org.test_content.test_default_cache_lifetime_is_one_hour | pass | 0.058 |
| econ-forecast.tests.synthetic.org.test_determinism.test_i2a_byte_identical_rerun | pass | 1.792 |
| econ-forecast.tests.synthetic.org.test_determinism.test_i2b_habits_only_change_leaves_structural_files_identical | pass | 0.004 |
| econ-forecast.tests.synthetic.org.test_determinism.test_i2b_per_session_token_totals_unchanged | pass | 0.043 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_expected_rollup_rows_carry_five_token_fields | pass | 0.681 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_org_manifest_is_schema_2 | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i1_per_type_in_process | pass | 20.455 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i1_per_type_catches_offsetting_perturbations[input-cache_write_5m] | pass | 0.135 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i1_per_type_catches_offsetting_perturbations[cache_write_5m-cache_write_1h] | pass | 0.132 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i1_per_type_catches_offsetting_perturbations[cache_write_1h-cache_read] | pass | 0.129 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i1_per_type_catches_offsetting_perturbations[cache_read-output] | pass | 0.130 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i1_per_type_catches_offsetting_perturbations[output-input] | pass | 0.130 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i1_rollup_exactness | pass | 0.003 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i3_referential_integrity | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i4_temporal_order | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_i5_cost_consistency | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_expand_invariants.test_no_allowances_file_is_emitted | pass | 0.063 |
| econ-forecast.tests.synthetic.org.test_git.test_artifact_keyset_matches_mapper | pass | 0.013 |
| econ-forecast.tests.synthetic.org.test_git.test_link_confidence_parity | pass | 0.000 |
| econ-forecast.tests.synthetic.org.test_git.test_numbering_unique_per_repo_chronological | pass | 0.199 |
| econ-forecast.tests.synthetic.org.test_git.test_temporal_order_per_pr | pass | 0.197 |
| econ-forecast.tests.synthetic.org.test_git.test_events_source_key_unique | pass | 0.197 |
| econ-forecast.tests.synthetic.org.test_git.test_events_monotonic_per_artifact | pass | 0.199 |
| econ-forecast.tests.synthetic.org.test_git.test_reviewers_not_author | pass | 0.198 |
| econ-forecast.tests.synthetic.org.test_git.test_revert_references_merge_sha | pass | 2.774 |
| econ-forecast.tests.synthetic.org.test_git.test_links_only_to_existing_prs | pass | 0.197 |
| econ-forecast.tests.synthetic.org.test_git.test_children_linked_like_parent | pass | 0.004 |
| econ-forecast.tests.synthetic.org.test_git.test_session_links_sha_method_has_real_commit | pass | 0.003 |
| econ-forecast.tests.synthetic.org.test_git.test_open_prs_at_series_end | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_identity.test_consistent_email_matches_name | pass | 0.005 |
| econ-forecast.tests.synthetic.org.test_identity.test_login_unique_and_valid | pass | 0.092 |
| econ-forecast.tests.synthetic.org.test_identity.test_consistent_deterministic | pass | 0.010 |
| econ-forecast.tests.synthetic.org.test_identity.test_user_ids_style_independent | pass | 0.013 |
| econ-forecast.tests.synthetic.org.test_identity.test_independent_bytes_unchanged | pass | 0.013 |
| econ-forecast.tests.synthetic.org.test_ids.test_repo_num_stable_and_in_range | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_ids.test_repo_num_deterministic_and_distinguishing | pass | 0.000 |
| econ-forecast.tests.synthetic.org.test_ids.test_installation_id_range_and_deterministic | pass | 0.000 |
| econ-forecast.tests.synthetic.org.test_ids.test_session_id_is_uuid5_and_deterministic | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_ids.test_child_session_id_matches_agent_hex_shape | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_ids.test_sha_is_hex_sha1_and_deterministic | pass | 0.000 |
| econ-forecast.tests.synthetic.org.test_ids.test_review_id_deterministic_and_distinct | pass | 0.000 |
| econ-forecast.tests.synthetic.org.test_ids.test_team_slug_matches_d7 | pass | 0.000 |
| econ-forecast.tests.synthetic.org.test_ids.test_artifact_id_shape | pass | 0.000 |
| econ-forecast.tests.synthetic.org.test_ids.test_choice_deterministic_and_weighted | pass | 0.009 |
| econ-forecast.tests.synthetic.org.test_ids.test_domain_registry_complete | pass | 0.105 |
| econ-forecast.tests.synthetic.org.test_org_config.test_empty_defaults_yields_built_ins | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_org_config.test_team_override_is_field_level | pass | 0.292 |
| econ-forecast.tests.synthetic.org.test_org_config.test_mixture_override_replaces_whole | pass | 0.088 |
| econ-forecast.tests.synthetic.org.test_org_config.test_module_weights_must_sum_to_one | pass | 0.080 |
| econ-forecast.tests.synthetic.org.test_org_config.test_pr_size_min_must_be_below_max | pass | 0.087 |
| econ-forecast.tests.synthetic.org.test_org_config.test_min_max_pair_ordered | pass | 0.078 |
| econ-forecast.tests.synthetic.org.test_org_config.test_repo_names_unique | pass | 0.074 |
| econ-forecast.tests.synthetic.org.test_org_config.test_group_child_in_two_groups_rejected | pass | 0.093 |
| econ-forecast.tests.synthetic.org.test_org_config.test_series_dir_must_be_a_complete_series | pass | 0.091 |
| econ-forecast.tests.synthetic.org.test_org_config.test_series_dir_schema_1_is_rejected_with_a_regenerate_message | pass | 0.091 |
| econ-forecast.tests.synthetic.org.test_org_config.test_series_dir_requires_schema_version_2[3] | pass | 0.093 |
| econ-forecast.tests.synthetic.org.test_org_config.test_series_dir_requires_schema_version_2[True] | pass | 0.096 |
| econ-forecast.tests.synthetic.org.test_org_config.test_series_dir_requires_schema_version_2[2] | pass | 0.094 |
| econ-forecast.tests.synthetic.org.test_org_config.test_series_dir_requires_schema_version_2[None] | pass | 0.095 |
| econ-forecast.tests.synthetic.org.test_org_config.test_series_dir_must_be_status_complete | pass | 0.093 |
| econ-forecast.tests.synthetic.org.test_org_config.test_project_repo_mapping_must_be_bijective | pass | 0.091 |
| econ-forecast.tests.synthetic.org.test_org_config.test_every_rostered_team_needs_a_teams_entry | pass | 0.093 |
| econ-forecast.tests.synthetic.org.test_org_config.test_teams_rejects_extra_names | pass | 0.095 |
| econ-forecast.tests.synthetic.org.test_org_config.test_leads_must_be_team_members | pass | 0.092 |
| econ-forecast.tests.synthetic.org.test_org_config.test_groups_children_must_reference_existing_teams | pass | 0.095 |
| econ-forecast.tests.synthetic.org.test_org_config.test_users_need_github_login | pass | 0.193 |
| econ-forecast.tests.synthetic.org.test_org_config.test_admin_login_collides_with_a_user | pass | 0.092 |
| econ-forecast.tests.synthetic.org.test_org_config.test_non_claude_code_agent_rejected | pass | 0.121 |
| econ-forecast.tests.synthetic.org.test_org_config.test_unpriced_model_rejected | pass | 0.141 |
| econ-forecast.tests.synthetic.org.test_org_config.test_allowances_section_is_rejected | pass | 0.093 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold.test_long_turn_threshold_matches | pass | 0.148 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold.test_tokens_total_of_matches | pass | 0.000 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold.test_channel_cost_of_matches | pass | 0.012 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold.test_channel_cost_of_matches_over_generated_sessions | pass | 22.051 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold.test_story_shape_matches | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold.test_story_downsample_matches | pass | 0.001 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold.test_rewrite_recoverable_usd_micros_matches | pass | 0.011 |
| econ-forecast.tests.synthetic.org.test_parity_graph_fold.test_cache_busts_and_resume_cost_match_the_real_formula_directly | pass | 0.390 |
| econ-forecast.tests.synthetic.org.test_personal_spikes_org.test_org_expand_runs_on_a_series_with_personal_spikes | pass | 0.998 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_directory_records_parse_with_mainline_models | pass | 0.109 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_every_rostered_user_has_one_team_and_unique_login | pass | 0.100 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_each_team_has_exactly_one_lead | pass | 0.104 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_admin_has_org_admin_grant_and_no_team | pass | 0.098 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_unrostered_users_not_emitted | pass | 0.095 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_auto_lead_picks_heaviest_profile | pass | 0.093 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_explicit_lead_is_respected | pass | 0.097 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_repo_rows_match_d8_regexes | pass | 0.102 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_lower_full_name_bridges_git_and_session_side | pass | 0.099 |
| econ-forecast.tests.synthetic.org.test_repos_directory.test_installation_id_in_range | pass | 0.095 |
| econ-forecast.tests.synthetic.org.test_requests.test_per_model_exactness | pass | 0.324 |
| econ-forecast.tests.synthetic.org.test_requests.test_cache_write_is_the_sum_of_the_two_lifetimes | pass | 0.316 |
| econ-forecast.tests.synthetic.org.test_requests.test_no_minimum_input_per_request | pass | 0.317 |
| econ-forecast.tests.synthetic.org.test_requests.test_context_window_never_exceeded | pass | 0.321 |
| econ-forecast.tests.synthetic.org.test_requests.test_timestamps_monotonic_and_within_session | pass | 0.329 |
| econ-forecast.tests.synthetic.org.test_requests.test_resume_and_bust_gap_bounds | pass | 0.332 |
| econ-forecast.tests.synthetic.org.test_requests.test_habits_change_leaves_totals_unchanged | pass | 0.160 |
| econ-forecast.tests.synthetic.org.test_requests.test_request_count_is_derived_from_reads_plus_writes | pass | 0.173 |
| econ-forecast.tests.synthetic.org.test_requests.test_session_with_no_reads_and_large_writes_gets_many_requests_within_the_window | pass | 0.156 |
| econ-forecast.tests.synthetic.org.test_sessions.test_split_exact_per_category | pass | 0.345 |
| econ-forecast.tests.synthetic.org.test_sessions.test_single_token_group | pass | 0.092 |
| econ-forecast.tests.synthetic.org.test_sessions.test_huge_group | pass | 0.097 |
| econ-forecast.tests.synthetic.org.test_sessions.test_zero_share_model_absent | pass | 0.090 |
| econ-forecast.tests.synthetic.org.test_sessions.test_empty_session_merged | pass | 0.095 |
| econ-forecast.tests.synthetic.org.test_sessions.test_children_carved_from_parent | pass | 0.143 |
| econ-forecast.tests.synthetic.org.test_sessions.test_child_within_parent | pass | 0.148 |
| econ-forecast.tests.synthetic.org.test_sessions.test_layout_same_local_day | pass | 0.148 |
| econ-forecast.tests.synthetic.org.test_sessions.test_layout_no_overlap_roots | pass | 0.150 |
| econ-forecast.tests.synthetic.org.test_sessions.test_layout_deterministic | pass | 0.198 |
| econ-forecast.tests.synthetic.org.test_work.test_budget_accounting | pass | 0.163 |
| econ-forecast.tests.synthetic.org.test_work.test_unopened_items_have_no_pr | pass | 0.154 |
| econ-forecast.tests.synthetic.org.test_work.test_calibration_hits_target | pass | 2.053 |
| econ-forecast.tests.synthetic.org.test_work.test_parallel_items_bounded | pass | 0.155 |
| econ-forecast.tests.synthetic.org.test_work.test_feature_sizes_respected | pass | 0.158 |
| econ-forecast.tests.synthetic.org.test_work.test_shared_feature_same_team_same_repo | pass | 0.156 |
| econ-forecast.tests.synthetic.org.test_work.test_link_mix_frequencies | pass | 0.167 |
| econ-forecast.tests.synthetic.org.test_work.test_ramp_increases_budget_early | pass | 0.097 |
| econ-forecast.tests.synthetic.test_behavior.test_numerical_oracle_and_single_project_per_user_day | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_profile_largest_remainder_is_exact_and_seed_independent_of_usage | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_duration_extension_preserves_earlier_days_and_mapping_order | pass | 0.017 |
| econ-forecast.tests.synthetic.test_behavior.test_cache_boundaries_conserve_processed_input | pass | 0.003 |
| econ-forecast.tests.synthetic.test_behavior.test_cache_write_split_worked_example | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_cache_write_ratio_zero_reproduces_the_non_cached_input | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_cache_write_ratio_one_leaves_no_uncached_input_and_share_edges | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_write_ratios_change_only_the_split_not_the_seeded_totals | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_weekend_reduction_preserves_activity_and_complete_coverage | pass | 0.003 |
| econ-forecast.tests.synthetic.test_behavior.test_weekend_reduction_is_drawn_per_user_before_project_aggregation | pass | 0.013 |
| econ-forecast.tests.synthetic.test_behavior.test_zero_period_keeps_zero_when_a_spike_rule_is_selected | pass | 0.001 |
| econ-forecast.tests.synthetic.test_behavior.test_overlapping_rules_are_additive_from_the_immutable_baseline | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_rules_never_apply_on_weekends | pass | 0.003 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_range_is_inclusive_and_still_excludes_weekends | pass | 0.004 |
| econ-forecast.tests.synthetic.test_behavior.test_team_and_technology_targets_intersect_while_agent_and_model_union | pass | 0.003 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_increment_uses_exact_baseline_proportions_with_integer_residue | pass | 0.002 |
| econ-forecast.tests.synthetic.test_behavior.test_spike_rule_order_and_duration_extension_preserve_the_prefix | pass | 0.012 |
| econ-forecast.tests.synthetic.test_behavior.test_shared_contract_nulls_additive_fields_and_invalid_counts | pass | 0.000 |
| econ-forecast.tests.synthetic.test_cache_write_acceptance.test_dashboard_residual_of_the_worked_example_is_0_72_percent | pass | 0.001 |
| econ-forecast.tests.synthetic.test_cache_write_acceptance.test_rate_lookup_matched_and_unmatched | pass | 0.000 |
| econ-forecast.tests.synthetic.test_cache_write_acceptance.test_table_marks_unpriced_models | pass | 0.001 |
| econ-forecast.tests.synthetic.test_cache_write_acceptance.test_worked_example_priced_through_the_pricer_request_path | pass | 0.006 |
| econ-forecast.tests.synthetic.test_core.test_allocation_exact_and_ties | pass | 0.001 |
| econ-forecast.tests.synthetic.test_core.test_shipped_configs_parse | pass | 0.010 |
| econ-forecast.tests.synthetic.test_core.test_boolean_count_and_duplicate_yaml_rejected | pass | 0.002 |
| econ-forecast.tests.synthetic.test_core.test_users_and_roster_are_deterministic | pass | 0.065 |
| econ-forecast.tests.synthetic.test_core.test_workflow_and_replay | pass | 6.151 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_identity_default_is_flat_everywhere | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_team_overrides_org_else_falls_back | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_flat_when_neither_org_nor_team_bound | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_levels_agent_multiplies_or_flat | pass | 0.000 |
| econ-forecast.tests.synthetic.test_curve_binding.test_org_curve_scales_the_daily_baseline_exactly | pass | 0.004 |
| econ-forecast.tests.synthetic.test_curve_binding.test_team_curve_overrides_org_without_stacking | pass | 0.003 |
| econ-forecast.tests.synthetic.test_curve_binding.test_flat_agent_reproduces_the_configured_ratio_exactly | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_agent_curve_shifts_the_split_and_renormalizes | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_never_changes_coverage_or_activity_counts | pass | 0.034 |
| econ-forecast.tests.synthetic.test_curve_binding.test_all_bound_agents_at_level_zero_raises_clear_error | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_flat_curve_is_byte_identical_to_v1 | pass | 0.438 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name-2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[name_x] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_accepts_name_and_name_id[n1:0123456789abcdef] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[Bad Name] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:short] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:0123456789ABCDEF] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[:0123456789abcdef] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_ref_format_rejects_bad_strings[name:0123456789abcdef0] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_artifacts_path_resolves_relative_to_series_yaml | pass | 0.009 |
| econ-forecast.tests.synthetic.test_curve_binding.test_curve_artifacts_path_absolute_stays_put | pass | 0.009 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_unknown_team | pass | 0.184 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_unknown_agent | pass | 0.178 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_non_directory_artifacts | pass | 0.019 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_missing_name_id | pass | 0.018 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_rejects_ambiguous_bare_name | pass | 0.339 |
| econ-forecast.tests.synthetic.test_curve_binding.test_validate_series_inputs_accepts_bare_unique_name | pass | 0.180 |
| econ-forecast.tests.synthetic.test_curve_binding.test_bound_run_manifest_has_curve_section_and_snapshots_exist | pass | 0.264 |
| econ-forecast.tests.synthetic.test_curve_binding.test_series_days_equal_one_uses_the_first_anchor_level | pass | 0.239 |
| econ-forecast.tests.synthetic.test_curve_binding.test_bound_run_is_deterministic_on_repeat | pass | 0.374 |
| econ-forecast.tests.synthetic.test_force.test_refuse_by_default_is_preserved | pass | 0.002 |
| econ-forecast.tests.synthetic.test_force.test_force_recreates_an_existing_output_directory | pass | 0.009 |
| econ-forecast.tests.synthetic.test_force.test_force_refuses_a_non_directory_target | pass | 0.001 |
| econ-forecast.tests.synthetic.test_force.test_force_confines_deletion_to_the_resolved_target | pass | 0.008 |
| econ-forecast.tests.synthetic.test_force.test_force_refuses_an_output_that_would_delete_a_required_input | pass | 0.008 |
| econ-forecast.tests.synthetic.test_force.test_force_cli_flag_recreates_existing_output | pass | 3.147 |
| econ-forecast.tests.synthetic.test_interfaces.test_cli_help_and_invalid_arguments_do_no_io | pass | 3.683 |
| econ-forecast.tests.synthetic.test_interfaces.test_validate_only_emits_one_summary_and_writes_nothing | pass | 1.223 |
| econ-forecast.tests.synthetic.test_interfaces.test_reports_and_moved_series_replay | pass | 5.059 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_valid_combinations_and_boundaries[fields0] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_valid_combinations_and_boundaries[fields1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_valid_combinations_and_boundaries[fields2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_valid_combinations_and_boundaries[fields3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_valid_combinations_and_boundaries[fields4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_valid_combinations_and_boundaries[fields5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_absent_profile_has_no_personal_spikes | pass | 0.000 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_all_or_nothing[present0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_all_or_nothing[present1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_all_or_nothing[present2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_all_or_nothing[present3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_all_or_nothing[present4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_all_or_nothing[present5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_zero_still_requires_and_validates_the_multipliers | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_outside_unit_interval_rejected[-0.01] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_outside_unit_interval_rejected[1.01] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_outside_unit_interval_rejected[2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_outside_unit_interval_rejected[-1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_multiplier_bounds_rejected[0.99-2.5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_multiplier_bounds_rejected[0-2.5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_multiplier_bounds_rejected[-1-2.5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_multiplier_bounds_rejected[1.5-0.5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_multiplier_bounds_rejected[1.5-1.4999] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_probability-nan] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_probability-inf] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_probability--inf] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_multiplier_min-nan] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_multiplier_min-inf] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_multiplier_min--inf] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_multiplier_max-nan] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_multiplier_max-inf] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_rejected[spike_multiplier_max--inf] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_probability-0.5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_probability-True] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_probability-bad2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_probability-bad3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_multiplier_min-0.5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_multiplier_min-True] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_multiplier_min-bad2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_multiplier_min-bad3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_multiplier_max-0.5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_multiplier_max-True] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_multiplier_max-bad2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_wrong_types_rejected[spike_multiplier_max-bad3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_non_finite_yaml_scalars_rejected | pass | 0.018 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_unknown_profile_fields_still_rejected | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_explicit_null_is_the_same_as_absent | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_dump_omits_unset_fields_and_round_trips_set_ones | pass | 0.001 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_absent_fields_are_byte_identical_to_the_generator_before_the_feature[series_realistic] | pass | 0.106 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_absent_fields_are_byte_identical_to_the_generator_before_the_feature[series_shipped] | pass | 2.063 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_absent_fields_are_byte_identical_to_the_generator_before_the_feature[series_small] | pass | 0.075 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_explicit_null_fields_are_byte_identical_to_omitting_them[series_small] | pass | 0.075 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_explicit_null_fields_are_byte_identical_to_omitting_them[series_realistic] | pass | 0.099 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_zero_changes_data_not_at_all_but_adds_report_rows | pass | 0.075 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_same_config_twice_is_deterministic | pass | 0.341 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_one_scales_every_active_weekday_of_the_profile_only | pass | 0.086 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_zero_and_absent_produce_the_same_usage | pass | 0.106 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_observed_rate_matches_the_probability | pass | 1.700 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_a_draw_equal_to_the_probability_does_not_spike | pass | 0.013 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_multiplier_is_uniform_within_bounds_and_hits_both_halves | pass | 0.416 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_min_equals_max_gives_a_fixed_multiplier | pass | 0.013 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_weekends_never_spike_with_or_without_weekend_reduction | pass | 0.055 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_inactive_and_forced_zero_days_never_spike | pass | 0.056 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_draw_keys_are_usage_seed_domain_user_date_exactly | pass | 0.109 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_composes_with_volume_jitter_exactly | pass | 0.023 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_composes_with_a_curve_and_with_a_spike_rule_hand_computed | pass | 0.003 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_personal_spike_scales_all_of_the_users_rows_together | pass | 0.002 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_multiplier_conversion_is_exact_decimal_not_binary_float | pass | 0.003 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_event_carries_the_users_team_and_profile | pass | 0.009 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_spike_pushing_a_day_past_signed_64_bit_raises_and_never_wraps | pass | 0.003 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_no_event_when_rounding_leaves_the_count_unchanged | pass | 0.009 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_other_profiles_fields_do_not_move_an_unaffected_users_output | pass | 0.304 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_another_teams_profile_mix_does_not_move_a_users_output | pass | 0.293 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_raising_the_probability_only_adds_spike_days_and_max_only_moves_the_value | pass | 0.523 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_dates_are_part_of_the_key_so_retiming_shifts_spikes_but_overlap_is_stable | pass | 0.209 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_event_schema_and_discriminator | pass | 0.002 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_events_are_sorted_by_kind_then_user_alongside_other_kinds | pass | 0.004 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_series_artifacts_record_and_report_personal_spikes | pass | 0.084 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_personal_spike_report_rows_are_in_sorted_profile_order | pass | 0.083 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_probability_zero_survives_the_saved_config_round_trip | pass | 0.085 |
| econ-forecast.tests.synthetic.test_personal_spikes.test_saved_config_replays_to_identical_artifacts | pass | 0.144 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path0] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path5] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_behavior_settings_are_rejected[path6] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_weekend_reduction_defaults_to_off | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_user_settings_are_rejected[count] | pass | 0.000 |
| econ-forecast.tests.synthetic.test_regressions.test_missing_user_settings_are_rejected[locale] | pass | 0.000 |
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
| econ-forecast.tests.synthetic.test_regressions.test_all_inactive_report_and_manifest_are_complete | pass | 0.023 |
| econ-forecast.tests.synthetic.test_regressions.test_weekend_reduction_preserves_usage_but_excludes_spikes | pass | 0.028 |
| econ-forecast.tests.synthetic.test_regressions.test_streams_write_each_day_before_requesting_the_next | pass | 0.029 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[report] | pass | 0.028 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[manifest] | pass | 0.029 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[stream] | pass | 0.016 |
| econ-forecast.tests.synthetic.test_regressions.test_failure_cleans_only_owned_output[snapshot] | pass | 0.014 |
| econ-forecast.tests.synthetic.test_regressions.test_existing_output_and_dangling_symlink_are_not_modified | pass | 0.014 |
| econ-forecast.tests.synthetic.test_regressions.test_input_corruption_and_record_error_dont_leak_payload | pass | 0.014 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest0] | pass | 0.002 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest1] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest2] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest3] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_malformed_manifests_raise_input_errors[manifest4] | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_roster_legacy_manual_id_rejected_during_validation | pass | 1.437 |
| econ-forecast.tests.synthetic.test_regressions.test_logging_has_consistent_run_ids_modules_and_no_payload | pass | 1.440 |
| econ-forecast.tests.synthetic.test_regressions.test_large_token_boundary_and_overflow_are_explicit | pass | 0.039 |
| econ-forecast.tests.synthetic.test_regressions.test_fake_email_collisions_empty_name_fallback_and_length | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_projects_random_mix_coverage_and_profile_independence | pass | 0.132 |
| econ-forecast.tests.synthetic.test_regressions.test_hash_seed_does_not_change_streams | pass | 0.214 |
| econ-forecast.tests.synthetic.test_regressions.test_project_probability_sanity | pass | 0.013 |
| econ-forecast.tests.synthetic.test_regressions.test_shared_record_rejects_numeric_or_timestamp_date | pass | 0.001 |
| econ-forecast.tests.synthetic.test_regressions.test_cli_config_errors_are_actionable_without_echoing_input | pass | 1.398 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0-1] | pass | 0.023 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0-1000000] | pass | 0.020 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0.8-1] | pass | 0.019 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-0.8-1000000] | pass | 0.022 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-1-1] | pass | 0.019 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0-1-1000000] | pass | 0.022 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0-1] | pass | 0.017 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0-1000000] | pass | 0.021 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0.8-1] | pass | 0.018 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-0.8-1000000] | pass | 0.022 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-1-1] | pass | 0.018 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.03-1-1000000] | pass | 0.022 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0-1] | pass | 0.018 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0-1000000] | pass | 0.020 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0.8-1] | pass | 0.018 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-0.8-1000000] | pass | 0.020 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-1-1] | pass | 0.018 |
| econ-forecast.tests.synthetic.test_regressions.test_generated_token_bounds_include_tiny_and_fully_cached_usage[0.99-1-1000000] | pass | 0.021 |
| econ-forecast.tests.synthetic.test_regressions.test_manifest_version_belongs_to_the_dataset_directory | pass | 0.032 |
| econ-forecast.tests.synthetic.test_regressions.test_forecast_dataset_rejects_a_version_1_series_manifest | pass | 0.032 |
| econ-forecast.tests.synthetic.test_regressions.test_series_config_requires_schema_version_3 | pass | 0.016 |

Reproduce:

```bash
venv/bin/python -m pytest econ-forecast/tests/synthetic/ --junitxml=docs/test/reports/synthetic-personal-spikes-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Synthetic generator: personal spikes (Layer 1)" \
    --junit docs/test/reports/synthetic-personal-spikes-junit.xml \
    --out   docs/test/reports/synthetic-personal-spikes-test-report.md \
    --tests econ-forecast/tests/synthetic/
```
