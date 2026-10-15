"""msg_id block allocator (design §3.1).

`msg_id` is the durable correlation key for at-least-once delivery: an INT,
monotonic per producer, assigned by this lib. Gaps are explicitly fine --
that is precisely what makes block reservation legal.

Fsync-per-send is too much IO. Instead we reserve a block of ids at a time:
persist a high-water mark that is always already past everything handed out,
then hand out ids from memory until the block is exhausted.

Crash invariant: the value on disk must always be >= every id already handed
out, so a crash can only burn the remainder of a block (a gap), never reuse
an id. Reuse is far worse than a gap -- the server dedupes on
`(producer_id, msg_id)`, so a reused id makes a genuinely new msg look like a
duplicate and gets silently dropped. This is why the reservation write
happens *before* any id from the new block is handed out, not after.

The counter file goes down through `durable.write_atomic` -- the same
`write tmp -> fsync tmp -> rename -> fsync dir` sequence D30 mandates for a
msg record -- and for the same reason. Fsyncing an in-place `O_TRUNC` write
is durable but not *atomic*: a crash or an ENOSPC between the truncate and
the write publishes a torn `msg_id.json` under its real name. Since the file
is rewritten on every block boundary and again on `close()`, that window is
routine, not exotic. Cost: the sequence fsyncs twice (file, then directory)
where the in-place write fsynced once, so a block boundary costs two fsyncs
instead of one. D29's budget is *per block, not per msg*, and two fsyncs per
`msg_id_block` (default 1000) msgs is still nothing next to §3.1's mandatory
two fsyncs per msg record.

**A counter we cannot read is treated as lost, not as fatal.** The obvious
alternative -- let the parse error out of `__init__` -- keeps the crash
invariant (nothing is issued, so nothing is reissued) and trades it for an
availability failure that is strictly worse: `register()` raises a bare
`JSONDecodeError`, which is in neither §8.3's list for that call
(`ConfigError`, `RegistrationError`) nor anything an app can branch on, the
producer never starts, and every durable msg in its store sits there until an
operator deletes the counter by hand. Deleting it by hand lands on exactly
the recovery below anyway. So an unreadable counter logs loudly (§10.4) and
falls back, and the producer comes up and ships its backlog.

Two facts make that fallback safe rather than a licence to reuse ids:

1. A fresh (or lost) store starts from a random base, not 1. If `state/` is
   lost but `identity.json` survives, the producer keeps its `producer_id`
   while the counter resets -- a fixed base would replay ids the server
   already saw.
2. The base is then raised past the highest msg_id still on disk
   (`store.highest_record_id`). A random base alone is *not* good enough
   here: the store this counter belongs to may still hold records, and
   `_RANDOM_BASE_HI` is only 10**6, so a random base landing at or below a
   live record's id is an ordinary event, not a negligible one -- and it
   would hand out an id a durable record already wears, i.e. the one thing
   §3.1 forbids. The floor makes that collision impossible by construction,
   for the cost of one directory scan at startup.

The floor is a *floor*, not the source of the counter: §3.1 explicitly
rejects deriving the counter by scanning for max msg_id, because a store that
has ACKed and purged everything is empty and would restart the counter low.
That rejection is about the normal path, which still reads the file; here the
scan only ever raises the random base, never lowers it, and when the store is
empty it does nothing and the random base stands -- which is precisely §3.1's
answer for a lost store.

Residual, and accepted: ids already ACKed and purged leave no trace on disk,
so the floor cannot bound against those. That is the lost-store case §3.1
already accepts, and its answer is the random base.
"""
from __future__ import annotations

import json
import random
import threading
from pathlib import Path

from common.msging.durable import write_atomic
from common.msging.clnt.store import highest_record_id
from common.msging.log import get_logger

_RANDOM_BASE_LO = 1
_RANDOM_BASE_HI = 10**6

_logger = get_logger(__name__)


class MsgIdAllocator:
    """Hands out monotonic `msg_id`s for one producer, block at a time.

    Thread-safe: `send()` may be called from several producer threads
    (design §6.4), so both the in-memory counters and the on-disk write are
    guarded by a single lock.
    """

    def __init__(self, path: Path, block: int = 1000,
                 store_root: Path | None = None) -> None:
        """
        Args:
            path: the counter file, conventionally `<store_dir>/msg_id.json`.
            block: ids reserved per fsync (`msg_id_block`, default 1000).
            store_root: producer directory holding `pending/`, `sent/` and
                `nacked/`, scanned for the reuse floor described in the module
                docstring. Defaults to `path.parent`, which is where the
                client puts both.
        """
        self._path = Path(path)
        self._block = block
        self._store_root = Path(store_root) if store_root is not None \
            else self._path.parent
        self._lock = threading.Lock()

        # _next: next id to hand out. _reserved_until: exclusive upper bound
        # already persisted to disk -- i.e. everything in [_next, _reserved_until)
        # is safe to hand out without another fsync.
        self._next, self._reserved_until = self._load_or_init()

    def next(self) -> int:
        """Return the next monotonic msg_id, reserving a new block first if
        the current one is exhausted."""
        with self._lock:
            if self._next >= self._reserved_until:
                self._reserve_block_locked()
            msg_id = self._next
            self._next += 1
            return msg_id

    def close(self) -> None:
        """Write back the true next id so a clean shutdown does not burn the
        rest of the reserved block."""
        with self._lock:
            self._write(self._next)
            self._reserved_until = self._next

    def _load_or_init(self) -> tuple[int, int]:
        next_id = self._read_counter()
        if next_id is None:
            next_id = random.randint(_RANDOM_BASE_LO, _RANDOM_BASE_HI)

        # The reuse floor, applied on *both* paths. On the fallback path it is
        # what makes a random base safe over a surviving store; on the normal
        # path it catches a counter that parsed cleanly but reads below a live
        # record -- a rolled-back file, a restore from an older snapshot, a
        # store directory copied over a stale counter. Either way an id already
        # on disk must not be handed out a second time (§3.1, D29).
        highest = highest_record_id(self._store_root)
        if highest is not None and next_id <= highest:
            _logger.warning("msg_id_counter_below_store",
                            path=str(self._path), counter_next_msg_id=next_id,
                            highest_msg_id_on_disk=highest,
                            raised_to=highest + 1)
            next_id = highest + 1

        reserved_until = next_id + self._block
        self._write(reserved_until)
        return next_id, reserved_until

    def _read_counter(self) -> int | None:
        """`next_msg_id` from disk, or `None` when there is nothing usable
        there -- absent, empty, torn, missing its key, or holding something
        that is not an int.

        All of those collapse to the same answer on purpose. The allocator's
        job is to never reissue an id; it cannot do that job from a file it
        cannot read, and there is no partial credit to be had from
        distinguishing `{"next_msg_id": 99` from `{"next_msg_id": "abc"}`.
        The distinction that *is* worth making is absent-vs-unreadable, and
        that is in the log level: a missing file is a first start (debug), an
        unreadable one is data loss an operator should see (error, §10.4).
        """
        if not self._path.exists():
            _logger.debug("msg_id_counter_absent", path=str(self._path))
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return int(data["next_msg_id"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError,
                OSError) as exc:
            # Loud: the counter is gone, and only the on-disk floor plus the
            # random base stand between this producer and reissued ids. The
            # file is left in place for inspection -- the next write replaces
            # it atomically, so nothing depends on removing it first.
            _logger.error("msg_id_counter_unreadable", path=str(self._path),
                          error=f"{type(exc).__name__}: {exc}")
            return None

    def _reserve_block_locked(self) -> None:
        reserved_until = self._reserved_until + self._block
        self._write(reserved_until)
        self._reserved_until = reserved_until

    def _write(self, next_msg_id: int) -> None:
        """Persist `next_msg_id` durably *and* atomically before returning.

        The write must actually reach disk -- a buffered write that has not
        been fsynced defeats the crash invariant entirely -- and it must
        never be visible half-written under the real name, or the next start
        cannot tell a reserved block from a torn one. `write_atomic` is both
        (D30); see its module docstring for why each step matters.

        Raises:
            OSError: the counter could not be made durable. Not swallowed:
                this write is what makes the block reservation true, and
                continuing to hand out ids from a block that never reached
                disk is the reuse this allocator exists to prevent. At
                startup it surfaces from `register()` alongside the same
                condition `MsgStore.__init__`'s `mkdir` already raises -- a
                store directory that cannot be written to.
        """
        write_atomic(self._path,
                     json.dumps({"next_msg_id": next_msg_id}).encode("utf-8"))
        _logger.debug("msg_id_block_reserved", next_msg_id=next_msg_id,
                      path=str(self._path))

    # Intentionally no __del__. A real process crash never runs __del__, and
    # test_crash_never_reuses_an_id relies on that: writing back the true
    # next id from __del__ would make a `del a` "crash" clean-close silently,
    # which is exactly the bug this allocator exists to prevent.
