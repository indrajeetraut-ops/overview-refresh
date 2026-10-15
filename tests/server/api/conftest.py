import os

import pytest

from common.model_pricing import Pricer
from server.api.config import ApiConfig
from server.storage.config import StorageConfig
from server.storage.engine import dispose_engine, get_engine
from server.storage.queries import QueryCatalog
from server.storage.schema import Base

DSN = os.environ.get("TALIUM_TEST_DSN")
requires_db = pytest.mark.skipif(DSN is None, reason="set TALIUM_TEST_DSN to run DB tests")


@pytest.fixture
def api_config() -> ApiConfig:
    # webui_dist -> a path that never exists, so the StaticFiles mount is deterministically
    # absent regardless of whether someone ran `npm run build` locally. Tests that want the
    # mount active point it at a tmp dir explicitly.
    return ApiConfig(webui_dist="/nonexistent/webui/dist")


@pytest.fixture
def pricer():
    # The real model_pricing catalog -- only the X4 model-fit savings estimate (§3.4) needs
    # one; every other route works with `pricing=None`.
    return Pricer.load(None, None)


@pytest.fixture
def storage_config() -> StorageConfig:
    return StorageConfig(dsn=DSN or "postgresql+psycopg://invalid/none")


@pytest.fixture
def engine(storage_config):
    eng = get_engine(storage_config)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    dispose_engine()


@pytest.fixture
def catalog() -> QueryCatalog:
    return QueryCatalog.load("config/queries")
