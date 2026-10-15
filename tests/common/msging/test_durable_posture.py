"""Durability and on-disk permission posture, both halves (design §3.1, D30).

These pin properties that are invisible until a crash or a hostile local user:
a write that is atomic but not durable, and a private file inside a directory
anyone may write to. Both were real defects found by the final whole-branch
review, not hypotheticals.
"""
import io
import json
import os
import stat
from pathlib import Path

import pytest
import structlog

from common.msging.log import configure_logging

from common.msging import clnt
from common.msging.clnt.sse import MAX_EVENT_BYTES
from common.msging.clnt.store import MsgStore
from common.msging.config import ServerConfig
from common.msging.durable import private_dir
from common.msging.errors import ConfigError
from common.msging.srv.producer_store import FileProducerStore
from common.msging.models import ProducerRecord

from .conftest import write_cfg


def _capture():
    """Rendered JSON log lines, same idiom as test_api.py. The autouse
    structlog-restoring fixture there is per-module, so this file does its
    own configure and the `_logging_restored` fixture below puts it back."""
    out = io.StringIO()
    configure_logging(json=True,
                      logger_factory=structlog.PrintLoggerFactory(file=out))
    return out


@pytest.fixture(autouse=True)
def _logging_restored():
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


# --------------------------------------------------------- I2: server fsync

def _record(pid: str = "p1") -> ProducerRecord:
    return ProducerRecord(producer_id=pid, key_prefix="tk_prod_abcd",
                          key_hash="0" * 64, registered_at="2026-07-25T10:15:30Z")


def test_producer_store_writes_are_durable_not_merely_atomic(tmp_path, monkeypatch):
    """§3.1/D30: producers.json is the server's ONLY durable state and the sole
    copy of every producer's key hash. `write_text` + `replace` is atomic but
    not durable -- the classic rename-without-fsync outcome is a zero-length
    file, after which every producer's stored api_key is unknown to the server,
    the whole fleet 401s, and each needs a manual re_register() that by design
    orphans its durable backlog.

    Asserts the sequence, not the outcome: a filesystem that happens to flush
    would make an outcome-based test pass against an undurable write.
    """
    store = FileProducerStore(tmp_path / "sub" / "producers.json")
    calls: list[str] = []

    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1])

    store.upsert(_record())

    assert calls.count("replace") == 1, f"expected one atomic publish, got {calls}"
    assert calls == ["fsync", "replace", "fsync"], (
        f"expected fsync(tmp) -> replace -> fsync(dir), got {calls}")
    assert json.loads((tmp_path / "sub" / "producers.json").read_text())


@pytest.mark.parametrize("mutate", ["touch_last_seen", "set_revoked"])
def test_every_producer_store_mutation_is_durable(tmp_path, monkeypatch, mutate):
    """touch_last_seen lands on every auth cache miss, so its crash window is
    the widest of the three -- it must not be the one that skips the fsync."""
    store = FileProducerStore(tmp_path / "producers.json")
    store.upsert(_record())

    calls: list[str] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])

    if mutate == "touch_last_seen":
        store.touch_last_seen("p1", "2026-07-26T00:00:00Z")
    else:
        store.set_revoked("p1", True)

    assert len(calls) == 2, f"{mutate}: expected fsync(tmp) and fsync(dir), got {calls}"


# ------------------------------------------------- I4: directory permissions

def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture()
def permissive_umask():
    """The failure only shows under a umask that does not mask the bits for
    us. Restored afterwards -- umask is process-global."""
    old = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(old)


def test_msg_store_dirs_are_not_world_writable(tmp_path, permissive_umask):
    """Deleting a record is governed by the containing DIRECTORY's write bit,
    not the record's. A world-writable pending/ lets any local user drop msgs
    and break at-least-once with no trace."""
    MsgStore(tmp_path / "s")
    for state in ("pending", "sent", "nacked"):
        d = tmp_path / "s" / state
        assert _mode(d) == 0o700, f"{state}/ is {oct(_mode(d))}, expected 0o700"


def test_producer_store_dir_is_not_world_writable(tmp_path, permissive_umask):
    FileProducerStore(tmp_path / "srv" / "producers.json")
    d = tmp_path / "srv"
    assert _mode(d) == 0o700, f"{d} is {oct(_mode(d))}, expected 0o700"


def test_private_dir_narrows_a_directory_that_already_exists(tmp_path,
                                                             permissive_umask):
    """mkdir's mode is ignored on an existing directory, so a store created by
    an earlier build -- or under a permissive umask -- is exactly the one that
    needs narrowing. The chmod is not redundant with the mkdir."""
    d = tmp_path / "old"
    d.mkdir(mode=0o777)
    assert _mode(d) == 0o777
    private_dir(d)
    assert _mode(d) == 0o700


def test_private_dir_keeps_a_tighter_mode_an_operator_chose(tmp_path):
    """Only bits WIDER than 0700 are cleared: a deliberately tightened
    directory keeps its setting."""
    d = tmp_path / "tight"
    d.mkdir(mode=0o500)
    os.chmod(d, 0o500)
    private_dir(d)
    assert _mode(d) == 0o500


# ------------------------------------------- I3: notice cap vs client parser

def test_max_notice_bytes_cannot_exceed_the_clients_frame_cap():
    """The client's SSE parser caps one event at MAX_EVENT_BYTES and a frame
    over that is not resynchronisable, so an oversized notice drops the link on
    EVERY delivery attempt -- a permanent reconnect loop escaped only by
    sent_timeout_s. The two constants live in packages forbidden to import each
    other, so only this bound keeps them in step.
    """
    with pytest.raises(Exception) as exc:
        ServerConfig(max_notice_bytes=MAX_EVENT_BYTES * 2)
    assert "max_notice_bytes" in str(exc.value)

    ceiling = ServerConfig.model_fields["max_notice_bytes"].metadata
    limit = next(m.le for m in ceiling if hasattr(m, "le"))
    assert limit < MAX_EVENT_BYTES, (
        f"the cap ({limit}) must leave room for the event:/data: framing "
        f"inside the client's {MAX_EVENT_BYTES}-byte event limit")
    ServerConfig(max_notice_bytes=limit)  # the boundary itself is legal


def test_max_notice_bytes_over_the_bound_is_rejected_at_config_load(tmp_path):
    """An operator raising it for a big `info` bag must fail at load, not
    discover it as a producer that can never receive an outcome."""
    from common.msging.config import load_config

    p = tmp_path / "m.yaml"
    p.write_text("server:\n  max_notice_bytes: 1000000\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "max_notice_bytes" in str(exc.value)


# ------------------------------- I1: idle timer derived from the keepalive

def _reader_idle_timeout(pid: str) -> float:
    """The value actually handed to the SSE reader, not merely computed."""
    return clnt._producer_for_tests(pid).idle_timeout_s


def test_the_idle_timer_is_derived_from_the_servers_keepalive(tmp_path,
                                                              mock_server):
    """§4.2/§6.1: the timer has to outlast several heartbeats, and that is a
    relationship between the server's `heartbeat_s` and the client's
    tolerance -- not a value either side can get right alone.

    Configured as an absolute it could not be right for two servers at once,
    and an operator raising `heartbeat_s` on one of them silently put every
    producer into a permanent reconnect cycle. The client configures the
    MULTIPLE; the timer follows the server it actually bound to.
    """
    mock_server.heartbeat_s = 5.0
    cfg = write_cfg(tmp_path, idle_heartbeats=5.0, idle_timeout_s=45.0)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "h"},
                        _transport=mock_server.transport())["producer_id"]

    assert _reader_idle_timeout(pid) == 25.0, "the timer ignored the server"


def test_the_same_client_config_adapts_to_a_slower_server(tmp_path,
                                                          mock_server):
    """The point of the multiple: one YAML, correct against every server it
    binds to. A three-times-slower keepalive gets a three-times-longer timer
    with no config change anywhere on the client."""
    mock_server.heartbeat_s = 15.0
    cfg = write_cfg(tmp_path, idle_heartbeats=5.0)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "h"},
                        _transport=mock_server.transport())["producer_id"]

    assert _reader_idle_timeout(pid) == 75.0


def test_the_derived_timer_survives_a_restart_onto_a_stored_identity(
        tmp_path, mock_server):
    """A reattaching process never sees a /register response, so `heartbeat_s`
    is persisted in identity.json beside `max_msg_bytes`. Without that, a
    restart would silently fall back to the absolute and reintroduce exactly
    the mismatch the multiple exists to prevent."""
    mock_server.heartbeat_s = 5.0
    cfg = write_cfg(tmp_path, idle_heartbeats=5.0, idle_timeout_s=45.0)
    clnt.clnt_init(cfg)
    clnt.register({"host": "h"}, _transport=mock_server.transport())
    clnt._reset_for_tests()

    clnt.clnt_init(cfg)                       # second start, same store dir
    pid = clnt.register({"host": "h"},
                        _transport=mock_server.transport())["producer_id"]
    assert _reader_idle_timeout(pid) == 25.0, "the reattach path lost heartbeat_s"


def test_a_server_that_reports_no_keepalive_falls_back_to_the_absolute(
        tmp_path, mock_server):
    """The one case with nothing to derive from. Quiet, not a warning: it is
    version skew, not an operator error."""
    mock_server.heartbeat_s = None
    cfg = write_cfg(tmp_path, idle_heartbeats=5.0, idle_timeout_s=45.0)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "h"},
                        _transport=mock_server.transport())["producer_id"]

    assert _reader_idle_timeout(pid) == 45.0


def test_the_multiple_is_the_operators_choice(tmp_path, mock_server):
    """How many heartbeats to tolerate is config, not a rule this library
    holds an opinion about -- and nothing warns about the number chosen."""
    mock_server.heartbeat_s = 10.0
    cfg = write_cfg(tmp_path, idle_heartbeats=2.0)
    clnt.clnt_init(cfg)
    out = _capture()
    pid = clnt.register({"host": "h"},
                        _transport=mock_server.transport())["producer_id"]

    assert _reader_idle_timeout(pid) == 20.0
    assert "heartbeat_drift" not in out.getvalue()


# ------------------------------------------------- I5: cleartext transport

def _cleartext_lines(out) -> list[dict]:
    return [json.loads(l) for l in out.getvalue().splitlines()
            if l.strip().startswith("{") and '"cleartext_transport"' in l]


def test_a_remote_http_endpoint_is_warned_about(tmp_path, mock_server):
    """§1: "The transport is HTTPS." Nothing enforced it, so http:// worked
    silently and put the Bearer api_key in cleartext on every POST and every
    SSE connect."""
    cfg = write_cfg(tmp_path, base_path="http://ingest.internal:8443")
    out = _capture()
    clnt.clnt_init(cfg)
    clnt.register({"host": "h"}, _transport=mock_server.transport())

    warned = _cleartext_lines(out)
    assert warned, "a remote cleartext endpoint went unreported"
    assert warned[0]["scheme"] == "http"
    assert warned[0]["host"] == "ingest.internal"
    assert "SSL_CERT_FILE" in warned[0]["action"]


def test_https_is_silent(tmp_path, mock_server):
    cfg = write_cfg(tmp_path, base_path="https://ingest.internal:8443")
    out = _capture()
    clnt.clnt_init(cfg)
    clnt.register({"host": "h"}, _transport=mock_server.transport())
    assert not _cleartext_lines(out)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_loopback_over_http_is_silent(tmp_path, mock_server, host):
    """Every example and the whole suite run on loopback. Warning on them
    would train the warning away, which is worse than not having it."""
    cfg = write_cfg(tmp_path, base_path=f"http://{host}:8099")
    out = _capture()
    clnt.clnt_init(cfg)
    clnt.register({"host": "h"}, _transport=mock_server.transport())
    assert not _cleartext_lines(out)
