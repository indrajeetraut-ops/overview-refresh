"""The durable-create sequence, in one place (design §3.1, decision D30).

    write tmp -> fsync tmp -> rename over the real name -> fsync dir

Every step is required and each one fails differently if skipped:

- **write to a tmp name, not the real one.** A reader must never see a
  half-written file under the name it trusts. `os.replace` is atomic within a
  filesystem, so the real name only ever refers to a complete file -- there is
  no torn intermediate state to parse.
- **fsync the tmp file.** Otherwise the contents may still be in page cache
  when the rename lands, and a crash publishes an empty or short file under
  the real name -- exactly the failure the tmp name was supposed to prevent.
- **fsync the containing directory.** The step people skip and the one that
  matters most: without it the *rename itself* is not durable. `os.replace()`
  can return successfully and the file can still vanish on crash, because the
  directory entry pointing at it was never forced to disk.

Every persistent file this library owns goes through here -- client msg records
(`clnt/store.py`), `identity.json` (`clnt/binding.py`), `msg_id.json`
(`clnt/msg_id.py`) and the server's `producers.json`
(`srv/producer_store.py`). Hand-rolled copies of the sequence are chances for
one of them to quietly lose a step; a counter file that had lost the tmp+
rename half, and a producer store that had lost both fsyncs, are the two
defects this module was extracted to close.

**This module sits above `clnt/` and `srv/` deliberately.** The two halves are
forbidden to import each other (`test_boundaries.py`), and durability is not a
client concern that the server happens to reuse -- `producers.json` is the
server's *only* durable state and the sole copy of every producer's key hash.
It joins models/codes/config/errors/log/metrics as shared ground (§6.4).

Mode is 0o600 at creation time, atomically with the file coming into
existence, rather than a later `os.chmod`. `identity.json` holds a plaintext
api_key and must never exist -- even for an instant -- with a broader mode.
Because 0o600 grants nothing to group/other the process umask cannot widen it
either (umask only clears bits, never adds them), and `os.replace` preserves
the tmp file's mode, so the published file is 0600 without a second syscall.

`private_dir` is the directory-side counterpart, and it is not cosmetic:
unlinking a file is governed by the *containing directory's* write bit, not
the file's. A 0600 `identity.json` inside a 0777 directory can be deleted and
replaced wholesale by any local user -- substituting an attacker's `base_path`
and api_key redirects the producer's entire msg stream -- and records can be
removed from `pending/`, breaking at-least-once with no trace.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

_FILE_MODE = 0o600
_DIR_MODE = 0o700


def write_atomic(path: Path, payload: bytes) -> None:
    """Durably publish `payload` at `path`. See module docstring for the
    sequence and why each step is load-bearing.

    The tmp file is `<path>.tmp` in the same directory -- it has to be on the
    same filesystem for `os.replace` to be atomic, and callers that scan the
    directory are expected to ignore that suffix (see `MsgStore._valid_ids`).

    Raises:
        OSError: the write, the rename or either fsync failed. Deliberately
            not swallowed: this call *is* the durability promise, and a caller
            told it succeeded when it did not is worse off than one told
            nothing.
    """
    path = Path(path)
    tmp_path = path.with_name(path.name + ".tmp")

    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)

    os.replace(tmp_path, path)
    fsync_dir(path.parent)


def fsync_dir(dir_path: Path) -> None:
    """Force `dir_path`'s own entries to disk, making a rename or an unlink
    inside it durable."""
    dir_fd = os.open(dir_path, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def private_dir(path: Path, parents: bool = True) -> Path:
    """Create `path` as an owner-only (0700) directory, and narrow it if it is
    already there and wider. See the module docstring for why the directory
    bit, not the file bit, is what protects `identity.json` and `pending/`.

    Two calls, not one, because `mkdir(mode=...)` covers neither case on its
    own: the mode is masked by the process umask on creation, and it is
    ignored outright when the directory already exists (`exist_ok=True`). A
    store directory created by an earlier build of this library -- or by a
    deployment that ran under a permissive umask -- is exactly the one that
    needs narrowing, so the chmod is not redundant with the mkdir.

    Only the permission bits are touched, and only when they are wider than
    0700: an operator who has *deliberately* tightened a directory further
    keeps their setting.

    Raises:
        OSError: creation failed, or the mode could not be read or set. Not
            swallowed -- a directory this call reported as private, and is
            not, is worse than no call at all.
    """
    path = Path(path)
    path.mkdir(parents=parents, exist_ok=True, mode=_DIR_MODE)
    current = stat.S_IMODE(path.stat().st_mode)
    if current & ~_DIR_MODE:
        os.chmod(path, current & _DIR_MODE)
    return path
