import io
import json

from fixtures.uploader import archives

from common.manifest import DeclaredFields
from server.uploader import service
from server.uploader.ids import make_producer_id


def test_handle_upload_wire_contract(local_store, uploader_config):
    declared = DeclaredFields(org_id="acme", user_id="anil", vendor="claude_code")
    reader = io.BytesIO(archives.build_normal_archive())

    response = service.handle_upload(reader, declared, local_store, uploader_config)

    assert response.status == "staged"
    assert response.file_count == 2
    assert response.bytes > 0
    assert response.declared == declared
    assert local_store.exists(f"uploads/{response.upload_id}/manifest.json")


def test_manifest_carries_synthetic_producer_id(local_store, uploader_config):
    declared = DeclaredFields(org_id="acme", user_id="anil")
    reader = io.BytesIO(archives.build_normal_archive())

    response = service.handle_upload(reader, declared, local_store, uploader_config)

    manifest_path = local_store.root / f"uploads/{response.upload_id}/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["producer_id"] == make_producer_id("acme", "anil")


def test_manifest_written_last(local_store, uploader_config):
    calls: list[str] = []
    original_put_stream = local_store.put_stream

    def recording_put_stream(key, fileobj, size):
        calls.append(key)
        return original_put_stream(key, fileobj, size)

    local_store.put_stream = recording_put_stream

    declared = DeclaredFields(org_id="acme", user_id="anil")
    reader = io.BytesIO(archives.build_normal_archive())
    service.handle_upload(reader, declared, local_store, uploader_config)

    assert calls[-1].endswith("manifest.json")
    assert len(calls) == 3  # 2 files + the manifest


def test_roundtrip_every_manifest_file_resolves(local_store, uploader_config):
    declared = DeclaredFields(org_id="acme", user_id="anil")
    reader = io.BytesIO(archives.build_subagents_archive())

    response = service.handle_upload(reader, declared, local_store, uploader_config)

    manifest_path = local_store.root / f"uploads/{response.upload_id}/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["file_count"] == len(manifest["files"]) == 3
    for entry in manifest["files"]:
        full_key = f"uploads/{response.upload_id}/{entry['key']}"
        assert local_store.exists(full_key)
