from fastapi.testclient import TestClient
from fixtures.uploader import archives

from server.uploader.app import create_app
from server.uploader.config import (
    LimitsConfig,
    LocalStoreConfig,
    ObjectStoreConfig,
    UploaderConfig,
)


def test_upload_happy_path(uploader_config):
    client = TestClient(create_app(uploader_config))
    resp = client.post(
        "/v1/uploads",
        params={"org": "acme", "user": "anil", "vendor": "claude_code"},
        content=archives.build_normal_archive(),
        headers={"Content-Type": "application/gzip"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "staged"
    assert data["file_count"] == 2
    assert data["declared"]["org_id"] == "acme"


def test_upload_malformed_archive_400(uploader_config):
    client = TestClient(create_app(uploader_config))
    resp = client.post(
        "/v1/uploads",
        params={"org": "acme", "user": "anil"},
        content=archives.build_malformed_archive("bad_gzip"),
    )
    assert resp.status_code == 400
    assert resp.json()["reason"] == "not_gzip"


def test_upload_path_traversal_400(uploader_config):
    client = TestClient(create_app(uploader_config))
    resp = client.post(
        "/v1/uploads",
        params={"org": "acme", "user": "anil"},
        content=archives.build_path_traversal_archive("absolute"),
    )
    assert resp.status_code == 400
    assert resp.json()["reason"] == "absolute_path"


def test_upload_too_large_413(tmp_path):
    cfg = UploaderConfig(
        object_store=ObjectStoreConfig(
            backend="local", local=LocalStoreConfig(root=tmp_path / "bucket")
        ),
        limits=LimitsConfig(max_upload_bytes=1, upload_timeout_s=5.0),
    )
    client = TestClient(create_app(cfg))
    resp = client.post(
        "/v1/uploads",
        params={"org": "acme", "user": "anil"},
        content=archives.build_normal_archive(),
    )
    assert resp.status_code == 413


def test_healthz(uploader_config):
    client = TestClient(create_app(uploader_config))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
