import os

import pytest

from server.storage.config import StorageConfig
from server.storage.engine import dispose_engine, get_engine

DSN = os.environ.get("TALIUM_TEST_DSN")
requires_db = pytest.mark.skipif(DSN is None, reason="set TALIUM_TEST_DSN to run DB tests")


@pytest.fixture
def cfg() -> StorageConfig:
    return StorageConfig(dsn=DSN or "postgresql+psycopg://invalid/none")


@pytest.fixture
def engine(cfg):
    eng = get_engine(cfg)
    yield eng
    dispose_engine()
