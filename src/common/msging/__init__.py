"""Event messaging library -- shared transport between producers and servers.

Design: docs/design/event-messaging-design.md
Decisions: docs/design/notes/event-messaging-clarifications.md

One library, both ends of the wire (design §1). Payloads are opaque to it.

    from common import msging as msg

    msg.clnt_init("config/msging.yaml")      # producer side (§6.1)
    app.include_router(msg.srv_init("ingest"))   # server side (§6.2)

**Both halves are resolved lazily, and that is load-bearing.** Importing the
server half pulls FastAPI; importing this package must not, because the same
package is installed on a laptop collector that has no web framework (§5.2,
§6.4). So `msg.send` imports `common.msging.clnt` on first touch and
`msg.srv_init` imports `common.msging.srv` on first touch, and a process that
only ever uses one half never pays for the other.

`msg.metrics` is the metrics *module*, so §10.5's snapshot is
`msg.metrics.metrics()` -- or `msg.clnt.metrics()`, the client-facing function
of the same name. It is deliberately **not** re-exported here as a function:
`__getattr__` is consulted only on an attribute *miss*, and importing
`common.msging.metrics` anywhere in the process (every `clnt` module does)
binds the submodule onto this package, so a lazily-exported `metrics` function
would resolve to the module about half the time depending on import order. One
of the two had to give, and shadowing a real submodule with a function of the
same name is the worse of the two.

`from common.msging import *` gives you only the names that are already
loaded -- the enums and `ProducerRecord`. That is not an oversight: `import *`
consults `__all__` and resolves every name in it, so advertising `send` and
`srv_init` there would import *both* halves, FastAPI included, on a collector
that has neither. Import the half you want (`msg.send`, `from common.msging
import clnt`); `__dir__` still advertises everything for completion.
"""
import importlib
from typing import Any

from .codes import Action, Code
from .models import ProducerRecord

#: §6.1's `Action` values, so `msg.PURGE` works without importing an enum out
#: of a submodule (the README's example does exactly that).
PURGE = Action.PURGE
RETAIN = Action.RETAIN

#: Public name -> the submodule that defines it. Consulted only on an
#: attribute miss, so neither submodule is imported until something in it is
#: actually asked for.
_CLIENT_NAMES = frozenset({
    "clnt_init", "register", "re_register", "producer_status", "on_notify",
    "set_info", "send", "resolve", "flush", "close", "poll_notify",
})
_SERVER_NAMES = frozenset({
    "srv_init", "srv_start", "srv_stop", "srv_shutdown", "on_event", "on_close",
    "send_notice",
    "make_app", "ENROLL_HEADER", "CONFIG_ENV",
})


def __getattr__(name: str) -> Any:
    """PEP 562 lazy resolution for the two halves and their public names."""
    if name in ("clnt", "srv"):
        return importlib.import_module(f"{__name__}.{name}")
    if name in _CLIENT_NAMES:
        return getattr(importlib.import_module(f"{__name__}.clnt"), name)
    if name in _SERVER_NAMES:
        return getattr(importlib.import_module(f"{__name__}.srv"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | _CLIENT_NAMES | _SERVER_NAMES)


#: Only the eagerly-imported names. `import *` resolves every entry here, so
#: anything lazy in this list would defeat the lazy import outright -- see the
#: module docstring. `__dir__` above is the complete surface.
__all__ = [
    "Action",
    "Code",
    "PURGE",
    "RETAIN",
    "ProducerRecord",
]
