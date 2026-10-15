"""Object store abstraction (spec: docs/modules/batch-uploader.md §6, §8).

Keys are full paths relative to the store root — for S3 that's the bucket root
(e.g. "uploads/<upload_id>/raw/projects/...", "uploads/<upload_id>/manifest.json");
for the local backend, the same relative path under a filesystem root. Callers
never need to know which backend is behind the interface.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import IO, Protocol

import structlog

from .config import ObjectStoreConfig

log = structlog.get_logger(module="uploader.objectstore")

_COPY_CHUNK_BYTES = 1024 * 1024


class ObjectStore(Protocol):
    def put_stream(self, key: str, fileobj: IO[bytes], size: int) -> int:
        """Writes fileobj's contents to key, durable on return. `size` is the
        declared content length (known upfront from the tar header or the
        manifest bytes) — required so the S3 backend can set Content-Length
        without needing a seekable body. Returns bytes actually written."""
        ...

    def exists(self, key: str) -> bool: ...

    def list_prefix(self, prefix: str) -> list[str]:
        """Returns keys under prefix. Not on the request hot path — used by tests
        and debugging (doc §10)."""
        ...


class LocalObjectStore:
    """Dev/test backend: writes under root/<key> via tmp-file + atomic rename."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put_stream(self, key: str, fileobj: IO[bytes], size: int) -> int:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.part")
        written = 0
        try:
            with tmp.open("wb") as out:
                while chunk := fileobj.read(_COPY_CHUNK_BYTES):
                    out.write(chunk)
                    written += len(chunk)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        return written

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_prefix(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        return [
            str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
        ]


class S3ObjectStore:
    """boto3-backed. Uses put_object with an explicit Content-Length (known
    upfront from the tar header) rather than upload_fileobj's transfer
    manager — our entries are small (doc §8, ~2 MB) so multipart adds no
    value, and a plain single-threaded PUT works with a non-seekable
    streaming body, which extractfile() in "r|gz" mode is."""

    def __init__(
        self,
        bucket: str,
        region: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        import boto3

        self.bucket = bucket
        self._client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def put_stream(self, key: str, fileobj: IO[bytes], size: int) -> int:
        # put_object either transmits exactly `size` bytes of Body or raises;
        # there's no partial-write outcome to report, so echoing the declared
        # size back is exact, not a guess.
        self._client.put_object(Bucket=self.bucket, Key=key, Body=fileobj, ContentLength=size)
        return size

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise

    def list_prefix(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys


def make_store(cfg: ObjectStoreConfig) -> ObjectStore:
    if cfg.backend == "local":
        if cfg.local is None:
            raise ValueError("object_store.backend=local requires object_store.local.root")
        return LocalObjectStore(cfg.local.root)
    if cfg.backend == "s3":
        if cfg.s3 is None:
            raise ValueError("object_store.backend=s3 requires object_store.s3.bucket")
        return S3ObjectStore(cfg.s3.bucket, cfg.s3.region, cfg.s3.endpoint_url)
    raise ValueError(f"unknown object_store backend: {cfg.backend}")
