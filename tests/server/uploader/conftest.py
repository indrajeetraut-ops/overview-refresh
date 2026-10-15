import pytest

from server.uploader.config import (
    LimitsConfig,
    LocalStoreConfig,
    ObjectStoreConfig,
    UploaderConfig,
)
from server.uploader.objectstore import LocalObjectStore


@pytest.fixture
def local_store(tmp_path):
    return LocalObjectStore(tmp_path / "bucket")


@pytest.fixture
def uploader_config(tmp_path):
    return UploaderConfig(
        object_store=ObjectStoreConfig(
            backend="local", local=LocalStoreConfig(root=tmp_path / "bucket")
        ),
        limits=LimitsConfig(max_upload_bytes=10 * 1024 * 1024, upload_timeout_s=5.0),
    )
