"""Stream-unpack: tar structure only, never content (spec: docs/modules/batch-uploader.md §5, §7).

Walks a gzip'd tar stream entry-by-entry with bounded memory (tarfile's "r|gz"
streaming mode — no getmembers()/seek), writing each safe regular-file entry to
its own object. Never parses or interprets entry bytes.
"""
from __future__ import annotations

import tarfile
from typing import IO

from common.manifest import ManifestFileEntry

from .errors import MalformedArchiveError, UploadTooLargeError
from .objectstore import ObjectStore

PROJECTS_ROOT = ".claude/projects"


def _normalize_member_name(name: str) -> str:
    if name.startswith("./"):
        name = name[2:]
    return name


def sanitize_member(member: tarfile.TarInfo) -> str | None:
    """Returns the safe 'rel' path (relative to ~/.claude/projects), or None to
    skip a directory entry. Raises MalformedArchiveError on any unsafe entry —
    the one security control this service enforces (doc §7, normative)."""
    if member.isdir():
        return None
    name = _normalize_member_name(member.name)
    if not name:
        raise MalformedArchiveError("path_traversal")
    if name.startswith("/"):
        raise MalformedArchiveError("absolute_path")
    if any(part == ".." for part in name.split("/")):
        raise MalformedArchiveError("path_traversal")
    if member.issym() or member.islnk():
        raise MalformedArchiveError("symlink_entry")
    if not member.isfile():
        raise MalformedArchiveError("unsupported_entry_type")
    basename = name.rsplit("/", 1)[-1]
    if basename == ".DS_Store" or basename.startswith("._"):
        # macOS Finder/tar metadata noise (AppleDouble resource-fork sidecars, Spotlight)
        # -- `tar czf` on macOS emits these alongside real entries unless the archiver ran
        # with COPYFILE_DISABLE=1. Not content, not unsafe -- just skipped, like a dir entry.
        return None
    prefix = PROJECTS_ROOT + "/"
    if name == PROJECTS_ROOT:
        return None
    if name.startswith(prefix):
        return name[len(prefix) :]
    return name


def stream_unpack(
    reader: IO[bytes],
    store: ObjectStore,
    upload_id: str,
    max_uncompressed_bytes: int,
) -> tuple[list[ManifestFileEntry], int]:
    """Decompresses + iterates tar entries one at a time, writing each to
    store under uploads/<upload_id>/raw/projects/<rel>. Enforces the size cap
    on cumulative *uncompressed* bytes — a wire-only cap would miss a
    decompression bomb. Returns (manifest file entries, total bytes)."""
    try:
        tar = tarfile.open(fileobj=reader, mode="r|gz")
    except (tarfile.ReadError, OSError) as exc:
        raise MalformedArchiveError("not_gzip") from exc

    files: list[ManifestFileEntry] = []
    total_bytes = 0
    try:
        while True:
            try:
                member = tar.next()
            except (tarfile.ReadError, EOFError) as exc:
                # Once ≥1 member has already been read, the gzip/tar layers are
                # known-good, so a later failure is a truncation, not a format
                # error at the very start.
                reason = "truncated_tar" if files else "not_tar"
                raise MalformedArchiveError(reason) from exc
            if member is None:
                break
            rel = sanitize_member(member)
            if rel is None:
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            key = f"raw/projects/{rel}"
            full_key = f"uploads/{upload_id}/{key}"
            try:
                written = store.put_stream(full_key, extracted, member.size)
            except (tarfile.ReadError, EOFError) as exc:
                # Reading this member's content ran off the end of the
                # (de)compressed stream — a truncation, discovered lazily
                # since extractfile() reads are pulled by the store's copy
                # loop rather than eagerly here.
                raise MalformedArchiveError("truncated_tar") from exc
            total_bytes += written
            if total_bytes > max_uncompressed_bytes:
                raise UploadTooLargeError()
            files.append(ManifestFileEntry(key=key, rel=rel, bytes=written))
    finally:
        tar.close()

    if not files:
        raise MalformedArchiveError("empty_archive")

    return files, total_bytes
