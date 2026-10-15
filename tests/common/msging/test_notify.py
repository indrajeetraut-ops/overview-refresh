"""SSE notify hub (design §4.1 open, §4.2 keepalive, §4.3 server_epoch,
§6.2 notify_buffer/notify_ttl_s, §8.2 notify_ev, §10.4/§10.5 logs+metrics).

The hub is the only thing standing between an outcome and a client that would
otherwise sit in SENT forever, so the tests below pin behaviour, not shape:
the epoch must actually be constant per process, the heartbeat must actually
keep coming, expiry and overflow must actually drop (and be counted), and a
subscriber that stops reading must not be able to wedge the server.
"""
import asyncio
import io
import json
import threading

import pytest
import structlog

from common.msging.config import ServerConfig
from common.msging.log import configure_logging
from common.msging.metrics import metrics, reset_metrics
from common.msging.models import NotifyEv
from common.msging.srv.notify import NotifyHub

pytestmark = pytest.mark.asyncio


def _ev(n: int, code: str = "ack", producer_id: str = "p") -> NotifyEv:
    return NotifyEv(msg_id=n, producer_id=producer_id, code=code,
                    ts="2026-07-25T10:15:31Z")


def _series(name: str, **labels) -> float:
    """One metric series' current value, 0 when it was never touched."""
    key = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    return metrics().get(name, {}).get(key, 0)


def _data(frame: str) -> dict:
    """The JSON body of an SSE data frame."""
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"no data line in frame: {frame!r}")


async def _next(stream, timeout: float = 1.0) -> str:
    return await asyncio.wait_for(anext(stream), timeout=timeout)


@pytest.fixture(autouse=True)
def _clean_metrics():
    """Metrics live in a process-global store; a test that asserts on them must
    not inherit another test's counts (§10.5)."""
    reset_metrics()
    yield
    reset_metrics()


@pytest.fixture()
def log_lines():
    """Rendered JSON log lines (§10.4), as the support bundle would carry them."""
    stream = io.StringIO()
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=stream))
    yield lambda: [json.loads(ln) for ln in stream.getvalue().splitlines() if ln.strip()]
    structlog.reset_defaults()


# --------------------------------------------------------------- §4.3 epoch

async def test_first_frame_carries_the_epoch():
    hub = NotifyHub(ServerConfig())
    stream = hub.subscribe("p")
    first = await _next(stream)
    assert hub.epoch in first
    await stream.aclose()


async def test_epoch_frame_is_an_sse_epoch_event():
    """§4.3: the boot id is *an event*, first on the stream -- not a comment
    and not smuggled into the first notification."""
    hub = NotifyHub(ServerConfig())
    stream = hub.subscribe("p")
    first = await _next(stream)
    assert first.startswith("event: epoch\n")
    assert first.endswith("\n\n")
    assert _data(first) == {"server_epoch": hub.epoch}
    await stream.aclose()


async def test_every_stream_from_one_hub_carries_the_same_epoch():
    """§4.3: one boot id *per process*. Two producers connecting to the same
    running server must not be told the server restarted between them."""
    hub = NotifyHub(ServerConfig())
    a, b = hub.subscribe("a"), hub.subscribe("b")
    first_a, first_b = await _next(a), await _next(b)
    assert _data(first_a)["server_epoch"] == _data(first_b)["server_epoch"]
    await a.aclose()
    await b.aclose()


async def test_epoch_does_not_change_across_a_reconnect():
    """§4.3: a changed epoch makes the client sweep all of sent/ back to
    pending/. A reconnect that has not crossed a restart must therefore report
    the same epoch, or every dropped link triggers a full resend storm."""
    hub = NotifyHub(ServerConfig())
    first = hub.subscribe("p")
    before = _data(await _next(first))["server_epoch"]
    await first.aclose()                      # link drops

    again = hub.subscribe("p")
    after = _data(await _next(again))["server_epoch"]
    await again.aclose()

    assert after == before == hub.epoch


async def test_two_hubs_have_different_epochs():
    """A restart must be detectable -- that is the whole point (§4.3)."""
    assert NotifyHub(ServerConfig()).epoch != NotifyHub(ServerConfig()).epoch


# ------------------------------------------------------- §8.2 live delivery

async def test_published_event_reaches_a_live_subscriber():
    hub = NotifyHub(ServerConfig())
    stream = hub.subscribe("p")
    await _next(stream)                       # epoch frame
    hub.publish(_ev(42))
    frame = await _next(stream)
    assert '"msg_id":42' in frame.replace(" ", "")
    await stream.aclose()


async def test_notify_frame_carries_the_whole_notify_ev():
    """§8.2: code is the only outcome field, and info/reason ride along."""
    hub = NotifyHub(ServerConfig())
    stream = hub.subscribe("p")
    await _next(stream)
    hub.publish(NotifyEv(msg_id=7, producer_id="p", code="nack",
                         reason="bad row", info={"row": 3},
                         ts="2026-07-25T10:15:31Z"))
    frame = await _next(stream)
    assert frame.startswith("event: notify\n")
    body = _data(frame)
    assert body["msg_id"] == 7
    assert body["producer_id"] == "p"
    assert body["code"] == "nack"
    assert body["reason"] == "bad row"
    assert body["info"] == {"row": 3}
    assert body["ts"] == "2026-07-25T10:15:31Z"
    await stream.aclose()


async def test_notices_are_delivered_to_the_right_producer():
    hub = NotifyHub(ServerConfig())
    a, b = hub.subscribe("a"), hub.subscribe("b")
    await _next(a)
    await _next(b)
    hub.publish(_ev(1, producer_id="a"))
    frame = await _next(a)
    assert _data(frame)["producer_id"] == "a"
    assert hub.buffered("b") == 0
    await a.aclose()
    await b.aclose()


# -------------------------------------------------- §6.2 ring while SSE down

async def test_events_buffer_while_nobody_is_subscribed():
    hub = NotifyHub(ServerConfig(notify_buffer=10))
    hub.publish(_ev(1))
    hub.publish(_ev(2))
    assert hub.buffered("p") == 2


async def test_buffered_events_flush_on_reconnect():
    hub = NotifyHub(ServerConfig(notify_buffer=10))
    hub.publish(_ev(7))
    stream = hub.subscribe("p")
    await _next(stream)                       # epoch frame
    frame = await _next(stream)
    assert '"msg_id":7' in frame.replace(" ", "")
    await stream.aclose()


async def test_buffered_events_flush_in_order():
    hub = NotifyHub(ServerConfig(notify_buffer=10))
    for i in range(3):
        hub.publish(_ev(i))
    stream = hub.subscribe("p")
    await _next(stream)
    got = [_data(await _next(stream))["msg_id"] for _ in range(3)]
    assert got == [0, 1, 2]
    await stream.aclose()


async def test_flushed_events_leave_the_ring_empty():
    hub = NotifyHub(ServerConfig(notify_buffer=10, heartbeat_s=0.02))
    hub.publish(_ev(7))
    stream = hub.subscribe("p")
    await _next(stream)                       # epoch frame
    await _next(stream)                       # notify frame
    await _next(stream)                       # heartbeat: the notice was taken
    assert hub.buffered("p") == 0
    await stream.aclose()


async def test_buffer_overflow_drops_oldest_and_does_not_grow():
    hub = NotifyHub(ServerConfig(notify_buffer=3))
    for i in range(10):
        hub.publish(_ev(i))
    assert hub.buffered("p") == 3


async def test_buffer_overflow_keeps_the_newest():
    """Drop-oldest is this module's choice; §6.2 only says overflow drops."""
    hub = NotifyHub(ServerConfig(notify_buffer=3))
    for i in range(10):
        hub.publish(_ev(i))
    stream = hub.subscribe("p")
    await _next(stream)
    got = [_data(await _next(stream))["msg_id"] for _ in range(3)]
    assert got == [7, 8, 9]
    await stream.aclose()


async def test_buffer_overflow_counts_the_drops():
    hub = NotifyHub(ServerConfig(notify_buffer=3))
    for i in range(10):
        hub.publish(_ev(i))
    assert _series("notices_dropped_total", reason="overflow") == 7


async def test_zero_sized_buffer_drops_immediately():
    """notify_buffer=0 is 'hold nothing'; it must drop, never grow."""
    hub = NotifyHub(ServerConfig(notify_buffer=0))
    hub.publish(_ev(1))
    assert hub.buffered("p") == 0
    assert _series("notices_dropped_total", reason="overflow") == 1


# ------------------------------------------------------ §6.2 notify_ttl_s

async def test_expired_events_are_not_delivered():
    hub = NotifyHub(ServerConfig(notify_ttl_s=0.01, heartbeat_s=0.05))
    hub.publish(_ev(9))
    await asyncio.sleep(0.05)
    stream = hub.subscribe("p")
    await _next(stream)                       # epoch frame
    frame = await _next(stream)
    # Nothing but the keepalive: the notice aged out of the ring (§6.2), and
    # the client's sent_timeout_s is what recovers it (§4.4).
    assert frame.startswith(":")
    await stream.aclose()


async def test_expired_events_leave_the_ring():
    # >=100ms of headroom on purpose: at notify_ttl_s=0.01 a >10ms stall in a
    # throttled CI container expires the notice before the first assert.
    hub = NotifyHub(ServerConfig(notify_ttl_s=0.2))
    hub.publish(_ev(9))
    assert hub.buffered("p") == 1
    await asyncio.sleep(0.3)
    assert hub.buffered("p") == 0


async def test_expiry_counts_as_a_drop():
    hub = NotifyHub(ServerConfig(notify_ttl_s=0.01))
    hub.publish(_ev(9))
    hub.publish(_ev(10))
    await asyncio.sleep(0.05)
    hub.buffered("p")
    assert _series("notices_dropped_total", reason="expired") == 2


async def test_fresh_events_survive_an_expiry_pass():
    """Expiry must take the aged head only, not the whole ring."""
    # The live window has to cover a subscribe plus two anext()s, so keep it
    # well over 100ms rather than the 50ms this used to run on.
    hub = NotifyHub(ServerConfig(notify_ttl_s=0.3))
    hub.publish(_ev(1))
    await asyncio.sleep(0.4)
    hub.publish(_ev(2))
    assert hub.buffered("p") == 1
    stream = hub.subscribe("p")
    await _next(stream)
    assert _data(await _next(stream))["msg_id"] == 2
    await stream.aclose()


# --------------------------------------------------------- §4.2 keepalive

async def test_heartbeat_is_a_comment_frame():
    hub = NotifyHub(ServerConfig(heartbeat_s=0.05))
    stream = hub.subscribe("p")
    await _next(stream)                       # epoch frame
    frame = await _next(stream)
    assert frame.startswith(":")              # SSE comment, ignored by parsers
    await stream.aclose()


async def test_heartbeat_keeps_coming():
    """§4.2: the client's idle_timeout_s fires on *silence*, so one heartbeat
    and then nothing is the same as a dead link."""
    hub = NotifyHub(ServerConfig(heartbeat_s=0.02))
    stream = hub.subscribe("p")
    await _next(stream)
    beats = [await _next(stream) for _ in range(3)]
    assert all(b.startswith(":") for b in beats)
    await stream.aclose()


async def test_a_notice_arriving_mid_wait_interrupts_the_heartbeat():
    """The stream must not sit out the full heartbeat_s before delivering.

    Driven by a *background consumer*, which is the only shape that exercises
    the wakeup. An async generator only advances while its consumer is inside
    `__anext__`; a test that publishes between two `anext()` calls leaves the
    generator parked at a `yield`, so the notice is simply found by the next
    drain and `st.wake.set()` is never what delivers it. `StreamingResponse`
    runs `async for` in a task, so the generator really does sit in
    `wait_for(st.wake.wait(), heartbeat_s)` -- delete `st.wake.set()` from
    `publish()` and this test is what catches it.
    """
    hub = NotifyHub(ServerConfig(heartbeat_s=30.0))
    stream = hub.subscribe("p")
    frames: list[str] = []
    opened = asyncio.Event()

    async def consume():
        async for frame in stream:
            frames.append(frame)
            opened.set()

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(opened.wait(), 1.0)     # epoch frame handed over
        # Let the consumer re-enter __anext__ and park in wake.wait().
        await asyncio.sleep(0.05)
        assert len(frames) == 1

        hub.publish(_ev(5))
        for _ in range(100):                           # up to ~1s, << heartbeat_s
            if len(frames) > 1:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert len(frames) > 1, "publish() did not wake a stream parked in wake.wait()"
    assert _data(frames[1])["msg_id"] == 5


async def test_a_notice_is_kept_when_the_reader_is_cancelled_mid_yield():
    """Delivery is only real once the consumer has taken the frame.

    Starlette cancels the response task on client disconnect, which lands on
    the generator while it is suspended at the `yield` of a notify frame. If
    the notice were popped, counted and logged *before* that yield, the
    disconnect would erase it: gone from the ring, absent from
    `notices_dropped_total`, and never redelivered -- a second, uncounted loss
    site. §4.4/§9.1 make a redundant resend explicitly safe, so the notice must
    stay in the ring until the yield resumes.
    """
    hub = NotifyHub(ServerConfig(notify_buffer=10, heartbeat_s=30.0))
    stream = hub.subscribe("p")
    await _next(stream)                        # epoch frame
    hub.publish(_ev(11))
    frame = await _next(stream)                # notice handed out; generator parked
    assert _data(frame)["msg_id"] == 11
    await stream.aclose()                      # client disconnects at that instant

    again = hub.subscribe("p")
    await _next(again)                         # epoch frame
    assert _data(await _next(again))["msg_id"] == 11
    await again.aclose()


async def test_a_notice_taken_by_the_reader_leaves_the_ring():
    """The other half of the deliver-then-remove rule: once the consumer has
    come back for the next frame, the notice must not be resent forever."""
    hub = NotifyHub(ServerConfig(notify_buffer=10, heartbeat_s=0.02))
    stream = hub.subscribe("p")
    await _next(stream)
    hub.publish(_ev(11))
    await _next(stream)                        # notify frame
    await _next(stream)                        # heartbeat: the notice was taken
    assert hub.buffered("p") == 0
    await stream.aclose()


async def test_publish_from_another_thread_raises():
    """§6.4: hub methods are loop-thread only. Cross-thread today does not
    raise -- `Event.set()` -> `Future.set_result` -> `loop.call_soon` from a
    foreign thread skips `_write_to_self()`, so the wakeup is merely delayed by
    up to heartbeat_s with no error at all. An app that follows queue.py's own
    advice ("hand the work to its own executor") and then calls send_notice()
    from that thread deserves a loud failure, not a silent stall."""
    hub = NotifyHub(ServerConfig())
    caught: list[BaseException] = []

    def worker():
        try:
            hub.publish(_ev(1))
        except BaseException as exc:           # noqa: BLE001
            caught.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    await asyncio.to_thread(t.join)

    assert caught, "publish() from a foreign thread silently did nothing"
    assert isinstance(caught[0], RuntimeError)
    assert "call_soon_threadsafe" in str(caught[0])


# -------------------------------------------------------------- drop()

async def test_drop_discards_the_buffer():
    hub = NotifyHub(ServerConfig(notify_buffer=10))
    hub.publish(_ev(1))
    hub.publish(_ev(2))
    hub.drop("p")
    assert hub.buffered("p") == 0


async def test_drop_counts_the_discarded_notices():
    hub = NotifyHub(ServerConfig(notify_buffer=10))
    hub.publish(_ev(1))
    hub.publish(_ev(2))
    hub.drop("p")
    assert _series("notices_dropped_total", reason="dropped") == 2


async def test_dropped_notices_are_not_delivered_to_a_later_stream():
    hub = NotifyHub(ServerConfig(notify_buffer=10, heartbeat_s=0.05))
    hub.publish(_ev(1))
    hub.drop("p")
    stream = hub.subscribe("p")
    await _next(stream)                       # epoch frame
    assert (await _next(stream)).startswith(":")
    await stream.aclose()


async def test_drop_ends_a_live_stream():
    hub = NotifyHub(ServerConfig(heartbeat_s=30.0))
    stream = hub.subscribe("p")
    await _next(stream)
    hub.drop("p")
    with pytest.raises(StopAsyncIteration):
        await _next(stream)


async def test_drop_of_an_unknown_producer_is_a_no_op():
    hub = NotifyHub(ServerConfig())
    hub.drop("never-seen")                    # must not raise
    assert hub.buffered("never-seen") == 0


async def test_reconnect_supersedes_the_previous_stream():
    """A half-open TCP link (§4.2) leaves a zombie generator behind. The new
    stream owns the ring; the zombie must end, not split the notices."""
    hub = NotifyHub(ServerConfig(heartbeat_s=30.0))
    stale = hub.subscribe("p")
    await _next(stale)
    fresh = hub.subscribe("p")
    await _next(fresh)

    hub.publish(_ev(3))
    assert _data(await _next(fresh))["msg_id"] == 3
    with pytest.raises(StopAsyncIteration):
        await _next(stale)
    await fresh.aclose()


async def test_supersession_follows_connect_order_not_iteration_order():
    """"Last connect wins" (§4.1) must mean connect, not first-iteration.

    An async generator's body does not run until the first `__anext__`, so a
    `stream_id` claimed inside the body makes the winner whichever stream the
    server happened to iterate first. Under StreamingResponse that is usually
    the same order; for any route that treats subscribe() as registration it is
    not, and the newer stream can supersede *itself*.
    """
    hub = NotifyHub(ServerConfig(heartbeat_s=30.0))
    first = hub.subscribe("p")                # connects
    second = hub.subscribe("p")               # connects, must win
    await _next(second)                       # ...but is iterated first

    hub.publish(_ev(1))
    assert _data(await _next(second))["msg_id"] == 1
    with pytest.raises(StopAsyncIteration):
        await _next(first)                    # superseded before it ever ran
    await second.aclose()


# ------------------------------------------------- backpressure / liveness

async def test_a_stalled_subscriber_does_not_block_publish():
    """publish() is the server's hot path (a worker finishing a msg). A
    subscriber that stopped reading must cost it nothing and must not grow the
    ring past notify_buffer."""
    hub = NotifyHub(ServerConfig(notify_buffer=5, heartbeat_s=30.0))
    stalled = hub.subscribe("p")
    await _next(stalled)                      # connected, then never reads again

    loop = asyncio.get_running_loop()
    started = loop.time()
    for i in range(2000):
        hub.publish(_ev(i))
    assert loop.time() - started < 1.0
    assert hub.buffered("p") == 5
    await stalled.aclose()


async def test_a_stalled_subscriber_does_not_wedge_other_producers():
    hub = NotifyHub(ServerConfig(notify_buffer=5, heartbeat_s=30.0))
    stalled = hub.subscribe("p")
    await _next(stalled)
    for i in range(50):
        hub.publish(_ev(i))

    other = hub.subscribe("q")
    await _next(other)
    hub.publish(_ev(1, producer_id="q"))
    assert _data(await _next(other))["msg_id"] == 1
    await other.aclose()
    await stalled.aclose()


async def test_publishing_for_producers_that_never_subscribe_does_not_leak():
    """§6.2 bounds each ring, but nothing bounds the number of producer_ids a
    server sees. Idle, empty rings must be reclaimed."""
    hub = NotifyHub(ServerConfig(notify_buffer=5, notify_ttl_s=0.01))
    for i in range(500):
        hub.publish(_ev(1, producer_id=f"p{i}"))
    await asyncio.sleep(0.05)
    hub.publish(_ev(1, producer_id="live"))   # any publish sweeps the idle state
    assert hub.tracked() == 1


async def test_subscribing_producers_that_never_publish_do_not_leak():
    """§10.1's v1 default is `registration: open`: anyone can mint a
    producer_id, open /notify and disconnect. If only publish() sweeps, that
    connect/disconnect traffic grows `tracked()` without bound."""
    hub = NotifyHub(ServerConfig(notify_buffer=5, notify_ttl_s=0.05,
                                 heartbeat_s=30.0))
    for i in range(20):
        stream = hub.subscribe(f"p{i}")
        await _next(stream)                   # epoch frame, then gone
        await stream.aclose()
    await asyncio.sleep(0.15)

    last = hub.subscribe("last")              # the subscribe path must sweep too
    await _next(last)
    await last.aclose()
    assert hub.tracked() == 1


async def test_a_closed_stream_releases_its_producer_for_sweeping():
    """The `st.subscribers -= 1` release in the finally: without it every
    producer that ever connected is pinned against the sweep forever."""
    hub = NotifyHub(ServerConfig(notify_buffer=5, notify_ttl_s=0.05,
                                 heartbeat_s=30.0))
    stream = hub.subscribe("gone")
    await _next(stream)
    await stream.aclose()
    await asyncio.sleep(0.15)

    hub.publish(_ev(1, producer_id="other"))  # triggers a sweep
    assert hub.tracked() == 1                 # "other" only; "gone" reclaimed
    assert hub.buffered("gone") == 0


async def test_a_live_producer_is_never_swept():
    hub = NotifyHub(ServerConfig(notify_buffer=5, notify_ttl_s=0.01,
                                 heartbeat_s=30.0))
    stream = hub.subscribe("keep")
    await _next(stream)
    await asyncio.sleep(0.05)
    hub.publish(_ev(1, producer_id="other"))  # triggers the sweep
    hub.publish(_ev(2, producer_id="keep"))
    assert _data(await _next(stream))["msg_id"] == 2
    await stream.aclose()


# ---------------------------------------------------- §10.4 / §10.5 telemetry

async def test_delivered_notices_are_counted_by_code():
    """Counted once the consumer has taken the frame, so the trailing read is
    load-bearing: a notice still suspended at its yield is not yet delivered."""
    hub = NotifyHub(ServerConfig(heartbeat_s=0.02))
    stream = hub.subscribe("p")
    await _next(stream)
    hub.publish(_ev(1, code="ack"))
    await _next(stream)
    hub.publish(_ev(2, code="nack"))
    await _next(stream)
    await _next(stream)                       # heartbeat: both frames taken
    assert _series("notices_sent_total", code="ack") == 1
    assert _series("notices_sent_total", code="nack") == 1
    await stream.aclose()


async def test_open_streams_are_gauged():
    hub = NotifyHub(ServerConfig(heartbeat_s=30.0))
    a, b = hub.subscribe("a"), hub.subscribe("b")
    await _next(a)
    await _next(b)
    assert _series("sse_connections") == 2
    await a.aclose()
    assert _series("sse_connections") == 1
    await b.aclose()
    assert _series("sse_connections") == 0


async def test_notice_sent_is_logged(log_lines):
    hub = NotifyHub(ServerConfig(heartbeat_s=0.02), service_name="ingest")
    stream = hub.subscribe("p")
    await _next(stream)
    hub.publish(_ev(42, code="ack"))
    await _next(stream)
    await _next(stream)                       # heartbeat: the frame was taken
    await stream.aclose()

    sent = [ln for ln in log_lines() if ln.get("event") == "notice_sent"]
    assert len(sent) == 1
    assert sent[0]["producer_id"] == "p"
    assert sent[0]["msg_id"] == 42
    assert sent[0]["code"] == "ack"


async def test_log_lines_carry_the_service_name(log_lines):
    """§10.4's server field list is normative and names `service_name`; without
    it a support bundle from a two-service process cannot be split per service."""
    hub = NotifyHub(ServerConfig(notify_buffer=1, heartbeat_s=30.0),
                    service_name="ingest")
    stream = hub.subscribe("p")
    await _next(stream)
    hub.publish(_ev(1))
    hub.publish(_ev(2))                       # evicts msg_id 1 -> notice_dropped
    await _next(stream)
    await stream.aclose()

    seen = {ln["event"]: ln for ln in log_lines()}
    for event in ("sse_opened", "sse_closed", "notice_dropped"):
        assert seen[event]["service_name"] == "ingest", event


async def test_notice_dropped_is_logged_loudly(log_lines):
    hub = NotifyHub(ServerConfig(notify_buffer=1))
    hub.publish(_ev(1))
    hub.publish(_ev(2))                       # evicts msg_id 1

    dropped = [ln for ln in log_lines() if ln.get("event") == "notice_dropped"]
    assert len(dropped) == 1
    assert dropped[0]["producer_id"] == "p"
    assert dropped[0]["msg_id"] == 1
    assert dropped[0]["reason"] == "overflow"
    assert dropped[0]["level"] == "warning"   # loud, per §10.4


async def test_stream_lifecycle_is_logged_with_the_epoch(log_lines):
    hub = NotifyHub(ServerConfig(heartbeat_s=30.0))
    stream = hub.subscribe("p")
    await _next(stream)
    await stream.aclose()

    events = {ln["event"]: ln for ln in log_lines()}
    assert events["sse_opened"]["server_epoch"] == hub.epoch
    assert events["sse_opened"]["producer_id"] == "p"
    assert events["sse_closed"]["producer_id"] == "p"


async def test_logs_never_carry_the_notice_info_bag(log_lines):
    """§10.4 is normative: never log the payload. `info` is app-authored and
    routinely echoes the very data the payload rule protects."""
    hub = NotifyHub(ServerConfig(heartbeat_s=0.02))
    stream = hub.subscribe("p")
    await _next(stream)
    hub.publish(NotifyEv(msg_id=1, producer_id="p", code="nack",
                         reason="secret-reason", info={"leak": "s3cret"},
                         ts="2026-07-25T10:15:31Z"))
    await _next(stream)
    await _next(stream)                       # heartbeat: notice_sent is logged
    await stream.aclose()

    rendered = json.dumps(log_lines())
    assert "notice_sent" in rendered          # the line under test really ran
    assert "s3cret" not in rendered
    assert "secret-reason" not in rendered
