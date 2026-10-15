# Synthetic demo refresh (utils/synth_demo_refresh.sh) — Test Report

Run: `2026-10-09T08:59:41.420626+05:30`

- Total: 112  Passed: 112  Failed: 0  Skipped: 0
- Wall time (sum of cases): 62.37s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| tests.utils.test_synth_demo_refresh | 112 | 112 | 0 | 0 | 62.37 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| tests.utils.test_synth_demo_refresh.test_script_is_valid_bash | pass | 0.097 |
| tests.utils.test_synth_demo_refresh.test_help_exits_zero_and_lists_the_options | pass | 0.071 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args0] | pass | 0.074 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args1] | pass | 0.057 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args2] | pass | 0.056 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args3] | pass | 0.059 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args4] | pass | 0.057 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args5] | pass | 0.060 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args6] | pass | 0.056 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args7] | pass | 0.054 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args8] | pass | 0.054 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args9] | pass | 0.057 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args10] | pass | 0.054 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args11] | pass | 0.056 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args12] | pass | 0.056 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args13] | pass | 0.055 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args14] | pass | 0.054 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args15] | pass | 0.077 |
| tests.utils.test_synth_demo_refresh.test_usage_errors_exit_2_before_any_stage[args16] | pass | 0.076 |
| tests.utils.test_synth_demo_refresh.test_dsn_whose_database_is_not_synth_exits_2_before_any_stage[postgresql+psycopg://postgres@localhost:5432/econ_synth_test] | pass | 0.077 |
| tests.utils.test_synth_demo_refresh.test_dsn_whose_database_is_not_synth_exits_2_before_any_stage[postgresql+psycopg://postgres@localhost:5432/econ] | pass | 0.077 |
| tests.utils.test_synth_demo_refresh.test_dsn_whose_database_is_not_synth_exits_2_before_any_stage[postgresql+psycopg://postgres@localhost:5432/econ_synth/] | pass | 0.075 |
| tests.utils.test_synth_demo_refresh.test_dsn_whose_database_is_not_synth_exits_2_before_any_stage[postgresql+psycopg://postgres@localhost:5432/] | pass | 0.076 |
| tests.utils.test_synth_demo_refresh.test_dsn_whose_database_is_not_synth_exits_2_before_any_stage[postgresql+psycopg://postgres@localhost:5432/econ?application… | pass | 0.077 |
| tests.utils.test_synth_demo_refresh.test_dsn_whose_database_is_not_synth_exits_2_before_any_stage[postgresql+psycopg://postgres@localhost:5432/econsynth] | pass | 0.080 |
| tests.utils.test_synth_demo_refresh.test_dsn_whose_database_is_not_synth_exits_2_before_any_stage[postgresql+psycopg://postgres@localhost:5432/_synth] | pass | 0.073 |
| tests.utils.test_synth_demo_refresh.test_dsn_with_query_string_is_accepted | pass | 1.104 |
| tests.utils.test_synth_demo_refresh.test_there_is_no_default_database | pass | 0.740 |
| tests.utils.test_synth_demo_refresh.test_dump_without_docker_exits_2_before_any_stage | pass | 0.087 |
| tests.utils.test_synth_demo_refresh.test_non_empty_output_without_force_is_refused_and_untouched | pass | 0.110 |
| tests.utils.test_synth_demo_refresh.test_force_removes_exactly_the_owned_entries_and_regenerates | pass | 0.885 |
| tests.utils.test_synth_demo_refresh.test_force_refuses_a_directory_holding_anything_else | pass | 0.165 |
| tests.utils.test_synth_demo_refresh.test_force_refuses_a_hidden_extra_entry | pass | 0.132 |
| tests.utils.test_synth_demo_refresh.test_force_with_only_some_owned_entries_is_allowed | pass | 0.831 |
| tests.utils.test_synth_demo_refresh.test_empty_existing_output_directory_needs_no_force | pass | 0.778 |
| tests.utils.test_synth_demo_refresh.test_output_that_is_a_file_or_a_symlink_is_refused | pass | 0.167 |
| tests.utils.test_synth_demo_refresh.test_force_does_not_follow_a_symlinked_entry | pass | 0.823 |
| tests.utils.test_synth_demo_refresh.test_relative_output_is_relative_to_the_callers_directory | pass | 0.727 |
| tests.utils.test_synth_demo_refresh.test_dry_run_prints_every_stage_with_its_command_and_changes_nothing | pass | 0.587 |
| tests.utils.test_synth_demo_refresh.test_dry_run_without_dsn_or_dump_lists_two_stages | pass | 0.356 |
| tests.utils.test_synth_demo_refresh.test_dry_run_still_validates_and_refuses_a_non_empty_output | pass | 0.122 |
| tests.utils.test_synth_demo_refresh.test_dry_run_with_force_does_not_remove_anything | pass | 0.420 |
| tests.utils.test_synth_demo_refresh.test_dry_run_default_dump_path_is_under_the_repo_seed_directory | pass | 0.510 |
| tests.utils.test_synth_demo_refresh.test_stage_order_and_arguments | pass | 1.324 |
| tests.utils.test_synth_demo_refresh.test_presets_flag_is_not_passed_when_unset_and_defaults_are_used | pass | 1.020 |
| tests.utils.test_synth_demo_refresh.test_end_date_today_is_resolved_once_to_a_date | pass | 0.814 |
| tests.utils.test_synth_demo_refresh.test_load_stage_only_with_dsn_and_dump_stage_only_with_dump | pass | 2.289 |
| tests.utils.test_synth_demo_refresh.test_the_loader_gets_the_repo_python_environment | pass | 0.974 |
| tests.utils.test_synth_demo_refresh.test_neither_dsn_nor_dump_prints_how_to_continue | pass | 0.791 |
| tests.utils.test_synth_demo_refresh.test_dump_hint_says_the_dump_must_be_reloaded_with_replace | pass | 1.004 |
| tests.utils.test_synth_demo_refresh.test_a_failing_stage_stops_the_run[generate-after0] | pass | 0.433 |
| tests.utils.test_synth_demo_refresh.test_a_failing_stage_stops_the_run[forecast-after1] | pass | 0.679 |
| tests.utils.test_synth_demo_refresh.test_a_failing_stage_stops_the_run[load-after2] | pass | 1.022 |
| tests.utils.test_synth_demo_refresh.test_a_failing_stage_stops_the_run[dump-after3] | pass | 1.186 |
| tests.utils.test_synth_demo_refresh.test_a_forecast_line_that_is_not_json_fails_the_forecast_stage | pass | 0.811 |
| tests.utils.test_synth_demo_refresh.test_a_forecast_that_did_not_complete_fails_the_forecast_stage | pass | 0.796 |
| tests.utils.test_synth_demo_refresh.test_last_stdout_line_is_one_json_object_with_the_documented_keys | pass | 1.194 |
| tests.utils.test_synth_demo_refresh.test_dump_path_is_null_without_dump | pass | 0.763 |
| tests.utils.test_synth_demo_refresh.test_stage_stdout_is_forwarded_to_stderr_not_stdout | pass | 0.813 |
| tests.utils.test_synth_demo_refresh.test_a_leap_day_is_a_real_date | pass | 0.721 |
| tests.utils.test_synth_demo_refresh.test_a_config_dir_without_one_of_the_four_yamls_exits_2[users.yaml] | pass | 0.086 |
| tests.utils.test_synth_demo_refresh.test_a_config_dir_without_one_of_the_four_yamls_exits_2[roster.yaml] | pass | 0.083 |
| tests.utils.test_synth_demo_refresh.test_a_config_dir_without_one_of_the_four_yamls_exits_2[series.yaml] | pass | 0.085 |
| tests.utils.test_synth_demo_refresh.test_a_config_dir_without_one_of_the_four_yamls_exits_2[org.yaml] | pass | 0.086 |
| tests.utils.test_synth_demo_refresh.test_the_default_config_dir_is_checked_too | pass | 0.094 |
| tests.utils.test_synth_demo_refresh.test_the_dsn_password_is_masked_in_every_form[postgresql+psycopg://postgres:p@ss w0rd@localhost:5432/t8_load_synth] | pass | 0.575 |
| tests.utils.test_synth_demo_refresh.test_the_dsn_password_is_masked_in_every_form[postgresql+psycopg://postgres:s3cr3t@localhost:5432/t8_load_synth?password=qq… | pass | 0.575 |
| tests.utils.test_synth_demo_refresh.test_the_dsn_password_is_masked_in_every_form[postgresql+psycopg://postgres@localhost:5432/t8_load_synth?sslpassword=zz88&p… | pass | 0.546 |
| tests.utils.test_synth_demo_refresh.test_the_real_run_does_not_print_the_password_either | pass | 0.973 |
| tests.utils.test_synth_demo_refresh.test_dot_components_in_the_output_path_are_normalised | pass | 1.807 |
| tests.utils.test_synth_demo_refresh.test_output_with_quote_backslash_and_newline_round_trips_through_the_json | pass | 0.763 |
| tests.utils.test_synth_demo_refresh.test_a_symlinked_output_is_refused_whatever_it_points_at | pass | 0.190 |
| tests.utils.test_synth_demo_refresh.test_dump_dry_run_without_docker_only_notes_it | pass | 0.435 |
| tests.utils.test_synth_demo_refresh.test_a_relative_out_is_relative_to_the_repo_root | pass | 0.989 |
| tests.utils.test_synth_demo_refresh.test_the_temporary_stage_file_is_removed_on_success_and_on_failure | pass | 1.227 |
| tests.utils.test_synth_demo_refresh.test_forecast_success_without_an_export_file_fails_the_forecast_stage | pass | 0.790 |
| tests.utils.test_synth_demo_refresh.test_dump_success_without_a_dump_file_fails_the_dump_stage | pass | 0.985 |
| tests.utils.test_synth_demo_refresh.test_generate_success_without_a_manifest_fails_the_generate_stage | pass | 0.570 |
| tests.utils.test_synth_demo_refresh.test_seconds_is_a_real_measurement | pass | 2.812 |
| tests.utils.test_synth_demo_refresh.test_stage_output_is_streamed_live | pass | 3.822 |
| tests.utils.test_synth_demo_refresh.test_force_success_deletes_the_aside_copy | pass | 0.957 |
| tests.utils.test_synth_demo_refresh.test_a_failed_generate_with_force_keeps_the_old_demo | pass | 0.490 |
| tests.utils.test_synth_demo_refresh.test_a_generate_without_manifest_with_force_keeps_the_old_demo | pass | 0.658 |
| tests.utils.test_synth_demo_refresh.test_an_empty_existing_directory_is_restored_after_a_failed_generate | pass | 0.468 |
| tests.utils.test_synth_demo_refresh.test_a_failure_after_generate_keeps_the_new_demo_and_deletes_the_old | pass | 0.851 |
| tests.utils.test_synth_demo_refresh.test_when_the_restore_fails_the_message_says_where_the_old_copy_is | pass | 0.483 |
| tests.utils.test_synth_demo_refresh.test_dry_run_with_an_existing_output_says_it_would_move_it_aside | pass | 0.394 |
| tests.utils.test_synth_demo_refresh.test_an_interrupt_during_generate_restores_the_old_demo_and_reports_it[15-143] | pass | 1.047 |
| tests.utils.test_synth_demo_refresh.test_an_interrupt_during_generate_restores_the_old_demo_and_reports_it[2-130] | pass | 1.060 |
| tests.utils.test_synth_demo_refresh.test_an_interrupt_during_generate_restores_the_old_demo_and_reports_it[1-129] | pass | 1.143 |
| tests.utils.test_synth_demo_refresh.test_an_interrupt_during_generate_restores_the_old_demo_and_reports_it[3-131] | pass | 1.047 |
| tests.utils.test_synth_demo_refresh.test_an_interrupt_during_a_later_stage_keeps_the_new_demo | pass | 1.315 |
| tests.utils.test_synth_demo_refresh.test_the_venv_is_put_on_the_path_after_generate_creates_it | pass | 0.965 |
| tests.utils.test_synth_demo_refresh.test_a_signal_before_the_move_never_deletes_the_users_demo | pass | 0.721 |
| tests.utils.test_synth_demo_refresh.test_a_failed_move_aside_changes_nothing | pass | 0.355 |
| tests.utils.test_synth_demo_refresh.test_when_moving_the_old_demo_back_fails_the_message_says_where_it_is | pass | 0.685 |
| tests.utils.test_synth_demo_refresh.test_an_existing_aside_name_is_never_overwritten | pass | 0.155 |
| tests.utils.test_synth_demo_refresh.test_an_owned_name_that_is_a_broken_symlink_is_removed_with_the_old_demo | pass | 0.905 |
| tests.utils.test_synth_demo_refresh.test_a_failed_generate_removes_a_partial_output_that_is_a_broken_symlink | pass | 0.479 |
| tests.utils.test_synth_demo_refresh.test_the_stage_stdin_is_dev_null | pass | 0.771 |
| tests.utils.test_synth_demo_refresh.test_config_dir_or_presets_inside_the_output_are_refused_before_any_change | pass | 0.309 |
| tests.utils.test_synth_demo_refresh.test_config_dir_inside_the_output_through_a_symlink_is_refused | pass | 0.096 |
| tests.utils.test_synth_demo_refresh.test_config_next_to_the_output_is_fine | pass | 0.349 |
| tests.utils.test_synth_demo_refresh.test_a_refused_dsn_never_echoes_a_password[user:s3cr3t@host] | pass | 0.073 |
| tests.utils.test_synth_demo_refresh.test_a_refused_dsn_never_echoes_a_password[s3cr3t] | pass | 0.076 |
| tests.utils.test_synth_demo_refresh.test_a_refused_dsn_never_echoes_a_password[postgresql+psycopg://user:s3cr3t@host:5432/db?x=s3cr3t] | pass | 0.079 |
| tests.utils.test_synth_demo_refresh.test_a_refused_dsn_never_echoes_a_password[postgres://u:s3cr3t@h/db wi th space] | pass | 0.076 |
| tests.utils.test_synth_demo_refresh.test_a_plain_database_name_is_still_shown_in_the_refusal | pass | 0.075 |
| tests.utils.test_synth_demo_refresh.test_an_unknown_argument_that_is_a_dsn_is_masked | pass | 0.094 |
| tests.utils.test_synth_demo_refresh.test_force_works_when_the_output_path_contains_a_newline | pass | 0.944 |
| tests.utils.test_synth_demo_refresh.test_force_still_refuses_an_extra_entry_whose_name_has_a_newline | pass | 0.126 |
| tests.utils.test_synth_demo_refresh.test_a_signal_while_the_temp_file_is_made_leaves_the_demo_alone | pass | 0.733 |

Reproduce:

```bash
venv/bin/python -m pytest tests/utils/test_synth_demo_refresh.py --junitxml=docs/test/reports/synth-demo-refresh-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Synthetic demo refresh (utils/synth_demo_refresh.sh)" \
    --junit docs/test/reports/synth-demo-refresh-junit.xml \
    --out   docs/test/reports/synth-demo-refresh-test-report.md \
    --tests tests/utils/test_synth_demo_refresh.py
```

T8: docker-free and database-free; the four wrappers are stubs in a throwaway copy of the script's tree, so the real generator, forecast, loader and dump are covered by the acceptance run in the T8 handoff, not by these tests. Run with /bin/bash (3.2 on macOS).
