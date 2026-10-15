"""The whole client lifecycle, against a live server, with the failures in it.

    clnt_init -> register -> set_info -> on_notify -> send -> resolve -> close

and, because an app that only ever sees `ok` learns nothing: `too_large`,
`slot_full`, and a NACK resolved both ways -- PURGE for a msg that will never
be accepted, RETAIN for one that failed for a reason that has passed.

Run it, from the repo root, with a server example already running:

    venv/bin/python src/common/msging/examples/srv_hosted.py     # terminal 1
    venv/bin/python src/common/msging/examples/clnt_full.py      # terminal 2

Against `srv_two.py`, the alias picks which service to talk to:

    venv/bin/python src/common/msging/examples/clnt_full.py audit_local

Expect, abridged (a real run, `2>/dev/null`):

    [2] register
        producer_id  565468bf327345c4bf12573d3f955844
        server_alias ingest_local -> http://127.0.0.1:8099 (service ingest)
        max_msg_bytes 65536 -- the SERVER's limit, handed back by /register ...
        api_key      withheld: never printed, never logged (design §10.4)
    [5] send one good msg
        send(good) -> ok  msg_id=246847  # in flight; the outcome arrives ...
        <- ack msg_id=246847 info={'row_id': 246847}
        store is empty: the ack purged the record (§2.2)
    [6] send one msg over the server's max_msg_bytes
        send(oversized) -> too_large  msg_id=None  # NOT stored: split it ...
    [7] fill the durable store with NACKs (max_pending=4)
        send(poison) -> ok  msg_id=246849
        <- nack msg_id=246849 info={'retryable': False} reason='record is ...'
        ... four in all ...
        pending/sent/nacked = (0, 0, 4)
    [8] one more send, with the store full
        send(good) -> slot_full  msg_id=None  # NOT stored: back off or drop
    [9] resolve every NACK: PURGE what is poison, RETAIN what was transient
        resolve(msg_id=246849, purge) # info.retryable=False
        resolve(msg_id=246851, retain) # info.retryable=True
        <- ack msg_id=246851 info={'row_id': 246851}
        pending/sent/nacked = (0, 0, 0)
    [10] close: stop accepting sends, flush, drain (§2.4)
        close -> status=drained unresolved=0
        send() after close() -> ProducerClosingError (§8.3)
    metrics (§10.5):
        msgs_acked_total = 3
        msgs_nacked_total = 4
        msgs_resolved_total{"action":"purge"} = 2
        msgs_resolved_total{"action":"retain"} = 2
        slot_full_total = 1
        ...

Two things about that output are not cosmetic:

  - **msg_ids are per producer, and a fresh store starts from a random base**
    (§3.1), so the numbers differ every run. They also continue across runs:
    the store is durable and `register()` reattaches the stored identity.
  - **A `<- nack` line can appear before the `send(...) -> ok` above it.** The
    server answers one msg twice -- the 202 and the SSE outcome -- and nothing
    sequences those two (§2.2, §4.1). Over loopback the outcome wins often.

Config is `msging.example.yaml`, next to this file. Nothing to edit.
Library logs are JSON on stderr; add `2>/dev/null` for the narrative alone.
"""
# Run as a script there is no package root on sys.path yet, so put `src/`
# there before importing anything from `common`.
if __package__ in (None, ""):                                    # noqa: E402
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import socket                                                    # noqa: E402
import sys                                                       # noqa: E402
import time                                                      # noqa: E402
from pathlib import Path                                         # noqa: E402

import structlog                                                 # noqa: E402

# Note what is NOT imported: no FastAPI, no `common.msging.srv`. The client
# half is deliberately free of the server's dependencies (§6.4).
from common.msging import PURGE, RETAIN, clnt                     # noqa: E402
from common.msging.log import configure_logging                   # noqa: E402

CONFIG_PATH = Path(__file__).with_name("msging.example.yaml")

#: §7's producer descriptor: free-form, stored by the server, carried for
#: troubleshooting. A `producer_desc` string in it rides on every envelope.
DESCRIPTOR = {
    "host": socket.gethostname(),
    "kind": "collector",
    "producer_desc": "msging example client",
}

#: §8.1, in full. An app branches on `err_code` and never on `reason`.
WHAT_TO_DO = {
    "ok": "in flight; the outcome arrives over SSE",
    "already_processed": "purged inline; terminal",
    "stored_timeout": "durable; the cleaner loop retries",
    "stored_unreachable": "durable; the cleaner loop retries",
    "stored_throttled": "durable; ships when a window slot frees",
    "server_busy": "durable; the loop honors Retry-After",
    "slot_full": "NOT stored: back off or drop",
    "too_large": "NOT stored: split it at the producer",
    "unauthorized": "halted: re_register()",
    "rejected": "NACKED: fix and resend, or purge",
}

WAIT_S = 20.0

#: Every NACK the SSE reader has handed us, appended from the reader thread.
nacks: list = []


def say(*parts: object) -> None:
    print(*parts, flush=True)


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}", flush=True)


def wait_until(predicate, timeout_s: float = WAIT_S) -> bool:
    """Poll a real condition to a deadline. Never a bare sleep -- the point is
    to observe the library getting there, not to guess how long it takes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def on_outcome(msg_id: int, ev) -> None:
    """§6.1's `notify_callback_fn`, and it runs on the SSE reader thread.

    Thread-safe and quick (§6.4): a slow callback stalls every notification
    behind it. This one appends and prints. The PURGE/RETAIN decision is left
    to the main thread below -- `resolve()` is the app's call, and deferring it
    is legal: no `resolve()` at all means RETAIN (§2.2 step 5).
    """
    if ev.code == "nack":
        nacks.append(ev)
    say(f"    <- {ev.code} msg_id={msg_id} info={ev.info}"
        + (f" reason={ev.reason!r}" if ev.reason else ""))


def report(what: str, result) -> None:
    say(f"    send({what}) -> {result.err_code}  msg_id={result.msg_id}"
        f"  # {WHAT_TO_DO.get(result.err_code, 'unknown code')}")


def counts(pid: str) -> tuple[int, int, int]:
    st = clnt.producer_status(pid)
    return st["pending"], st["sent"], st["nacked"]


def main(argv: list[str]) -> int:
    # An entry point configures logging, never the library (§10.4). Stderr, so
    # the narrative below owns stdout.
    configure_logging(json=True,
                      logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    alias = argv[1] if len(argv) > 1 else None      # None -> default_server

    step(1, f"clnt_init: {CONFIG_PATH.name}")
    clnt.clnt_init(CONFIG_PATH)                     # once per process (§6.1)

    step(2, "register")
    producer = clnt.register(DESCRIPTOR, alias)
    pid = producer["producer_id"]
    say(f"    producer_id  {pid}")
    say(f"    server_alias {producer['server_alias']} -> "
        f"{producer['base_path']} (service {producer['service_name']})")
    say(f"    max_msg_bytes {producer['max_msg_bytes']} -- the SERVER's limit, "
        "handed back by /register so the two configs cannot drift")
    # producer["api_key"] exists. It is never printed and never logged (§10.4).
    say("    api_key      withheld: never printed, never logged (design §10.4)")

    step(3, "on_notify: required before the SSE channel opens (§4.1)")
    clnt.on_notify(pid, on_outcome, notify_on_ack=True)
    say("    notify_on_ack=True, so this example can show ACKs too; the "
        "default is False")

    step(4, "set_info: the producer-level bag (§7)")
    clnt.set_info(pid, {"agent_version": "1.4.2", "host": DESCRIPTOR["host"]})

    step(5, "send one good msg")
    result = clnt.send(pid, {"kind": "good", "rows": 3}, {"batch": "b-9"})
    report("good", result)
    if result.err_code == "unauthorized" or clnt.producer_status(pid)["unauthorized"]:
        # §8.1. The library will not do this behind your back: a new
        # producer_id owns a new store directory, so it would orphan this
        # binding's backlog. Usually means the server's producer store was
        # wiped while this client's identity survived.
        say("    the server refused this api_key; re_register() mints a new "
            "identity (§8.1)")
        pid = clnt.re_register(pid, DESCRIPTOR)["producer_id"]
        clnt.on_notify(pid, on_outcome, notify_on_ack=True)
        clnt.set_info(pid, {"agent_version": "1.4.2", "host": DESCRIPTOR["host"]})
        result = clnt.send(pid, {"kind": "good", "rows": 3})
        report("good", result)
    if not wait_until(lambda: counts(pid) == (0, 0, 0)):
        say(f"    !! the store never settled: {counts(pid)}")
    say("    store is empty: the ack purged the record (§2.2)")

    step(6, "send one msg over the server's max_msg_bytes")
    oversized = clnt.send(pid, {"kind": "good", "blob": "x" * 100_000})
    report("oversized", oversized)
    say("    checked client-side, before storing: it never went on the wire")

    step(7, "fill the durable store with NACKs (max_pending=4)")
    say("    an unresolved NACK still occupies a slot -- it is waiting on YOUR "
        "resolve()")
    for kind in ("poison", "poison", "transient", "transient"):
        report(kind, clnt.send(pid, {"kind": kind}))
    if not wait_until(lambda: counts(pid)[2] == 4):
        say(f"    !! expected 4 NACKs, got {counts(pid)}")
    say(f"    pending/sent/nacked = {counts(pid)}")

    step(8, "one more send, with the store full")
    rejected = clnt.send(pid, {"kind": "good"})
    report("good", rejected)
    if rejected.err_code == "slot_full":
        # §8.1's one piece of backpressure every producer must handle. The msg
        # was NOT stored, so the caller -- not the library -- still holds it.
        say("    the caller still holds this event: back off and retry, or "
            "drop it. Nothing was stored, so nothing will be resent.")

    step(9, "resolve every NACK: PURGE what is poison, RETAIN what was transient")
    for ev in list(nacks):
        # `info`, never `reason`: the reason is human-only text and code must
        # never branch on it (§8). Machine-readable facts ride in `info`.
        retryable = bool(ev.info.get("retryable"))
        action = RETAIN if retryable else PURGE
        say(f"    resolve(msg_id={ev.msg_id}, {action}) "
            f"# info.retryable={retryable}")
        clnt.resolve(pid, ev.msg_id, action)
    say("    RETAIN puts the msg back in pending/; the cleaner loop resends it")
    if not wait_until(lambda: counts(pid) == (0, 0, 0)):
        say(f"    !! the store never settled: {counts(pid)}")
    say(f"    pending/sent/nacked = {counts(pid)}")

    step(10, "close: stop accepting sends, flush, drain (§2.4)")
    closed = clnt.close(pid)
    say(f"    close -> status={closed.status} unresolved={closed.unresolved}")
    try:
        clnt.send(pid, {"kind": "good"})
    except Exception as exc:                                     # noqa: BLE001
        say(f"    send() after close() -> {type(exc).__name__} (§8.3)")

    say("\nmetrics (§10.5):")
    for name, series in sorted(clnt.metrics().items()):
        for labels, value in sorted(series.items()):
            # `{}` is the no-label key -- see metrics._label_key.
            say(f"    {name}{'' if labels == '{}' else labels} = {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
