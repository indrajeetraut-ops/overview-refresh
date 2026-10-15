"""Cursor persistence per logical source (spec: docs/modules/source-adapter.md §6).

Mirrors src/common/msging/store.py's FileProducerStore pattern exactly: one JSON file,
atomic tmp+replace, a lock for concurrent commits. Checkpoint granularity here is batch's
(§1): per-source done/not-done plus an identity fingerprint -- no byte offset, since batch
reprocesses a changed source whole rather than resuming mid-file (§4.1).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from .config import CheckpointStoreConfig


class Checkpoint(BaseModel):
    identity: str
    done: bool = False


class CheckpointStore(Protocol):
    def get(self, logical_key: str) -> Checkpoint | None: ...
    def commit(self, logical_key: str, ckpt: Checkpoint) -> None: ...


class FileCheckpointStore:
    """Day-one backend: one JSON file keyed by logical source key, atomic replace."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def _save(self, data: dict[str, dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, logical_key: str) -> Checkpoint | None:
        rec = self._load().get(logical_key)
        return Checkpoint.model_validate(rec) if rec else None

    def commit(self, logical_key: str, ckpt: Checkpoint) -> None:
        with self._lock:
            data = self._load()
            data[logical_key] = ckpt.model_dump()
            self._save(data)


def make_checkpoint_store(cfg: CheckpointStoreConfig) -> CheckpointStore:
    if cfg.backend == "file":
        if cfg.file is None:
            raise ValueError("checkpoint_store.backend=file requires checkpoint_store.file.path")
        return FileCheckpointStore(cfg.file.path)
    # TODO(collector): bind the db adapter when the db backend design settles (design in
    # flux -- do not assume any specific db access mechanism), mirroring msging/store.py.
    raise NotImplementedError("db checkpoint-store backend: design in flux")
