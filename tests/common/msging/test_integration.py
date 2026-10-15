"""End-to-end integration: the real client half against a real server (§2, §4).

Everything else in this suite tests one layer with the other one faked. This
module fakes nothing on the wire. A **real** `uvicorn` server runs on a real
TCP port in a thread; the client half -- its own threads and blocking httpx --
talks to it exactly as a deployed collector would, through the public API in
`src/common/msging/README.md` and nothing else.

**Why a real socket and not `httpx.ASGITransport`.** That transport
concatenates the whole response body before returning it, so an endless
`/notify` stream can never be read through it -- Task 13's router tests had to
drive the ASGI app directly for that reason. Every outcome in this library
arrives over that stream (§4.1), so an integration test that cannot read it
tests nothing at all. Hence uvicorn on an ephemeral port.

What is covered, in design terms:

    §2.2  register -> send -> handler -> send_notice -> SSE -> purge
    §2.2  NACK -> callback -> resolve(PURGE | RETAIN)
    §2.3  restart reconciliation: PENDING and SENT resend on the next start
    §2.4  close() drains and reports {drained, 0}; nacked/ is excluded
    §4.3  server_epoch change sweeps sent/ back to pending/
    §4.4  sent_timeout_s demotes a msg whose outcome never came
    §5.1  slot_full (max_pending) and server_busy (queue_max) backpressure
    §5.2  too_large, checked against the limit /register handed back
    §5.1  two aliases = two bindings = two identities and two stores

**No `sleep` is used as a synchronisation primitive.** Every wait is
`conftest.wait_until` polling a real condition with a deadline, so these run
the same on a loaded CI container as on a laptop.

**Three defects this pass surfaced, all three since fixed.** Two were pinned
here as `xfail(strict=True)` and are now plain tests -- they are the regression
cover for the fix, and each one's docstring still describes the failure it
pins, because that is what makes it obvious when it comes back.

1.  `test_a_nack_that_beats_its_own_post_response_reaches_nacked` -- a NACK
    that arrived before its own POST response was discarded and the msg parked
    in `sent/` until `sent_timeout_s`; 10-18% of NACKs over loopback.
    `MsgStore.mark_nacked` now accepts the record from `pending/` as well as
    `sent/` and says which, so the outcome lands whichever answer wins.
2.  `test_on_event_before_srv_init_can_carry_an_explicit_config` -- the
    README's wiring order could not carry a config object, because `on_event()`
    built the machinery out of the defaults first. It now parks the handler
    instead of constructing anything.
3.  `SseReader.stop()` sometimes missed its join and left the reader parked
    until `idle_timeout_s`: `_abort` shut the socket down (which wakes the
    read) and then closed the client (which raced that wakeup away). It no
    longer does both. See `Lab.assert_no_leaked_threads`.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from common.msging import PURGE, RETAIN, clnt, srv
from common.msging.config import MsgingConfig
# The seams are private on purpose (they are destructive against a live
# server); §5.2's queue-full path has no other reachable trigger.
from common.msging.srv import _testing, api as srv_api

from .conftest import wait_until

#: Bound on every `wait_until` here. Generous: these are real sockets and real
#: threads on a possibly-loaded CI box, and the cost of a too-short deadline is
#: a flaky suite, while the cost of a long one is paid only when a test is
#: already failing.
WAIT_S = 15.0

#: How long uvicorn is allowed to wait for a live SSE connection to drain
#: before it cancels it. The client's reader is by design parked in a blocking
#: read, so without a bound `server.should_exit` would never return.
GRACEFUL_S = 0.5

#: Server heartbeat. Comfortably under the client `idle_timeout_s` below, so a
#: quiet stream is never mistaken for a dead one (§4.2).
HEARTBEAT_S = 0.5

#: How long "the store is empty" has to keep being true before it is believed.
#: See `Lab.wait_empty`. A rename is sub-millisecond; this is a sweep interval
#: wide, which is orders of magnitude more than enough and still cheap.
SETTLE_S = 0.05


# ------------------------------------------------------------ the app half


def ack(env):
    """The README's handler: do the work, then ACK with what it produced."""
    return "ack", "", {"row_id": env.msg_id}


def nack(env):
    return "nack", "payload rejected by the app", {}


def drop(env):
    """Answer nothing at all -- the outcome dies in the app (§4.4)."""
    return None


class AppHandler:
    """The `on_event` callback an app registers, with a swappable policy.

    Records every envelope it is handed, so a test can assert *the app ran*
    and how many times -- which is the whole point of an at-least-once
    transport and is invisible from the client side.

    `async def` on purpose: §6.2 lets a handler return an awaitable, and it is
    what `gate` needs -- a handler that parks the event loop with a blocking
    wait would stop the server answering anything, this one just yields.
    """

    def __init__(self) -> None:
        self.policy = ack
        self.seen: list = []
        #: Set it to a `threading.Event` and the handler answers nothing until
        #: the test sets the event. That is how a test says "the POST response
        #: reaches `send()` **before** the outcome does" without a sleep and
        #: without assuming which of the two wins on the day -- and over
        #: loopback the outcome genuinely wins about a quarter of the time.
        self.gate: threading.Event | None = None
        self._lock = threading.Lock()

    async def __call__(self, env) -> None:
        with self._lock:
            self.seen.append(env)
        gate = self.gate
        if gate is not None:
            deadline = time.monotonic() + WAIT_S
            while not gate.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
        outcome = self.policy(env)
        if outcome is None:
            return
        code, reason, info = outcome
        srv.send_notice(env.producer_id, env.msg_id, code, reason=reason,
                        info=info)

    # -- assertions read these ------------------------------------------

    def deliveries(self, msg_id: int) -> int:
        with self._lock:
            return sum(1 for env in self.seen if env.msg_id == msg_id)

    def payloads(self) -> list[dict]:
        with self._lock:
            return [env.payload for env in self.seen]

    def envelope(self, msg_id: int):
        with self._lock:
            for env in self.seen:
                if env.msg_id == msg_id:
                    return env
        return None

    def drop_once(self):
        """Policy: swallow the first delivery of each msg_id, ACK the rest.

        This is §4.4's exact condition -- an outcome that never arrives -- and
        it has to be per-msg_id, not "the first N calls", so the resend is
        answered rather than dropped again forever.
        """
        dropped: set[int] = set()

        def policy(env):
            if env.msg_id in dropped:
                return ack(env)
            dropped.add(env.msg_id)
            return None

        self.policy = policy


# --------------------------------------------------------- the server half


def _bind(port: int = 0) -> socket.socket:
    """A listening socket on loopback. `SO_REUSEADDR` so a restart can take
    the *same* port back while the previous run's connections sit in
    TIME_WAIT -- §4.3's test is only meaningful if the client reconnects to
    the address it was configured with."""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(64)
    return sock


class LiveServer:
    """One msging server: process machinery + uvicorn, on a real port.

    The machinery is per-*process* by design (§6.2/§6.3) -- one queue, one
    `NotifyHub`, one `server_epoch` -- so there is at most one of these alive
    at a time, and `restart()` is what a real server restart looks like: the
    same producer store, the same port, a **new** `server_epoch`.
    """

    def __init__(self, services: tuple[str, ...], store_path: Path,
                 handler: AppHandler, on_close, port: int = 0,
                 **server_cfg) -> None:
        self.services = services
        self.store_path = store_path
        self.handler = handler
        self.on_close = on_close
        self.server_cfg = server_cfg
        self.port = port
        self.thread: threading.Thread | None = None
        self.server: uvicorn.Server | None = None
        self.epoch: str | None = None

    @property
    def base_path(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "LiveServer":
        cfg = MsgingConfig()
        cfg.server.heartbeat_s = HEARTBEAT_S
        for key, value in self.server_cfg.items():
            setattr(cfg.server, key, value)
        # Same file across a restart: a server that forgot every producer_id
        # would 401 the reconnecting client, which is a different §8.1 story
        # than the one §4.3 is about.
        cfg.producer_store.file.path = str(self.store_path)
        _testing.reset_for_tests(config=cfg)
        srv.on_event(self.handler)
        srv.on_close(self.on_close)
        app = srv.make_app(*self.services, config=cfg)

        sock = _bind(self.port)
        self.port = sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(
            app, log_level="critical", timeout_graceful_shutdown=GRACEFUL_S))
        self.thread = threading.Thread(
            # NOT `msging-*`: `Lab.leaked_threads` matches that prefix to find
            # threads the *library* started, and a test harness thread wearing
            # the library's prefix would make that assertion meaningless.
            target=lambda: self.server.run(sockets=[sock]),
            name="itest-uvicorn", daemon=True)
        self.thread.start()
        assert wait_until(lambda: self.server.started, WAIT_S), \
            "uvicorn never reported started"
        self.epoch = srv_api.machinery().hub.epoch
        return self

    def stop(self) -> None:
        if self.server is None or self.thread is None:
            return
        self.server.should_exit = True
        self.thread.join(WAIT_S)
        assert not self.thread.is_alive(), "uvicorn thread outlived its stop"
        self.server, self.thread = None, None

    def restart(self) -> "LiveServer":
        """Stop and boot again on the same port. New process, new epoch."""
        previous = self.epoch
        self.stop()
        self.start()
        assert self.epoch != previous, \
            "a restarted server must mint a new server_epoch (§4.3)"
        return self


# ------------------------------------------------------- the client config

_CFG = """\
client:
  default_server: {default_server}
  servers:
{servers}
  defaults:
    req_timeout_s:    {req_timeout_s}
    retry_num:        {retry_num}
    max_pending:      {max_pending}
    slot_wait_s:      {slot_wait_s}
    max_in_flight:    {max_in_flight}
    sent_timeout_s:   {sent_timeout_s}
    close_timeout_s:  {close_timeout_s}
    msg_id_block:     1000
    idle_timeout_s:   {idle_timeout_s}
    sweep_interval_s: {sweep_interval_s}
    gzip_min_bytes:   1024
    base_dir:         "{base_dir}"
"""


def _servers_block(endpoints: dict[str, tuple[str, str]]) -> str:
    return "".join(
        f"    {alias}:\n"
        f'      base_path: "{base_path}"\n'
        f"      service_name: {service_name}\n"
        for alias, (base_path, service_name) in endpoints.items())


# ------------------------------------------------------------------- lab


class Lab:
    """Everything one test starts, and the single teardown that ends it.

    Owning both halves in one object is what makes the leak assertion
    possible: teardown stops the client threads *first* (an app calls
    `close()`; a crashed one does not, and this stands in for the crash), then
    the server, then asserts that nothing this library started is still
    running.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.handler = AppHandler()
        self.closed: list[str] = []
        self.server: LiveServer | None = None
        self.endpoints: dict[str, tuple[str, str]] = {}
        self._restores: list = []
        self._remotes: list[subprocess.Popen] = []
        self._transports: list[httpx.BaseTransport] = []
        self.remote_log: Path | None = None

    # -- server ----------------------------------------------------------

    def start_server(self, *services: str, **server_cfg) -> LiveServer:
        services = services or ("ingest",)
        self.server = LiveServer(services, self.tmp / "producers.json",
                                 self.handler, self.closed.append,
                                 **server_cfg).start()
        for service in services:
            self.endpoints[service] = (self.server.base_path, service)
        return self.server

    def start_remote(self, alias: str, service: str = "audit") -> str:
        """A second server in a **separate process**.

        The machinery is process-wide (§6.3): one queue, one hub, one
        `server_epoch`. Two in one process would share all three, and an
        outcome published from one event loop would try to wake an
        `asyncio.Event` belonging to the other -- which fails silently, and is
        precisely why `notify.py` forbids it. So "two servers" means what it
        says, and the second one is a real OS process.
        """
        sock = _bind()
        port = sock.getsockname()[1]
        sock.close()
        script = self.tmp / f"remote_{alias}.py"
        log = self.tmp / f"remote_{alias}.jsonl"
        script.write_text(_REMOTE_SERVER, encoding="utf-8")
        # §6.2's `TALIUM_MSGING_CONFIG`, which is how a deployed server is
        # configured -- and the only way the README's call order (`on_event`
        # first, then `make_app`) can carry a non-default config at all. See
        # `test_on_event_before_srv_init_can_carry_an_explicit_config`.
        server_cfg = self.tmp / f"server_{alias}.yaml"
        server_cfg.write_text(
            "server:\n"
            "  heartbeat_s: 0.5\n"
            "producer_store:\n"
            "  backend: file\n"
            f"  file:\n    path: \"{self.tmp / f'producers_{alias}.json'}\"\n",
            encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path),
                   TALIUM_MSGING_CONFIG=str(server_cfg))
        proc = subprocess.Popen(
            [sys.executable, str(script), str(port), service, str(log)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._remotes.append(proc)
        assert wait_until(lambda: _port_open(port), WAIT_S), \
            f"remote server on {port} never came up (exit={proc.poll()})"
        base_path = f"http://127.0.0.1:{port}"
        self.endpoints[alias] = (base_path, service)
        self.remote_log = log
        return base_path

    def remote_deliveries(self) -> list[dict]:
        """What the out-of-process server handled, as it wrote it down."""
        if self.remote_log is None or not self.remote_log.exists():
            return []
        return [json.loads(line)
                for line in self.remote_log.read_text().splitlines() if line]

    def own(self, transport: httpx.BaseTransport) -> httpx.BaseTransport:
        """Take responsibility for closing a caller-supplied transport. The
        library never closes one it was handed, so the test has to."""
        self._transports.append(transport)
        return transport

    def queue_max(self, size: int) -> None:
        """§5.1's `server_busy`: shrink the process queue and remove the
        workers, so `queue_max` offers fill it and the next one is a 503."""
        self._restores.append(_testing.set_queue_max_for_tests(size))

    # -- client ----------------------------------------------------------

    def client_init(self, default_server: str | None = None, **defaults) -> str:
        """`clnt_init()` against every endpoint started so far.

        The store root is fixed per Lab, not per call, so a second
        `client_init()` after `clnt._reset_for_tests()` is a **process
        restart** against the same durable store -- which is exactly what §2.3
        reconciles.
        """
        # `idle_timeout_s` is 10x `HEARTBEAT_S` (so a healthy stream is never
        # mistaken for a dead one) and well under `WAIT_S` (so the known
        # `stop()` join miss resolves inside the leak deadline -- see
        # `assert_no_leaked_threads`).
        opts = dict(req_timeout_s=5, retry_num=3, max_pending=10000,
                    slot_wait_s=0.1, max_in_flight=64, sent_timeout_s=300,
                    close_timeout_s=10, idle_timeout_s=5,
                    sweep_interval_s=0.05,
                    base_dir=str(self.tmp / "store"))
        opts.update(defaults)
        path = self.tmp / "msging.yaml"
        path.write_text(_CFG.format(
            default_server=default_server or next(iter(self.endpoints)),
            servers=_servers_block(self.endpoints), **opts), encoding="utf-8")
        clnt.clnt_init(str(path))
        return str(path)

    def producer(self, alias: str | None = None, *, notify_on_ack: bool = False,
                 descriptor: dict | None = None,
                 transport: httpx.BaseTransport | None = None
                 ) -> tuple[str, list]:
        """`register()` + `on_notify()`, i.e. the README's opening lines.

        Returns `(producer_id, notices)`, where `notices` is appended to on the
        SSE reader thread.
        """
        if transport is not None:
            self.own(transport)
        pid = clnt.register(descriptor or {"host": "laptop-7",
                                           "kind": "collector"},
                            alias, _transport=transport)["producer_id"]
        notices: list = []
        clnt.on_notify(pid, lambda msg_id, ev: notices.append(ev),
                       notify_on_ack=notify_on_ack)
        return pid, notices

    # -- waits -----------------------------------------------------------

    def wait_empty(self, pid: str) -> None:
        """Wait for the durable store to stay empty.

        *Stay*, and that word is load-bearing. An empty store is not the same
        claim as a settled one: the cleaner demotes, resends and purges on
        every pass, so `pending/` and `sent/` can both be momentarily empty
        with a POST still on the wire for a record about to come back. A single
        poll would believe it. Requiring the answer to hold across several
        makes that unable to satisfy it; a genuinely finished store satisfies
        it forever. Still no fixed sleep -- the poll and the deadline belong to
        `wait_until`.

        (`total()` itself is a real snapshot: it holds the store lock across
        all three listings, so a record mid-rename between two of them is
        counted in exactly one. It did not always, which is what this settle
        window was originally written for.)
        """
        store = clnt._store_for_tests(pid)
        settled_at: list[float] = [0.0]

        def stable() -> bool:
            if store.total() != 0:
                settled_at[0] = 0.0
                return False
            now = time.monotonic()
            if not settled_at[0]:
                settled_at[0] = now
            return now - settled_at[0] >= SETTLE_S

        assert wait_until(stable, WAIT_S), (
            f"store never settled empty: pending={store.count('pending')} "
            f"sent={store.count('sent')} nacked={store.count('nacked')}")

    def wait_state(self, pid: str, state: str, count: int) -> None:
        """Wait for one state directory to hold exactly `count` records.

        No settling needed, unlike `wait_empty`: the split-listing race can
        only make a record *invisible*, so a directory that reads N really
        holds N.
        """
        store = clnt._store_for_tests(pid)
        assert wait_until(lambda: store.count(state) == count, WAIT_S), (
            f"{state}/ never reached {count}: pending={store.count('pending')} "
            f"sent={store.count('sent')} nacked={store.count('nacked')}")

    # -- teardown --------------------------------------------------------

    def teardown(self) -> None:
        clnt._reset_for_tests()
        for transport in self._transports:
            # A caller-supplied transport is the caller's to close -- the
            # library deliberately does not close one it was handed.
            transport.close()
        for restore in reversed(self._restores):
            restore()
        if self.server is not None:
            self.server.stop()
        for proc in self._remotes:
            proc.terminate()
            try:
                proc.wait(timeout=WAIT_S)
            except subprocess.TimeoutExpired:            # pragma: no cover
                proc.kill()
                proc.wait(timeout=WAIT_S)

    def leaked_threads(self) -> list[str]:
        """Threads this library started that are still running.

        Named rather than counted: pytest's own workers and whatever the
        interpreter already had are irrelevant, and a count would make this
        assertion fail for reasons that have nothing to do with msging.
        """
        return sorted(t.name for t in threading.enumerate()
                      if t.name.startswith("msging-") and t.is_alive())

    def assert_no_leaked_threads(self) -> None:
        """Nothing this library started outlives the test.

        Polled rather than sampled once, and the tolerance that buys is
        deliberate and bounded. It used to cover one **known defect**:
        `SseReader.stop()` occasionally failed to join its reader -- it set the
        flag, `_abort()` shut the socket down (successfully -- measured) and
        then closed the client, which raced the wakeup that shutdown had just
        delivered; the join timed out, `sse_stop_timeout` was logged, and the
        thread stayed parked in `recv()` until `idle_timeout_s`. Roughly 1 in
        150-250 `stop()` calls against a real server on loopback; `_abort` no
        longer does both, and 2800 measured start/stop cycles miss no joins.

        The poll stays because it is the honest assertion either way: a thread
        gone within the deadline was late, and a thread still alive at the
        deadline is genuinely leaked. `idle_timeout_s` is held well under
        `WAIT_S` in `client_init` precisely so those two stay distinguishable
        -- a regression of the old defect shows up as a slow pass here and as a
        `sse_stop_timeout` line, never as a green run.
        """
        assert wait_until(lambda: not self.leaked_threads(), WAIT_S), (
            f"msging threads outlived the test by more than {WAIT_S}s: "
            f"{self.leaked_threads()}")


class HoldEventResponse(httpx.BaseTransport):
    """A **real** HTTP transport that holds `/event`'s answer back.

    Nothing here is mocked: the POST goes over the socket, the server accepts
    it and enqueues it exactly as always. The only thing delayed is the moment
    `send()` gets to *see* the 202 -- a loaded box, a slow proxy, a GC pause
    between `recv()` and the return.

    That is enough to decide a race the client half is otherwise at the mercy
    of: the server can publish an outcome before the client has finished
    handling the response to the POST that produced it. `hold` is a predicate,
    not a duration, so the ordering is pinned rather than assumed.
    """

    def __init__(self, hold) -> None:
        self.inner = httpx.HTTPTransport()
        self.hold = hold

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        if request.url.path.endswith("/event"):
            wait_until(self.hold, WAIT_S)
        return response

    def close(self) -> None:
        self.inner.close()


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


#: The second server, as its own process. Deliberately the README's server
#: recipe verbatim -- `on_event`, then `make_app`, config from
#: `TALIUM_MSGING_CONFIG` -- so what the two-server test exercises is the
#: documented wiring and not a test-only shortcut.
_REMOTE_SERVER = textwrap.dedent('''\
    import json
    import sys
    from pathlib import Path

    import uvicorn

    from common.msging import srv

    port, service, log = sys.argv[1], sys.argv[2], sys.argv[3]
    log_path = Path(log)


    def handle(env):
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"msg_id": env.msg_id,
                                 "service_name": env.service_name,
                                 "producer_id": env.producer_id,
                                 "payload": env.payload}) + "\\n")
        srv.send_notice(env.producer_id, env.msg_id, "ack",
                        info={"served_by": service})


    srv.on_event(handle)
    app = srv.make_app(service)
    uvicorn.run(app, host="127.0.0.1", port=int(port), log_level="critical")
''')


@pytest.fixture(autouse=True)
def lab(tmp_path):
    """The one fixture every test here uses. Autouse so its teardown -- and
    the leak assertion in it -- cannot be skipped by forgetting to ask."""
    lab = Lab(tmp_path)
    try:
        yield lab
    finally:
        lab.teardown()
        lab.assert_no_leaked_threads()


# ==================================================== §2.2 the steady state


def test_send_runs_the_handler_and_the_ack_purges_the_record(lab):
    """§2.2 end to end, and every step of it observed:

    register -> send -> 202 -> app handler runs -> `send_notice` -> SSE
    delivers -> record purged. Nothing here is faked: the payload assertion
    proves the bytes crossed a socket and came back out of `Envelope`.
    """
    lab.start_server()
    lab.client_init()
    pid, notices = lab.producer(notify_on_ack=True)

    clnt.set_info(pid, {"agent_version": "1.4.2"})
    result = clnt.send(pid, {"session": "s-1", "rows": 3}, {"batch": "b-9"})

    assert result.err_code == "ok"
    assert result.msg_id is not None

    env = lab.handler.envelope(result.msg_id)
    assert env is not None, "the app handler never ran"
    assert env.payload == {"session": "s-1", "rows": 3}
    # §7: per-msg info shallow-merges over the producer-level bag.
    assert env.info == {"agent_version": "1.4.2", "batch": "b-9"}
    assert env.producer_id == pid
    assert env.service_name == "ingest"

    assert wait_until(lambda: bool(notices), WAIT_S), "no outcome over SSE"
    ev = notices[0]
    assert (ev.code, ev.msg_id, ev.info) == ("ack", result.msg_id,
                                             {"row_id": result.msg_id})

    lab.wait_empty(pid)
    status = clnt.producer_status(pid)
    assert (status["pending"], status["sent"], status["nacked"]) == (0, 0, 0)
    assert status["unauthorized"] is False


def test_default_callback_stays_silent_on_ack_but_the_record_still_purges(lab):
    """§2.2: `notify_on_ack` is off by default, because on an ACK the record
    is already gone and the app has no decision to make. The delivery still
    has to happen -- so the proof is the empty store, not the callback."""
    lab.start_server()
    lab.client_init()
    pid, notices = lab.producer()               # notify_on_ack defaults False

    result = clnt.send(pid, {"x": 1})
    assert result.err_code == "ok"

    lab.wait_empty(pid)
    assert notices == []
    assert clnt.poll_notify(pid) == []          # §6.1 item 9 holds the same set


def test_many_msgs_all_land(lab):
    """At-least-once over a real socket, more than one msg deep: every one of
    them reaches the app and every record is purged."""
    lab.start_server()
    lab.client_init()
    pid, notices = lab.producer(notify_on_ack=True)

    ids = [clnt.send(pid, {"n": n}).msg_id for n in range(12)]
    assert all(msg_id is not None for msg_id in ids)

    lab.wait_empty(pid)
    assert wait_until(lambda: len(notices) >= 12, WAIT_S)

    # A *set*, and deliberately so. `send()` documents that the record sits in
    # `pending/` from `put_pending` until its POST returns, so a cleaner pass
    # in that window can ship it a second time -- at-least-once behaving as
    # specified (§1 promises no exactly-once and no ordering). What must hold
    # is that every msg arrived at least once and none was invented.
    delivered = [p["n"] for p in lab.handler.payloads()]
    assert set(delivered) == set(range(12))
    assert len(delivered) >= 12
    assert {ev.code for ev in notices} == {"ack"}
    assert {ev.msg_id for ev in notices} == set(ids)


# ============================================================ §2.2 the NACK


def test_nack_fires_the_callback_and_purge_removes_the_record(lab):
    """§2.2 step 4: a NACK reaches the app, and `resolve(PURGE)` is terminal.

    The record has to be *in* `nacked/` first -- that is the state the app's
    decision is made against -- and gone after.
    """
    lab.start_server()
    lab.handler.policy = nack
    # The outcome is released only once `send()` has come back, so the record
    # is provably in `sent/` when the NACK lands. Without the gate this test
    # is a coin flip -- see
    # `test_a_nack_that_beats_its_own_post_response_reaches_nacked`.
    gate = lab.handler.gate = threading.Event()
    lab.client_init()
    pid, notices = lab.producer()

    result = clnt.send(pid, {"bad": True})
    assert result.err_code == "ok"              # the POST landed; the app said no
    gate.set()

    assert wait_until(lambda: bool(notices), WAIT_S), "no NACK over SSE"
    ev = notices[0]
    assert ev.code == "nack"
    assert ev.msg_id == result.msg_id
    assert ev.reason == "payload rejected by the app"

    store = clnt._store_for_tests(pid)
    assert wait_until(lambda: store.count("nacked") == 1, WAIT_S)

    clnt.resolve(pid, result.msg_id, PURGE)
    assert store.total() == 0
    assert store.get(result.msg_id) is None

    closed = clnt.close(pid)
    assert (closed.status, closed.unresolved) == ("drained", 0)


@pytest.mark.slow
def test_nack_retain_resends_and_the_msg_is_eventually_acked(lab):
    """§2.2 step 5 + §6.1: `RETAIN` is the default, and it means *resend*.

    The app NACKs the first delivery and accepts the second -- a fixed payload
    with a transient server-side reason, which is the case RETAIN exists for.
    The proof is two deliveries to the handler and an empty store.
    """
    lab.start_server()
    nacked_once: set[int] = set()

    def policy(env):
        if env.msg_id in nacked_once:
            return ack(env)
        nacked_once.add(env.msg_id)
        return nack(env)

    lab.handler.policy = policy
    gate = lab.handler.gate = threading.Event()
    lab.client_init()
    pid, notices = lab.producer(notify_on_ack=True)

    result = clnt.send(pid, {"transient": True})
    msg_id = result.msg_id
    gate.set()

    store = clnt._store_for_tests(pid)
    assert wait_until(lambda: store.count("nacked") == 1, WAIT_S)

    clnt.resolve(pid, msg_id, RETAIN)           # the default; spelled out here

    lab.wait_empty(pid)
    assert lab.handler.deliveries(msg_id) == 2, "RETAIN did not resend"
    assert [ev.code for ev in notices] == ["nack", "ack"]


def test_close_does_not_count_an_unresolved_nack(lab):
    """§2.4: `unresolved` is `pending/` + `sent/`. A NACK waiting on the app's
    `resolve()` is not something draining can fix, so `close()` reports
    `drained` with it still on disk -- and §2.3 folds it into `pending/` next
    start."""
    lab.start_server()
    lab.handler.policy = nack
    gate = lab.handler.gate = threading.Event()
    lab.client_init()
    pid, notices = lab.producer()

    clnt.send(pid, {"x": 1})
    gate.set()
    assert wait_until(lambda: bool(notices), WAIT_S)
    store = clnt._store_for_tests(pid)
    assert wait_until(lambda: store.count("nacked") == 1, WAIT_S)

    result = clnt.close(pid)
    assert (result.status, result.unresolved) == ("drained", 0)
    assert store.count("nacked") == 1           # still there, still the app's call

    # §6.1: resolve() stays legal after close(); send() does not.
    clnt.resolve(pid, notices[0].msg_id, PURGE)
    assert store.total() == 0


def test_a_nack_that_beats_its_own_post_response_reaches_nacked(lab):
    """§2.2: a NACK puts the record in `nacked/`, whichever order the two
    answers to one POST happen to arrive in.

    The server answers a msg **twice**: once synchronously with the 202, and
    once asynchronously over SSE (§2.2, §4.1). Nothing sequences those two,
    and over a fast link the outcome routinely wins -- the client is still
    inside `send()`, and the record is still in `pending/` because
    `mark_sent()` runs on the way out.

    `handle_notice` used to be unable to find it in `sent/`, wrote it off as a
    late duplicate, and `_apply` moved it to `sent/` a moment later -- where it
    then waited for an outcome that had already been and gone. The fallout that
    was actually observed, and that this test is the cover for:

      - `producer_status()` reported `nacked: 0`, `sent: 1`;
      - `resolve(msg_id, RETAIN)` was a silent no-op (`restore_to_pending`
        wants `nacked/` too), so the app's decision was lost;
      - `close()` counted it in `unresolved` and answered `timeout`, though
        §2.4 says a NACK awaiting `resolve()` is explicitly *not* counted;
      - nothing moved until `sent_timeout_s` -- 300 s by default.

    `HoldEventResponse` only pins the ordering; it does not create it. The
    ACK path always tolerated exactly this race (`_apply` catches
    `FileNotFoundError` from `mark_sent`, "the server can push an ACK before
    this call returns"), which is what made the NACK path's silence a defect
    rather than a design choice. `MsgStore.mark_nacked` now takes the record
    from `pending/` too, and returns which state it came from so that only a
    move out of `sent/` hands a window slot back.
    """
    lab.start_server()
    lab.handler.policy = nack
    lab.client_init(sweep_interval_s=3600)      # no loop: nothing else may move it

    landed = threading.Event()
    notices: list = []

    def on_outcome(msg_id, ev):
        notices.append(ev)
        landed.set()                            # ...now let the 202 through

    transport = lab.own(HoldEventResponse(landed.is_set))
    pid = clnt.register({"host": "laptop-7"}, _transport=transport)["producer_id"]
    clnt.on_notify(pid, on_outcome)

    result = clnt.send(pid, {"bad": True})
    assert result.err_code == "ok"

    # The app was told -- the callback fired before `send()` even returned.
    assert [ev.code for ev in notices] == ["nack"]

    store = clnt._store_for_tests(pid)
    assert wait_until(lambda: store.count("nacked") == 1, 2.0), (
        f"NACK not recorded: pending={store.count('pending')} "
        f"sent={store.count('sent')} nacked={store.count('nacked')}")

    # ...and every consequence of it being recorded, since each one was an
    # observed symptom of the defect and not one of them is implied by the
    # count above.
    status = clnt.producer_status(pid)
    assert (status["pending"], status["sent"], status["nacked"]) == (0, 0, 1)

    # §2.4: a NACK waiting on the app's decision is not `unresolved`.
    closed = clnt.close(pid)
    assert (closed.status, closed.unresolved) == ("drained", 0)

    # §6.1: and the decision still reaches the record (resolve() outlives
    # close() by design).
    clnt.resolve(pid, result.msg_id, PURGE)
    assert store.total() == 0


# =================================================================== §2.4


def test_close_returns_drained_after_the_outcomes_land(lab):
    """§2.4: `close()` drains, POSTs `/close`, and answers `{drained, 0}`.

    The `on_close` hook firing is what proves the POST actually reached the
    server rather than being swallowed -- `_post_close` never raises.
    """
    lab.start_server()
    lab.client_init()
    pid, notices = lab.producer(notify_on_ack=True)

    for n in range(5):
        assert clnt.send(pid, {"n": n}).err_code == "ok"

    result = clnt.close(pid)
    assert (result.status, result.unresolved) == ("drained", 0)
    assert clnt._store_for_tests(pid).total() == 0
    assert wait_until(lambda: lab.closed == [pid], WAIT_S), \
        f"the server's on_close hook never fired: {lab.closed}"

    # §2.4 step 1: the gate is shut for anything that writes a msg.
    with pytest.raises(Exception) as exc:
        clnt.send(pid, {"late": True})
    assert "draining" in str(exc.value)


# ================================================== §2.3 restart reconcile


@pytest.mark.slow
def test_restart_reconciliation_resends_pending_and_sent(lab):
    """§2.3: a client that died mid-flight resends everything on the next
    start.

    The two states are set up without stopping the server, so what is under
    test is reconciliation and nothing else:

      - `sent/`   : the app swallows the outcome, so the msg stays SENT.
      - `pending/`: `max_in_flight=1` is already spent by that msg, so the
                    next `send()` is `stored_throttled` -- stored, never
                    POSTed (§5.1).

    Then `clnt._reset_for_tests()` -- documented as exactly this simulation:
    the threads stop, the durable store is untouched, no `/close` is sent.
    """
    lab.start_server()
    lab.handler.policy = drop
    lab.client_init(max_in_flight=1)
    pid, _ = lab.producer()

    in_flight = clnt.send(pid, {"which": "sent"})
    assert in_flight.err_code == "ok"
    lab.wait_state(pid, "sent", 1)

    throttled = clnt.send(pid, {"which": "pending"})
    assert throttled.err_code == "stored_throttled"
    lab.wait_state(pid, "pending", 1)
    assert lab.handler.deliveries(throttled.msg_id) == 0, \
        "a throttled msg must never be POSTed"

    store_dir = clnt._producer_for_tests(pid).store_dir

    # -- the crash. No close(), no /close, nothing flushed.
    clnt._reset_for_tests()
    lab.assert_no_leaked_threads()

    # -- the restart, same config, same store, same identity.
    lab.handler.policy = ack
    lab.client_init(max_in_flight=1)
    restarted, notices = lab.producer(notify_on_ack=True)
    assert restarted == pid, "register() must reattach the stored identity (§3)"
    assert clnt._producer_for_tests(pid).store_dir == store_dir

    lab.wait_empty(pid)
    assert wait_until(lambda: len(notices) >= 2, WAIT_S)
    assert {ev.msg_id for ev in notices} == {in_flight.msg_id, throttled.msg_id}
    # The SENT one was delivered twice: once before the crash (outcome
    # swallowed) and once after. At-least-once, exactly as specified.
    assert lab.handler.deliveries(in_flight.msg_id) == 2
    assert lab.handler.deliveries(throttled.msg_id) == 1


# ========================================================== §4.3 the epoch


@pytest.mark.slow
def test_server_restart_sweeps_sent_back_to_pending_and_redelivers(lab):
    """§4.3: a changed `server_epoch` means the server lost whatever it had in
    flight, so every `SENT` record goes back to `PENDING` and is resent.

    `sent_timeout_s` is deliberately far out of reach here (300 s), so the
    only thing that can move the record is the epoch sweep -- otherwise this
    test would pass on §4.4's backstop and say nothing about §4.3.
    """
    server = lab.start_server()
    lab.handler.policy = drop                   # first boot answers nothing
    lab.client_init(sent_timeout_s=300)
    pid, notices = lab.producer(notify_on_ack=True)

    result = clnt.send(pid, {"x": 1})
    assert result.err_code == "ok"
    lab.wait_state(pid, "sent", 1)

    lab.handler.policy = ack                    # the new boot answers
    server.restart()

    lab.wait_empty(pid)
    assert wait_until(lambda: bool(notices), WAIT_S)
    assert notices[-1].code == "ack"
    assert lab.handler.deliveries(result.msg_id) == 2, \
        "the epoch sweep did not resend the SENT record"


# ================================================ §4.4 the sent_timeout_s


def test_sent_timeout_demotes_a_msg_whose_outcome_never_came(lab):
    """§4.4: the backstop for an outcome dropped anywhere -- a crashed
    handler, a lost frame, a notice shed for size. The server keeps running
    and the epoch never changes, so the *only* thing that can move this record
    is `sent_timeout_s`."""
    lab.start_server()
    lab.handler.drop_once()
    lab.client_init(sent_timeout_s=0.3, sweep_interval_s=0.05)
    pid, notices = lab.producer(notify_on_ack=True)

    result = clnt.send(pid, {"x": 1})
    assert result.err_code == "ok"
    lab.wait_state(pid, "sent", 1)

    lab.wait_empty(pid)
    assert lab.handler.deliveries(result.msg_id) == 2, "no demotion + resend"
    assert wait_until(lambda: bool(notices), WAIT_S)
    assert notices[-1].code == "ack"


# ============================================================ §5 rejections


def test_slot_full_when_the_durable_store_is_at_max_pending(lab):
    """§5.1: `slot_full` is one of only two outright rejections. The msg is
    **not stored** -- the caller still holds it and backs off.

    The cleaner loop is off (`sweep_interval_s=3600`), and not for speed. The
    slot check reads `MsgStore.total()`; with the loop running, its
    first-connect epoch sweep renames the record out of `sent/` and into
    `pending/`, and a `total()` that straddled that rename used to see the
    store as empty and hand out a slot `max_pending` should have refused --
    an over-limit `send()` came back `ok`. `total()` now takes the store lock
    across all three listings, so the rename is no longer observable
    mid-flight; the loop stays off anyway, because what this test is about is
    the bound and not the race, and holding the store still keeps it saying
    only that.
    """
    lab.start_server()
    lab.handler.policy = drop                   # nothing ever frees the slot
    lab.client_init(max_pending=1, slot_wait_s=0.1, sent_timeout_s=300,
                    sweep_interval_s=3600)
    pid, _ = lab.producer()

    first = clnt.send(pid, {"n": 1})
    assert first.err_code == "ok"
    lab.wait_state(pid, "sent", 1)

    rejected = clnt.send(pid, {"n": 2})
    assert rejected.err_code == "slot_full"
    assert rejected.msg_id is None, "slot_full means NOT stored (§8.1)"

    store = clnt._store_for_tests(pid)
    assert store.total() == 1
    # The rejected msg never reached the app. The accepted one may have
    # arrived more than once (the cleaner can race `send()`'s own POST, see
    # `test_many_msgs_all_land`), which is not what this test is about.
    assert {"n": 2} not in lab.handler.payloads()
    assert {p["n"] for p in lab.handler.payloads()} == {1}


def test_server_busy_when_the_server_queue_is_full(lab):
    """§5.2/§8.1: a full server queue is a 503 + `Retry-After`, the msg stays
    PENDING, and nothing is lost. The client must not treat it as a failure."""
    lab.start_server()
    lab.client_init(sweep_interval_s=3600)      # no loop: the record must stay put
    lab.queue_max(1)                            # replacement queue, no workers
    pid, _ = lab.producer()

    swallowed = clnt.send(pid, {"n": 1})        # fills the replacement queue
    assert swallowed.err_code == "ok"

    busy = clnt.send(pid, {"n": 2})
    assert busy.err_code == "server_busy"
    assert busy.msg_id is not None, "server_busy means STORED (§8.1)"

    store = clnt._store_for_tests(pid)
    assert store.count("pending") == 1
    assert store.get(busy.msg_id) is not None


def test_too_large_is_rejected_against_the_limit_register_returned(lab):
    """§5.2/§6.1: the server's `max_msg_bytes` rides back on `/register`, so
    client and server configs cannot drift. Over it -> `too_large`, not
    stored, and nothing ever goes on the wire."""
    lab.start_server(max_msg_bytes=1024)
    lab.client_init()
    pid, _ = lab.producer()

    assert clnt.producer_status(pid)["pending"] == 0
    result = clnt.send(pid, {"blob": "x" * 4096})

    assert result.err_code == "too_large"
    assert result.msg_id is None, "too_large means NOT stored (§8.1)"
    assert clnt._store_for_tests(pid).total() == 0
    assert lab.handler.seen == [], "an oversized msg must never reach the app"

    # ...and the limit is the server's, not a client-side guess.
    small = clnt.send(pid, {"blob": "x" * 64})
    assert small.err_code == "ok"
    lab.wait_empty(pid)


# ================================================== §5.1 the binding model


def test_two_producers_against_one_server(lab):
    """Two aliases = two bindings = two `producer_id`s, two durable stores,
    and two SSE channels. The server must route each outcome to the producer
    whose msg it answers -- a stream carries one binding's outcomes only."""
    server = lab.start_server("ingest", "audit")
    assert lab.endpoints["ingest"][0] == lab.endpoints["audit"][0] == \
        server.base_path

    lab.client_init(default_server="ingest")
    ingest, ingest_notices = lab.producer("ingest", notify_on_ack=True)
    audit, audit_notices = lab.producer("audit", notify_on_ack=True)

    assert ingest != audit
    assert (clnt._producer_for_tests(ingest).store_dir
            != clnt._producer_for_tests(audit).store_dir)

    a = clnt.send(ingest, {"who": "ingest"})
    b = clnt.send(audit, {"who": "audit"})
    assert (a.err_code, b.err_code) == ("ok", "ok")

    lab.wait_empty(ingest)
    lab.wait_empty(audit)
    assert wait_until(lambda: ingest_notices and audit_notices, WAIT_S)

    assert [ev.msg_id for ev in ingest_notices] == [a.msg_id]
    assert [ev.msg_id for ev in audit_notices] == [b.msg_id]
    assert all(ev.producer_id == ingest for ev in ingest_notices)
    assert all(ev.producer_id == audit for ev in audit_notices)

    by_service = {env.service_name: env.payload for env in lab.handler.seen}
    assert by_service == {"ingest": {"who": "ingest"},
                          "audit": {"who": "audit"}}

    assert clnt.close(ingest).status == "drained"
    assert clnt.close(audit).status == "drained"


@pytest.mark.slow
def test_one_client_against_two_separate_servers(lab):
    """§5.1: an alias names a *binding* -- endpoint + service -- and one
    process can hold several. Two real servers, two ports, two processes.

    Each producer's msgs must reach its own server and no other, and each
    binding must keep its own identity and its own durable store.
    """
    lab.start_server("ingest")
    lab.start_remote("remote_audit", service="audit")

    lab.client_init(default_server="ingest")
    local, local_notices = lab.producer("ingest", notify_on_ack=True)
    remote, remote_notices = lab.producer("remote_audit", notify_on_ack=True)

    assert local != remote
    assert clnt.producer_status(local)["base_path"] != \
        clnt.producer_status(remote)["base_path"]

    here = clnt.send(local, {"where": "local"})
    there = clnt.send(remote, {"where": "remote"})
    assert (here.err_code, there.err_code) == ("ok", "ok")

    lab.wait_empty(local)
    lab.wait_empty(remote)
    assert wait_until(lambda: local_notices and remote_notices, WAIT_S)

    # The in-process server saw only the local msg...
    assert lab.handler.payloads() == [{"where": "local"}]
    # ...and the other process saw only the remote one.
    delivered = lab.remote_deliveries()
    assert [d["payload"] for d in delivered] == [{"where": "remote"}]
    assert delivered[0]["producer_id"] == remote
    assert delivered[0]["service_name"] == "audit"
    assert remote_notices[-1].info == {"served_by": "audit"}
    assert local_notices[-1].info == {"row_id": here.msg_id}


# ============================================== §6.2 wiring (known defect)


def test_on_event_before_srv_init_can_carry_an_explicit_config(lab):
    """§6.2's wiring order must be able to carry `srv_init(config=...)`.

    The trap this pins, in the README's own order:

        srv.on_event(handle)                  # built _Machinery(default cfg)
        srv.srv_init("ingest", config=cfg)    # RuntimeError: already set

    and the error said the config was "already set", which was misleading: the
    host never set one. `on_event()` did, implicitly, out of the defaults.

    The consequences were real, not cosmetic -- the defaults it silently
    installed include `max_msg_bytes = 32 MiB`, the default `queue_max`, and a
    `producer_store` path under `~/.talium`, so a host that reordered its calls
    to dodge the exception ran on those rather than on the config it wrote.
    `on_event()`/`on_close()` now park the handler until something builds the
    machinery, so `srv_init()` really is the first call.
    """
    cfg = MsgingConfig()
    cfg.server.max_msg_bytes = 4096
    cfg.producer_store.file.path = str(lab.tmp / "wiring.json")

    # A virgin process: no machinery at all. `reset_for_tests()` cannot stand
    # in here -- it *installs* one, which is the very thing under test.
    srv_api._state = None
    handler = lambda env: None                          # noqa: E731
    srv.on_event(handler)
    srv.on_close(lambda producer_id: None)
    srv.srv_init("ingest", config=cfg)

    st = srv_api.machinery()
    assert st.cfg is cfg
    assert st.cfg.server.max_msg_bytes == 4096
    # Parked, not dropped: a handler registered before the machinery existed
    # still has to be the one the worker pool dispatches to.
    assert st.ev_handler is handler
    assert st.close_handler is not None

    # A genuine conflict -- a second, *different* config -- still raises (§6.2).
    with pytest.raises(RuntimeError, match="process-wide"):
        srv.srv_init("audit", config=MsgingConfig())


# ================================================================= hygiene


def test_a_full_cycle_leaves_no_threads_or_file_descriptors_behind(lab):
    """Explicitly, once: a register/send/close cycle must not leak.

    The autouse teardown asserts the thread half after every test in this
    module; this one also pins file descriptors, because the client opens a
    long-lived SSE connection and one leaked socket per producer is a
    collector that runs out of fds after a few days.
    """
    lab.start_server()

    def cycle() -> None:
        lab.client_init()                       # a fresh process, each time
        pid, notices = lab.producer(notify_on_ack=True)
        assert clnt.send(pid, {"x": 1}).err_code == "ok"
        assert wait_until(lambda: bool(notices), WAIT_S)
        assert clnt.close(pid).status == "drained"
        clnt._reset_for_tests()

    # One warm-up, measured *after*: the first cycle pays for lazily-imported
    # modules and the server's own first connection, and counting that as a
    # leak would make this assertion noise.
    cycle()
    lab.assert_no_leaked_threads()
    baseline_fds = _open_fds()

    for _ in range(3):
        cycle()

    lab.assert_no_leaked_threads()

    if baseline_fds is not None:
        after_fds = _open_fds()
        assert after_fds is not None
        # Not equality: the interpreter and pytest open and close descriptors
        # of their own. One leaked socket per cycle would show up as +3.
        assert after_fds <= baseline_fds + 2, (
            f"file descriptors grew {baseline_fds} -> {after_fds} over three "
            "register/send/close cycles")


def _open_fds() -> int | None:
    """Open descriptors for this process, or None where that is not knowable."""
    for path in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(path):
            try:
                return len(os.listdir(path))
            except OSError:                              # pragma: no cover
                return None
    return None                                          # pragma: no cover
