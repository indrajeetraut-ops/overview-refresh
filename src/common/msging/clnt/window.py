"""The in-flight window: a bound on `SENT` awaiting an outcome (design §5.1).

`max_in_flight` is not `max_pending`, and the difference is the whole point of
this class:

  - `max_pending` bounds the *durable store*. It protects local disk during an
    outage, and it is the only limit that ever rejects a `send()` (`slot_full`,
    msg not stored).
  - `max_in_flight` bounds what is *on the wire*. It protects the server and
    throttles the thundering herd after a reconnect (§4.3). A full window
    **never fails a send**: the msg is stored and returns `stored_throttled`,
    and it ships when a slot frees.

So `acquire()` returns a bool the caller maps to a code -- it never blocks,
never waits, and never raises. "Full" is a normal steady state, not an error.

**Release is the half that leaks.** §5.1 is explicit that a slot is released
on any terminal notification *and* on a `SENT -> PENDING` demotion. Those are
two different threads (the SSE reader and the cleaner loop) reaching the same
msg, and they can both decide it is finished: an ACK can land for a msg the
cleaner has just demoted on `sent_timeout_s`. That double-release is expected
traffic, not a bug in the caller, which is why `release()` clamps at zero
instead of asserting. Without the clamp the counter drifts negative and the
window silently stops bounding anything.

Thread-safety is part of the contract, not an implementation detail: the send
path (any producer thread, §6.4), the SSE reader and the cleaner loop all
touch one `Window`. The check-and-increment in `acquire()` is a read-modify-
write and must be atomic, or two threads racing a nearly-full window both see
a free slot and the server gets more in flight than it was promised.

§10.5's `in_flight_count` gauge is deliberately not emitted here: a process
with two server aliases has two Windows, and an unlabelled gauge written from
both would report whichever wrote last. The owner knows the `server_alias`;
this class is a pure counter. `clnt/cleaner.py` publishes it (labelled) at the
end of every sweep, which is the one layer that sees the number on a schedule.
"""
from __future__ import annotations

import threading


class Window:
    """A counting semaphore that reports fullness instead of waiting on it.

    Args:
        maxsize: the most msgs allowed in flight at once (`max_in_flight`,
            §5.1). Must be >= 1.

    Raises:
        ValueError: `maxsize` < 1. A window of zero would throttle every msg
            forever -- not even the cleaner loop could ship one -- so it is a
            config error and raises rather than wedging the client silently
            (§8.3: programming/config errors raise, runtime conditions return
            a code).
    """

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError(
                f"max_in_flight must be >= 1, got {maxsize}: a window of "
                "zero throttles every msg forever and nothing would ever ship"
            )
        self._maxsize = int(maxsize)
        self._in_flight = 0
        self._lock = threading.Lock()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def acquire(self) -> bool:
        """Take a slot. `True` if one was free, `False` if the window is full.

        Never blocks and never raises: a `False` is the caller's cue to store
        the msg and return `stored_throttled` (§5.1), not to fail the send.
        """
        with self._lock:
            if self._in_flight >= self._maxsize:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        """Give a slot back. Idempotent below zero -- see the module docstring
        on why ACK and demotion can both release the same msg."""
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1

    def in_flight(self) -> int:
        """Slots currently taken. A snapshot -- it can be stale the moment it
        returns, so use it for logging/metrics, never as a check-then-act."""
        with self._lock:
            return self._in_flight
