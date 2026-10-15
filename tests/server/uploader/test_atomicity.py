"""Doc §10's required atomicity test: a crash mid-unpack must leave objects
with no manifest.json, so the source adapter never sees the partial upload."""
import io

import pytest
from fixtures.uploader import archives

from common.manifest import DeclaredFields
from server.uploader import service


class _CrashAfterNWritesStore:
    """Wraps a real store, raising after N successful writes to simulate a
    mid-unpack crash."""

    def __init__(self, inner, crash_after: int) -> None:
        self._inner = inner
        self._crash_after = crash_after
        self._count = 0

    def put_stream(self, key, fileobj, size):
        if self._count >= self._crash_after:
            raise RuntimeError("simulated crash mid-unpack")
        self._count += 1
        return self._inner.put_stream(key, fileobj, size)

    def exists(self, key):
        return self._inner.exists(key)

    def list_prefix(self, prefix):
        return self._inner.list_prefix(prefix)


def test_crash_mid_unpack_leaves_no_manifest(local_store, uploader_config):
    crashing_store = _CrashAfterNWritesStore(local_store, crash_after=1)
    declared = DeclaredFields(org_id="acme", user_id="anil")
    reader = io.BytesIO(archives.build_normal_archive())

    with pytest.raises(RuntimeError):
        service.handle_upload(reader, declared, crashing_store, uploader_config)

    written = local_store.list_prefix("uploads")
    assert len(written) == 1  # exactly the one file that landed before the crash
    assert not any(key.endswith("manifest.json") for key in written)
