"""What the three server examples have in common: the app, not the wiring.

The wiring is the point of `srv_hosted.py`, `srv_make_app.py` and
`srv_two.py`, and it is different in each of them. The handler they run, the
config they load and the uvicorn call that serves them are not, so they live
here once and each example file stays short enough to read in one sitting.

`clnt_full.py` deliberately does **not** import this module: importing it
pulls `common.msging.srv`, and with it FastAPI. The client half never needs a
web framework (design §6.4), and an example that quietly imported one would
teach the opposite.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import structlog
import uvicorn

from common.msging import srv
from common.msging.config import MsgingConfig, load_config
from common.msging.log import configure_logging

#: The examples' own config. The shipped `config/msging.yaml` points at
#: `https://ingest.prod:8443`, so it cannot serve a local demo.
CONFIG_PATH = Path(__file__).with_name("msging.example.yaml")

#: One port for every server example: they take turns, which is §6.3's
#: "one server per process" convention in practice.
PORT = int(os.environ.get("TALIUM_EXAMPLE_PORT", "8099"))


def say(*parts: object) -> None:
    """The narrative, on stdout, unbuffered. Library logs go to stderr."""
    print(*parts, flush=True)


def configure_example_logging() -> None:
    """JSON logs on **stderr**, so the narrative on stdout stays readable.

    `configure_logging()` is the entry point's job, never the library's (see
    `common.msging.log`). Run an example with `2>/dev/null` for the narrative
    alone, or `1>/dev/null` for the logs alone.
    """
    configure_logging(json=True,
                      logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def load_example_config() -> MsgingConfig:
    return load_config(CONFIG_PATH)


#: (producer_id, msg_id) pairs this process has already been handed once.
#: Lets `handle` answer a redelivery differently from a first delivery, which
#: is what makes `resolve(RETAIN)` observable from the client side.
_seen: set[tuple[str, int]] = set()


def handle(env) -> None:
    """The app handler (§6.2 step 3). Runs on a worker, never in the request.

    One msg in, one `send_notice` out -- that is the whole contract. This one
    answers by payload `kind`, so `clnt_full.py` can drive every §8.1 path it
    has to teach:

        good       -> ack
        poison     -> nack, and it will never be accepted
        transient  -> nack the first time, ack the redelivery

    The `info` bag is where a machine-readable fact belongs. `reason` is
    human-only and **code must never branch on it** (§8), so the retryability
    of a NACK rides in `info`, not in the sentence next to it.
    """
    key = (env.producer_id, env.msg_id)
    first_delivery = key not in _seen
    _seen.add(key)
    kind = env.payload.get("kind", "good")

    if kind == "poison":
        code, reason, info = "nack", "record is unparseable", {"retryable": False}
    elif kind == "transient" and first_delivery:
        code, reason, info = "nack", "downstream is unavailable", {"retryable": True}
    else:
        code, reason, info = "ack", "", {"row_id": env.msg_id}

    say(f"  [{env.service_name}] msg_id={env.msg_id} kind={kind}"
        f" delivery={'first' if first_delivery else 'again'} -> {code}")
    srv.send_notice(env.producer_id, env.msg_id, code, reason=reason, info=info)


def producer_closed(producer_id: str) -> None:
    """The optional `on_close` hook (§6.2 step 4): the client drained and said
    goodbye. Informational -- the server holds no durable state (§2.4)."""
    say(f"  producer {producer_id} closed and drained")


def serve(app, service_names: tuple[str, ...], port: int = PORT) -> None:
    """Run `app` until Ctrl-C, then let the lifespan drain the machinery.

    **Uvicorn's defaults, deliberately.** This used to pass
    `timeout_graceful_shutdown=2`, because `/notify` is an endless stream
    (§4.1) and ASGI runs lifespan *shutdown* only after connections drain -- so
    `srv_stop()`'s `hub.close_all()`, the thing that would end that stream,
    could not run until uvicorn had already given up waiting for it. Without a
    bound, Ctrl-C with a producer connected hung forever.

    `srv_start()` now chains `srv_shutdown()` onto SIGINT/SIGTERM, and that
    ends the streams *before* the drain, so the deadlock is gone. The timeout
    is therefore no longer needed -- and it is not kept "just in case", because
    it is exactly what hid the defect from these examples' own verification:
    with it set, a server that could not shut down looked like a server that
    took two seconds to. An example must run the path a deployment runs
    (§6.3), so this one does.
    """
    for name in service_names:
        say(f"serving  http://127.0.0.1:{port}/talium/{name}/v1/*")
    say("Ctrl-C to stop.")
    # `uvicorn.run`, not `uvicorn.Server(...).run()`: only the former absorbs
    # the KeyboardInterrupt uvicorn re-raises after a graceful shutdown, and an
    # example that answers Ctrl-C with a traceback teaches the wrong thing.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    say("stopped.")
