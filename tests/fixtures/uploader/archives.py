"""Programmatic tar+gzip archive builders for batch-uploader tests (spec:
docs/modules/batch-uploader.md §10). No binary blobs committed — every
fixture is built here so diffs stay reviewable."""
from __future__ import annotations

import gzip
import io
import tarfile
from typing import Literal

PROJECTS_ROOT = ".claude/projects"


def _add_file(tar: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    tar.addfile(info, io.BytesIO(content))


def build_normal_archive() -> bytes:
    """A handful of small <project>/<session>.jsonl entries, no subagents."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|gz") as tar:
        _add_file(
            tar,
            f"{PROJECTS_ROOT}/-Users-dev-repo/session-aaa.jsonl",
            b'{"type": "user", "uuid": "evt-1"}\n{"type": "assistant", "uuid": "evt-2"}\n',
        )
        _add_file(
            tar,
            f"{PROJECTS_ROOT}/-Users-dev-repo/session-bbb.jsonl",
            b'{"type": "user", "uuid": "evt-3"}\n',
        )
    return buf.getvalue()


def build_archive_with_macos_metadata() -> bytes:
    """One real session file plus the AppleDouble/.DS_Store noise macOS `tar czf` emits
    alongside real entries unless run with COPYFILE_DISABLE=1 -- proves the unpacker skips
    this junk instead of staging it as (unparseable) session content."""
    buf = io.BytesIO()
    session_dir = f"{PROJECTS_ROOT}/-Users-dev-repo"
    with tarfile.open(fileobj=buf, mode="w|gz") as tar:
        _add_file(tar, f"{session_dir}/session-aaa.jsonl", b'{"type": "user", "uuid": "evt-1"}\n')
        # AppleDouble sidecar: binary resource-fork data, not valid JSON/UTF-8.
        _add_file(tar, f"{session_dir}/._session-aaa.jsonl", b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X ")
        _add_file(tar, f"{session_dir}/.DS_Store", b"\x00\x00\x00\x01Bud1\x00\x00")
    return buf.getvalue()


def build_subagents_archive() -> bytes:
    """A session plus its subagents/agent-*.jsonl + .meta.json siblings,
    co-located under the session's own prefix (doc §6 S3 layout)."""
    buf = io.BytesIO()
    session_dir = f"{PROJECTS_ROOT}/-Users-dev-repo"
    with tarfile.open(fileobj=buf, mode="w|gz") as tar:
        _add_file(
            tar,
            f"{session_dir}/session-parent.jsonl",
            b'{"type": "user", "uuid": "evt-1"}\n',
        )
        _add_file(
            tar,
            f"{session_dir}/session-parent/subagents/agent-1.jsonl",
            b'{"type": "assistant", "uuid": "evt-2"}\n',
        )
        _add_file(
            tar,
            f"{session_dir}/session-parent/subagents/agent-1.meta.json",
            b'{"agentId": "agent-1", "toolUseId": "tool-1"}',
        )
    return buf.getvalue()


def build_malformed_archive(kind: Literal["bad_gzip", "truncated", "empty"]) -> bytes:
    if kind == "bad_gzip":
        return b"this is not a gzip stream at all, just plain bytes"
    if kind == "empty":
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w|gz"):
            pass  # a well-formed tar+gzip with zero entries
        return buf.getvalue()
    if kind == "truncated":
        # Build the *uncompressed* tar first and cut it off squarely inside
        # the second file's content, then gzip the truncated bytes fresh.
        # tarfile pads a finished archive out to a full 10240-byte record
        # (mostly trailing zero padding), so slicing by "N bytes off the end"
        # only ever removes padding — cutting at a fixed absolute offset
        # (past the first file's header+content+second file's header, and
        # partway into the second file's 2000-byte content) is what actually
        # lands inside real content. This keeps the gzip layer perfectly
        # valid — it's the tar structure that's incomplete, so the first
        # member still reads cleanly (unpack.py's not_tar-vs-truncated_tar
        # heuristic keys off whether any member was already read before the
        # failure).
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tar:
            _add_file(tar, f"{PROJECTS_ROOT}/-Users-dev-repo/session-aaa.jsonl", b'{"type": "user"}\n')
            _add_file(tar, f"{PROJECTS_ROOT}/-Users-dev-repo/session-bbb.jsonl", b"x" * 2000)
        raw_bytes = raw.getvalue()
        truncated_raw = raw_bytes[:2036]  # header+content+header, then 500B into the 2nd file
        compressed = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed, mode="wb") as gz:
            gz.write(truncated_raw)
        return compressed.getvalue()
    raise ValueError(f"unknown malformed archive kind: {kind}")


def build_path_traversal_archive(
    kind: Literal["absolute", "traversal", "symlink", "hardlink"],
) -> bytes:
    """One legitimate entry plus one unsafe entry of the given kind — proves
    the malicious entry doesn't corrupt processing of the safe one, up to the
    point of rejection (doc §7's normative security control)."""
    buf = io.BytesIO()
    session_dir = f"{PROJECTS_ROOT}/-Users-dev-repo"
    with tarfile.open(fileobj=buf, mode="w|gz") as tar:
        _add_file(tar, f"{session_dir}/session-ok.jsonl", b'{"type": "user", "uuid": "evt-1"}\n')
        if kind == "absolute":
            _add_file(tar, "/etc/passwd", b"root:x:0:0::/root:/bin/sh\n")
        elif kind == "traversal":
            _add_file(tar, f"{PROJECTS_ROOT}/../../../etc/shadow", b"malicious\n")
        elif kind == "symlink":
            link = tarfile.TarInfo(name=f"{session_dir}/evil-link.jsonl")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tar.addfile(link)
        elif kind == "hardlink":
            link = tarfile.TarInfo(name=f"{session_dir}/evil-hardlink.jsonl")
            link.type = tarfile.LNKTYPE
            link.linkname = f"{session_dir}/session-ok.jsonl"
            tar.addfile(link)
        else:
            raise ValueError(f"unknown path-traversal kind: {kind}")
    return buf.getvalue()
