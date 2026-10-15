# Forecast storage and loader (server.storage) — Test Report

Run: `2026-10-08T18:08:25.866753+05:30`

- Total: 261  Passed: 261  Failed: 0  Skipped: 0
- Wall time (sum of cases): 37.59s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| tests.server.storage.test_cli_load_forecast | 17 | 17 | 0 | 0 | 2.63 |
| tests.server.storage.test_forecast_load | 183 | 183 | 0 | 0 | 9.28 |
| tests.server.storage.test_forecast_schema | 8 | 8 | 0 | 0 | 2.82 |
| tests.server.storage.test_synthetic_load | 12 | 12 | 0 | 0 | 20.72 |
| tests.server.storage.test_synthetic_load_unit | 41 | 41 | 0 | 0 | 2.13 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| tests.server.storage.test_forecast_schema.test_models_are_registered_on_the_shared_base | pass | 0.045 |
| tests.server.storage.test_forecast_schema.test_forecast_runs_columns_types_and_key | pass | 0.000 |
| tests.server.storage.test_forecast_schema.test_forecast_scope_series_columns_types_and_composite_key | pass | 0.000 |
| tests.server.storage.test_forecast_schema.test_forecast_tables_have_no_foreign_keys_or_extra_indexes | pass | 0.000 |
| tests.server.storage.test_forecast_schema.test_migration_follows_the_previous_head_and_is_the_only_head | pass | 0.009 |
| tests.server.storage.test_forecast_schema.test_migration_applies_from_the_previous_head_and_downgrades_cleanly | pass | 0.966 |
| tests.server.storage.test_forecast_schema.test_no_model_drift_after_migrate | pass | 0.869 |
| tests.server.storage.test_forecast_schema.test_cli_db_status_reports_no_drift_after_migrate | pass | 0.934 |
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
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[{"schema": "dashboard-forecast/1.0"-not valid JSON] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[\xff\xfe\x00-not valid JSON] | pass | 0.001 |
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
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[… | pass | 0.070 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[[]-JSON object] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused["dashboard-forecast/1.0"-JSON object] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[null-JSON object] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_files_that_are_not_a_json_object_are_refused[42-JSON object] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_a_truncated_real_document_is_refused | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema null] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema empty] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema no minor] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema three parts] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema other family] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema leading zero] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema non ascii digit] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema major 2] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema major 10] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema major 0] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing generated_at] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing timezone] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing origin] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing data_through] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing band] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing quantile_grid] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing dates] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing periods] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing presets] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[missing scopes] | pass | 0.001 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at out of range] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[schema with a trailing newline] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[date with a trailing newline] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at garbage] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at naive] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[generated_at date only] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[timezone empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[timezone not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin wrong format] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin basic format] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin week date] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin not a calendar date] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[data_through feb 30] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[origin after data_through] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band high missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low a float] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low a bool] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low not in grid] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band high not in grid] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low equals high] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[band low above high] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid too short] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid has a float] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid has a bool] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid has a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid does not start at 0] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[grid does not end at 100] | pass | 0.013 |
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
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset id empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset id not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset id duplicated] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset label missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[preset label not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scopes not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scopes empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope type missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope type unknown] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope type not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope id missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope id empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope id not a string] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope name missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope name empty] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[scope duplicated] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[two org scopes] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series lacks a preset] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series has an extra preset] | pass | 0.008 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series key renamed] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[series entry not an object] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[status missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[status unknown] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[status not a string] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[status case differs] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily low missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily expected not a list] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily expected too short] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[daily high too long] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[periods missing] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[periods not a list1] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period count too small] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period count too large] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[period entry not an object] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative expected missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative expected wrong length] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative expected for the wrong period] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative quantiles missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative quantiles one row short] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative quantile row too short] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[cumulative quantile row not a list] | pass | 0.003 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[total missing] | pass | 0.002 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[total quantiles missing] | pass | 0.006 |
| tests.server.storage.test_forecast_load.test_structurally_wrong_documents_are_refused_with_a_clear_message[total quantiles wrong length] | pass | 0.003 |
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
| tests.server.storage.test_forecast_load.test_replace_run_stores_exactly_the_expected_rows | pass | 0.453 |
| tests.server.storage.test_forecast_load.test_replace_run_stores_incomplete_as_an_empty_payload | pass | 0.413 |
| tests.server.storage.test_forecast_load.test_an_extra_series_key_survives_the_database | pass | 0.442 |
| tests.server.storage.test_forecast_load.test_reloading_the_identical_file_is_idempotent_and_loaded_at_moves | pass | 0.473 |
| tests.server.storage.test_forecast_load.test_loading_a_second_different_export_leaves_exactly_one_run | pass | 0.440 |
| tests.server.storage.test_forecast_load.test_replace_run_never_commits_the_caller_owns_the_transaction | pass | 0.426 |
| tests.server.storage.test_forecast_load.test_a_failure_in_a_later_batch_rolls_everything_back_and_keeps_the_previous_run | pass | 0.433 |
| tests.server.storage.test_forecast_load.test_a_failure_while_inserting_the_run_row_keeps_the_previous_run | pass | 0.445 |
| tests.server.storage.test_forecast_load.test_two_concurrent_replaces_are_serialised_and_leave_one_run | pass | 1.477 |
| tests.server.storage.test_forecast_load.test_load_forecast_export_loads_and_returns_the_summary | pass | 0.565 |
| tests.server.storage.test_forecast_load.test_load_forecast_export_replaces_the_previous_run | pass | 0.674 |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[truncated] | pass | 0.458 |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[bad-version] | pass | 0.483 |
| tests.server.storage.test_forecast_load.test_a_malformed_export_changes_nothing[bad-grid] | pass | 0.513 |
| tests.server.storage.test_forecast_load.test_load_forecast_export_on_a_fresh_database_migrates_first | pass | 1.171 |
| tests.server.storage.test_cli_load_forecast.test_success_prints_one_json_summary_line_and_loads_the_rows | pass | 0.578 |
| tests.server.storage.test_cli_load_forecast.test_loading_twice_keeps_one_run_and_the_same_id | pass | 0.628 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_exits_2_with_nothing_on_stdout[missing-cannot read forecast export] | pass | 0.003 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_exits_2_with_nothing_on_stdout[truncated-not valid JSON] | pass | 0.003 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_exits_2_with_nothing_on_stdout[bad-version-unsupported schema version 'dashboard-forecast/2.0'] | pass | 0.003 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_exits_2_with_nothing_on_stdout[not-object-JSON object] | pass | 0.003 |
| tests.server.storage.test_cli_load_forecast.test_a_non_synth_dsn_exits_2_without_connecting | pass | 0.002 |
| tests.server.storage.test_cli_load_forecast.test_a_bad_file_leaves_the_database_unchanged | pass | 0.479 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv0] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv1] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv2] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv3] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_argparse_errors_exit_2[argv4] | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_help_exits_zero | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_an_unexpected_failure_exits_1_with_a_traceback_on_stderr | pass | 0.004 |
| tests.server.storage.test_cli_load_forecast.test_the_wrapper_script_is_executable_and_runs_the_right_module | pass | 0.001 |
| tests.server.storage.test_cli_load_forecast.test_the_wrapper_runs_the_cli_end_to_end | pass | 0.918 |
| tests.server.storage.test_synthetic_load_unit.test_guard_rejects_non_synth_names[postgresql+psycopg://user@localhost:5433/econ] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_guard_rejects_non_synth_names[postgresql+psycopg://user@localhost:5433/econ_synth_x] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_guard_rejects_non_synth_names[postgresql+psycopg://user@localhost:5433/] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_guard_accepts_synth_names[postgresql+psycopg://user@localhost:5433/econ_synth] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_guard_accepts_synth_names[postgresql+psycopg://user@localhost:5433/talium_test_synth] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_guard_rejects_unparseable_dsn | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_tamper_detected | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_schema_1_is_rejected_with_a_regenerate_message | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_unknown_schema_is_rejected | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_wrong_operation_detected | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_incomplete_status_detected | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_manifest_missing_file_detected | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_org_id_mismatch_detected | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_org_id_match_passes_through_to_dry_run | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_dry_run_never_calls_get_engine | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_list_turns_normalizes_to_its_length | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_normalize_segment_turns_tolerates_missing_shapes | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_a_pre_removal_dataset_listing_allowances_still_verifies_and_is_not_an_input | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_generated_dataset_is_schema_2_and_rollup_rows_feed_the_per_type_check | pass | 2.042 |
| tests.server.storage.test_synthetic_load_unit.test_daily_rollup_query_selects_input_and_cache_write_separately | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_match_when_the_writes_sum_to_the_database_cache_write | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes0] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes1] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes2] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes3] | pass | 0.000 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes4] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_rollup_tuples_detect_a_per_type_mismatch[changes5] | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_dry_run_with_an_export_plans_the_forecast_rows_and_never_connects | pass | 0.008 |
| tests.server.storage.test_synthetic_load_unit.test_dry_run_without_an_export_has_no_forecast_counts | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_an_export_for_another_org_is_refused_before_any_database_work | pass | 0.005 |
| tests.server.storage.test_synthetic_load_unit.test_unknown_team_and_repo_ids_are_named_in_the_refusal | pass | 0.005 |
| tests.server.storage.test_synthetic_load_unit.test_a_dry_run_also_refuses_a_foreign_export | pass | 0.004 |
| tests.server.storage.test_synthetic_load_unit.test_a_malformed_export_is_a_synthetic_load_error_and_touches_nothing[{-not valid JSON] | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_a_malformed_export_is_a_synthetic_load_error_and_touches_nothing[{"schema": "dashboard-forecast/2.0"}-unsupp… | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_a_malformed_export_is_a_synthetic_load_error_and_touches_nothing[[]-JSON object] | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_a_missing_export_file_is_a_synthetic_load_error | pass | 0.003 |
| tests.server.storage.test_synthetic_load_unit.test_the_guard_still_comes_first | pass | 0.002 |
| tests.server.storage.test_synthetic_load_unit.test_cli_dry_run_with_forecast_export_prints_the_planned_counts | pass | 0.008 |
| tests.server.storage.test_synthetic_load_unit.test_cli_exits_2_for_a_foreign_export | pass | 0.005 |
| tests.server.storage.test_synthetic_load_unit.test_cli_forecast_export_option_defaults_to_none | pass | 0.001 |
| tests.server.storage.test_synthetic_load_unit.test_load_synthetic_row_counts_and_truncate_cover_the_forecast_tables | pass | 0.002 |
| tests.server.storage.test_synthetic_load.test_load_with_replace_matches_file_counts | pass | 1.373 |
| tests.server.storage.test_synthetic_load.test_reload_without_replace_is_idempotent | pass | 1.959 |
| tests.server.storage.test_synthetic_load.test_pr_classification_one_row_per_pr_and_rework_set_on_merged | pass | 1.016 |
| tests.server.storage.test_synthetic_load.test_admin_viewer_id_resolves | pass | 1.036 |
| tests.server.storage.test_synthetic_load.test_load_with_a_forecast_export_puts_the_rows_in | pass | 1.341 |
| tests.server.storage.test_synthetic_load.test_load_without_an_export_reports_zero_forecast_rows_and_adds_none | pass | 1.358 |
| tests.server.storage.test_synthetic_load.test_a_load_without_an_export_leaves_an_existing_run_alone_and_replace_clears_it | pass | 3.594 |
| tests.server.storage.test_synthetic_load.test_reloading_with_a_second_export_leaves_exactly_one_run | pass | 2.390 |
| tests.server.storage.test_synthetic_load.test_a_foreign_export_is_refused_and_the_database_is_untouched | pass | 1.385 |
| tests.server.storage.test_synthetic_load.test_a_rollup_failure_rolls_the_forecast_rows_back_with_the_rest_of_the_load | pass | 1.467 |
| tests.server.storage.test_synthetic_load.test_the_forecast_is_written_before_the_rollup_check | pass | 1.451 |
| tests.server.storage.test_synthetic_load.test_a_rollup_failure_keeps_the_previous_forecast_run | pass | 2.353 |

Reproduce:

```bash
venv/bin/python -m pytest tests/server/storage/ --junitxml=docs/test/reports/forecast-storage-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Forecast storage and loader (server.storage)" \
    --junit docs/test/reports/forecast-storage-junit.xml \
    --out   docs/test/reports/forecast-storage-test-report.md \
    --tests tests/server/storage/
```

All five files ran in one invocation against one scratch *_synth Postgres database (TALIUM_TEST_DSN), so no DB test is skipped. T5 covers the forecast_runs/forecast_scope_series models and migration 231990ad3837 (up, down, no drift), the export parser and its refusals, the one-transaction replace (idempotent, one run, rollback on failure, serialised by a table lock), the standalone CLI and wrapper, and synthetic_load --forecast-export.
