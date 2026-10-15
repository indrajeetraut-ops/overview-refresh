import hashlib

from collector.store import LocalFsSourceStore


def test_list_prefix_returns_keys_relative_to_root(tmp_path):
    (tmp_path / "uploads" / "u1").mkdir(parents=True)
    (tmp_path / "uploads" / "u1" / "manifest.json").write_text("{}")
    (tmp_path / "uploads" / "u1" / "raw.jsonl").write_text("x")

    store = LocalFsSourceStore(tmp_path)

    assert set(store.list_prefix("uploads/u1")) == {"uploads/u1/manifest.json", "uploads/u1/raw.jsonl"}


def test_list_prefix_empty_for_missing_dir(tmp_path):
    store = LocalFsSourceStore(tmp_path)
    assert store.list_prefix("nope") == []


def test_open_whole_file(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hello world")
    store = LocalFsSourceStore(tmp_path)

    with store.open("f.txt", 0, None) as f:
        assert f.read() == b"hello world"


def test_open_ranged(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"hello world")
    store = LocalFsSourceStore(tmp_path)

    with store.open("f.txt", 6, 11) as f:
        assert f.read() == b"world"


def test_size(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"12345")
    store = LocalFsSourceStore(tmp_path)
    assert store.size("f.txt") == 5


def test_identity_stable_for_same_content(tmp_path):
    (tmp_path / "f.txt").write_bytes(b"same content")
    store = LocalFsSourceStore(tmp_path)
    assert store.identity("f.txt") == store.identity("f.txt")


def test_identity_changes_when_content_grows(tmp_path):
    path = tmp_path / "f.txt"
    path.write_bytes(b"v1")
    store = LocalFsSourceStore(tmp_path)
    identity_v1 = store.identity("f.txt")

    path.write_bytes(b"v1 plus a lot more content appended")
    identity_v2 = store.identity("f.txt")

    assert identity_v1 != identity_v2


def test_identity_empty_file(tmp_path):
    (tmp_path / "empty.txt").write_bytes(b"")
    store = LocalFsSourceStore(tmp_path)

    assert store.identity("empty.txt") == f"0:{hashlib.sha256(b'').hexdigest()}"
