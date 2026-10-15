"""The centerpiece: the *actual* uploader staging an archive, then the *actual* engine
reading it, normalizing, and shipping -- wired to a fake in-process Ingest via
httpx.MockTransport (dump_transport._FakeIngest), end to end with no real S3 or Ingest
process involved.

Post msging-rewrite (event-messaging-design.md §2.2): send() is fire-and-forget and the ACK
rides back over the SSE channel, so the fake Ingest ACKs every msg and the engine's
checkpoint-on-ACK path runs for real. **One NormalizedSession = one msg** now, so `received`
holds one flat Envelope per session (events in `payload["events"]`, identity in `info`) --
not one per event as the old per-event transport produced.
"""
from __future__ import annotations

import io
import json
import tarfile

from fixtures.normalizer import sessions
from fixtures.uploader import archives
import pytest

from collector.config import (
    CheckpointStoreConfig,
    CollectorConfig,
    FileCheckpointStoreConfig,
    LocalSourceStoreConfig,
    SourceStoreConfig,
)
from collector.dump_transport import make_recording_transport
from collector.engine import _apply_git_remote, run_once
from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.models import EventIn, NormalizedSession
from common.manifest import DeclaredFields, Manifest, ManifestFileEntry
from common.msging.clnt import api as clnt
from common.msging.config import ClientConfig, ClientDefaults, MsgingConfig, ServerBinding
from common.msging.models import Envelope
from server.uploader import service
from server.uploader.config import LimitsConfig, LocalStoreConfig, ObjectStoreConfig, UploaderConfig
from server.uploader.objectstore import LocalObjectStore


@pytest.fixture(autouse=True)
def _reset_client_state():
    """Each test starts with fresh msging client globals (the library is process-global,
    like tests/common/msging/conftest.py)."""
    clnt._reset_for_tests()
    yield
    clnt._reset_for_tests()


def _make_config(tmp_path, staging_root) -> CollectorConfig:
    return CollectorConfig(
        source_store=SourceStoreConfig(backend="local", local=LocalSourceStoreConfig(root=staging_root)),
        checkpoint_store=CheckpointStoreConfig(
            backend="file", file=FileCheckpointStoreConfig(path=tmp_path / "checkpoints.json")
        ),
        msging=MsgingConfig(
            client=ClientConfig(
                default_server="ingest",
                servers={"ingest": ServerBinding(alias="ingest", base_path="http://testserver", service_name="ingest")},
                # short waits so a lost ACK fails the test fast instead of hanging on the defaults
                defaults=ClientDefaults(
                    base_dir=str(tmp_path / "msging"), sent_timeout_s=4.0, close_timeout_s=4.0, idle_timeout_s=4.0
                ),
            )
        ),
        flush_timeout_s=6.0,
    )


def _uploader_config(staging_root) -> UploaderConfig:
    return UploaderConfig(
        object_store=ObjectStoreConfig(backend="local", local=LocalStoreConfig(root=staging_root)),
        limits=LimitsConfig(max_upload_bytes=10 * 1024 * 1024, upload_timeout_s=5.0),
    )


def _stage_upload(staging_root, archive_bytes: bytes, org_id="acme", user_id="anil") -> None:
    local_store = LocalObjectStore(staging_root)
    declared = DeclaredFields(org_id=org_id, user_id=user_id)
    service.handle_upload(io.BytesIO(archive_bytes), declared, local_store, _uploader_config(staging_root))


def _build_single_session_archive(rel: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|gz") as tar:
        info = tarfile.TarInfo(name=f".claude/projects/{rel}")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _session_start_payload(envelope: Envelope) -> dict:
    return next(ev["payload"] for ev in envelope.payload["events"] if ev["event_type"] == "session_start")


def _session_ids(received: list[Envelope]) -> list[str]:
    return [e.info.get("session_id") for e in received]


def _event_types(received: list[Envelope]) -> set[str]:
    return {ev["event_type"] for e in received for ev in e.payload["events"]}


def test_end_to_end_upload_to_shipped_envelopes(tmp_path):
    staging_root = tmp_path / "bucket"
    _stage_upload(staging_root, archives.build_normal_archive())

    received: list[Envelope] = []
    config = _make_config(tmp_path, staging_root)

    summary = run_once(config, transport=make_recording_transport(received.append))

    assert summary.manifests_found == 1
    assert summary.sources_processed == 2  # build_normal_archive: two session files
    assert summary.sources_incomplete == 0
    # one Envelope per session now, identity in info (no wire header)
    assert set(_session_ids(received)) == {"session-aaa", "session-bbb"}
    # events ride in payload["events"]; the fixture's message-less records pass through as
    # "unknown", still proving unpack -> group -> normalize -> ship ran.
    types = _event_types(received)
    assert "session_start" in types
    assert "unknown" in types
    assert all(e.info.get("org_id") == "acme" and e.info.get("user_id") == "anil" for e in received)


def test_second_run_ships_nothing_new(tmp_path):
    staging_root = tmp_path / "bucket"
    _stage_upload(staging_root, archives.build_normal_archive())

    received: list[Envelope] = []
    config = _make_config(tmp_path, staging_root)

    run_once(config, transport=make_recording_transport(received.append))
    shipped_after_first_run = len(received)

    summary2 = run_once(config, transport=make_recording_transport(received.append))

    assert len(received) == shipped_after_first_run
    assert summary2.sources_processed == 0
    assert summary2.sources_skipped_unchanged == 2


def test_reupload_of_a_longer_file_reships_whole_session(tmp_path):
    staging_root = tmp_path / "bucket"
    rel = "-Users-dev-repo/session-aaa.jsonl"
    v1 = b'{"type": "user", "uuid": "evt-1", "message": {"role": "user", "content": "hi"}}\n'
    v2 = v1 + (
        b'{"type": "assistant", "uuid": "evt-2", "message": {"role": "assistant", '
        b'"model": "m", "content": [], "usage": {}}}\n'
    )

    _stage_upload(staging_root, _build_single_session_archive(rel, v1))

    received: list[Envelope] = []
    config = _make_config(tmp_path, staging_root)

    run_once(config, transport=make_recording_transport(received.append))
    shipped_after_first_upload = len(received)

    # A re-upload of a longer version of the SAME session (fresh upload_id, same declared
    # org/user + rel path -- a real growing ~/.claude/projects re-upload).
    _stage_upload(staging_root, _build_single_session_archive(rel, v2))
    run_once(config, transport=make_recording_transport(received.append))

    assert len(received) > shipped_after_first_upload  # the whole (longer) file reprocessed
    # the same session ships again -- duplicate session_id / record_ids are expected and
    # correct here (ingest's dedupe job, not this library's; event-messaging-design.md §1).
    assert _session_ids(received).count("session-aaa") == 2
    record_ids = [ev["record_id"] for e in received for ev in e.payload["events"] if ev["record_id"]]
    assert len(record_ids) != len(set(record_ids))


def test_crash_resume_skips_done_sources_and_redoes_only_the_lost_one(tmp_path):
    staging_root = tmp_path / "bucket"
    _stage_upload(staging_root, archives.build_normal_archive())

    received: list[Envelope] = []
    config = _make_config(tmp_path, staging_root)

    run_once(config, transport=make_recording_transport(received.append))
    assert received  # sanity: the first pass shipped something
    received.clear()

    # Simulate a crash that lost one source's checkpoint commit while the other's landed.
    ckpt_path = config.checkpoint_store.file.path
    data = json.loads(ckpt_path.read_text())
    lost_key = "acme/anil/-Users-dev-repo/session-aaa.jsonl"
    assert lost_key in data
    del data[lost_key]
    ckpt_path.write_text(json.dumps(data))

    summary = run_once(config, transport=make_recording_transport(received.append))

    assert summary.sources_processed == 1
    assert summary.sources_skipped_unchanged == 1
    assert set(_session_ids(received)) == {"session-aaa"}


def test_legacy_manifest_leaves_session_start_remote_null(tmp_path):
    """An old-generation (Python uploader) manifest carries no per-file repo at all -- git_remote
    must stay null, no crash, no spurious value."""
    staging_root = tmp_path / "bucket"
    rel = "-Users-dev-widgets/session-aaa.jsonl"
    content = b'{"type": "user", "uuid": "evt-1", "cwd": "/Users/dev/widgets"}\n'
    _stage_upload(staging_root, _build_single_session_archive(rel, content))

    received: list[Envelope] = []
    config = _make_config(tmp_path, staging_root)

    run_once(config, transport=make_recording_transport(received.append))

    assert _session_start_payload(received[0])["git_remote"] is None


def _new_generation_normalized_session(repo: str | None = None) -> NormalizedSession:
    events = [
        EventIn(event_type="session_start", payload={"cwd": "/repo", "git_branch": "main"}),
        EventIn(event_type="user_message", payload={}),
    ]
    return NormalizedSession(
        vendor="claude_code", session_id="s1", source="batch",
        agent_version=None, parent_session_id=None, events=events,
    )


def test_apply_git_remote_sets_session_start():
    nsession = _new_generation_normalized_session()
    _apply_git_remote(nsession, "acme/widgets")
    assert nsession.events[0].payload["git_remote"] == "acme/widgets"


def test_apply_git_remote_noop_when_absent():
    nsession = _new_generation_normalized_session()
    _apply_git_remote(nsession, None)
    assert "git_remote" not in nsession.events[0].payload


def test_apply_git_remote_noop_on_empty_events():
    nsession = NormalizedSession(
        vendor="claude_code", session_id="s1", source="batch",
        agent_version=None, parent_session_id=None, events=[],
    )
    _apply_git_remote(nsession, "acme/widgets")  # must not raise
    assert nsession.events == []


def _write_new_generation_manifest(staging_root, *, rel: str, content: bytes, repo: str | None,
                                    endpoint_id="ep-1", extra_files: list[ManifestFileEntry] = ()) -> None:
    """Lays out a manifest.json + raw file(s) directly, the way the Go uploader (../upload)
    writes them -- no tar/unpack step, since that machinery is specific to the old Python
    uploader's ingestion path."""
    prefix = f"uploads/{endpoint_id}/"
    key = f"claude/raw/{rel}"
    (staging_root / prefix / key).parent.mkdir(parents=True, exist_ok=True)
    (staging_root / prefix / key).write_bytes(content)

    entry = ManifestFileEntry(
        key=key, rel=rel, bytes=len(content), vendor="claude_code",
        session_id=rel.rsplit("/", 1)[-1].removesuffix(".jsonl"),
        repo=repo, repo_url=repo, branch="main", state="live", active=False,
    )
    manifest = Manifest(
        endpoint_id=endpoint_id,
        received_ts="2026-09-22T00:00:00Z",
        declared=DeclaredFields(org_id="acme", user_id="anil"),
        producer_id="prd1",
        file_count=1 + len(extra_files),
        files=[entry, *extra_files],
        uploader_version="0.1.0",
        vendors=["claude_code"],
    )
    (staging_root / prefix / "manifest.json").write_text(manifest.model_dump_json())


def test_end_to_end_new_generation_manifest(tmp_path):
    staging_root = tmp_path / "bucket"
    rel = "-Users-dev-widgets/session-aaa.jsonl"
    content = b'{"type": "user", "uuid": "evt-1", "cwd": "/Users/dev/widgets"}\n'
    _write_new_generation_manifest(staging_root, rel=rel, content=content, repo="acme/widgets")

    received: list[Envelope] = []
    config = _make_config(tmp_path, staging_root)

    summary = run_once(config, transport=make_recording_transport(received.append))

    assert summary.manifests_found == 1
    assert summary.sources_processed == 1
    assert len(received) == 1
    assert _session_start_payload(received[0])["git_remote"] == "acme/widgets"


def test_end_to_end_legacy_manifest(tmp_path):
    staging_root = tmp_path / "bucket"
    _stage_upload(staging_root, archives.build_normal_archive())

    received: list[Envelope] = []
    config = _make_config(tmp_path, staging_root)

    summary = run_once(config, transport=make_recording_transport(received.append))

    assert summary.manifests_found == 1
    assert summary.sources_processed == 2
    assert set(_session_ids(received)) == {"session-aaa", "session-bbb"}


def test_stale_state_is_still_collected(tmp_path):
    """`state: "stale"` means "unchanged since the last manifest write," not "already
    collected" -- a stale file with no prior checkpoint must still be processed (a file
    uploaded before the collector first ran would otherwise be skipped forever)."""
    staging_root = tmp_path / "bucket"
    rel = "-Users-dev-widgets/session-aaa.jsonl"
    content = b'{"type": "user", "uuid": "evt-1"}\n'
    prefix = "uploads/ep-1/"
    key = f"claude/raw/{rel}"
    (staging_root / prefix / key).parent.mkdir(parents=True, exist_ok=True)
    (staging_root / prefix / key).write_bytes(content)
    entry = ManifestFileEntry(
        key=key, rel=rel, bytes=len(content), vendor="claude_code",
        session_id="session-aaa", state="stale", active=False,
    )
    manifest = Manifest(
        endpoint_id="ep-1", received_ts="2026-09-22T00:00:00Z",
        declared=DeclaredFields(org_id="acme", user_id="anil"),
        producer_id="prd1", file_count=1, files=[entry], uploader_version="0.1.0",
        vendors=["claude_code"],
    )
    (staging_root / prefix / "manifest.json").write_text(manifest.model_dump_json())

    received: list[Envelope] = []
    config = _make_config(tmp_path, staging_root)

    summary = run_once(config, transport=make_recording_transport(received.append))

    assert summary.sources_processed == 1
    assert summary.sources_skipped_unchanged == 0
    assert len(received) == 1


def test_subagent_sessions_inherit_repo():
    """engine.run_once applies the SAME group.repo to every NormalizedSession a group's
    normalize() yields -- parent and subagents alike, since a subagent's own SessionGroup is
    never looked up independently (integration plan §A4/§B5: a subagent inherits its parent's
    resolved repo). Exercises the real normalizer against the fixture that spawns a real
    Task subagent (one level of nesting), rather than hand-building spawn records."""
    normalizer = ClaudeCodeNormalizer()
    nsessions = list(normalizer.normalize(sessions.mixed_model_subagent_session()))
    assert len(nsessions) > 1  # sanity: the fixture really does yield subagent sessions too

    for nsession in nsessions:
        _apply_git_remote(nsession, "acme/widgets")

    for nsession in nsessions:
        assert _session_start_payload_from_events(nsession.events)["git_remote"] == "acme/widgets"


def _session_start_payload_from_events(events: list[EventIn]) -> dict:
    return next(e.payload for e in events if e.event_type == "session_start")
