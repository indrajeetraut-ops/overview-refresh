"""The examples in `src/common/msging/examples/` must keep working.

An example nobody runs is documentation that lies, and these four are the
README's own recipes in executable form. So: they import, they wire the
routers they claim to wire, and -- the one that matters -- `clnt_full.py`
really does run green against `srv_hosted.py` over a real socket, in two real
processes, with the durable store on disk.

The live test is `slow` (it starts a server and waits out a cleaner sweep);
everything else here is cheap enough to run on every commit.
"""
from __future__ import annotations

import importlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from common.msging.srv import api as srv_api

EXAMPLES = Path(__file__).resolve().parents[3] / "src/common/msging/examples"
REPO_ROOT = Path(__file__).resolve().parents[3]

SERVER_EXAMPLES = ("srv_hosted", "srv_make_app", "srv_two")
ALL_EXAMPLES = SERVER_EXAMPLES + ("clnt_full",)

#: Generous: two real processes on a possibly-loaded box.
WAIT_S = 30.0


def _example_env(tmp_path: Path, port: int) -> dict:
    """The environment the examples document: two knobs, both optional."""
    return dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "src")]),
        TALIUM_EXAMPLE_DIR=str(tmp_path),
        TALIUM_EXAMPLE_PORT=str(port),
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(autouse=True)
def _example_dir(tmp_path, monkeypatch):
    """No example, in-process or out, may write to the real `~/.talium`."""
    monkeypatch.setenv("TALIUM_EXAMPLE_DIR", str(tmp_path))


@pytest.mark.parametrize("name", ALL_EXAMPLES)
def test_every_example_imports_and_has_a_main(name):
    """The cheapest guard there is, and it catches the common rot: a renamed
    library function, a moved import, a typo in the shared module."""
    module = importlib.import_module(f"common.msging.examples.{name}")
    assert callable(module.main)


def test_the_example_config_loads_and_resolves_both_aliases():
    """`msging.example.yaml` is shipped *because* `config/msging.yaml` points
    at a production endpoint. It has to load with no environment set at all --
    "nothing to edit before anything runs" is a promise the examples make."""
    from common.msging.examples._shared import load_example_config

    cfg = load_example_config()
    assert set(cfg.client.servers) == {"ingest_local", "audit_local"}
    for alias, service in (("ingest_local", "ingest"), ("audit_local", "audit")):
        binding = cfg.client.servers[alias]
        assert binding.base_path.startswith("http://127.0.0.1:")
        assert binding.service_name == service
    # The two limits the client example depends on to reach §8.1's rejections.
    assert cfg.client.settings_for("ingest_local").max_pending == 4
    assert cfg.server.max_msg_bytes == 65536


def _paths(routes) -> set[str]:
    """Every path in an app, through whatever wrapper this FastAPI version
    puts around an included router."""
    found: set[str] = set()
    for route in routes or ():
        path = getattr(route, "path", None)
        if isinstance(path, str):
            found.add(path)
        for holder in ("routes", "router", "original_router"):
            nested = getattr(route, holder, None)
            if nested is not None and not isinstance(nested, (str, bytes)):
                found |= _paths(getattr(nested, "routes", nested))
    return found


@pytest.mark.parametrize("name,expected", [
    ("srv_hosted", ["/talium/ingest/v1/event", "/healthz"]),
    ("srv_make_app", ["/talium/ingest/v1/event"]),
    # §6.3: one server per process is a convention, not a limit.
    ("srv_two", ["/talium/ingest/v1/event", "/talium/audit/v1/event"]),
])
def test_each_server_example_mounts_what_it_claims(name, expected):
    """`build_app()` for real -- routers, prefixes and the host's own routes --
    without booting uvicorn.

    `_state = None` rather than `_testing.reset_for_tests()`: the reset
    *installs* a machinery, and each example passes its own config to the first
    `srv_init()`, which is exactly the call that would then raise.
    """
    srv_api._state = None
    try:
        module = importlib.import_module(f"common.msging.examples.{name}")
        assert set(expected) <= _paths(module.build_app().routes)
    finally:
        srv_api._state = None


def test_clnt_full_never_imports_the_server_half():
    """§6.4, and the reason the client example does not share `_shared.py`:
    a collector installs the client half without a web framework, so importing
    `clnt_full.py` must not pull FastAPI."""
    probe = (
        "import importlib, sys;"
        "importlib.import_module('common.msging.examples.clnt_full');"
        "print(sorted(m for m in sys.modules "
        "if m in ('fastapi', 'uvicorn', 'common.msging.srv')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(
            [str(REPO_ROOT), str(REPO_ROOT / "src")])),
        capture_output=True, text=True, timeout=WAIT_S)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


@pytest.mark.slow
def test_clnt_full_runs_green_against_srv_hosted(tmp_path):
    """The whole point of this module: the two examples, as documented, in two
    real processes over a real socket.

    Asserted on the client's own narrative rather than on library internals --
    if a line the file's docstring promises stops appearing, the docstring has
    started lying, which is the failure mode examples actually have.
    """
    port = _free_port()
    env = _example_env(tmp_path, port)

    server = subprocess.Popen(
        [sys.executable, str(EXAMPLES / "srv_hosted.py")],
        cwd=REPO_ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + WAIT_S
        while not _port_open(port) and time.monotonic() < deadline:
            assert server.poll() is None, "srv_hosted.py exited before serving"
            time.sleep(0.05)
        assert _port_open(port), f"srv_hosted.py never listened on {port}"

        client = subprocess.run(
            [sys.executable, str(EXAMPLES / "clnt_full.py")],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
            timeout=WAIT_S * 2)
        assert client.returncode == 0, client.stderr[-4000:]
        out = client.stdout

        # The lifecycle, step by step (§6.1).
        for fragment in (
            "[2] register",
            "-> ok  msg_id=",                       # send
            "<- ack msg_id=",                       # the outcome over SSE
            "store is empty: the ack purged the record",
            "-> too_large  msg_id=None",            # §8.1, not stored
            "-> slot_full  msg_id=None",            # §8.1's one backpressure
            ", purge) # info.retryable=False",      # resolve, both ways
            ", retain) # info.retryable=True",
            "pending/sent/nacked = (0, 0, 0)",
            "close -> status=drained unresolved=0",
            "send() after close() -> ProducerClosingError",
            "metrics (§10.5):",
            "slot_full_total = 1",
            'msgs_resolved_total{"action":"purge"} = 2',
            'msgs_resolved_total{"action":"retain"} = 2',
        ):
            assert fragment in out, f"missing from clnt_full.py output: {fragment!r}"

        # §10.4: the api_key exists in this process's store and must appear in
        # neither stream. The strongest form of the check -- the real key, not
        # a pattern that might not match it.
        identities = list(tmp_path.glob("store/*/*/identity.json"))
        assert identities, "the client wrote no identity.json"
        api_key = json.loads(identities[0].read_text())["api_key"]
        assert api_key
        assert api_key not in out
        assert api_key not in client.stderr

        # ...and the server ran the app handler, including the redelivery that
        # makes resolve(RETAIN) observable.
        server.send_signal(signal.SIGINT)
        server_out, _ = server.communicate(timeout=WAIT_S)
        assert server.returncode == 0, server_out[-4000:]
        for fragment in (
            "kind=good delivery=first -> ack",
            "kind=poison delivery=first -> nack",
            "kind=transient delivery=again -> ack",
            "closed and drained",
            "stopped.",                             # a graceful Ctrl-C
        ):
            assert fragment in server_out, \
                f"missing from srv_hosted.py output: {fragment!r}"
        assert "Traceback" not in server_out.split("stopped.")[-1]
    finally:
        if server.poll() is None:                            # pragma: no cover
            server.kill()
            server.communicate(timeout=WAIT_S)
