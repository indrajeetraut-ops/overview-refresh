"""`make_app()`: the same server as `srv_hosted.py`, for a dedicated process.

Sugar over `srv_init()` + §6.3's lifespan, and nothing more -- it is not a
second path into the library. Use it when the process exists only to run this
service. The moment the host wants its own middleware, auth, CORS, `/healthz`
or lifespan, go back to `srv_hosted.py`: that one is the primitive.

Run it, from the repo root:

    venv/bin/python src/common/msging/examples/srv_make_app.py

Expect, immediately:

    srv_make_app: a dedicated process; make_app() owns the app
    serving  http://127.0.0.1:8099/talium/ingest/v1/*
    Ctrl-C to stop.

then one line per msg, once something sends. Drive it from a second terminal:

    venv/bin/python src/common/msging/examples/clnt_full.py

    [ingest] msg_id=246856 kind=good delivery=first -> ack
    [ingest] msg_id=246857 kind=poison delivery=first -> nack
    [ingest] msg_id=246858 kind=transient delivery=first -> nack
    [ingest] msg_id=246858 kind=transient delivery=again -> ack
    producer <producer_id> closed and drained

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

SERVICE = "ingest"


def build_app() -> FastAPI:
    """Three calls: handler, close hook, app. The lifespan is inside.

    Note the order -- `on_event()` before `make_app()` is §6.2's documented
    wiring, and it carries an explicit config: registering a handler does not
    build the process machinery, so `make_app(config=...)` is still the first
    call that does.
    """
    cfg = load_example_config()

    srv.on_event(handle)
    srv.on_close(producer_closed)

    return srv.make_app(SERVICE, config=cfg)


def main() -> None:
    configure_example_logging()
    say("srv_make_app: a dedicated process; make_app() owns the app")
    serve(build_app(), (SERVICE,))


if __name__ == "__main__":
    main()
