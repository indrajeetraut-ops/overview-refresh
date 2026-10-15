# Model pricing (common.model_pricing) — Test Report

Run: `2026-09-26T16:22:51.949707+05:30`

- Total: 185  Passed: 185  Failed: 0  Skipped: 0
- Wall time (sum of cases): 0.78s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| tests.common.model_pricing.test_build | 24 | 24 | 0 | 0 | 0.17 |
| tests.common.model_pricing.test_catalog | 17 | 17 | 0 | 0 | 0.05 |
| tests.common.model_pricing.test_cli | 21 | 21 | 0 | 0 | 0.43 |
| tests.common.model_pricing.test_config | 36 | 36 | 0 | 0 | 0.04 |
| tests.common.model_pricing.test_config_templates | 4 | 4 | 0 | 0 | 0.02 |
| tests.common.model_pricing.test_decimals | 2 | 2 | 0 | 0 | 0.00 |
| tests.common.model_pricing.test_normalize | 26 | 26 | 0 | 0 | 0.01 |
| tests.common.model_pricing.test_reports | 14 | 14 | 0 | 0 | 0.03 |
| tests.common.model_pricing.test_source_aws | 9 | 9 | 0 | 0 | 0.01 |
| tests.common.model_pricing.test_source_azure | 12 | 12 | 0 | 0 | 0.01 |
| tests.common.model_pricing.test_source_gcp | 7 | 7 | 0 | 0 | 0.00 |
| tests.common.model_pricing.test_source_litellm | 13 | 13 | 0 | 0 | 0.01 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| tests.common.model_pricing.test_build.test_aws_wins_over_litellm_for_bedrock_rows_with_litellm_rates_kept | pass | 0.069 |
| tests.common.model_pricing.test_build.test_litellm_fallback_when_aws_lacks_a_model | pass | 0.004 |
| tests.common.model_pricing.test_build.test_source_diff_not_raised_when_only_derived_rate_differs | pass | 0.001 |
| tests.common.model_pricing.test_build.test_source_diff_raised_for_genuine_rate_difference | pass | 0.001 |
| tests.common.model_pricing.test_build.test_vendor_rows_from_litellm | pass | 0.002 |
| tests.common.model_pricing.test_build.test_derived_cache_write_for_model_without_cache_write_fields | pass | 0.002 |
| tests.common.model_pricing.test_build.test_only_cache_write_1h_absent_stays_none | pass | 0.002 |
| tests.common.model_pricing.test_build.test_hash_determinism | pass | 0.004 |
| tests.common.model_pricing.test_build.test_second_build_with_previous_is_noop | pass | 0.004 |
| tests.common.model_pricing.test_build.test_one_changed_rate_new_version_and_change_warning | pass | 0.004 |
| tests.common.model_pricing.test_build.test_first_build_skips_change_check | pass | 0.002 |
| tests.common.model_pricing.test_build.test_ten_percent_change_and_new_model_no_warning | pass | 0.005 |
| tests.common.model_pricing.test_build.test_source_fetch_failure_raises_sourceerror_and_nothing_written | pass | 0.010 |
| tests.common.model_pricing.test_build.test_write_catalog_creates_four_files_and_load_catalog_reads_back | pass | 0.007 |
| tests.common.model_pricing.test_build.test_write_catalog_is_idempotent_on_crash_recovered_version_file | pass | 0.006 |
| tests.common.model_pricing.test_build.test_noop_result_is_not_written | pass | 0.007 |
| tests.common.model_pricing.test_build.test_load_previous | pass | 0.006 |
| tests.common.model_pricing.test_build.test_retirement_row_written_on_disappearance | pass | 0.004 |
| tests.common.model_pricing.test_build.test_retirement_lookup_before_and_after_retired_on | pass | 0.012 |
| tests.common.model_pricing.test_build.test_retirement_carried_unchanged_and_v3_is_noop | pass | 0.006 |
| tests.common.model_pricing.test_build.test_retirement_republished_becomes_live_again | pass | 0.009 |
| tests.common.model_pricing.test_build.test_unmapped_entries_recorded | pass | 0.002 |
| tests.common.model_pricing.test_build.test_official_source_model_promoted_and_priced | pass | 0.002 |
| tests.common.model_pricing.test_build.test_azure_channel_wins_from_azure_retail | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_lookup_table_backward_forward | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_model_absent_from_newer_version_keeps_last_price | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_retired_rule | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_retired_rule_uses_latest_row_not_resolved_row | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_pinning_an_earlier_version_ignores_later_versions | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_example_talium_ai_bedrock_sonnet | pass | 0.004 |
| tests.common.model_pricing.test_catalog.test_example_talium_ai_openai_codex_default_channel | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_example_unmatched_model | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_org_without_config_uses_vendor_channel | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_no_fallback_across_channels | pass | 0.004 |
| tests.common.model_pricing.test_catalog.test_rates_convenience_returns_all_token_types | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_mapping_report_failures_first | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_configured_channel_not_priced_raises_config_error | pass | 0.003 |
| tests.common.model_pricing.test_catalog.test_tampered_version_file_raises_catalog_error | pass | 0.002 |
| tests.common.model_pricing.test_catalog.test_unknown_version_raises_catalog_error | pass | 0.002 |
| tests.common.model_pricing.test_catalog.test_no_index_raises_catalog_error | pass | 0.001 |
| tests.common.model_pricing.test_catalog.test_vendor_conflict_across_versions_raises_catalog_error | pass | 0.002 |
| tests.common.model_pricing.test_cli.test_help_exits_0_without_network_config_or_catalog[common.model_pricing.cli_update] | pass | 0.002 |
| tests.common.model_pricing.test_cli.test_help_exits_0_without_network_config_or_catalog[common.model_pricing.cli_match] | pass | 0.002 |
| tests.common.model_pricing.test_cli.test_help_exits_0_without_network_config_or_catalog[common.model_pricing.cli_show] | pass | 0.002 |
| tests.common.model_pricing.test_cli.test_help_exits_0_without_network_config_or_catalog[common.model_pricing.cli_versions] | pass | 0.002 |
| tests.common.model_pricing.test_cli.test_update_dry_run_writes_nothing | pass | 0.003 |
| tests.common.model_pricing.test_cli.test_update_dry_run_json_prints_refresh_detail | pass | 0.003 |
| tests.common.model_pricing.test_cli.test_update_writes_catalog_then_second_run_is_noop | pass | 0.007 |
| tests.common.model_pricing.test_cli.test_update_corrupt_catalog_exits_1 | pass | 0.003 |
| tests.common.model_pricing.test_cli.test_update_failing_source_exits_1_and_writes_nothing | pass | 0.002 |
| tests.common.model_pricing.test_cli.test_match_all_matched_exits_0 | pass | 0.008 |
| tests.common.model_pricing.test_cli.test_match_any_unmatched_exits_2 | pass | 0.008 |
| tests.common.model_pricing.test_cli.test_match_json_format | pass | 0.007 |
| tests.common.model_pricing.test_cli.test_match_unknown_catalog_root_exits_1 | pass | 0.002 |
| tests.common.model_pricing.test_cli.test_show_known_model_exits_0 | pass | 0.007 |
| tests.common.model_pricing.test_cli.test_show_unknown_model_exits_2 | pass | 0.007 |
| tests.common.model_pricing.test_cli.test_versions_lists_built_version | pass | 0.006 |
| tests.common.model_pricing.test_cli.test_versions_limit | pass | 0.006 |
| tests.common.model_pricing.test_cli.test_versions_empty_catalog_exits_0 | pass | 0.002 |
| tests.common.model_pricing.test_cli.test_shell_wrapper_help | pass | 0.321 |
| tests.common.model_pricing.test_cli.test_shell_wrapper_unknown_command_exits_2 | pass | 0.019 |
| tests.common.model_pricing.test_cli.test_shell_wrapper_missing_command_exits_2 | pass | 0.015 |
| tests.common.model_pricing.test_config.test_absent_model_pricing_yaml_gives_vendor_channels_only | pass | 0.002 |
| tests.common.model_pricing.test_config.test_absent_channel_config_yaml_defaults_every_org_to_vendor | pass | 0.001 |
| tests.common.model_pricing.test_config.test_absent_aliases_yaml_gives_empty_aliases | pass | 0.001 |
| tests.common.model_pricing.test_config.test_valid_channel_ids[anthropic] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_valid_channel_ids[openai] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_valid_channel_ids[gemini] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_valid_channel_ids[aws_bedrock:us-east-1:regional] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_valid_channel_ids[aws_bedrock:us-east-1:global] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_valid_channel_ids[azure:eastus2:data_zone] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_valid_channel_ids[azure:eastus2:global] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_valid_channel_ids[gcp_vertex:us-central1:regional] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_invalid_channel_ids[not-a-channel] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_invalid_channel_ids[aws_bedrock:us-east-1] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_invalid_channel_ids[aws_bedrock:us-east-1:data_zone] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_invalid_channel_ids[azure:eastus2:regional] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_invalid_channel_ids[unknown_prefix:us-east-1:regional] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_invalid_channel_ids[aws_bedrock::regional] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_channel_cloud_splits_cloud_ids | pass | 0.001 |
| tests.common.model_pricing.test_config.test_model_pricing_yaml_rejects_vendor_channel_in_channels_list | pass | 0.002 |
| tests.common.model_pricing.test_config.test_model_pricing_yaml_rejects_invalid_channel | pass | 0.002 |
| tests.common.model_pricing.test_config.test_model_pricing_yaml_loads_valid_channels | pass | 0.002 |
| tests.common.model_pricing.test_config.test_model_pricing_yaml_rejects_unknown_key | pass | 0.002 |
| tests.common.model_pricing.test_config.test_channel_config_yaml_rejects_unknown_vendor | pass | 0.002 |
| tests.common.model_pricing.test_config.test_channel_config_yaml_rejects_invalid_channel | pass | 0.002 |
| tests.common.model_pricing.test_config.test_channel_config_yaml_channel_for | pass | 0.002 |
| tests.common.model_pricing.test_config.test_invalid_yaml_raises_config_error | pass | 0.002 |
| tests.common.model_pricing.test_config.test_model_pricing_config_defaults_vendor_prefixes | pass | 0.001 |
| tests.common.model_pricing.test_config.test_infer_vendor_recognized_prefixes[claude-sonnet-4-anthropic] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_infer_vendor_recognized_prefixes[claude-3-haiku-anthropic] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_infer_vendor_recognized_prefixes[gpt-5-6-sol-openai] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_infer_vendor_recognized_prefixes[gemini-2-5-flash-gemini] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_infer_vendor_recognized_prefixes[o1-openai] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_infer_vendor_recognized_prefixes[o3-mini-openai] | pass | 0.001 |
| tests.common.model_pricing.test_config.test_infer_vendor_unknown_prefix_is_none | pass | 0.000 |
| tests.common.model_pricing.test_config.test_infer_vendor_o_series_not_confused_with_openai_prefix | pass | 0.000 |
| tests.common.model_pricing.test_config.test_infer_vendor_respects_custom_prefixes | pass | 0.000 |
| tests.common.model_pricing.test_config_templates.test_model_pricing_yaml_template_loads | pass | 0.004 |
| tests.common.model_pricing.test_config_templates.test_channel_config_example_loads_under_real_name | pass | 0.003 |
| tests.common.model_pricing.test_config_templates.test_aliases_example_loads_under_real_name | pass | 0.003 |
| tests.common.model_pricing.test_config_templates.test_channel_config_example_channels_are_all_priced_in_model_pricing_yaml | pass | 0.006 |
| tests.common.model_pricing.test_decimals.test_trailing_zeros_do_not_change_the_string | pass | 0.000 |
| tests.common.model_pricing.test_decimals.test_fixed_point_never_scientific | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_norm[us.anthropic.claude-sonnet-5-claude-sonnet-5] | pass | 0.001 |
| tests.common.model_pricing.test_normalize.test_norm[gpt-5.6-sol-gpt-5-6-sol] | pass | 0.001 |
| tests.common.model_pricing.test_normalize.test_norm[global.anthropic.claude-opus-5-claude-opus-5] | pass | 0.001 |
| tests.common.model_pricing.test_normalize.test_norm[vertex_ai/gemini-3-pro-preview-gemini-3-pro-preview] | pass | 0.001 |
| tests.common.model_pricing.test_normalize.test_norm[azure_ai/claude-haiku-4-5-claude-haiku-4-5] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_norm[azure/eu/gpt-4o-2024-08-06-gpt-4o-2024-08-06] | pass | 0.001 |
| tests.common.model_pricing.test_normalize.test_norm[CLAUDE-SONNET-5-claude-sonnet-5] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_strip_date[claude-haiku-4-5-20251001-v1:0-claude-haiku-4-5] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_strip_date[claude-haiku-4-5-20251001-claude-haiku-4-5] | pass | 0.001 |
| tests.common.model_pricing.test_normalize.test_strip_date[claude-3-5-haiku@20241022-claude-3-5-haiku] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_strip_date[claude-3-5-haiku@default-claude-3-5-haiku] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_strip_date[gpt-4o-2024-08-06-gpt-4o] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_strip_date[model-v2-model] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_strip_date[no-date-here-no-date-here] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_canon[gpt-5.5-2026-04-24-gpt-5-5] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_canon[us.anthropic.claude-haiku-4-5-20251001-v1:0-claude-haiku-4-5] | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_registry_collapses_identical_rates_to_one_model | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_gpt4o_family_conflict_makes_four_models | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_registry_groups_undated_variants_with_identical_rates | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_normalize_direct_model_id_hit_wins_over_ambiguous_set | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_normalize_dated_variant_with_exact_registry_entry_matches | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_normalize_unseen_dated_variant_of_ambiguous_family_is_ambiguous | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_normalize_canonical_step_for_non_ambiguous_dated_variant | pass | 0.001 |
| tests.common.model_pricing.test_normalize.test_normalize_region_prefixed_string_matches_via_norm | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_normalize_unmatched | pass | 0.000 |
| tests.common.model_pricing.test_normalize.test_normalize_alias_wins_first | pass | 0.000 |
| tests.common.model_pricing.test_reports.test_render_refresh_header_and_summary_sections | pass | 0.002 |
| tests.common.model_pricing.test_reports.test_render_refresh_no_per_record_names | pass | 0.004 |
| tests.common.model_pricing.test_reports.test_render_refresh_source_diff_count_only | pass | 0.002 |
| tests.common.model_pricing.test_reports.test_render_refresh_errors_none_when_clean | pass | 0.002 |
| tests.common.model_pricing.test_reports.test_render_refresh_excluded_and_warnings_listed | pass | 0.002 |
| tests.common.model_pricing.test_reports.test_render_refresh_previous_version_and_run_at | pass | 0.003 |
| tests.common.model_pricing.test_reports.test_render_refresh_json_has_full_detail_and_parses | pass | 0.004 |
| tests.common.model_pricing.test_reports.test_render_refresh_json_stable_key_order | pass | 0.002 |
| tests.common.model_pricing.test_reports.test_render_refresh_noop_result | pass | 0.003 |
| tests.common.model_pricing.test_reports.test_render_mapping_md_failures_first_no_prices | pass | 0.000 |
| tests.common.model_pricing.test_reports.test_render_mapping_json_no_prices | pass | 0.000 |
| tests.common.model_pricing.test_reports.test_render_mapping_invalid_format_raises | pass | 0.000 |
| tests.common.model_pricing.test_reports.test_render_mapping_with_prices_shows_rates_and_source | pass | 0.004 |
| tests.common.model_pricing.test_reports.test_format_rate_per_million | pass | 0.000 |
| tests.common.model_pricing.test_source_aws.test_sonnet_5_regional_rates | pass | 0.002 |
| tests.common.model_pricing.test_source_aws.test_sonnet_5_global_rates | pass | 0.001 |
| tests.common.model_pricing.test_source_aws.test_opus_4_8_regional_rates | pass | 0.001 |
| tests.common.model_pricing.test_source_aws.test_haiku_4_5_regional_rates_older_scheme | pass | 0.001 |
| tests.common.model_pricing.test_source_aws.test_batch_dimension_does_not_affect_regional_input_rate | pass | 0.001 |
| tests.common.model_pricing.test_source_aws.test_reserved_dimension_is_ignored_not_unmapped | pass | 0.001 |
| tests.common.model_pricing.test_source_aws.test_non_vendor_product_is_a_candidate_not_yet_unmapped | pass | 0.001 |
| tests.common.model_pricing.test_source_aws.test_claude_sonnet_4_is_a_candidate_on_both_scopes | pass | 0.001 |
| tests.common.model_pricing.test_source_aws.test_conflicting_schemes_newer_wins_with_warning | pass | 0.000 |
| tests.common.model_pricing.test_source_azure.test_data_zone_gpt_5_6_sol_rates | pass | 0.001 |
| tests.common.model_pricing.test_source_azure.test_global_gpt_5_6_sol_rates | pass | 0.001 |
| tests.common.model_pricing.test_source_azure.test_gpt_5_5_data_zone_and_global_rates | pass | 0.001 |
| tests.common.model_pricing.test_source_azure.test_longco_batch_pp_meters_are_ignored_not_unmapped | pass | 0.001 |
| tests.common.model_pricing.test_source_azure.test_mixed_case_tokens_parsed | pass | 0.001 |
| tests.common.model_pricing.test_source_azure.test_non_gpt_product_is_unmapped | pass | 0.001 |
| tests.common.model_pricing.test_source_azure.test_unsupported_unit_is_unmapped | pass | 0.001 |
| tests.common.model_pricing.test_source_azure.test_wrong_region_items_are_skipped | pass | 0.000 |
| tests.common.model_pricing.test_source_azure.test_only_input_no_output_is_unmapped_missing_rate | pass | 0.000 |
| tests.common.model_pricing.test_source_azure.test_fetch_follows_next_page_link | pass | 0.002 |
| tests.common.model_pricing.test_source_azure.test_fetch_first_request_carries_the_filter | pass | 0.001 |
| tests.common.model_pricing.test_source_azure.test_fetch_filter_excludes_non_openai_products | pass | 0.001 |
| tests.common.model_pricing.test_source_gcp.test_auto_without_env_var_is_skipped | pass | 0.000 |
| tests.common.model_pricing.test_source_gcp.test_default_toggle_argument_behaves_like_auto | pass | 0.000 |
| tests.common.model_pricing.test_source_gcp.test_auto_with_env_var_set_is_enabled_and_raises | pass | 0.000 |
| tests.common.model_pricing.test_source_gcp.test_explicit_false_is_skipped_even_with_env_var_set | pass | 0.000 |
| tests.common.model_pricing.test_source_gcp.test_explicit_true_raises_even_without_env_var | pass | 0.000 |
| tests.common.model_pricing.test_source_gcp.test_parse_of_no_payload_gives_no_rows | pass | 0.000 |
| tests.common.model_pricing.test_source_gcp.test_parse_of_a_payload_raises_not_implemented | pass | 0.000 |
| tests.common.model_pricing.test_source_litellm.test_registry_has_the_gpt4o_conflict | pass | 0.002 |
| tests.common.model_pricing.test_source_litellm.test_vendor_channel_rows | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_deprecation_date_becomes_retired_on | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_bedrock_regional_and_global_rows | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_bedrock_regional_and_global_rows_for_openai_prefix | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_bedrock_rows_are_not_duplicated_across_scopes | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_azure_global_row_from_bare_azure_prefix | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_azure_ai_prefix_also_maps_to_global | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_azure_geo_prefix_maps_to_data_zone | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_vertex_row | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_unmapped_vertex_entry_is_listed | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_no_cloud_channels_configured_gives_vendor_rows_only | pass | 0.001 |
| tests.common.model_pricing.test_source_litellm.test_model_without_cache_write_fields_has_none_rates | pass | 0.001 |

Reproduce:

```bash
venv/bin/python -m pytest tests/common/model_pricing/ --junitxml=docs/test/reports/model-pricing-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Model pricing (common.model_pricing)" \
    --junit docs/test/reports/model-pricing-junit.xml \
    --out   docs/test/reports/model-pricing-test-report.md \
    --tests tests/common/model_pricing/
```
