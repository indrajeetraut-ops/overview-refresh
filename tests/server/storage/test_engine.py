import pytest

from server.storage.engine import dispose_engine, get_engine, session_scope
from tests.server.storage.conftest import requires_db


def test_get_engine_requires_cfg_first():
    dispose_engine()
    with pytest.raises(RuntimeError):
        get_engine()


@requires_db
def test_session_scope_commits(engine):
    from sqlalchemy import text
    with session_scope() as s:
        val = s.execute(text("select 1")).scalar_one()
    assert val == 1
