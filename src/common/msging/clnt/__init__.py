"""Client half: threads + blocking httpx. Never imports srv (design §6.4).

The §6.1 client API, in the order an app uses it:

    from common.msging import clnt

    clnt.clnt_init("config/msging.yaml")
    p   = clnt.register({"host": "laptop-7", "kind": "collector"})
    pid = p["producer_id"]

    clnt.on_notify(pid, on_outcome)          # BEFORE anything is sent (§4.1)
    clnt.set_info(pid, {"agent_version": "1.4.2"})

    r = clnt.send(pid, {"session": "...whole file..."})
    if r.err_code == "slot_full":            # the only rejection to handle
        back_off()

    print(clnt.close(pid))                   # {"status": "drained", "unresolved": 0}

Importing this package pulls httpx and nothing heavier -- no FastAPI, no
SQLAlchemy. That is what lets a laptop collector install the same library the
ingest service runs.

`metrics()` here is §10.5's snapshot function. The module of the same name,
`common.msging.metrics`, is where the counters live; this is the one an app
calls.
"""
from .api import (
    PURGE,
    RETAIN,
    clnt_init,
    close,
    flush,
    metrics,
    on_notify,
    poll_notify,
    producer_status,
    re_register,
    register,
    resolve,
    send,
    set_info,
)
# Test seams. Private, re-exported so tests reach them by their real names
# rather than poking at `clnt.api` internals; see `api.py`'s "test seams".
from .api import (  # noqa: F401
    _deliver_for_tests,
    _producer_for_tests,
    _reset_for_tests,
    _sse_for_tests,
    _store_for_tests,
)

__all__ = [
    "PURGE",
    "RETAIN",
    "clnt_init",
    "close",
    "flush",
    "metrics",
    "on_notify",
    "poll_notify",
    "producer_status",
    "re_register",
    "register",
    "resolve",
    "send",
    "set_info",
]
