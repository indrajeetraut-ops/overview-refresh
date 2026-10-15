"""Configuration loading (project mandate §10).

YAML only; developer-facing; secrets via env (``${VAR}`` interpolation), never in the
file. No other module parses YAML — everything goes through :func:`load_config`.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .logging import get_logger

log = get_logger(__name__)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class Dataset:
    name: str
    url: str
    subpath: str | None = None


@dataclass
class Source:
    mode: str = "stream"  # stream | download
    cache_dir: str = "~/.talium/trace_cache"
    hf_token_env: str = "HF_TOKEN"

    @property
    def cache_path(self) -> Path:
        return Path(os.path.expanduser(self.cache_dir))


@dataclass
class Config:
    datasets: list[Dataset] = field(default_factory=list)
    source: Source = field(default_factory=Source)

    def dataset(self, name: str) -> Dataset | None:
        for d in self.datasets:
            if d.name == name:
                return d
        return None


def _interpolate(value: Any) -> Any:
    """Recursively replace ``${VAR}`` with the environment value (empty if unset)."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def load_config(path: str | os.PathLike) -> Config:
    """Load and validate the YAML config into a typed :class:`Config`."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    raw = _interpolate(raw)

    datasets = []
    for i, d in enumerate(raw.get("datasets", []) or []):
        if not d.get("url"):
            raise ValueError(f"datasets[{i}] missing required 'url'")
        datasets.append(
            Dataset(
                name=d.get("name") or d["url"],
                url=d["url"],
                subpath=d.get("subpath"),
            )
        )

    s = raw.get("source", {}) or {}
    mode = s.get("mode", "stream")
    if mode not in ("stream", "download"):
        raise ValueError(f"source.mode must be 'stream' or 'download', got {mode!r}")
    source = Source(
        mode=mode,
        cache_dir=s.get("cache_dir", "~/.talium/trace_cache"),
        hf_token_env=s.get("hf_token_env", "HF_TOKEN"),
    )
    log.info("config_loaded", n_datasets=len(datasets), mode=source.mode)
    return Config(datasets=datasets, source=source)


def token_from_env(source: Source) -> str | None:
    """Return the HF token from the configured env var, if set."""
    return os.environ.get(source.hf_token_env) or os.environ.get("HF_TOKEN")
