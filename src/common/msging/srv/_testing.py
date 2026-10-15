"""Test-only seams into the process machinery. **Not public API.**

Underscore-named module, absent from `srv.__all__` and never re-exported: these
three functions force conditions the public API cannot reach without racing the
worker pool, and every one of them is destructive if it is called against a
live server.

    reset_for_tests           mints a NEW server_epoch -- every reconnecting
                              client sweeps sent/ back to pending/ and resends
    set_queue_max_for_tests   installs a queue with no workers; whatever it
                              accepts is answered 202 and never dispatched
    set_max_msg_bytes_for_tests   rewrites a live server's §5.2 size limit

They used to sit in `srv.__all__` alongside `srv_start` and `send_notice`,
which made a silent msg black hole one plausible autocomplete away. The seams
are still needed, so they moved here rather than being deleted; what changed is
that reaching them now requires importing a private module, and each one either
refuses to run against a started process or hands back a restore so the module
is left as it was found.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..config import MsgingConfig
from . import api
from .queue import InboundQueue


def reset_for_tests(base_dir: str | Path | None = None,
                    config: MsgingConfig | None = None) -> None:
    """Throw away the process machinery and build a fresh one.

    `base_dir` points the file producer store somewhere disposable; `config`
    supplies a whole config instead.

    Refuses while a worker pool is running. It cannot stop one -- it is sync --
    and dropping the reference would leave that pool running against machinery
    nothing routes to any more, a fresh `server_epoch` handed to every client
    that reconnects, and a `send_notice()` publishing into a hub with no
    subscribers. `srv_stop()` first.

    Raises:
        RuntimeError: `srv_start()` has been called and `srv_stop()` has not.
        ConfigError: the registration stanza is unusable (§10.1).
    """
    if api._state is not None and api._state.loop is not None:
        raise RuntimeError(
            "reset_for_tests() while the worker pool is running: it cannot "
            "stop one (it is sync), and dropping the machinery would orphan "
            "the pool and mint a new server_epoch. await srv_stop() first."
        )
    if config is None:
        config = MsgingConfig()
        if base_dir is not None:
            config.producer_store.file.path = str(Path(base_dir) / "producers.json")
    # `_build`, not `_Machinery` directly: it is the one construction site that
    # adopts a handler registered before the machinery existed, and -- just as
    # important here -- clears that slot, so nothing a previous test parked can
    # leak into the next one's machinery.
    api._state = api._build(config)


def set_max_msg_bytes_for_tests(limit: int) -> Callable[[], None]:
    """Shrink the §5.2 size limit so the 413 path is reachable without moving
    32 MiB through the transport.

    Returns a callable that puts the previous value back.
    """
    cfg = api.machinery().cfg.server
    previous = cfg.max_msg_bytes
    cfg.max_msg_bytes = limit

    def restore() -> None:
        cfg.max_msg_bytes = previous

    return restore


def set_queue_max_for_tests(queue_max: int) -> Callable[[], None]:
    """Replace the process queue with a small, **unstarted** one.

    Two things make this the hook rather than a config knob:

    - `ServerConfig.queue_max` is `gt=0`, so a config carrying 0 is rejected at
      load; and `asyncio.Queue(maxsize=0)` means *unbounded*, so 0 would be the
      opposite of full anyway. The cap therefore has to be applied to the queue
      object, and it has to be >= 1.
    - A started pool drains what you offer it, so "full" would be a race. The
      replacement has no workers, which makes `queue_max` offers fill it and
      offer number `queue_max + 1` return False, deterministically.

    **The replacement is a black hole by construction:** the `queue_max` offers
    it does accept are answered `202` and never dispatched, and the client that
    sent them waits out `sent_timeout_s`. That is the mechanism, not a bug --
    and it is exactly why this must never be reachable from `srv`.

    The queue it displaces is parked in `retired` so `srv_stop()` still reaps
    the workers running on it. Returns a callable that reinstates it and
    discards the replacement, leaving the machinery as it was found.
    """
    if queue_max < 1:
        raise ValueError(
            "set_queue_max_for_tests() needs queue_max >= 1: asyncio.Queue "
            "reads maxsize=0 as unbounded, which never reports full"
        )
    st = api.machinery()
    displaced = st.queue
    st.retired.append(displaced)
    st.queue = InboundQueue(maxsize=queue_max, workers=st.cfg.server.workers)

    def restore() -> None:
        st.queue = displaced
        if displaced in st.retired:
            st.retired.remove(displaced)

    return restore
