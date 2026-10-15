"""End-to-end acceptance for the hybrid pipeline (otel-intake design §9 v1).

Both signal sources, one session, one process:

- the real collector ships the session's JSONL over real HTTP to the real ingest, which folds
  it (the path `test_ingest_to_fold.py` covers on its own);
- a real OTLP client body — protobuf, over the same server, same port — lands metrics and
  `api_request` events in the `otel_*` tables;
- one true-up tick joins them on `request_id` and writes the session cost block.

The two fixtures are generated from one another (`tests/fixtures/otel/make_fixtures.py`), so
they agree on session id, request ids and token counts by construction rather than by
maintenance. What this asserts that no earlier test can: the join actually closes across two
independently-shipped sources, and running the whole thing twice changes nothing.
"""
import io
import json
import os
import tarfile
import threading
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import uvicorn

from collector.config import (CheckpointStoreConfig, CollectorConfig, FileCheckpointStoreConfig,
                              LocalSourceStoreConfig, SourceStoreConfig)
from collector.engine import run_once
from common.manifest import DeclaredFields
from common.msging.clnt import api as clnt_api
from common.msging.config import (ClientConfig, ClientDefaults, MsgingConfig, ProducerStoreConfig,
                                  ProducerStoreFileConfig, ServerBinding, ServerConfig)
from common.msging.srv import api as srv_api
from server.ingest.app import create_app
from server.ingest.config import IngestConfig
from server.ingest.otlp.decode import encode_protobuf
from server.ingest.otlp.mapping import MappingRegistry
from server.storage.config import StorageConfig
from server.storage.engine import dispose_engine, get_engine, session_scope
from server.storage.org_token_store import PgOrgTokenStore
from server.storage.queries import QueryCatalog
from server.storage.schema import Base
from server.storage.schema import SessionInfo as SessionInfoRow
from server.uploader import service
from server.uploader.config import (LimitsConfig, LocalStoreConfig, ObjectStoreConfig,
                                    UploaderConfig)
from server.uploader.objectstore import LocalObjectStore
from server.worker.otel_trueup import (BASIS_VENDOR, SRC_BOTH, SRC_JSONL_ONLY,
                                       SRC_OTEL_ONLY, OtelTrueUp)

DSN = os.environ.get("TALIUM_TEST_DSN")
pytestmark = pytest.mark.skipif(DSN is None, reason="set TALIUM_TEST_DSN to run the e2e")

OTEL = Path("tests/fixtures/otel")
SESSION_ID = "8f87e0a5-872f-4617-9d27-d9d0bc8db54d"

#: The golden cost block. Measures are the last-value fold (README); the source split is
#: all-matched, because the JSONL fixture carries every request the OTel one does.
GOLDEN_MEASURES = {
    "active_time_s": "148.502",
    "cost_usd": "1.05188999999999989",
    "tokens_cache_creation": "59053",
    "tokens_cache_read": "545250",
    "tokens_input": "517",
    "tokens_output": "7446",
    # Background-call sub-views (PM.3) -- a datapoint contributes to both its unconditional
    # total above and, if labelled `auxiliary`, its narrower `_bg` view (measures_of).
    "cost_usd_bg": "0.06480549999999999",
    "tokens_cache_creation_bg": "2254",
    "tokens_cache_read_bg": "78841",
    "tokens_input_bg": "504",
    "tokens_output_bg": "13",
}
GOLDEN_SOURCES = {SRC_BOTH: 8, SRC_OTEL_ONLY: 0, SRC_JSONL_ONLY: 0}


def _jsonl_archive() -> bytes:
    """A tar of the OTel-matched JSONL session in the layout the uploader expects."""
    proj = ".claude/projects/-work-repo"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|gz") as tar:
        tar.add(str(OTEL / "jsonl_session.jsonl"), arcname=f"{proj}/{SESSION_ID}.jsonl")
    return buf.getvalue()


def _batches(name: str) -> list[dict]:
    return [json.loads(line) for line in
            (OTEL / name).read_text(encoding="utf-8").splitlines()]


def _serve(app):
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning",
                                       lifespan="on"))
    uv.install_signal_handlers = lambda: None      # not the main thread
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if uv.started and uv.servers and uv.servers[0].sockets:
            break
        time.sleep(0.05)
    return uv, thread, uv.servers[0].sockets[0].getsockname()[1]


def _reset_msging_globals():
    clnt_api._reset_for_tests()
    srv_api._state = None


def _ship_otlp(port: int, api_key: str) -> None:
    """Post the fixtures as a real OTel client would: protobuf, gzipped, one body per export."""
    headers = {"authorization": f"Bearer {api_key}",
               "content-type": "application/x-protobuf"}
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as client:
        for signal, name in (("metrics", "metrics_session.jsonl"),
                             ("logs", "logs_session.jsonl")):
            for batch in _batches(name):
                response = client.post(f"/otlp/v1/{signal}",
                                       content=encode_protobuf(batch, signal), headers=headers)
                assert response.status_code == 200, response.text


def _ship_jsonl(port: int, tmp_path: Path, staging: Path, run: int) -> None:
    service.handle_upload(
        io.BytesIO(_jsonl_archive()),
        DeclaredFields(org_id="acme", user_id="dev1"),
        LocalObjectStore(staging),
        UploaderConfig(
            object_store=ObjectStoreConfig(backend="local",
                                           local=LocalStoreConfig(root=staging)),
            limits=LimitsConfig(max_upload_bytes=10 * 1024 * 1024, upload_timeout_s=5.0),
        ),
    )
    collector_cfg = CollectorConfig(
        source_store=SourceStoreConfig(backend="local",
                                       local=LocalSourceStoreConfig(root=staging)),
        checkpoint_store=CheckpointStoreConfig(
            backend="file", file=FileCheckpointStoreConfig(path=tmp_path / f"ckpt{run}.json")),
        msging=MsgingConfig(client=ClientConfig(
            default_server="ingest",
            servers={"ingest": ServerBinding(alias="ingest",
                                             base_path=f"http://127.0.0.1:{port}",
                                             service_name="ingest")},
            defaults=ClientDefaults(base_dir=str(tmp_path / f"cmsging{run}"),
                                    sent_timeout_s=8.0, close_timeout_s=8.0,
                                    idle_timeout_s=8.0))),
        flush_timeout_s=10.0,
    )
    assert run_once(collector_cfg).sources_incomplete == 0


def _cost_block() -> dict:
    with session_scope() as s:
        return s.get(SessionInfoRow, SESSION_ID).otel


@pytest.mark.slow
def test_both_signals_join_into_one_cost_block(tmp_path):
    _reset_msging_globals()
    storage_cfg = StorageConfig(dsn=DSN, queries_dir="config/queries")
    engine = get_engine(storage_cfg)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    api_key, _hash = PgOrgTokenStore().issue("org-acme", {"note": "e2e"})

    server_msging = MsgingConfig(
        server=ServerConfig(shutdown_signals=False, heartbeat_s=2.0),
        producer_store=ProducerStoreConfig(
            file=ProducerStoreFileConfig(path=str(tmp_path / "producers.json"))),
    )
    uv, thread, port = _serve(create_app(IngestConfig(), server_msging, storage_cfg))
    try:
        trueup = OtelTrueUp(QueryCatalog.load("config/queries"),
                            MappingRegistry.load("config/otel"), watermark_lag_s=0.0)

        _ship_jsonl(port, tmp_path, tmp_path / "bucket", run=1)
        _ship_otlp(port, api_key)
        trueup.tick()
        first = _cost_block()

        assert first["vendor"] == "claude_code"
        assert first["measures"] == GOLDEN_MEASURES
        # the join actually closed: every OTel request found its JSONL counterpart
        assert first["sources"] == GOLDEN_SOURCES
        # every request the vendor priced keeps the vendor's number, not an estimate
        assert first["cost_basis"][BASIS_VENDOR] == 8
        assert first["cost_usd_micros"] == first["cost_effective_usd_micros"]
        # and the price table reproduces those vendor costs -- the calibration loop closing
        assert first["calibration"]["claude-opus-5"]["max_abs_micros"] <= 1
        assert first["cost_complete"] is True
        assert len(first["requests"]) == 8
        assert first["cost_usd_micros"] == sum(r["cost_usd_micros"] for r in first["requests"])
        # background calls the JSONL path is blind to are present and labelled (§3)
        assert {r["query_source"] for r in first["requests"]} >= {"prompt_suggestion", "sdk"}

        # the JSONL fold's own output is untouched beside it
        with session_scope() as s:
            info_row = s.get(SessionInfoRow, SESSION_ID)
            assert info_row.models                      # the JSONL fold wrote its own fields
            assert info_row.otel["vendor"] == "claude_code"

        # --- run it all again: same bodies, same fixtures, nothing may move -------------
        _ship_otlp(port, api_key)
        _ship_jsonl(port, tmp_path, tmp_path / "bucket2", run=2)
        trueup.tick()
        trueup.tick()
        second = _cost_block()
        assert {k: v for k, v in second.items() if k != "computed_at"} == \
               {k: v for k, v in first.items() if k != "computed_at"}
    finally:
        uv.should_exit = True
        thread.join(timeout=10)
        dispose_engine()
        srv_api._state = None


@pytest.mark.slow
def test_the_metrics_fold_and_the_request_ledger_agree_on_tokens(tmp_path):
    """Two independent views of the same session -- a counter fold and a request ledger --
    reported side by side rather than reconciled (§4.4). They should agree here; where they
    do not, that gap is the usage reconciler's signal, and this is the test that would find
    it first."""
    _reset_msging_globals()
    storage_cfg = StorageConfig(dsn=DSN, queries_dir="config/queries")
    engine = get_engine(storage_cfg)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    api_key, _hash = PgOrgTokenStore().issue("org-acme")

    server_msging = MsgingConfig(
        server=ServerConfig(shutdown_signals=False, heartbeat_s=2.0),
        producer_store=ProducerStoreConfig(
            file=ProducerStoreFileConfig(path=str(tmp_path / "producers.json"))),
    )
    uv, thread, port = _serve(create_app(IngestConfig(), server_msging, storage_cfg))
    try:
        _ship_otlp(port, api_key)
        OtelTrueUp(QueryCatalog.load("config/queries"),
                   MappingRegistry.load("config/otel"), watermark_lag_s=0.0).tick()
        block = _cost_block()

        ledger_output = sum(r["output_tokens"] for r in block["requests"])
        metrics_output = Decimal(block["measures"]["tokens_output"])
        # The metrics fold covers the whole session including exports after the last request
        # event in the fixture, so it is a ceiling on the ledger, never below it.
        assert metrics_output >= ledger_output > 0
    finally:
        uv.should_exit = True
        thread.join(timeout=10)
        dispose_engine()
        srv_api._state = None
