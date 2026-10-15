from pathlib import Path

from server.storage.cli_schema_dump import dump_ddl

GOLDEN = Path("tests/fixtures/storage_ddl.sql")


def test_ddl_contains_all_tables():
    ddl = dump_ddl()
    for t in ("producers", "sessions", "session_info", "users", "queue_jobs"):
        assert f"CREATE TABLE {t}" in ddl


def test_ddl_has_generated_columns():
    ddl = dump_ddl()
    assert "GENERATED ALWAYS AS" in ddl
    assert "STORED" in ddl


def test_ddl_matches_golden():
    # Regenerate with: python -m server.storage.cli_schema_dump --ddl > tests/fixtures/storage_ddl.sql
    assert GOLDEN.exists(), "golden missing; generate it (see comment)"
    assert dump_ddl() == GOLDEN.read_text(encoding="utf-8")
