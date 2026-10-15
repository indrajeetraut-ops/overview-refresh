"""Batch uploader — the file front-door (spec: docs/modules/batch-uploader.md).

Thin and content-blind: accepts a developer's ~/.claude/projects archive,
stream-unpacks it into per-file objects, and writes the completion manifest
that the source adapter treats as a doorbell."""
from common.manifest import DeclaredFields, Manifest, ManifestFileEntry

from .config import ObjectStoreConfig, UploaderConfig, load_config
from .errors import (
    MalformedArchiveError,
    UploadTimeoutError,
    UploaderError,
    UploadTooLargeError,
)
from .models import UploadResponse
from .objectstore import ObjectStore, make_store
from .service import handle_upload

__all__ = [
    "DeclaredFields",
    "Manifest",
    "ManifestFileEntry",
    "MalformedArchiveError",
    "ObjectStore",
    "ObjectStoreConfig",
    "UploadResponse",
    "UploadTimeoutError",
    "UploaderConfig",
    "UploaderError",
    "UploadTooLargeError",
    "handle_upload",
    "load_config",
    "make_store",
]
