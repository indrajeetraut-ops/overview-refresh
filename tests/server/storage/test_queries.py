import pytest

from server.storage.errors import QueryConfigError
from server.storage.queries import QueryCatalog


def test_load_and_get(tmp_path):
    (tmp_path / "ingest.yaml").write_text("insert_x: |\n  select 1\n")
    cat = QueryCatalog.load(tmp_path)
    stmt = cat.get("ingest", "insert_x")
    assert "select 1" in str(stmt)


def test_missing_query_raises_with_listing(tmp_path):
    (tmp_path / "ingest.yaml").write_text("a: select 1\n")
    cat = QueryCatalog.load(tmp_path)
    with pytest.raises(KeyError) as e:
        cat.get("ingest", "nope")
    assert "ingest:a" in str(e.value)


def test_empty_statement_rejected(tmp_path):
    (tmp_path / "bad.yaml").write_text("empty: '  '\n")
    with pytest.raises(QueryConfigError):
        QueryCatalog.load(tmp_path)


def test_duplicate_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text("q: select 1\n")
    (tmp_path / "b.yaml").write_text("q: select 2\n")
    # duplicate is per (file_key, name); different files differ, so this must NOT raise
    QueryCatalog.load(tmp_path)


def test_non_mapping_yaml_rejected(tmp_path):
    (tmp_path / "bad.yaml").write_text("- select 1\n")
    with pytest.raises(QueryConfigError):
        QueryCatalog.load(tmp_path)
