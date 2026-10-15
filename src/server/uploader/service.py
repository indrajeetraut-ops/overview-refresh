"""Orchestration for one upload (spec: docs/modules/batch-uploader.md §4, §7).

Framework-free — takes a plain sync reader, independent of how it was
produced, so it is directly testable without FastAPI in the loop.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from typing import IO

import structlog

from common.manifest import DeclaredFields, Manifest

from . import unpack
from .config import UploaderConfig
from .ids import make_producer_id
from .models import UploadResponse
from .objectstore import ObjectStore

log = structlog.get_logger(module="uploader.service")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_upload(
    reader: IO[bytes],
    declared: DeclaredFields,
    store: ObjectStore,
    config: UploaderConfig,
) -> UploadResponse:
    upload_id = uuid.uuid4().hex
    producer_id = make_producer_id(declared.org_id, declared.user_id)

    files, total_bytes = unpack.stream_unpack(
        reader, store, upload_id, config.limits.max_upload_bytes
    )
    received_ts = _now_iso()

    manifest = Manifest(
        upload_id=upload_id,
        received_ts=received_ts,
        declared=declared,
        producer_id=producer_id,
        file_count=len(files),
        files=files,
        uploader_version=config.uploader_version,
    )
    # Objects-first-manifest-last (doc §7's atomicity boundary): this write
    # happens only after every per-file object above returned successfully.
    manifest_bytes = manifest.model_dump_json().encode("utf-8")
    store.put_stream(
        f"uploads/{upload_id}/manifest.json",
        io.BytesIO(manifest_bytes),
        len(manifest_bytes),
    )

    log.info(
        "upload_staged",
        upload_id=upload_id,
        file_count=len(files),
        bytes=total_bytes,
        org_id=declared.org_id,
        user_id=declared.user_id,
        vendor=declared.vendor,
    )

    return UploadResponse(
        upload_id=upload_id,
        received_ts=received_ts,
        file_count=len(files),
        bytes=total_bytes,
        declared=declared,
    )
