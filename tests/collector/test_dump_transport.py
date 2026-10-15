import io

from fixtures.uploader import archives
import pytest

from collector.config import (
    CheckpointStoreConfig,
    CollectorConfig,
    FileCheckpointStoreConfig,
    LocalSourceStoreConfig,
    SourceStoreConfig,
)
from collector.dump_transport import make_dump_transport
from collector.engine import run_once
from common.manifest import DeclaredFields
from common.msging.clnt import api as clnt
from common.msging.config import ClientConfig, ClientDefaults, MsgingConfig, ServerBinding
from common.msging.models import Envelope
from server.uploader import service
from server.uploader.config import LimitsConfig, LocalStoreConfig, ObjectStoreConfig, UploaderConfig
from server.uploader.objectstore import LocalObjectStore


@pytest.fixture(autouse=True)
def _reset_client_state():
    clnt._reset_for_tests()
    yield
    clnt._reset_for_tests()


def _config(tmp_path, staging_root) -> CollectorConfig:
    return CollectorConfig(
        source_store=SourceStoreConfig(backend="local", local=LocalSourceStoreConfig(root=staging_root)),
        checkpoint_store=CheckpointStoreConfig(
            backend="file", file=FileCheckpointStoreConfig(path=tmp_path / "checkpoints.json")
        ),
        msging=MsgingConfig(
            client=ClientConfig(
                default_server="ingest",
                servers={"ingest": ServerBinding(alias="ingest", base_path="http://testserver", service_name="ingest")},
                defaults=ClientDefaults(
                    base_dir=str(tmp_path / "msging"), sent_timeout_s=4.0, close_timeout_s=4.0, idle_timeout_s=4.0
                ),
            )
        ),
        flush_timeout_s=6.0,
    )


def test_dump_transport_writes_real_envelopes_to_a_file(tmp_path):
    staging_root = tmp_path / "bucket"
    uploader_config = UploaderConfig(
        object_store=ObjectStoreConfig(backend="local", local=LocalStoreConfig(root=staging_root)),
        limits=LimitsConfig(max_upload_bytes=10 * 1024 * 1024, upload_timeout_s=5.0),
    )
    service.handle_upload(
        io.BytesIO(archives.build_normal_archive()),
        DeclaredFields(org_id="acme", user_id="anil"),
        LocalObjectStore(staging_root),
        uploader_config,
    )

    config = _config(tmp_path, staging_root)
    dump_path = tmp_path / "shipped.jsonl"

    summary = run_once(config, transport=make_dump_transport(dump_path))

    assert summary.sources_processed == 2
    lines = dump_path.read_text(encoding="utf-8").splitlines()
    assert lines

    envelopes = [Envelope.model_validate_json(line) for line in lines]
    # one flat Envelope per session now -- identity in info, events in payload (design §7)
    assert {e.info.get("session_id") for e in envelopes} == {"session-aaa", "session-bbb"}
    assert all(e.producer_id and e.msg_id is not None for e in envelopes)
    assert all(e.payload.get("events") for e in envelopes)

    # checkpoints still commit in dump mode -- a second run is a no-op
    summary2 = run_once(config, transport=make_dump_transport(dump_path))
    assert summary2.sources_processed == 0
    assert summary2.sources_skipped_unchanged == 2
