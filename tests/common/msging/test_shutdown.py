"""§6.2/§6.3 shutdown: `srv_shutdown()` and the signal wiring around it.

**What this module exists to pin.** `srv_stop()` runs from the ASGI lifespan,
and ASGI runs lifespan *shutdown* only after the server has drained its
connections -- but `/notify` is an endless stream by design (§4.1), so it never
drains, so `srv_stop()` never runs. With uvicorn's default
`timeout_graceful_shutdown` (`None` = wait forever), which is exactly what
§6.3's recipe and the README quick start produce, a server with one producer
connected could not be shut down at all: SIGTERM and SIGINT both hung past 60 s
and needed SIGKILL, `srv_stop()` never ran, the worker pool never drained,
`on_close` never fired, and the whole in-memory queue died with the process.

`srv_shutdown()` is what makes the shutdown possible: it stops accepting, drains
the pool and ends every stream **before** the drain, so the connections finish
and the lifespan can complete. It is chained onto SIGINT/SIGTERM at
`srv_start()` and restored at `srv_stop()` -- a library that leaves its handlers
on a host's process is a bug, so that restore is asserted here too.

The unit half drives the ASGI app directly, for the same reason `test_router.py`
does: `httpx.ASGITransport` concatenates the whole response body before it
returns, so an endless `/notify` stream never yields a first frame through it,
and the streams are precisely what shutdown has to end.

The end-to-end half (`@pytest.mark.slow`) is a **real uvicorn process with
uvicorn's own defaults** and a real producer holding `/notify` open, killed with
a real signal. It must be a subprocess: the defect was a process that would not
exit, and nothing in-process can assert on that.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from common.msging import srv
from common.msging.config import MsgingConfig
from common.msging.srv import _testing, api as srv_api

from .conftest import wait_until

pytestmark = pytest.mark.asyncio

#: Bound on every in-process wait here.
WAIT_S = 5.0

#: How long the end-to-end server gets to die after its signal. The defect it
#: guards was "never" -- 60 s and still running, killed with SIGKILL -- so any
#: number in this range distinguishes fixed from broken. Generous enough that a
#: loaded CI box does not flake it.
EXIT_BOUND_S = 20.0


# --------------------------------------------------------------- helpers


def _scope(method: str, path: str, headers: dict[bytes, bytes]) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "client": ("127.0.0.1", 1234), "server": ("t", 80),
        "headers": [(b"host", b"t"), *headers.items()],
    }


class _Stream:
    """One `/notify` connection, driven straight at the ASGI app.

    `hold` parks the stream inside `send` -- i.e. mid-`yield`, before the hub
    has popped the notice it just handed over -- which is the only way to get a
    live subscriber whose ring does *not* drain. `srv_shutdown()`'s
    `shutdown_drain_s` window is unobservable without one.
    """

    def __init__(self, app, key: str, service: str = "ingest",
                 hold: bool = False) -> None:
        self.frames: list[str] = []
        self.opened = asyncio.Event()
        self.hung_up = asyncio.Event()
        self.released = asyncio.Event()
        self._hold = hold
        self._app = app
        self._path = f"/talium/{service}/v1/notify"
        self._key = key
        self.task: asyncio.Task | None = None

    async def _receive(self) -> dict:
        await self.hung_up.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict) -> None:
        if message["type"] != "http.response.body":
            return
        body = message.get("body", b"")
        if not body:
            return
        self.frames.append(body.decode())
        self.opened.set()
        if self._hold:
            await self.released.wait()

    async def open(self) -> "_Stream":
        scope = _scope("GET", self._path,
                       {b"authorization": f"Bearer {self._key}".encode()})
        self.task = asyncio.create_task(
            self._app(scope, self._receive, self._send))
        await asyncio.wait_for(self.opened.wait(), WAIT_S)
        return self

    async def close(self) -> None:
        self.released.set()
        self.hung_up.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def wait_ended(self, timeout: float = WAIT_S) -> bool:
        """True if the stream's ASGI call finished on its own -- which is what
        `srv_shutdown()` ending it looks like from the connection's side."""
        assert self.task is not None
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout)
        except asyncio.TimeoutError:
            return False
        return True


class _Server:
    """The app under test plus the identity a route needs to reach it."""

    def __init__(self, app: FastAPI, client: httpx.AsyncClient) -> None:
        self.app = app
        self.client = client
        self.producer_id = ""
        self.api_key = ""

    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def register(self, service: str = "ingest") -> "_Server":
        r = await self.client.post(f"/talium/{service}/v1/register", json={})
        assert r.status_code == 200, r.text
        self.producer_id = r.json()["producer_id"]
        self.api_key = r.json()["api_key"]
        return self

    async def post_event(self, msg_id: int, service: str = "ingest"):
        return await self.client.post(
            f"/talium/{service}/v1/event", headers=self.auth(),
            json={"producer_id": self.producer_id, "msg_id": msg_id,
                  "service_name": service,
                  "created_at": "2026-07-25T10:15:30Z", "payload": {"x": 1}})


def _app_for(*names: str, config: MsgingConfig | None = None) -> FastAPI:
    app = FastAPI()
    for i, name in enumerate(names):
        app.include_router(srv.srv_init(name, config=config if i == 0 else None))
    return app


@asynccontextmanager
async def held_drain():
    """Hold the drain open inside the block, and **always** let it go.

    Several tests here need `srv_shutdown()`'s drain to still be running while
    they assert on it, and the way to get that is to gate `queue.stop()`. The
    `finally` is the point: a gate left set on a failed assertion parks the
    handler forever, so the fixture's `srv_stop()` waits out the queue's ten-
    second drain timeout and the failure surfaces as a **hang instead of a
    failure report** -- which is no report at all. Two mutation runs proved
    that the hard way.
    """
    st = srv_api.machinery()
    gate = asyncio.Event()
    original = st.queue.stop

    async def gated_stop(*a, **kw):
        await gate.wait()
        return await original(*a, **kw)

    st.queue.stop = gated_stop
    try:
        yield gate
    finally:
        gate.set()
        st.queue.stop = original


async def _settle() -> None:
    """Let `call_soon_threadsafe` land and the drain task be created.

    `srv_shutdown()` is sync and hands off to the loop; two passes through the
    scheduler is what turns that hand-off into a task a test can await.
    """
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest_asyncio.fixture()
async def server(tmp_path):
    """Machinery + app + a started pool, torn down however the test left it.

    `srv_stop()` in the teardown is not optional: it is what restores the
    signal handlers `srv_start()` chained onto, and a test that skipped it
    would leave this library's handler on pytest's own process.
    """
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    await srv.srv_start()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as client:
        try:
            yield await _Server(app, client).register()
        finally:
            await srv.srv_stop()


# ==================================================== stop accepting (step 1)


async def test_shutdown_answers_503_server_busy_on_register_event_and_notify(server):
    """§6.2 step 1 and §8.1: a draining server stops accepting, and says so in
    the one vocabulary §8 allows -- `503` + `server_busy` + `Retry-After`, the
    code that already means "keep the msg and come back". The client holds the
    msg PENDING and retries, which is exactly what a rolling restart wants."""
    srv.srv_shutdown()

    for name, call in (
            ("register", server.client.post("/talium/ingest/v1/register", json={})),
            ("event", server.post_event(1)),
            ("notify", server.client.get("/talium/ingest/v1/notify",
                                         headers=server.auth()))):
        try:
            # Bounded, and `/notify` is why. `httpx.ASGITransport` concatenates
            # the whole response body before it returns, so a `/notify` that
            # *opens* here never answers at all -- the failure would be an
            # unbounded hang rather than an assertion, which is no failure
            # report at all.
            r = await asyncio.wait_for(call, WAIT_S)
        except asyncio.TimeoutError:
            raise AssertionError(
                f"{name} did not refuse while shutting down -- it is still "
                "serving") from None
        assert r.status_code == 503, f"{name}: {r.text}"
        assert r.json()["code"] == "server_busy"
        assert r.headers["retry-after"]


async def test_close_still_works_while_shutting_down(server):
    """§2.4: `/close` is the client saying it has already drained. A shutting-
    down server is when that is most useful -- it takes no queue slot and holds
    nothing open, so it is the one route with no shutdown check."""
    srv.srv_shutdown()
    r = await server.client.post("/talium/ingest/v1/close",
                                 headers=server.auth(),
                                 json={"producer_id": server.producer_id})
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "ok"


async def test_a_draining_server_never_reads_the_body_it_will_not_process(server):
    """This is what `/event`'s shutdown check actually buys, and it is not the
    status code.

    Drop the check and the request still ends in `503 server_busy`, because the
    queue is closed a moment later and `offer()` refuses -- so a test that only
    asserted on the status would pass with the check deleted. What changes is
    *when*: without it the server first buffers the body, up to
    `max_msg_bytes` (32 MiB by default), for a msg it has already decided not
    to process. The check is placed after auth and before the read precisely so
    that never happens, and the frame count is the only thing that says so.
    """
    pulls = [0]

    async def receive() -> dict:
        pulls[0] += 1
        return {"type": "http.request", "body": b'{"junk": 1}', "more_body": True}

    status: dict = {}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    srv.srv_shutdown()
    scope = _scope("POST", "/talium/ingest/v1/event",
                   {b"authorization": f"Bearer {server.api_key}".encode(),
                    b"content-type": b"application/json"})
    await asyncio.wait_for(server.app(scope, receive, send), WAIT_S)

    assert status["code"] == 503
    assert pulls[0] == 0, \
        f"a shutting-down server read {pulls[0]} body frame(s) it will not process"


async def test_accepting_stops_synchronously_not_when_the_loop_gets_round_to_it(server):
    """The flag is set inside `srv_shutdown()`, before the hand-off to the loop.

    Set inside the callback instead, every request arriving between the signal
    and the loop picking the callback up would be accepted into a pool that is
    about to stop -- answered `202`, marked SENT by the client, and never
    processed. This asserts the window does not exist: no scheduler pass runs
    between the call and the 503.
    """
    srv.srv_shutdown()
    assert srv_api.machinery().shutting_down is True
    r = await server.post_event(1)
    assert r.status_code == 503


# ================================================ drain the pool (step 2)


async def test_shutdown_drains_in_flight_work_before_it_ends_anything(server):
    """§6.2 step 2: a msg a worker has already picked up finishes.

    Gated so the msg is provably still in the handler when `srv_shutdown()` is
    called -- otherwise the pool could have drained it before the shutdown and
    the assertion would prove nothing.
    """
    seen: list = []
    started, working = asyncio.Event(), asyncio.Event()

    async def handler(env):
        seen.append(env.msg_id)
        started.set()
        try:
            await asyncio.wait_for(working.wait(), WAIT_S)
        except asyncio.TimeoutError:                      # pragma: no cover
            pass

    srv.on_event(handler)
    assert (await server.post_event(7)).status_code == 202
    await asyncio.wait_for(started.wait(), WAIT_S)

    try:
        srv.srv_shutdown()
        await _settle()
        task = srv_api.machinery().shutdown_task
        assert task is not None and not task.done(), \
            "the drain must not finish while a msg is still in the handler"
    finally:
        # Released whatever happened above: a handler parked forever would make
        # the fixture's srv_stop() wait out the queue drain timeout, turning a
        # failed assertion into a hang.
        working.set()
    await asyncio.wait_for(task, WAIT_S)
    assert seen == [7]


async def test_a_drained_pool_rejects_further_work(server):
    """After the drain the queue is closed, so nothing can be accepted into a
    pool with no workers left to process it (§8.1's `server_busy` again)."""
    srv.srv_shutdown()
    await _settle()
    await asyncio.wait_for(srv_api.machinery().shutdown_task, WAIT_S)
    assert srv_api.machinery().queue.offer(_envelope(server.producer_id)) is False


def _envelope(producer_id: str):
    from common.msging.models import Envelope
    return Envelope(producer_id=producer_id, msg_id=1, service_name="ingest",
                    created_at="2026-07-25T10:15:30Z", payload={})


# ================================================= end every stream (step 4)


async def test_shutdown_ends_an_open_notify_stream(server):
    """**The defect, in one assertion.** An open `/notify` stream is what kept
    the connection from draining, which is what kept the lifespan from running,
    which is what kept `srv_stop()` from ever executing. `srv_shutdown()` ends
    it from the *signal* side, before the drain, so the connection finishes."""
    stream = await _Stream(server.app, server.api_key).open()
    try:
        assert not await stream.wait_ended(0.1), "stream ended before shutdown"
        srv.srv_shutdown()
        await _settle()
        await asyncio.wait_for(srv_api.machinery().shutdown_task, WAIT_S)
        assert await stream.wait_ended(), \
            "srv_shutdown() left a /notify stream open -- the lifespan cannot run"
    finally:
        await stream.close()


async def test_shutdown_ends_every_stream_not_just_one(server):
    """`close_all()`, not "the first one": two producers is the ordinary case
    and one survivor is the same deadlock as none."""
    second = await _Server(server.app, server.client).register()
    streams = [await _Stream(server.app, server.api_key).open(),
               await _Stream(server.app, second.api_key).open()]
    try:
        srv.srv_shutdown()
        await _settle()
        await asyncio.wait_for(srv_api.machinery().shutdown_task, WAIT_S)
        for stream in streams:
            assert await stream.wait_ended()
    finally:
        for stream in streams:
            await stream.close()


async def test_streams_are_ended_even_when_the_drain_itself_fails(server, monkeypatch):
    """The `close_all()` is in a `finally` and that placement is load-bearing.

    The streams are what hold the ASGI shutdown open, so they have to end
    whether or not the pool drained cleanly -- a drain that raises must not
    turn a recoverable stop into the un-killable process this whole module is
    about.
    """
    async def boom(*_a, **_kw):
        raise RuntimeError("drain exploded")

    stream = await _Stream(server.app, server.api_key).open()
    try:
        monkeypatch.setattr(srv_api.machinery().queue, "stop", boom)
        srv.srv_shutdown()
        await _settle()
        await asyncio.wait_for(srv_api.machinery().shutdown_task, WAIT_S)
        assert await stream.wait_ended(), \
            "a failed drain must still end the streams"
    finally:
        await stream.close()


# ============================================== the drain window (step 3)


async def test_the_drain_window_waits_for_a_live_stream_to_flush_its_ring(server):
    """§6.2 step 3: `shutdown_drain_s` is the grace a still-connected producer
    gets to actually *receive* its outcomes, rather than waiting them out on
    `sent_timeout_s` (§4.4). The stream here is parked mid-yield, so its ring
    never empties and the window is spent in full -- which is what makes the
    window observable at all."""
    srv_api.machinery().cfg.server.shutdown_drain_s = 0.3
    stream = await _Stream(server.app, server.api_key, hold=True).open()
    try:
        srv.send_notice(server.producer_id, 1, "ack")
        assert wait_until(lambda: srv_api.machinery().hub.pending_notices() == 1,
                          WAIT_S)
        began = time.monotonic()
        srv.srv_shutdown()
        await _settle()
        await asyncio.wait_for(srv_api.machinery().shutdown_task, WAIT_S)
        assert time.monotonic() - began >= 0.3, \
            "shutdown_drain_s was not honoured for a stream still holding notices"
    finally:
        await stream.close()


async def test_shutdown_drain_s_zero_ends_the_streams_at_once(server):
    """0 is a legal setting: forfeit whatever is buffered and go. The client's
    `sent_timeout_s` recovers it (§4.4)."""
    srv_api.machinery().cfg.server.shutdown_drain_s = 0
    stream = await _Stream(server.app, server.api_key, hold=True).open()
    try:
        srv.send_notice(server.producer_id, 1, "ack")
        assert wait_until(lambda: srv_api.machinery().hub.pending_notices() == 1,
                          WAIT_S)
        began = time.monotonic()
        srv.srv_shutdown()
        await _settle()
        await asyncio.wait_for(srv_api.machinery().shutdown_task, WAIT_S)
        assert time.monotonic() - began < 0.3
    finally:
        await stream.close()


async def test_a_ring_nobody_is_connected_to_does_not_hold_the_drain_open(server):
    """`pending_notices()` counts only producers with a live stream.

    A ring belonging to a disconnected producer cannot flush however long we
    wait, so counting it would spend the whole `shutdown_drain_s` every single
    time one had a backlog -- a fixed delay on every restart, bought for
    nothing.
    """
    srv_api.machinery().cfg.server.shutdown_drain_s = 30
    srv.send_notice(server.producer_id, 1, "ack")     # no stream open
    assert srv_api.machinery().hub.buffered(server.producer_id) == 1
    assert srv_api.machinery().hub.pending_notices() == 0

    began = time.monotonic()
    srv.srv_shutdown()
    await _settle()
    await asyncio.wait_for(srv_api.machinery().shutdown_task, WAIT_S)
    assert time.monotonic() - began < 1.0


# ================================================== idempotence and lifecycle


async def test_srv_shutdown_is_idempotent_and_the_second_call_joins_the_first(server):
    """Two drains over one machinery would have the second reaping workers the
    first is still awaiting. A repeat call must therefore be a join, not a
    second drain -- and the signal path makes repeats ordinary: an operator who
    does not see the process die presses Ctrl-C again.

    The drain is gated open so the repeats land *while it is running*, which is
    the only window in which a second one could do damage. A drain that has
    already finished is not what the guard is for.
    """
    async with held_drain():
        srv.srv_shutdown()
        await _settle()
        first = srv_api.machinery().shutdown_task
        assert first is not None and not first.done()

        for _ in range(3):
            srv.srv_shutdown()
            await _settle()
            assert srv_api.machinery().shutdown_task is first, \
                "a second srv_shutdown() started a second drain"

        # The guard is on the task-creating half too, not only on the flag: a
        # host driving shutdown itself reaches `_start_shutdown` by a route the
        # `shutting_down` early-return never covers.
        srv_api._start_shutdown(srv_api.machinery())
        assert srv_api.machinery().shutdown_task is first

    await asyncio.wait_for(first, WAIT_S)
    # And once more after it is done: still no new task, still no raise.
    srv.srv_shutdown()
    await _settle()
    assert srv_api.machinery().shutdown_task is first


async def test_srv_shutdown_before_srv_start_is_a_no_op(tmp_path):
    """No loop to drain on and nothing to end. It must not raise: the signal
    handler that calls it is installed by `srv_start()`, but a host may call it
    from its own, which can fire at any point in the boot."""
    _testing.reset_for_tests(base_dir=tmp_path)
    srv.srv_shutdown()
    assert srv_api.machinery().shutdown_task is None
    assert srv_api.machinery().shutting_down is False


async def test_srv_shutdown_after_srv_stop_is_a_no_op(tmp_path):
    _testing.reset_for_tests(base_dir=tmp_path)
    await srv.srv_start()
    await srv.srv_stop()
    srv.srv_shutdown()
    assert srv_api.machinery().shutdown_task is None


async def test_srv_stop_joins_an_in_flight_shutdown_rather_than_racing_it(tmp_path):
    """The ordinary signal path: `srv_shutdown()` starts the drain, the streams
    end, the connections finish, and only *then* does the lifespan call
    `srv_stop()` -- possibly while the drain is still running. It must wait for
    that drain instead of starting its own over the same pool."""
    _testing.reset_for_tests(base_dir=tmp_path)
    _app_for("ingest")
    await srv.srv_start()

    async with held_drain():
        srv.srv_shutdown()
        await _settle()
        task = srv_api.machinery().shutdown_task
        assert task is not None and not task.done()

        stopping = asyncio.create_task(srv.srv_stop())
        await _settle()
        assert not stopping.done(), "srv_stop() did not wait for the running drain"
    await asyncio.wait_for(stopping, WAIT_S)
    assert task.done()
    assert srv_api.machinery().shutdown_task is None


async def test_a_shutdown_whose_loop_is_already_gone_does_not_raise(tmp_path):
    """A closed loop is the tail of a real race: the signal lands while the
    process is already tearing down. Nothing to drain, nothing to end -- but
    the flag stays set, so anything still routing sees a shutting-down server.
    """
    _testing.reset_for_tests(base_dir=tmp_path)
    await srv.srv_start()
    st = srv_api.machinery()
    dead = asyncio.new_event_loop()
    dead.close()
    st.loop = dead
    try:
        srv.srv_shutdown()
        assert st.shutting_down is True
        assert st.shutdown_task is None
    finally:
        st.loop = asyncio.get_running_loop()
        await srv.srv_stop()


async def test_srv_start_after_srv_stop_clears_the_shutdown_state(tmp_path):
    """§6.3's embed-in-a-longer-lived-process case: a host may stop and start
    its lifespan again. A `shutting_down` flag left set from the previous cycle
    would make the restarted server 503 every request forever."""
    _testing.reset_for_tests(base_dir=tmp_path)
    await srv.srv_start()
    srv.srv_shutdown()
    await _settle()
    await srv.srv_stop()

    await srv.srv_start()
    try:
        assert srv_api.machinery().shutting_down is False
        assert srv_api.machinery().shutdown_task is None
    finally:
        await srv.srv_stop()


# ========================================================= signal handlers


class _Handlers:
    """Install a recording handler on SIGINT/SIGTERM and put back what was
    there. Every signal test needs the same restore, and a leaked handler here
    would corrupt every test that ran after it -- pytest's own included."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self._previous: dict[int, object] = {}

    def install(self) -> "_Handlers":
        for signum in srv_api.SHUTDOWN_SIGNALS:
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda sig, frame: self.calls.append(sig))
        return self

    def install_default(self) -> "_Handlers":
        """SIG_DFL: no handler, so no graceful shutdown is intended and there
        is no deadlock to fix."""
        for signum in srv_api.SHUTDOWN_SIGNALS:
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, signal.SIG_DFL)
        return self

    def restore(self) -> None:
        for signum, previous in self._previous.items():
            signal.signal(signum, previous)

    def current(self) -> dict[int, object]:
        return {s: signal.getsignal(s) for s in srv_api.SHUTDOWN_SIGNALS}


@pytest.fixture()
def host_handlers():
    handlers = _Handlers()
    yield handlers
    handlers.restore()


async def test_srv_start_chains_and_srv_stop_restores_the_hosts_handlers(
        tmp_path, host_handlers):
    """A library that leaves its handlers on the host's process is a bug.

    Chained rather than replaced -- uvicorn still sets `should_exit`, a host's
    own handler still runs -- and put back, by identity, at `srv_stop()`.
    """
    host_handlers.install()
    before = host_handlers.current()
    _testing.reset_for_tests(base_dir=tmp_path)

    await srv.srv_start()
    try:
        during = host_handlers.current()
        assert set(during) == set(before)
        for signum in srv_api.SHUTDOWN_SIGNALS:
            assert during[signum] is not before[signum], \
                f"srv_start() did not chain onto the host's {signum} handler"
        assert len(srv_api.machinery().signal_handlers) == len(
            srv_api.SHUTDOWN_SIGNALS)
    finally:
        await srv.srv_stop()

    assert host_handlers.current() == before, \
        "srv_stop() left this library's signal handlers on the process"
    assert srv_api.machinery().signal_handlers == []


async def test_the_chained_handler_shuts_down_and_then_calls_the_previous_one(
        tmp_path, host_handlers):
    """Order matters: end the streams *first*, then let uvicorn set
    `should_exit`. The reverse is the deadlock -- uvicorn starts draining
    connections that nothing has ended."""
    host_handlers.install()
    _testing.reset_for_tests(base_dir=tmp_path)
    await srv.srv_start()
    try:
        installed = signal.getsignal(signal.SIGTERM)
        installed(int(signal.SIGTERM), None)          # as the OS would call it
        assert srv_api.machinery().shutting_down is True
        assert host_handlers.calls == [int(signal.SIGTERM)], \
            "the host's own handler was not called"
        await _settle()
        await asyncio.wait_for(srv_api.machinery().shutdown_task, WAIT_S)
    finally:
        await srv.srv_stop()


async def test_no_handler_is_installed_over_sig_dfl(tmp_path, host_handlers):
    """With `SIG_DFL` there is no graceful shutdown to deadlock: the process
    dies on the signal. Installing there would replace "terminate" with "trip a
    flag and keep running" -- it would *create* the hang, not fix it."""
    host_handlers.install_default()
    _testing.reset_for_tests(base_dir=tmp_path)
    await srv.srv_start()
    try:
        for signum in srv_api.SHUTDOWN_SIGNALS:
            assert signal.getsignal(signum) is signal.SIG_DFL
        assert srv_api.machinery().signal_handlers == []
    finally:
        await srv.srv_stop()


async def test_shutdown_signals_false_installs_nothing(tmp_path, host_handlers):
    """The escape hatch for a host that drives its own shutdown: it calls
    `srv_shutdown()` from its handler and this library touches no signals."""
    host_handlers.install()
    before = host_handlers.current()
    cfg = MsgingConfig()
    cfg.server.shutdown_signals = False
    cfg.producer_store.file.path = str(tmp_path / "producers.json")
    _testing.reset_for_tests(config=cfg)

    await srv.srv_start()
    try:
        assert host_handlers.current() == before
        assert srv_api.machinery().signal_handlers == []
    finally:
        await srv.srv_stop()


async def test_srv_stop_leaves_a_handler_the_host_installed_after_srv_start(
        tmp_path, host_handlers):
    """Restored *by identity* only. A host that set its own handler after
    `srv_start()` meant it, and putting ours back over the top on the way out
    would leave the process with a handler nobody asked for."""
    host_handlers.install()
    _testing.reset_for_tests(base_dir=tmp_path)
    await srv.srv_start()

    def late(sig, frame):                             # the host's second thought
        pass

    signal.signal(signal.SIGTERM, late)
    await srv.srv_stop()
    assert signal.getsignal(signal.SIGTERM) is late


# =================================================== end to end, real signals


#: The server the end-to-end test kills. Deliberately §6.3's documented recipe
#: with **uvicorn's own defaults** -- no `timeout_graceful_shutdown` -- because
#: those defaults (`None` = wait forever) are what turned an open `/notify`
#: stream into a process that could not be stopped.
_SERVER_SCRIPT = textwrap.dedent('''\
    import sys
    from pathlib import Path

    import structlog
    import uvicorn

    from common.msging import srv
    from common.msging.log import configure_logging

    port, log = sys.argv[1], sys.argv[2]
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(
        file=open(log, "a", buffering=1, encoding="utf-8")))


    def handle(env):
        srv.send_notice(env.producer_id, env.msg_id, "ack")


    srv.on_event(handle)
    app = srv.make_app("ingest")
    uvicorn.run(app, host="127.0.0.1", port=int(port), log_level="critical")
''')


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


class _RealProducer:
    """A real HTTP client holding `/notify` open on a real socket.

    Not the library's own client: this test is about the *server's* ability to
    die, and a client with its own reconnect loop and its own threads would put
    two things under test at once.
    """

    def __init__(self, base: str, key: str) -> None:
        self.base = base
        self.key = key
        self.connected = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="itest-notify-holder")

    def _run(self) -> None:
        try:
            with httpx.Client(timeout=None) as client:
                with client.stream(
                        "GET", f"{self.base}/talium/ingest/v1/notify",
                        headers={"Authorization": f"Bearer {self.key}"}) as r:
                    for _ in r.iter_lines():
                        self.connected.set()
        except Exception:                              # the server went away
            pass
        finally:
            self.connected.set()

    def start(self) -> "_RealProducer":
        self.thread.start()
        assert self.connected.wait(WAIT_S), "the producer never reached /notify"
        return self


@pytest.mark.slow
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT],
                         ids=["sigterm", "sigint"])
async def test_a_real_server_with_a_connected_producer_exits_on_a_real_signal(
        tmp_path, signum):
    """**The regression, end to end.** A real uvicorn on a real port with
    uvicorn's *default* `timeout_graceful_shutdown` (None = wait forever), a
    real producer holding `/notify` open, and a real signal.

    Before `srv_shutdown()` existed this process did not exit: both signals hung
    past 60 s and needed SIGKILL, `srv_stop()` never ran, the pool never
    drained, `on_close` never fired, and the whole in-memory queue was lost. So
    the assertions are exactly those two things -- the process **dies inside a
    bound**, and it reaches `srv_stopped` on the way out rather than being
    killed with work still in it.

    A subprocess, because "the process would not exit" cannot be asserted from
    inside the process that will not exit. The body is deliberately blocking --
    the server has its own loop in its own process and this side only waits on
    it -- and `async def` only because the module runs under one asyncio mark.

    **The exit code is uvicorn's, not this library's.** After a graceful
    shutdown uvicorn restores the signal handlers it displaced and re-raises
    the signal it captured, so a SIGTERM'd process reports `-SIGTERM` while
    `uvicorn.run()` absorbs the SIGINT KeyboardInterrupt and reports 0. Both
    are clean exits; what says the shutdown was *orderly* is `srv_stopped` in
    the log, not the number.
    """
    port = _free_port()
    script = tmp_path / "shutdown_server.py"
    script.write_text(_SERVER_SCRIPT, encoding="utf-8")
    log = tmp_path / "server.jsonl"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    proc = subprocess.Popen([sys.executable, str(script), str(port), str(log)],
                            env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        assert wait_until(lambda: _port_open(port), WAIT_S), \
            f"server never came up (exit={proc.poll()})"
        base = f"http://127.0.0.1:{port}"
        r = httpx.post(f"{base}/talium/ingest/v1/register", json={}, timeout=WAIT_S)
        assert r.status_code == 200, r.text
        producer = _RealProducer(base, r.json()["api_key"]).start()

        began = time.monotonic()
        proc.send_signal(signum)
        try:
            code = proc.wait(timeout=EXIT_BOUND_S)
        except subprocess.TimeoutExpired:              # pragma: no cover
            proc.kill()
            proc.wait(timeout=WAIT_S)
            raise AssertionError(
                f"a server with a connected producer did not exit within "
                f"{EXIT_BOUND_S}s of {signum.name} -- this is the C1 deadlock")
        elapsed = time.monotonic() - began
        assert code in (0, -int(signum)), \
            f"unclean exit {code} after {signum.name}"

        events = [json.loads(line) for line in
                  log.read_text(encoding="utf-8").splitlines() if line.strip()]
        names = [e.get("event") for e in events]
        assert "srv_shutdown_begin" in names, \
            f"the signal never reached srv_shutdown() (in {elapsed:.2f}s): {names}"
        assert "srv_shutdown_complete" in names
        assert "srv_stopped" in names, \
            "the process exited without the lifespan ever draining the machinery"
        producer.thread.join(WAIT_S)
    finally:
        if proc.poll() is None:                        # pragma: no cover
            proc.kill()
            proc.wait(timeout=WAIT_S)
