"""Server half: asyncio + FastAPI. Never imports clnt (design §6.4).

The §6.2 server API, in the order a host uses it:

    from common.msging import srv

    srv.on_event(handle)                         # app handler (§6.2 step 3)
    app.include_router(srv.srv_init("ingest"))   # talium/ingest/v1/*
    await srv.srv_start()                        # worker pool + SSE registry
    ...
    srv.send_notice(producer_id, msg_id, "ack")
    srv.srv_shutdown()                           # on SIGINT/SIGTERM (§6.3)
    await srv.srv_stop()

or `srv.make_app("ingest")` for a dedicated process (§6.3).

Importing this package pulls FastAPI. That is the server half's dependency and
not the client's -- `common.msging` itself, and `common.msging.clnt`, stay free
of it, which is what lets a producer install without a web framework.

The test seams (`reset_for_tests` and friends) are deliberately **not** here.
Each is destructive against a live server -- a new `server_epoch`, a queue with
no workers, a rewritten size limit -- and exporting them next to `srv_start`
put a silent msg black hole one autocomplete away. They live in the private
`srv._testing` module; see its docstring.
"""
from .api import (
    CONFIG_ENV,
    on_close,
    on_event,
    send_notice,
    srv_shutdown,
    srv_start,
    srv_stop,
)
from .router import ENROLL_HEADER, make_app, srv_init

__all__ = [
    "CONFIG_ENV",
    "ENROLL_HEADER",
    "make_app",
    "on_close",
    "on_event",
    "send_notice",
    "srv_init",
    "srv_shutdown",
    "srv_start",
    "srv_stop",
]
