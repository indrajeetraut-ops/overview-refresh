"""Wire models for the batch uploader (spec: docs/modules/batch-uploader.md §4, §6).

DeclaredFields/ManifestFileEntry/Manifest live in `common.manifest` now -- the collector reads
manifests from two producers (this uploader and the Go `talium-sync` client in `../upload`),
so the contract moved to a location neither producer is subordinate to. Importers that need
those three types (this package's other modules included) import them from `common.manifest`
directly rather than through here. This module keeps only `UploadResponse`, which has no
second producer.
"""
from __future__ import annotations

from pydantic import BaseModel

from common.manifest import DeclaredFields


class UploadResponse(BaseModel):
    upload_id: str
    received_ts: str
    file_count: int
    bytes: int
    status: str = "staged"
    declared: DeclaredFields
