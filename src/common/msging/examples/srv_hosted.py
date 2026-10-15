"""§6.3's primary wiring: the HOST owns the app, msging is one router on it.

Run it, from the repo root:

    venv/bin/python src/common/msging/examples/srv_hosted.py

Expect, immediately:

    srv_hosted: the host owns the FastAPI app; msging is one router on it
    host route: http://127.0.0.1:8099/healthz
    serving  http://127.0.0.1:8099/talium/ingest/v1/*
    Ctrl-C to stop.

then one line per msg, once something sends. Drive it from a second terminal:

    venv/bin/python src/common/msging/examples/clnt_full.py

    [ingest] msg_id=246847 kind=good delivery=first -> ack
    [ingest] msg_id=246849 kind=poison delivery=first -> nack
    [ingest] msg_id=246850 kind=poison delivery=first -> nack
    [ingest] msg_id=246851 kind=transient delivery=first -> nack
    [ingest] msg_id=246852 kind=transient delivery=first -> nack
    [ingest] msg_id=246851 kind=transient delivery=again -> ack
    [ingest] msg_id=246852 kind=transient delivery=again -> ack
    producer <producer_id> closed and drained

(msg_ids are per producer and a fresh store starts from a random base, §3.1,
so the numbers differ every time -- only their shape is stable.)

Ctrl-C shuts down through the lifespan: worker pool drained, SSE channels
closed, `stopped.`, exit 0, no traceback -- with a producer still connected or
without one, and on uvicorn's own defaults. `srv_start()` chains
`srv_shutdown()` onto the signal, which ends the endless `/notify` streams
before uvicorn starts draining connections; see `_shared.serve`.

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
    PORT,
    configure_example_logging,
    handle,
    load_example_config,
    producer_closed,
    say,
    serve,
)

SERVICE = "ingest"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """§6.2 step 6. The host's lifespan, with msging booted inside it: the
    library never owns the process."""
    await srv.srv_start()          # worker pool up, server_epoch minted (§4.3)
    try:
        yield
    finally:
        await srv.srv_stop()       # workers drained, SSE channels closed


def healthz() -> dict:
    """A host route next to the library's. That is the point of §6.3."""
    return {"status": "ok"}


def build_app() -> FastAPI:
    """§6.3's four lines, in the order §6.2 documents.

    A real host writes this at module scope, so `uvicorn srv_hosted:app` finds
    it. Here it is a function, so the example can also be imported -- by the
    test that keeps it honest -- without booting a server on import.
    """
    cfg = load_example_config()

    srv.on_event(handle)                    # step 3: the app handler
    srv.on_close(producer_closed)           # step 4: optional

    app = FastAPI(lifespan=lifespan)
    # Step 1: the lib returns a router, the host includes it. `config=` is
    # accepted on the FIRST srv_init() of a process only -- the machinery is
    # process-wide (§6.2).
    app.include_router(srv.srv_init(SERVICE, config=cfg))
    app.get("/healthz")(healthz)            # the host's own routes, untouched
    return app


def main() -> None:
    configure_example_logging()
    say("srv_hosted: the host owns the FastAPI app; msging is one router on it")
    app = build_app()
    say(f"host route: http://127.0.0.1:{PORT}/healthz")
    serve(app, (SERVICE,))


if __name__ == "__main__":
    main()
