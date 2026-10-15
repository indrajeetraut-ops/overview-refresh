"""Two named services, side by side, in one process (§6.3).

"One server per process" is a **convention, not a limit**: a second service is
`include_router` twice, and it already works. What is genuinely per-process is
everything underneath -- one inbound queue, one worker pool, one `NotifyHub`,
one `server_epoch`, one producer store. So this is two SERVICES sharing one
process's machinery, never two servers: services in one process boot together,
die together, and share `queue_max`.

Run it, from the repo root:

    venv/bin/python src/common/msging/examples/srv_two.py

Expect, immediately:

    srv_two: two services, one process, one queue and one server_epoch
    serving  http://127.0.0.1:8099/talium/ingest/v1/*
    serving  http://127.0.0.1:8099/talium/audit/v1/*
    Ctrl-C to stop.

Drive each one from a second terminal -- the alias picks the service:

    venv/bin/python src/common/msging/examples/clnt_full.py ingest_local
    venv/bin/python src/common/msging/examples/clnt_full.py audit_local

    [audit] msg_id=351398 kind=good delivery=first -> ack
    [audit] msg_id=351400 kind=poison delivery=first -> nack
    [audit] msg_id=351402 kind=transient delivery=first -> nack
    [audit] msg_id=351402 kind=transient delivery=again -> ack
    producer 61dced2506e24ac8a568a8d5ee09c3db closed and drained
    [ingest] msg_id=247861 kind=good delivery=first -> ack
    [ingest] msg_id=247863 kind=poison delivery=first -> nack
    ...
    producer 565468bf327345c4bf12573d3f955844 closed and drained

Two aliases = two bindings = two producer_ids and two durable stores, and each
outcome goes back over its own binding's SSE channel.

Ctrl-C ends with `stopped.` and exit 0, with a producer connected or without;
see `srv_hosted.py` for what makes that work.

Same port as the other two server examples, so run one at a time.
Config is `msging.example.yaml`, next to this file. Nothing to edit.
Library logs are JSON on stderr; add `2>/dev/null` for the narrative alone.
"""
# Run as a script there is no package root on sys.path yet, so put `src/`
# there before importing anything from `common`.
if __package__ in (None, ""):                                    # noqa: E402
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from contextlib import asynccontextmanager                       # noqa: E402

from fastapi import FastAPI                                      # noqa: E402

from common.msging import srv                                    # noqa: E402
from common.msging.examples._shared import (                     # noqa: E402
    configure_example_logging,
    handle,
    load_example_config,
    producer_closed,
    say,
    serve,
)

SERVICES = ("ingest", "audit")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One lifespan for the process, not one per service: `srv_start()` and
    `srv_stop()` run **once per process** (§6.2), whatever is mounted on it."""
    await srv.srv_start()
    try:
        yield
    finally:
        await srv.srv_stop()


def build_app() -> FastAPI:
    """One handler, two routers, one process.

    `handle` is shared here only because both services do the same thing in
    this example. `on_event` is per process, so an app that needs to treat
    them differently branches on `env.service_name` inside one handler -- there
    is no second handler slot to register.
    """
    cfg = load_example_config()

    srv.on_event(handle)
    srv.on_close(producer_closed)

    app = FastAPI(lifespan=lifespan)
    for i, service in enumerate(SERVICES):
        # `config=` on the first srv_init() only: the machinery is
        # process-wide, so passing a second one raises rather than silently
        # applying to nothing (§6.2).
        app.include_router(srv.srv_init(service, config=cfg if i == 0 else None))
    return app


def main() -> None:
    configure_example_logging()
    say("srv_two: two services, one process, one queue and one server_epoch")
    serve(build_app(), SERVICES)


if __name__ == "__main__":
    main()
