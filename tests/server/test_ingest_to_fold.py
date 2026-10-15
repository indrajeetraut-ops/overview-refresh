"""End-to-end acceptance for the msging-rewrite pipeline (ingest.md): the real uploader stages
an archive, the real collector (normalizer + msging client) ships it over real HTTP + SSE to
the real ingest (msging `srv` + fold handler), which folds into Postgres; the rollup worker then
computes the parent's subagent economics. Requires a real Postgres (`TALIUM_TEST_DSN`).

The ingest runs in a uvicorn thread on a free port; the sync collector client hits it over real
localhost HTTP — one process, no mocks."""
import io
import os
import tarfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import text

from collector.config import (CheckpointStoreConfig, CollectorConfig, FileCheckpointStoreConfig,
                              LocalSourceStoreConfig, SourceStoreConfig)
from collector.engine import run_once
from common.manifest import DeclaredFields
from common.msging.clnt import api as clnt_api
from common.msging.srv import api as srv_api
from common.msging.config import (ClientConfig, ClientDefaults, MsgingConfig, ProducerStoreConfig,
                                  ProducerStoreFileConfig, ServerBinding, ServerConfig)
from server.ingest.app import create_app
from server.ingest.config import IngestConfig
from server.storage.config import StorageConfig
from server.storage.engine import dispose_engine, get_engine, session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base
from server.uploader import service
from server.uploader.config import LimitsConfig, LocalStoreConfig, ObjectStoreConfig, UploaderConfig
from server.uploader.objectstore import LocalObjectStore
from server.worker.config import WorkerConfig
from server.worker.dispatch import claim_and_run
from server.worker.graph_fold import GraphFold
from server.worker.rollup import make_rollup_handler

DSN = os.environ.get("TALIUM_TEST_DSN")
pytestmark = pytest.mark.skipif(DSN is None, reason="set TALIUM_TEST_DSN to run the e2e")

REC = Path("tests/fixtures/normalizer/recorded")


def _subagents_archive() -> bytes:
    """A tar of the recorded session-subagents fixture (parent + 2 real Task subagents) in the
    ~/.claude/projects layout the uploader expects."""
    proj = ".claude/projects/-Users-dev-repo"
    files = [REC / "session-subagents.jsonl", *sorted((REC / "session-subagents").rglob("*"))]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|gz") as tar:
        for f in files:
            if f.is_file():
                tar.add(str(f), arcname=f"{proj}/{f.relative_to(REC)}")
    return buf.getvalue()


def _serve(app):
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
    uv.install_signal_handlers = lambda: None  # not the main thread
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if uv.started and uv.servers and uv.servers[0].sockets:
            break
        time.sleep(0.05)
    port = uv.servers[0].sockets[0].getsockname()[1]
    return uv, thread, port


def _reset_msging_globals():
    """The msging client + server keep process-wide state (a producer registry, the srv
    machinery + its once-set config). Prior tests in the suite leave theirs built, so reset
    both before this test stands up its own -- the msging suite's conftest does the same for
    its tests."""
    clnt_api._reset_for_tests()
    srv_api._state = None  # force make_app() to build our config, not inherit a prior test's


@pytest.mark.slow
def test_full_pipeline_upload_to_rollup(tmp_path):
    _reset_msging_globals()
    storage_cfg = StorageConfig(dsn=DSN, queries_dir="config/queries")
    engine = get_engine(storage_cfg)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    server_msging = MsgingConfig(
        server=ServerConfig(shutdown_signals=False, heartbeat_s=2.0),
        producer_store=ProducerStoreConfig(file=ProducerStoreFileConfig(path=str(tmp_path / "producers.json"))),
    )
    uv, thread, port = _serve(create_app(IngestConfig(), server_msging, storage_cfg))
    try:
        staging = tmp_path / "bucket"
        service.handle_upload(
            io.BytesIO(_subagents_archive()), DeclaredFields(org_id="acme", user_id="anil"),
            LocalObjectStore(staging),
            UploaderConfig(object_store=ObjectStoreConfig(backend="local", local=LocalStoreConfig(root=staging)),
                           limits=LimitsConfig(max_upload_bytes=10 * 1024 * 1024, upload_timeout_s=5.0)),
        )

        collector_cfg = CollectorConfig(
            source_store=SourceStoreConfig(backend="local", local=LocalSourceStoreConfig(root=staging)),
            checkpoint_store=CheckpointStoreConfig(backend="file", file=FileCheckpointStoreConfig(path=tmp_path / "ckpt.json")),
            msging=MsgingConfig(client=ClientConfig(
                default_server="ingest",
                servers={"ingest": ServerBinding(alias="ingest", base_path=f"http://127.0.0.1:{port}", service_name="ingest")},
                defaults=ClientDefaults(base_dir=str(tmp_path / "cmsging"), sent_timeout_s=8.0, close_timeout_s=8.0, idle_timeout_s=8.0))),
            flush_timeout_s=10.0,
        )
        summary = run_once(collector_cfg)
        assert summary.sources_incomplete == 0

        catalog = QueryCatalog.load("config/queries")
        rollup = make_rollup_handler(GraphFold())
        while claim_and_run(catalog, "rollup", rollup, WorkerConfig()):
            pass

        with session_scope() as s:
            sids = set(s.execute(text("SELECT session_id FROM sessions")).scalars().all())
            assert sids == {"session-subagents", "agent-a15f40f2871d0aa1b", "agent-a91d4028434ad77ae"}
            child_parents = s.execute(
                text("SELECT parent_session_id FROM sessions WHERE parent_session_id IS NOT NULL")
            ).scalars().all()
            assert child_parents == ["session-subagents", "session-subagents"]

            info = s.execute(text(
                "SELECT subagents, (info->>'inclusive_tokens')::bigint AS incl, "
                "(info->>'tokens_total')::bigint AS own FROM session_info WHERE session_id = 'session-subagents'"
            )).mappings().one()
            assert set(info["subagents"]) == {"agent-a15f40f2871d0aa1b", "agent-a91d4028434ad77ae"}
            assert info["incl"] == info["own"] + sum(sa["tokens"] for sa in info["subagents"].values())
    finally:
        uv.should_exit = True
        thread.join(timeout=10)
        dispose_engine()
        srv_api._state = None  # leave the process-wide srv machinery clean for later tests
