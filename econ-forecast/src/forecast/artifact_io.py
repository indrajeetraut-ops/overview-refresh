"""Verified JSONL input and exclusive, streaming artifact output."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel, ValidationError
from forecast.synthetic.errors import ArtifactError, InputDataError

RecordType = TypeVar("RecordType", bound=BaseModel)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False, default=lambda item: item.isoformat()) + "\n").encode("utf-8")


def read_records(path: Path, record_type: type[RecordType],
                 schema_version: int = 1) -> list[RecordType]:
    """Validate the same bytes that are parsed; tolerate additive record fields.

    `schema_version` is the manifest version of the dataset directory the file lives in."""
    path = Path(path)
    try:
        manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int:
            raise InputDataError("invalid manifest schema version")
        if manifest["schema_version"] != schema_version or manifest.get("status") != "complete":
            raise InputDataError("unsupported or incomplete manifest")
        files = manifest.get("files")
        meta = files.get(path.name) if isinstance(files, dict) else None
        if not isinstance(meta, dict) or type(meta.get("rows")) is not int or meta["rows"] < 0:
            raise InputDataError("missing or invalid record-file metadata")
        digest = hashlib.sha256()
        rows = []
        with path.open("rb") as stream:
            for number, line in enumerate(stream, 1):
                digest.update(line)
                if not line.strip():
                    raise InputDataError(f"blank record in {path.name} at line {number}")
                try:
                    record = record_type.model_validate_json(line)
                except ValidationError as exc:
                    fields = ", ".join(".".join(map(str, item["loc"])) for item in exc.errors())
                    raise InputDataError(f"invalid record in {path.name} at line {number}; fields: {fields}") from exc
                if hasattr(record, "org_id") and record.org_id != manifest.get("org_id"):
                    raise InputDataError(f"record Organization differs from manifest in {path.name} at line {number}")
                rows.append(record)
        if digest.hexdigest() != meta.get("sha256"):
            raise InputDataError(f"hash mismatch for {path.name}")
        if len(rows) != meta["rows"]:
            raise InputDataError(f"row count mismatch for {path.name}")
        return rows
    except InputDataError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise InputDataError(f"cannot read dataset {path.name}: {type(exc).__name__}") from exc


class RecordStream:
    """A single open JSONL stream with incremental digest and row count."""

    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.rows = 0

    def write(self, record: BaseModel) -> None:
        data = _json(record.model_dump(mode="json"))
        try:
            self.stream.write(data)
        except OSError as exc:
            raise ArtifactError("cannot write record stream") from exc
        self.digest.update(data)
        self.rows += 1

    def extend(self, records: Iterable[BaseModel]) -> None:
        for record in records:
            self.write(record)


class DatasetWriter:
    """Own a new output directory and publish its manifest only after all streams close."""

    def __init__(self, output_dir: Path, operation: str, *, log=None):
        self.output_dir = Path(output_dir)
        self.operation = operation
        self.log = log
        self.files = {}
        self.created = False
        self.open_streams = 0

    def __enter__(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            error = ArtifactError("cannot create output directory; choose a new writable path")
            error.cleanup_outcome = "not-created"
            raise error from exc
        self.created = True
        return self

    def _path(self, name: str) -> Path:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactError("artifact path must stay inside output directory")
        return self.output_dir / relative

    @contextmanager
    def open_records(self, name: str):
        """Stream repeated daily batches without retaining previous days."""
        path = self._path(name)
        if path.exists():
            raise ArtifactError("record artifact already exists")
        try:
            with path.open("wb") as raw:
                self.open_streams += 1
                output = RecordStream(raw)
                try:
                    yield output
                finally:
                    self.open_streams -= 1
            self.files[name] = {"sha256": output.digest.hexdigest(), "rows": output.rows}
        except OSError as exc:
            raise ArtifactError(f"cannot write artifact {name}") from exc
        if self.log:
            self.log.info("synthetic.artifact_written", artifact=name, row_count=output.rows,
                          sha256=output.digest.hexdigest())

    def write_records(self, name: str, records: Iterable[BaseModel]) -> None:
        with self.open_records(name) as output:
            output.extend(records)

    def write_bytes(self, name: str, data: bytes) -> None:
        try:
            self._path(name).write_bytes(data)
        except OSError as exc:
            raise ArtifactError(f"cannot write artifact {name}") from exc

    def snapshot(self, source: Path, destination: str) -> Path:
        path = self._path(destination)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
        except OSError as exc:
            raise ArtifactError("cannot snapshot input artifact") from exc
        return path

    def manifest(self, data: dict) -> None:
        if self.open_streams:
            raise ArtifactError("cannot complete dataset while record streams remain open")
        try:
            temporary = self.output_dir / ".manifest.tmp"
            temporary.write_bytes(_json(data))
            os.replace(temporary, self.output_dir / "manifest.json")
        except OSError as exc:
            raise ArtifactError("cannot publish completion manifest") from exc

    def __exit__(self, error_type, error, traceback):
        if error_type and self.created:
            try:
                shutil.rmtree(self.output_dir)
                error.cleanup_outcome = "removed-created-output"
            except OSError:
                error.cleanup_outcome = "cleanup-failed"
        return False
