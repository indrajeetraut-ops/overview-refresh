"""Inbound queue + worker pool (design §6.2 async-by-design, §5.2 queue_max).

`POST -> enqueue -> 202` is the whole point: the app handler must never run
inside the request handler, and a full queue must reject (503 server_busy)
rather than grow.
"""
import asyncio
import io
import json

import pytest
import structlog

from common.msging.config import ServerConfig
from common.msging.log import configure_logging
from common.msging.metrics import metrics, reset_metrics
from common.msging.models import Envelope
from common.msging.srv.queue import InboundQueue

pytestmark = pytest.mark.asyncio


def _env(n: int, payload: dict | None = None) -> Envelope:
    return Envelope(producer_id="p", msg_id=n, service_name="ingest",
                    created_at="2026-07-25T10:15:30Z", payload=payload or {})


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


async def test_offer_returns_false_when_full():
    q = InboundQueue(maxsize=2)
    assert q.offer(_env(1)) is True
    assert q.offer(_env(2)) is True
    assert q.offer(_env(3)) is False       # -> 503 server_busy


async def test_handler_runs_after_the_offer_returns():
    seen = []
    q = InboundQueue(maxsize=10)
    q.start(lambda env: seen.append(env.msg_id), workers=1)
    q.offer(_env(1))
    # The invariant (§6.2): offer() returns before the handler runs. Nothing has
    # been awaited yet, so a worker cannot have been scheduled -- anything in
    # `seen` here means the handler ran inline in the request handler.
    assert seen == []
    await asyncio.sleep(0.05)
    assert seen == [1]
    await q.stop()


async def test_async_handlers_are_supported():
    seen = []

    async def handler(env):
        seen.append(env.msg_id)

    q = InboundQueue(maxsize=10)
    q.start(handler, workers=2)
    for i in range(5):
        q.offer(_env(i))
    await asyncio.sleep(0.1)
    assert sorted(seen) == [0, 1, 2, 3, 4]
    await q.stop()


async def test_a_raising_handler_does_not_kill_the_worker():
    """One bad msg must not stop the queue draining."""
    seen = []

    def handler(env):
        if env.msg_id == 1:
            raise RuntimeError("boom")
        seen.append(env.msg_id)

    q = InboundQueue(maxsize=10)
    q.start(handler, workers=1)
    q.offer(_env(1))
    q.offer(_env(2))
    await asyncio.sleep(0.1)
    assert seen == [2]
    await q.stop()


async def test_handler_exception_message_never_reaches_a_log_line(log_lines):
    """C-1. The handler is arbitrary app code: its exception message can embed
    the payload or an api_key, and §10.4 forbids both in a log line. Log the
    exception type and where it was raised -- never `str(exc)`.
    """
    api_key = "tk_live_" + "z" * 40
    ssn = "123-45-6789"

    def handler(env):
        raise ValueError(f"bad event: api_key={api_key} ssn={ssn} "
                         f"payload={env.payload!r}")

    q = InboundQueue(maxsize=10)
    q.start(handler, workers=1)
    q.offer(_env(1, payload={"api_key": api_key, "ssn": ssn}))
    await asyncio.sleep(0.05)
    await q.stop()

    lines = log_lines()
    rendered = json.dumps(lines)
    assert api_key not in rendered
    assert ssn not in rendered
    assert "bad event" not in rendered

    errors = [ln for ln in lines if ln["event"] == "handler_error"]
    assert len(errors) == 1
    assert errors[0]["error"] == "ValueError"
    # location only: "<file>:<line>" of the raising frame
    where = errors[0]["error_at"]
    assert where.startswith("test_queue.py:") and where.split(":")[1].isdigit()
    assert "reason" not in errors[0]


async def test_depth_reflects_pending_work():
    q = InboundQueue(maxsize=10)
    q.offer(_env(1))
    assert q.depth() == 1


async def test_stop_drains_work_queued_before_it():
    """stop() finishes accepted work before cancelling the workers."""
    seen = []

    async def handler(env):
        await asyncio.sleep(0.01)
        seen.append(env.msg_id)

    q = InboundQueue(maxsize=10)
    q.start(handler, workers=1)
    for i in range(4):
        q.offer(_env(i))
    await q.stop()
    assert sorted(seen) == [0, 1, 2, 3]
    assert q.depth() == 0


async def test_offer_after_stop_is_rejected_not_accepted(log_lines):
    """I-2. A stopped queue that answers True black-holes the msg: the route
    replies 202, the client marks it SENT, and no outcome ever arrives -- only
    the slow `sent_timeout_s` backstop (§4.4) recovers it. Reject instead, so
    the route answers 503 server_busy (§8.1) and the client retries.
    """
    q = InboundQueue(maxsize=10)
    q.start(lambda env: None, workers=1)
    await q.stop()

    assert q.offer(_env(1)) is False
    assert q.depth() == 0
    rejects = [ln for ln in log_lines() if ln["event"] == "queue_closed"]
    assert rejects and rejects[0]["code"] == "server_busy"


async def test_start_after_stop_reopens_the_queue():
    """I-2 must not break restart: stop() then start() accepts again."""
    seen = []
    q = InboundQueue(maxsize=10)
    q.start(lambda env: seen.append(env.msg_id), workers=1)
    await q.stop()

    q.start(lambda env: seen.append(env.msg_id), workers=1)
    assert q.offer(_env(2)) is True
    await asyncio.sleep(0.05)
    assert seen == [2]
    await q.stop()


async def test_accepting_a_msg_records_the_metrics_contract():
    """I-3. §10.5 names these; the route must not count them a second time."""
    q = InboundQueue(maxsize=10)
    q.offer(_env(1))
    q.offer(_env(2))
    snap = metrics()
    assert snap["msgs_accepted_total"]["{}"] == 2
    assert snap["queue_depth"]["{}"] == 2


async def test_a_full_queue_records_the_metrics_contract():
    q = InboundQueue(maxsize=1)
    q.offer(_env(1))
    q.offer(_env(2))
    snap = metrics()
    assert snap["queue_full_total"]["{}"] == 1
    assert snap["msgs_accepted_total"]["{}"] == 1


async def test_queue_depth_gauge_falls_as_work_is_processed():
    q = InboundQueue(maxsize=10)
    q.start(lambda env: None, workers=1)
    q.offer(_env(1))
    await asyncio.sleep(0.05)
    await q.stop()
    assert metrics()["queue_depth"]["{}"] == 0


async def test_lifecycle_log_events_carry_the_required_fields(log_lines):
    """I-3. §10.4 is a contract, not decoration: msg_accepted and queue_full
    are what an operator greps when 503s go out."""
    q = InboundQueue(maxsize=1)
    q.start(lambda env: None, workers=1)
    q.offer(_env(1))
    q.offer(_env(2))          # rejected: maxsize=1 and no worker has run yet
    await asyncio.sleep(0.05)
    await q.stop()

    lines = log_lines()
    by_event = {ln["event"]: ln for ln in lines}
    assert set(by_event) >= {"queue_started", "msg_accepted", "queue_full",
                             "queue_stopped"}
    assert all(ln["module"] == "msging.srv.queue" for ln in lines)

    accepted = by_event["msg_accepted"]
    assert accepted["producer_id"] == "p"
    assert accepted["msg_id"] == 1
    assert accepted["service_name"] == "ingest"
    assert accepted["queue_depth"] == 1

    full = by_event["queue_full"]
    assert full["level"] == "warning"          # loud by design (§10.4)
    assert full["code"] == "server_busy"
    assert full["queue_depth"] == 1
    assert full["msg_id"] == 2

    assert by_event["queue_started"]["workers"] == 1
    assert by_event["queue_stopped"]["queue_depth"] == 0


async def test_task_done_is_called_so_join_completes():
    """I-3. A worker that never calls task_done() leaves join() hanging, so
    stop() can only ever end in the drain timeout. Assert join() directly."""
    q = InboundQueue(maxsize=10)
    q.start(lambda env: None, workers=1)
    for i in range(3):
        q.offer(_env(i))
    await asyncio.wait_for(q._q.join(), timeout=1.0)
    await q.stop()


async def test_stop_reaps_every_worker_task():
    """I-3. stop() must leave no task behind, or a restarted queue runs two
    pools against one queue."""
    q = InboundQueue(maxsize=10)
    q.start(lambda env: None, workers=3)
    workers = list(q._workers)
    assert len(workers) == 3

    await q.stop()
    assert all(t.done() for t in workers)
    assert q._workers == []


async def test_double_start_is_rejected():
    """I-3. Two pools on one queue silently double the concurrency."""
    q = InboundQueue(maxsize=10)
    q.start(lambda env: None, workers=1)
    with pytest.raises(RuntimeError):
        q.start(lambda env: None, workers=1)
    await q.stop()


async def test_zero_workers_is_rejected():
    """I-3. workers=0 accepts msgs forever and processes none."""
    q = InboundQueue(maxsize=10)
    with pytest.raises(ValueError):
        q.start(lambda env: None, workers=0)


async def test_drain_timeout_drops_undrained_work_and_zeroes_the_gauge(log_lines):
    """M-2. Work still queued when the drain deadline passes is dropped with
    the workers (the producer resends, §4.4). Leaving it in the queue pins the
    queue_depth gauge non-zero forever and holds the abandoned Envelopes."""
    async def handler(env):
        await asyncio.sleep(30)

    q = InboundQueue(maxsize=10)
    q.start(handler, workers=1)
    for i in range(3):
        q.offer(_env(i))
    await asyncio.sleep(0.01)          # let the worker pick up msg 0
    await q.stop(timeout_s=0.05)

    assert q.depth() == 0
    assert metrics()["queue_depth"]["{}"] == 0
    timeouts = [ln for ln in log_lines() if ln["event"] == "queue_drain_timeout"]
    assert timeouts and timeouts[0]["dropped"] == 2
    rendered = json.dumps(log_lines())
    assert "payload" not in rendered


async def test_cancelling_stop_mid_drain_still_reaps_the_workers():
    """M-3. A shutdown whose lifespan is itself cancelled must not leak the
    worker pool."""
    async def handler(env):
        await asyncio.sleep(30)

    q = InboundQueue(maxsize=10)
    q.start(handler, workers=2)
    workers = list(q._workers)
    q.offer(_env(1))

    stopping = asyncio.create_task(q.stop(timeout_s=30))
    await asyncio.sleep(0.02)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    await asyncio.sleep(0.02)

    assert all(t.done() for t in workers)


async def test_from_config_uses_queue_max():
    q = InboundQueue.from_config(ServerConfig(queue_max=1))
    assert q.offer(_env(1)) is True
    assert q.offer(_env(2)) is False


async def test_from_config_uses_the_configured_worker_count():
    """§5.2 `workers`: concurrent handler tasks."""
    q = InboundQueue.from_config(ServerConfig(queue_max=10, workers=3))
    q.start(lambda env: None)
    assert len(q._workers) == 3
    await q.stop()
