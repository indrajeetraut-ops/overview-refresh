"""Config models for the uploader. Layering per arch §12: defaults here → YAML → env vars.

TODO(uploader): switch load_config to the shared config loader in src/common
when that loader lands (arch §12 "one shared loader").
"""
import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

_ENV_REF = re.compile(r"\$\{(\w+)\}")


class LocalStoreConfig(BaseModel):
    root: Path


class S3StoreConfig(BaseModel):
    bucket: str
    region: str | None = None
    endpoint_url: str | None = None  # dev override (e.g. MinIO); None = real AWS


class ObjectStoreConfig(BaseModel):
    backend: Literal["s3", "local"] = "local"
    s3: S3StoreConfig | None = None
    local: LocalStoreConfig | None = None


class LimitsConfig(BaseModel):
    max_upload_bytes: int = 5 * 1024**3  # 5 GiB uncompressed aggregate (doc §12 U1)
    upload_timeout_s: float = 900.0


class HttpConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class UploaderConfig(BaseModel):
    object_store: ObjectStoreConfig = Field(default_factory=ObjectStoreConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    uploader_version: str = "0.1.0"


def load_config(path: str | Path) -> UploaderConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    return UploaderConfig.model_validate(yaml.safe_load(text) or {})
