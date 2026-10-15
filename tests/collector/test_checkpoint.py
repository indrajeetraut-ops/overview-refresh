from collector.checkpoint import Checkpoint, FileCheckpointStore


def test_get_missing_returns_none(tmp_path):
    store = FileCheckpointStore(tmp_path / "ckpt.json")
    assert store.get("nope") is None


def test_commit_then_get_roundtrips(tmp_path):
    store = FileCheckpointStore(tmp_path / "ckpt.json")
    store.commit("org/user/rel", Checkpoint(identity="123:abc", done=True))

    ckpt = store.get("org/user/rel")

    assert ckpt is not None
    assert ckpt.identity == "123:abc"
    assert ckpt.done is True


def test_commit_preserves_other_keys(tmp_path):
    path = tmp_path / "ckpt.json"
    store = FileCheckpointStore(path)
    store.commit("k1", Checkpoint(identity="a", done=True))
    store.commit("k2", Checkpoint(identity="b", done=False))

    assert store.get("k1").identity == "a"
    assert store.get("k2").identity == "b"
    assert not path.with_suffix(".tmp").exists()  # tmp file cleaned up after atomic replace


def test_multiple_store_instances_share_the_same_file(tmp_path):
    path = tmp_path / "ckpt.json"
    FileCheckpointStore(path).commit("k", Checkpoint(identity="a", done=True))

    reopened = FileCheckpointStore(path)

    assert reopened.get("k").identity == "a"
