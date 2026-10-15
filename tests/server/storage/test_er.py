from server.storage.er import dump_er


def test_er_lists_tables_and_columns():
    er = dump_er()
    assert er.startswith("erDiagram")
    for t in ("producers", "sessions", "session_info", "users"):
        assert t in er
    assert "session_id" in er
