import io

import pytest
from botocore.stub import Stubber

from server.uploader.config import LocalStoreConfig, ObjectStoreConfig
from server.uploader.objectstore import LocalObjectStore, S3ObjectStore, make_store


def test_local_put_stream_roundtrip(tmp_path):
    store = LocalObjectStore(tmp_path)
    content = b"hello world"
    key = "uploads/abc/raw/projects/session.jsonl"
    written = store.put_stream(key, io.BytesIO(content), len(content))
    assert written == len(content)
    assert store.exists(key)
    assert (tmp_path / key).read_bytes() == content


def test_local_put_stream_leaves_no_partial_file(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put_stream("uploads/abc/manifest.json", io.BytesIO(b"{}"), 2)
    leftover = list((tmp_path / "uploads/abc").glob("*.part"))
    assert leftover == []


def test_local_list_prefix(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put_stream("uploads/abc/raw/projects/a.jsonl", io.BytesIO(b"a"), 1)
    store.put_stream("uploads/abc/raw/projects/b.jsonl", io.BytesIO(b"b"), 1)
    store.put_stream("uploads/xyz/raw/projects/c.jsonl", io.BytesIO(b"c"), 1)
    keys = sorted(store.list_prefix("uploads/abc"))
    assert keys == [
        "uploads/abc/raw/projects/a.jsonl",
        "uploads/abc/raw/projects/b.jsonl",
    ]


def test_local_exists_false_for_missing(tmp_path):
    store = LocalObjectStore(tmp_path)
    assert store.exists("uploads/nope/manifest.json") is False


def test_make_store_local():
    cfg = ObjectStoreConfig(backend="local", local=LocalStoreConfig(root="/tmp/x"))
    assert isinstance(make_store(cfg), LocalObjectStore)


def test_make_store_local_requires_config():
    cfg = ObjectStoreConfig(backend="local", local=None)
    with pytest.raises(ValueError):
        make_store(cfg)


def test_make_store_s3_requires_config():
    cfg = ObjectStoreConfig(backend="s3", s3=None)
    with pytest.raises(ValueError):
        make_store(cfg)


def test_s3_put_stream_uploads_via_put_object_and_counts_bytes():
    store = S3ObjectStore(bucket="test-bucket", region="us-east-1")
    content = b"hello from s3"
    with Stubber(store._client) as stubber:
        stubber.add_response("put_object", {})
        written = store.put_stream(
            "uploads/abc/manifest.json", io.BytesIO(content), len(content)
        )
        stubber.assert_no_pending_responses()
    assert written == len(content)


def test_s3_exists_false_on_404():
    store = S3ObjectStore(bucket="test-bucket", region="us-east-1")
    with Stubber(store._client) as stubber:
        stubber.add_client_error(
            "head_object", service_error_code="404", http_status_code=404
        )
        assert store.exists("uploads/nope/manifest.json") is False


def test_s3_list_prefix_paginates():
    store = S3ObjectStore(bucket="test-bucket", region="us-east-1")
    with Stubber(store._client) as stubber:
        stubber.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "uploads/abc/manifest.json"}], "IsTruncated": False},
        )
        keys = store.list_prefix("uploads/abc")
    assert keys == ["uploads/abc/manifest.json"]
