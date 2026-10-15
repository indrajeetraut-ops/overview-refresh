import os

import pytest

from server.storage.config import StorageConfig
from server.storage.engine import dispose_engine, get_engine
from server.storage.queries import QueryCatalog
from server.storage.schema import Base

DSN = os.environ.get("TALIUM_TEST_DSN")
requires_db = pytest.mark.skipif(DSN is None, reason="set TALIUM_TEST_DSN to run DB tests")


@pytest.fixture
def engine():
    eng = get_engine(StorageConfig(dsn=DSN or "postgresql+psycopg://invalid/none"))
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    dispose_engine()


@pytest.fixture
def catalog() -> QueryCatalog:
    return QueryCatalog.load("config/queries")
