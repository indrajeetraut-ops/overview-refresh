"""`Window` bounds SENT (design §5.1). It is touched from both the send path
and the cleaner loop, so its thread-safety is part of the contract, not an
implementation detail -- hence the two concurrency tests below."""
import threading

import pytest

from common.msging.clnt.window import Window


def test_acquire_fails_when_full():
    w = Window(2)
    assert w.acquire() and w.acquire()
    assert w.acquire() is False


def test_release_frees_a_slot():
    w = Window(1)
    w.acquire()
    w.release()
    assert w.acquire() is True


def test_release_below_zero_is_ignored():
    """Demotion and ACK can both release; double-release must not create slots."""
    w = Window(1)
    w.release()
    w.release()
    assert w.in_flight() == 0
    assert w.acquire() is True
    assert w.acquire() is False


def test_in_flight_tracks_acquire_and_release():
    w = Window(3)
    assert w.in_flight() == 0
    w.acquire()
    w.acquire()
    assert w.in_flight() == 2
    w.release()
    assert w.in_flight() == 1


def test_acquire_returns_false_rather_than_raising_when_full():
    """§5.1: a full window never fails a send -- it throttles. The caller maps
    False to stored_throttled, so acquire() must never raise."""
    w = Window(1)
    w.acquire()
    for _ in range(5):
        assert w.acquire() is False
    assert w.in_flight() == 1


def test_zero_maxsize_is_rejected():
    """A window of 0 would throttle every msg forever -- nothing, not even the
    cleaner loop, could ever ship. Config error, so it raises (§8.3)."""
    with pytest.raises(ValueError):
        Window(0)
    with pytest.raises(ValueError):
        Window(-1)


def test_acquire_blocks_while_the_window_lock_is_held():
    """Deterministic proof that the check-and-increment is actually inside a
    lock: hold the window's lock and acquire() must not complete.

    Unlocked (or lock-free) implementations finish immediately and fail here;
    an implementation with no `_lock` at all fails on the attribute.
    """
    w = Window(4)
    lock = w._lock                      # noqa: SLF001 -- the lock IS the contract
    done = threading.Event()

    def worker():
        w.acquire()
        done.set()

    with lock:
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert done.wait(0.25) is False, "acquire() ran while the lock was held"
    assert done.wait(2.0) is True
    t.join(2.0)
    assert w.in_flight() == 1


def test_release_blocks_while_the_window_lock_is_held():
    """`release()` is the half that leaks. §5.1 has the SSE reader and the
    cleaner loop both releasing the same msg, so its read-modify-write needs
    the same lock `acquire()` uses -- a lost decrement leaks a slot for the
    life of the process.

    Stripping `with self._lock:` from `release()` passes every other test in
    this file; it fails here, because an unlocked release completes while the
    lock is held.
    """
    w = Window(4)
    w.acquire()
    lock = w._lock                      # noqa: SLF001 -- the lock IS the contract
    done = threading.Event()

    def worker():
        w.release()
        done.set()

    with lock:
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert done.wait(0.25) is False, "release() ran while the lock was held"
    assert done.wait(2.0) is True
    t.join(2.0)
    assert w.in_flight() == 0


def test_in_flight_blocks_while_the_window_lock_is_held():
    """`in_flight()` reads the same word the other two write. Unlocked, it can
    observe a torn value on a platform without an atomic int read, and it also
    stops proving that the counter is only ever touched under the lock."""
    w = Window(4)
    lock = w._lock                      # noqa: SLF001
    done = threading.Event()

    def worker():
        w.in_flight()
        done.set()

    with lock:
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert done.wait(0.25) is False, "in_flight() ran while the lock was held"
    assert done.wait(2.0) is True
    t.join(2.0)


def test_concurrent_release_never_loses_a_decrement():
    """Fill the window, then have many threads release exactly `maxsize` slots
    between them. A release that is not atomic loses decrements under
    contention and the window comes back short -- permanently."""
    maxsize = 64
    w = Window(maxsize)
    for _ in range(maxsize):
        assert w.acquire() is True

    threads = 16
    per_thread = maxsize // threads
    barrier = threading.Barrier(threads)

    def worker():
        barrier.wait()
        for _ in range(per_thread):
            w.release()

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10.0)

    assert w.in_flight() == 0
    # And every slot really is free again.
    assert all(w.acquire() for _ in range(maxsize))
    assert w.acquire() is False


def test_concurrent_acquire_never_over_grants():
    """32 threads storming a window of 8 must hand out exactly 8 slots.

    A bare `if count < max: count += 1` loses increments under contention and
    over-grants; the barrier maximises the odds of hitting that interleaving.
    """
    maxsize = 8
    threads = 32
    w = Window(maxsize)
    barrier = threading.Barrier(threads)
    granted = []
    granted_lock = threading.Lock()

    def worker():
        barrier.wait()
        for _ in range(50):
            if w.acquire():
                with granted_lock:
                    granted.append(1)

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10.0)

    assert len(granted) == maxsize
    assert w.in_flight() == maxsize


def test_concurrent_acquire_release_churn_settles_at_zero():
    """Balanced acquire/release churn from many threads must settle exactly at
    zero. A lost decrement leaks a slot permanently -- the window would shrink
    every restart until nothing ships (§5.1)."""
    w = Window(16)
    threads = 16
    barrier = threading.Barrier(threads)

    def worker():
        barrier.wait()
        for _ in range(500):
            if w.acquire():
                w.release()

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10.0)

    assert w.in_flight() == 0
