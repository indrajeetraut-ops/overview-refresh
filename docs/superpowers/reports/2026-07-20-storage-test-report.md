# Storage Module — Test Report

Run: `2026-07-21T00:12:37.789276-07:00`

- Total: 32  Passed: 32  Failed: 0  Skipped: 0
- Wall time (sum of cases): 0.43s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| tests.server.storage.test_cli_db | 3 | 3 | 0 | 0 | 0.01 |
| tests.server.storage.test_config | 2 | 2 | 0 | 0 | 0.00 |
| tests.server.storage.test_engine | 2 | 2 | 0 | 0 | 0.10 |
| tests.server.storage.test_er | 1 | 1 | 0 | 0 | 0.00 |
| tests.server.storage.test_errors | 1 | 1 | 0 | 0 | 0.00 |
| tests.server.storage.test_integration | 3 | 3 | 0 | 0 | 0.08 |
| tests.server.storage.test_operations | 3 | 3 | 0 | 0 | 0.10 |
| tests.server.storage.test_producer_store | 3 | 3 | 0 | 0 | 0.08 |
| tests.server.storage.test_queries | 5 | 5 | 0 | 0 | 0.01 |
| tests.server.storage.test_readme | 1 | 1 | 0 | 0 | 0.00 |
| tests.server.storage.test_report_generator | 1 | 1 | 0 | 0 | 0.06 |
| tests.server.storage.test_schema_dump | 3 | 3 | 0 | 0 | 0.00 |
| tests.server.storage.test_schema_metadata | 4 | 4 | 0 | 0 | 0.00 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| tests.server.storage.test_cli_db.test_help_exits_zero | pass | 0.002 |
| tests.server.storage.test_cli_db.test_unknown_verb_returns_error_code | pass | 0.001 |
| tests.server.storage.test_cli_db.test_import_reports_deferred | pass | 0.003 |
| tests.server.storage.test_config.test_defaults | pass | 0.000 |
| tests.server.storage.test_config.test_load_interpolates_env | pass | 0.002 |
| tests.server.storage.test_engine.test_get_engine_requires_cfg_first | pass | 0.000 |
| tests.server.storage.test_engine.test_session_scope_commits | pass | 0.100 |
| tests.server.storage.test_er.test_er_lists_tables_and_columns | pass | 0.000 |
| tests.server.storage.test_errors.test_errors_are_exceptions | pass | 0.000 |
| tests.server.storage.test_integration.test_public_api_imports | pass | 0.000 |
| tests.server.storage.test_integration.test_init_then_producer_roundtrip_is_idempotent | pass | 0.054 |
| tests.server.storage.test_integration.test_subagent_linkage_roundtrips_both_directions | pass | 0.026 |
| tests.server.storage.test_operations.test_init_creates_tables_then_noops | pass | 0.021 |
| tests.server.storage.test_operations.test_init_force_recreates | pass | 0.017 |
| tests.server.storage.test_operations.test_status_reports | pass | 0.065 |
| tests.server.storage.test_producer_store.test_upsert_get_roundtrip | pass | 0.022 |
| tests.server.storage.test_producer_store.test_upsert_is_idempotent | pass | 0.025 |
| tests.server.storage.test_producer_store.test_touch_and_revoke | pass | 0.030 |
| tests.server.storage.test_queries.test_load_and_get | pass | 0.001 |
| tests.server.storage.test_queries.test_missing_query_raises_with_listing | pass | 0.001 |
| tests.server.storage.test_queries.test_empty_statement_rejected | pass | 0.001 |
| tests.server.storage.test_queries.test_duplicate_rejected | pass | 0.001 |
| tests.server.storage.test_queries.test_non_mapping_yaml_rejected | pass | 0.001 |
| tests.server.storage.test_readme.test_readme_has_required_sections | pass | 0.000 |
| tests.server.storage.test_report_generator.test_generator_renders_markdown | pass | 0.057 |
| tests.server.storage.test_schema_dump.test_ddl_contains_all_tables | pass | 0.001 |
| tests.server.storage.test_schema_dump.test_ddl_has_generated_columns | pass | 0.000 |
| tests.server.storage.test_schema_dump.test_ddl_matches_golden | pass | 0.001 |
| tests.server.storage.test_schema_metadata.test_all_tables_present | pass | 0.000 |
| tests.server.storage.test_schema_metadata.test_producers_has_generated_key_columns | pass | 0.000 |
| tests.server.storage.test_schema_metadata.test_sessions_has_subagent_linkage | pass | 0.000 |
| tests.server.storage.test_schema_metadata.test_no_foreign_keys | pass | 0.000 |

Reproduce:

```bash
venv/bin/python -m pytest tests/server/storage/ --junitxml=docs/superpowers/reports/storage-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Storage Module" \
    --junit docs/superpowers/reports/storage-junit.xml \
    --out   docs/superpowers/reports/2026-07-20-storage-test-report.md \
    --tests tests/server/storage/
```

DB-backed tests show `skip` unless `TALIUM_TEST_DSN` points at a Postgres.
