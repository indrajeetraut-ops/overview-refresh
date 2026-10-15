"""Config models for the collector engine. Layering per arch §12: defaults here → YAML → env vars.

TODO(collector): switch load_config to the shared config loader in src/common when that
loader lands (arch §12 "one shared loader") -- same TODO already left in msging/uploader.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from common.msging.config import MsgingConfig

_ENV_REF = re.compile(r"\$\{(\w+)\}")


class LocalSourceStoreConfig(BaseModel):
    root: Path


class S3SourceStoreConfig(BaseModel):
    bucket: str
    region: str | None = None
    endpoint_url: str | None = None  # dev override (e.g. MinIO); None = real AWS


class SourceStoreConfig(BaseModel):
    backend: Literal["s3", "local"] = "local"
    s3: S3SourceStoreConfig | None = None
    local: LocalSourceStoreConfig | None = None


class FileCheckpointStoreConfig(BaseModel):
    path: Path


class CheckpointStoreConfig(BaseModel):
    backend: Literal["file", "db"] = "file"
    file: FileCheckpointStoreConfig | None = None
    db: dict = Field(default_factory=dict)


class CollectorConfig(BaseModel):
    source_store: SourceStoreConfig = Field(default_factory=SourceStoreConfig)
    checkpoint_store: CheckpointStoreConfig = Field(default_factory=CheckpointStoreConfig)
    msging: MsgingConfig  # the msging client config (event-messaging-design.md §5.1)
    flush_timeout_s: float = 30.0
    normalizer_config: Path | None = None  # None -> NormalizerConfig() defaults


def load_config(path: str | Path) -> CollectorConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    return CollectorConfig.model_validate(yaml.safe_load(text) or {})
