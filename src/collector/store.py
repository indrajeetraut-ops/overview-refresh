"""Byte-access shim for reading what the batch uploader staged (spec:
docs/modules/source-adapter.md §6).

SourceStore is the read-side counterpart to the uploader's ObjectStore (write-side,
src/server/uploader/objectstore.py) -- built fresh here rather than importing those classes:
collector is a reader, server.uploader is a writer, and they shouldn't share code across that
boundary even though they touch the same bucket layout. Operates on plain string keys (the
same keys ManifestFileEntry.key already carries), not a separate ref wrapper type.

Ranged reads exist for the live-tail seam (source-adapter.md §9); the batch path this module
serves today always reads whole-file (start=0, end=None), per the per-file, no-chunking
checkpoint granularity source-adapter.md §1 specifies for batch. The one place a bounded
read matters here is identity()'s fixed head window (§12 A4).
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import IO, Protocol

from .config import SourceStoreConfig

_HEAD_HASH_BYTES = 8192  # fixed small head window (source-adapter.md §12 A4)


class SourceStore(Protocol):
    def list_prefix(self, prefix: str) -> list[str]: ...

    def open(self, key: str, start: int, end: int | None) -> IO[bytes]:
        """Ranged read: [start, end). end=None reads to EOF."""
        ...

    def size(self, key: str) -> int: ...

    def identity(self, key: str) -> str:
        """Cheap fingerprint (size + hash of a fixed head window) to tell an unchanged
        source from a grown/rewritten one (doc §4.1)."""
        ...


class LocalFsSourceStore:
    """Dev/test backend: reads under root/<key>."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def list_prefix(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        return [str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()]

    def open(self, key: str, start: int, end: int | None) -> IO[bytes]:
        path = self._path(key)
        if end is None:
            f = path.open("rb")
            if start:
                f.seek(start)
            return f
        with path.open("rb") as f:
            if start:
                f.seek(start)
            return io.BytesIO(f.read(end - start))

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def identity(self, key: str) -> str:
        size = self.size(key)
        if size == 0:
            head = b""
        else:
            with self.open(key, 0, min(_HEAD_HASH_BYTES, size)) as f:
                head = f.read()
        return f"{size}:{hashlib.sha256(head).hexdigest()}"


class S3SourceStore:
    """boto3-backed ranged GetObject. Mirrors S3ObjectStore's client construction; the
    files this reads are small (~2MB, doc §7/§8), so a plain ranged GET is all that's
    needed -- no transfer-manager multipart machinery."""

    def __init__(
        self,
        bucket: str,
        region: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        import boto3

        self.bucket = bucket
        self._client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def list_prefix(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys

    def open(self, key: str, start: int, end: int | None) -> IO[bytes]:
        range_header = f"bytes={start}-{end - 1}" if end is not None else f"bytes={start}-"
        resp = self._client.get_object(Bucket=self.bucket, Key=key, Range=range_header)
        return resp["Body"]

    def size(self, key: str) -> int:
        resp = self._client.head_object(Bucket=self.bucket, Key=key)
        return resp["ContentLength"]

    def identity(self, key: str) -> str:
        size = self.size(key)
        if size == 0:
            head = b""
        else:
            with self.open(key, 0, min(_HEAD_HASH_BYTES, size)) as body:
                head = body.read()
        return f"{size}:{hashlib.sha256(head).hexdigest()}"


def make_source_store(cfg: SourceStoreConfig) -> SourceStore:
    if cfg.backend == "local":
        if cfg.local is None:
            raise ValueError("source_store.backend=local requires source_store.local.root")
        return LocalFsSourceStore(cfg.local.root)
    if cfg.backend == "s3":
        if cfg.s3 is None:
            raise ValueError("source_store.backend=s3 requires source_store.s3.bucket")
        return S3SourceStore(cfg.s3.bucket, cfg.s3.region, cfg.s3.endpoint_url)
    raise ValueError(f"unknown source_store backend: {cfg.backend}")
