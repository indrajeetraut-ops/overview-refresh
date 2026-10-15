"""Durable file-per-msg store (design §3, §3.1).

The folder IS the state. A msg record lives in exactly one of `pending/`,
`sent/`, or `nacked/`, and every state transition is `os.replace` between
those directories. Rename within a filesystem is atomic, so a transition
either happened or it did not -- there is no torn intermediate state, and no
separate state field on the record that could ever disagree with where the
file actually lives.

Durable-create sequence (design §3.1), normative, every step required:

    write tmp -> fsync tmp -> rename into pending/ -> fsync dir

Skipping the file fsync risks contents that never reached disk. Skipping the
directory fsync is the step people skip and the one that matters most here:
without it the *rename itself* is not durable -- `os.replace()` can return
successfully and the file can still vanish on crash, because the directory
entry pointing at it was never forced to disk.

A partially-written file must never be visible to a reader. That is why
`put_pending` never writes directly into `pending/`: it writes `<id>.json.tmp`
first and only a completed `os.replace` makes it appear under its real name.
`list()`/`get()` correspondingly ignore anything whose name is not exactly
`<int>.json`.

The whole envelope is stored, not just the payload, so a resend after a
restart can rebuild the request verbatim -- `event_id`, `dedup`, `info` and
`service_name` cannot be recomputed from the payload alone.

Corrupt-record policy: a record file that completed its rename (so it has a
proper `<id>.json` name) but fails to parse -- e.g. bit rot, an out-of-band
edit, a bug in some other writer -- is logged loudly at error level and
skipped by `list()`/`get()`/`sweep_sent_to_pending()`. It is deliberately
*not* raised: one corrupt record must not take down a cleaner sweep that is
processing hundreds of healthy ones, and it must not be silently dropped
either -- the error log is the operator's signal to go look at the file by
hand. (A stray `<id>.json.tmp` left by a crash mid-write is a different,
expected case and is skipped silently -- see `_completed_records`.)
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pydantic

from common.msging.durable import fsync_dir, private_dir, write_atomic
from common.msging.log import get_logger
from common.msging.models import MsgRecord

_logger = get_logger(__name__)

_STATES = ("pending", "sent", "nacked")
_RECORD_NAME_RE = re.compile(r"^(\d+)\.json$")


def highest_record_id(root: Path) -> int | None:
    """Highest msg_id occupying a record filename anywhere under `root`, or
    `None` if the store holds nothing.

    Read by `MsgIdAllocator` as a *floor* on the id it hands out next, so a
    counter that was lost or rolled back cannot reissue an id a surviving
    record already wears (design §3.1: gaps are fine, reuse is not). Deliberately
    a module function and not a `MsgStore` method: the allocator is built before
    -- and independently of -- the store, and this needs no lock, because it
    reads names only and every transition is a rename between two of the
    directories it scans, so a record moving mid-scan is seen once, twice or
    (harmlessly, for a maximum) not at all.

    **Names, not contents.** `_valid_ids` hides a corrupt record because the
    cleaner cannot act on one; here that would be exactly wrong -- a corrupt
    `pending/41.json` still means id 41 is spoken for, and handing it out again
    is the one outcome §3.1 forbids. A `<id>.json.tmp` is still excluded: it
    never landed via the durable-create rename, so no msg ever went out under it.

    Note this bounds reuse against what is *on disk*, not against what the
    server has already seen -- ids that were ACKed and purged leave no trace
    here. That residual case is the lost-store scenario §3.1 already accepts and
    answers with a random base.
    """
    root = Path(root)
    highest: int | None = None
    for state in _STATES:
        dir_path = root / state
        if not dir_path.is_dir():
            continue
        for entry in dir_path.iterdir():
            match = _RECORD_NAME_RE.match(entry.name)
            if match:
                msg_id = int(match.group(1))
                if highest is None or msg_id > highest:
                    highest = msg_id
    return highest


def _utc_now_iso() -> str:
    """`created_at`-shaped timestamp: `2026-07-25T10:15:30Z` (design §3 sample
    record). Matches the `Z`-suffixed, second-precision style used elsewhere
    in this store's own JSON examples, rather than `+00:00`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MsgStore:
    """One producer's durable msg store, rooted at `root`.

    `root` is expected to be the producer's own directory (design §3 sketch:
    `<base>/<server_alias>/<producer_id>/`); this class only owns the
    `pending/`, `sent/`, `nacked/` children of it, not `identity.json` or
    `msg_id.json`.

    Thread-safe *within one process only*. All state-mutating methods
    (`put_pending`, `mark_sent`, `mark_nacked`, `demote_to_pending`,
    `purge`, `sweep_sent_to_pending`) serialize on a single `threading.Lock`
    held for the duration of the call. This is a whole-store lock, not a
    per-msg_id lock: a single `EventClient` owns one store directory and
    drives it from a small, fixed set of threads (the producer thread(s)
    calling `send()`, the SSE reader thread driving outcomes, and the
    cleaner loop), so contention is low and the extra bookkeeping of a
    per-id lock table is not worth it here -- these are fast local-
    filesystem operations, not something callers block on for long.

    The lock only protects concurrent callers *inside this process*. It
    says nothing about two processes pointed at the same `root`: `os.replace`
    is atomic across processes too, but the read-modify-write sequences in
    `mark_sent`/`mark_nacked`/`purge` are not, so two processes sharing a
    store directory can still race each other. That is out of scope by
    design -- one `EventClient` owns its store directory -- but a caller
    that violates that assumption (e.g. two client instances pointed at the
    same `root`) gets no protection from this lock.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._dirs = {state: self._root / state for state in _STATES}
        for path in self._dirs.values():
            # 0700: deleting a record is governed by this directory's write
            # bit, not the record's, so a world-writable pending/ breaks
            # at-least-once with no trace.
            private_dir(path)
        self._lock = threading.Lock()

    # -- writes -----------------------------------------------------------

    def put_pending(self, rec: MsgRecord) -> None:
        """Durably create `rec` in `pending/`. See module docstring for the
        exact write/fsync/rename/fsync sequence and why every step matters."""
        with self._lock:
            self._write_into(self._dirs["pending"], rec)

    def mark_sent(self, msg_id: int) -> None:
        """PENDING -> SENT.

        Also stamps `sent_at` and bumps `attempts`: the cleaner loop's
        `sent_timeout_s` check (design §4.4) is measured from `sent_at`, and
        `attempts` feeds its backoff curve. Both must survive the transition,
        so -- unlike a pure rename -- this rewrites the record in place
        (durably) before moving it, mirroring `mark_nacked`.

        Held under the store lock end-to-end: read-modify-write followed by
        a rename is not itself atomic, and without the lock a concurrent
        `purge()` could delete the record between the read and the rewrite,
        resurrecting it (see class docstring).
        """
        with self._lock:
            rec = self._require(self._dirs["pending"], msg_id)
            rec.sent_at = _utc_now_iso()
            rec.attempts += 1
            self._write_into(self._dirs["pending"], rec)  # rewrite in place, still PENDING
            self._transition(msg_id, "pending", "sent")

    def mark_nacked(self, msg_id: int, reason: str) -> str:
        """-> NACKED, persisting `reason` on the record.

        Returns the state the record was moved **out of** -- `"sent"` or
        `"pending"`. The caller needs that fact, not just "it moved": a window
        slot belongs to a `SENT` record and to nothing else, so only a
        `sent/` -> `nacked/` move may hand one back (design §5.1, and the same
        rule `purge()` returns its state for).

        Raises:
            FileNotFoundError: no readable record in either directory.

        `sent/` first, because §2.2's steady state is "POST -> SENT -> outcome"
        and that is where the record almost always is. `pending/` is accepted
        because the server answers one msg **twice** -- once with the 202, once
        over SSE -- and nothing sequences those two: over a fast link the NACK
        routinely arrives while `send()` is still between `put_pending()` and
        `mark_sent()`, so the record is legitimately still `PENDING`. Refusing
        it there discarded the outcome as a late duplicate and parked the msg
        in `sent/` until `sent_timeout_s` -- measured at 10-18% of NACKs over
        loopback. §2.2 step 3 says a NACK marks the record NACKED; it does not
        say from where. `purge()` has always scanned every state for exactly
        this reason, and this is the same rule applied to the other outcome.

        Held under the store lock for the same reason as `mark_sent`: the
        read-modify-write must not interleave with a concurrent `purge()`, and
        the two-directory scan must not interleave with a concurrent
        `mark_sent()` moving the record between them mid-scan.
        """
        with self._lock:
            for state in ("sent", "pending"):
                rec = self._load(self._dirs[state] / f"{msg_id}.json")
                if rec is None:
                    continue
                rec.nack_reason = reason
                # Rewrite in place first, then one atomic rename -- the same
                # shape `mark_sent` uses. A crash between the two leaves the
                # record where it was, which is always a resend, never a loss.
                self._write_into(self._dirs[state], rec)
                self._transition(msg_id, state, "nacked")
                return state
            raise FileNotFoundError(
                f"no record for msg_id={msg_id} in sent/ or pending/")

    def demote_to_pending(self, msg_id: int) -> None:
        """SENT -> PENDING. Legal (design §2.1): driven by the cleaner loop
        on `sent_timeout_s` timeout or a changed `server_epoch`."""
        with self._lock:
            self._transition(msg_id, "sent", "pending")

    def restore_to_pending(self, msg_id: int) -> None:
        """NACKED -> PENDING. Two callers, both from design §2.2/§2.3 and
        neither of them the cleaner loop:

        - `resolve(RETAIN)`, the app's decision on a NACKed msg -- "back to
          `PENDING`, durably, then resend", and the default when the app says
          nothing at all.
        - §2.3's startup fold, run once per process by the client facade.

        Deliberately absent from the steady-state sweep (§4.4 leaves `NACKED`
        alone): while the process is live an unresolved NACK means the app has
        not decided yet, and folding it there would erase that window.
        """
        with self._lock:
            self._transition(msg_id, "nacked", "pending")

    def sweep_nacked_to_pending(self) -> int:
        """Bulk NACKED -> PENDING for §2.3's startup pass. Returns the number
        moved. A `NACKED` record found at startup can only mean the app's
        PURGE/RETAIN decision never finished committing, and RETAIN is the
        default -- so every one of them is safe to fold.

        Corrupt records are skipped by `_valid_ids`, and a record that moved
        under us is not an error, exactly as in `sweep_sent_to_pending`.
        """
        with self._lock:
            moved = 0
            for msg_id in self._valid_ids(self._dirs["nacked"]):
                try:
                    self._transition(msg_id, "nacked", "pending")
                except FileNotFoundError:
                    continue
                moved += 1
            return moved

    def purge(self, msg_id: int) -> str | None:
        """Delete the record wherever it currently lives. No-op if absent.

        Returns the state it was deleted **from** (`"pending"`, `"sent"` or
        `"nacked"`), or `None` when there was nothing to delete. The caller
        needs both facts, not just "purge ran": a window slot belongs to a
        `SENT` record and to nothing else, so a duplicate or unknown-msg_id
        outcome -- routine traffic on an at-least-once channel (§2.2: a resend
        hits the server's dedup and comes back as a *second* ACK) -- must not
        be able to hand back a slot it never took. An unconditional release
        there invents a slot per duplicate and `max_in_flight` stops bounding
        anything (§5.1).

        Held under the store lock so the scan-then-unlink across the three
        state directories cannot interleave with another mutator moving the
        same msg_id between directories mid-scan (which would otherwise let
        purge finish having deleted nothing, leaking the record on disk).
        """
        with self._lock:
            for state in _STATES:
                path = self._dirs[state] / f"{msg_id}.json"
                if path.exists():
                    path.unlink()
                    return state
        return None

    def sweep_sent_to_pending(self) -> int:
        """Bulk SENT -> PENDING. Used when a server restart (`server_epoch`
        change) invalidates every outstanding outcome at once (design §4.4).
        Returns the number of records moved. Corrupt records in `sent/` are
        logged and skipped, not fatal to the sweep."""
        with self._lock:
            moved = 0
            for msg_id in self._valid_ids(self._dirs["sent"]):
                try:
                    self._transition(msg_id, "sent", "pending")
                except FileNotFoundError:
                    continue  # raced with another mover; not our problem
                moved += 1
            return moved

    # -- reads --------------------------------------------------------------

    def get(self, msg_id: int) -> MsgRecord | None:
        for state in _STATES:
            path = self._dirs[state] / f"{msg_id}.json"
            if path.exists():
                return self._load(path)
        return None

    def list(self, state: str) -> list[int]:
        """Sorted msg_ids currently in `state`. Sorted numerically -- a
        lexical sort of filenames breaks on e.g. 2/10/100 and the cleaner
        loop relies on numeric order. A corrupt-but-completed record file is
        excluded here too (see `_valid_ids`), not just from `get()`."""
        return sorted(self._valid_ids(self._dirs[state]))

    def count(self, state: str) -> int:
        return len(self._valid_ids(self._dirs[state]))

    def file_count(self, state: str) -> int:
        """Completed record *files* in `state`, parseable or not.

        `count()` deliberately hides a corrupt record, because the cleaner
        cannot act on one. A drain cannot hide it: §2.4's `unresolved` is
        "what is still on disk", and an unreadable `pending/12.json` is still
        an undelivered msg. Counting it with `count()` would let `close()`
        report `drained` with a msg sitting in the store -- the one thing the
        status is supposed to rule out. `_load` has already logged it loudly.
        """
        return len(self._record_names(self._dirs[state]))

    def total(self) -> int:
        """Records in all three states, counted as one atomic observation.

        Under the store lock, and that is the whole point. Listing the three
        directories one after another is not a snapshot: every transition is an
        `os.replace` between two of them, so a record being renamed `sent/` ->
        `pending/` at exactly the wrong instant is listed in neither and the
        store reads one msg lighter than it is. `send()`'s `max_pending` check
        reads this number (`_wait_for_slot`), so that undercount handed out a
        slot §5.1 says it should have refused -- an over-limit `send()` came
        back `ok` while the cleaner's epoch sweep was moving a record. Narrow
        (one msg per concurrent rename), but every mutator already serialises
        on this lock, so closing it costs nothing but taking it.
        """
        with self._lock:
            return sum(len(self._valid_ids(self._dirs[state]))
                       for state in _STATES)

    # -- internals ------------------------------------------------------

    def _require(self, dir_path: Path, msg_id: int) -> MsgRecord:
        path = dir_path / f"{msg_id}.json"
        rec = self._load(path)
        if rec is None:
            raise FileNotFoundError(f"no record for msg_id={msg_id} in {dir_path}")
        return rec

    def _transition(self, msg_id: int, from_state: str, to_state: str) -> None:
        """Pure `os.replace` between state directories -- atomic, no
        content rewrite. Callers that need to change the record's content as
        part of a transition (mark_sent, mark_nacked) rewrite it in place
        first, then call this."""
        src = self._dirs[from_state] / f"{msg_id}.json"
        dst = self._dirs[to_state] / f"{msg_id}.json"
        os.replace(src, dst)
        fsync_dir(self._dirs[to_state])

    def _write_into(self, dir_path: Path, rec: MsgRecord) -> None:
        """write tmp -> fsync tmp -> rename into dir_path -> fsync dir_path
        (`durable.write_atomic`).

        Used both for the initial durable create (`put_pending`, target dir
        differs from any current location) and for an in-place durable
        rewrite (`mark_sent`/`mark_nacked`, target dir == current location).
        Either way the record is never visible under its real name until the
        write has already reached disk.
        """
        write_atomic(dir_path / f"{rec.msg_id}.json",
                     rec.model_dump_json().encode("utf-8"))

    def _load(self, path: Path) -> MsgRecord | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MsgRecord.model_validate(data)
        except (json.JSONDecodeError, pydantic.ValidationError, OSError):
            _logger.error("msg_store_corrupt_record", path=str(path))
            return None

    def _valid_ids(self, dir_path: Path) -> list[int]:
        """msg_ids of *completed and parseable* records in `dir_path`.

        Filename must match exactly `<int>.json` -- a stray `<id>.json.tmp`
        left behind by a crash mid-write is a different name and is silently
        excluded; it never landed via the durable-create rename, so it was
        never a completed record to begin with (see module docstring).

        A `<id>.json` whose name is well-formed but whose content fails to
        parse (corrupt-record policy, module docstring) is also excluded
        here -- `list()`/`count()`/`sweep_sent_to_pending()` must not surface
        or move a record no one can read -- but the file itself is left in
        place for inspection, and `_load` already logged it loudly.
        """
        return [msg_id for msg_id, path in self._record_names(dir_path)
                if self._load(path) is not None]

    @staticmethod
    def _record_names(dir_path: Path) -> list[tuple[int, Path]]:
        """`(msg_id, path)` for every *completed* record filename in
        `dir_path`, without reading any of them. The name filter is the same
        one `_valid_ids` uses -- a `<id>.json.tmp` never landed via the
        durable-create rename -- but the content is not consulted, which is
        what makes this the right basis for `file_count`."""
        out = []
        for entry in dir_path.iterdir():
            match = _RECORD_NAME_RE.match(entry.name)
            if match:
                out.append((int(match.group(1)), entry))
        return out
