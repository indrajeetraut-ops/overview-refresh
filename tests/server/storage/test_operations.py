from sqlalchemy import inspect

from server.storage import dbm
from server.storage.dbm import operations
from server.storage.engine import get_engine
from tests.server.storage.conftest import requires_db


@requires_db
def test_init_creates_tables_then_noops(cfg):
    eng = get_engine(cfg)
    operations.init(cfg, force=True)  # clean slate
    assert operations.init(cfg) == "exists"  # second run is a no-op
    tables = set(inspect(eng).get_table_names())
    assert {"producers", "sessions", "session_info", "users"} <= tables


@requires_db
def test_init_force_recreates(cfg):
    assert operations.init(cfg, force=True) == "recreated"


@requires_db
def test_status_reports(cfg):
    operations.init(cfg, force=True)
    st = operations.status(cfg)
    assert "revision" in st and "drift" in st
