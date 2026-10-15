"""Strict public reader for completed forecast runs."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib, json
from pathlib import Path
import numpy as np
from .errors import ExecutionError, InputError
from .schemas import identity

SERIES_STATUSES = {
    "qualified",
    "inactive",
    "insufficient_history",
    "insufficient_startup",
    "sampling_failed",
}
SCOPE_STATUSES = {"complete", "conditional", "inactive", "incomplete"}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                digest.update(block)
    except OSError as exc:
        raise ExecutionError(f"cannot read artifact: {path.name}") from exc
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"invalid JSON artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise InputError(f"{path.name} must contain an object")
    return value


def _inside(root: Path, relative) -> Path:
    if not isinstance(relative, str) or not relative:
        raise InputError("artifact path must be a nonempty string")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise InputError("unsafe artifact path")
    path = root / candidate
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise InputError(f"missing artifact: {relative}") from exc
    if root not in resolved.parents or path.is_symlink() or not resolved.is_file():
        raise InputError("artifact path escapes run")
    return resolved


def _required(mapping: dict, names, context):
    missing = [x for x in names if x not in mapping]
    if missing:
        raise InputError(f"{context} missing required fields: {', '.join(missing)}")


@dataclass
class SavedRun:
    root: Path
    manifest: dict
    index: dict
    #: Facts other modules need (cost design §3.1). `None` when a run predates their
    #: recording (T2); forecasting's own reading of a run never depends on them, so a
    #: pre-T2 run still loads -- a consumer that requires a fact raises its own error.
    origin: date | None = None
    periods: tuple[dict, ...] | None = None
    dataset: dict | None = None
    history_lookback_days: int | None = None

    def load_paths(self, entry: dict) -> np.ndarray:
        if not any(entry is item for item in self.index["series"]):
            raise InputError("series entry does not belong to this run")
        if entry["status"] not in ("qualified", "inactive"):
            raise InputError("series has no usable paths")
        path = _inside(self.root, entry["path"])
        try:
            array = np.load(path, mmap_mode="r", allow_pickle=False)
        except (OSError, ValueError, TypeError) as exc:
            raise InputError("invalid NumPy path artifact") from exc
        expected = [len(self.index["dates"]), len(self.index["sample_ids"])]
        if (
            array.dtype != np.dtype("float64")
            or not array.flags.c_contiguous
            or list(array.shape) != entry["shape"]
            or list(array.shape) != expected
        ):
            raise InputError("path array contract mismatch")
        for start in range(0, array.shape[0], 32):
            block = np.asarray(array[start : start + 32])
            if not np.isfinite(block).all() or np.any(block < 0):
                raise InputError("path array contains nonfinite or negative values")
            if entry["status"] == "inactive" and np.any(block != 0):
                raise InputError("inactive path array must be all zero")
        return array


def _run_facts(request_doc: dict) -> dict:
    """Tolerant extraction of the T2 facts from `request.json`: absent (pre-T2 run)
    yields `None` for that fact; present but malformed is an `InputError`. Additive
    only -- never touches the fields `load_run` already validates."""

    def _date(value, context):
        if not isinstance(value, str):
            raise InputError(f"invalid {context} in request.json")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise InputError(f"invalid {context} in request.json") from exc

    origin = (
        _date(request_doc["origin"], "origin")
        if request_doc.get("origin") is not None
        else None
    )

    periods = None
    raw_periods = request_doc.get("period_ranges")
    if raw_periods is not None:
        if not isinstance(raw_periods, list) or not raw_periods:
            raise InputError("invalid period_ranges in request.json")
        parsed_periods = []
        for item in raw_periods:
            if not isinstance(item, dict) or item.get("type") not in (
                "requested",
                "calendar",
            ):
                raise InputError("invalid period_ranges entry in request.json")
            parsed_periods.append(
                {
                    "type": item["type"],
                    "start": _date(item.get("start"), "period start"),
                    "end": _date(item.get("end"), "period end"),
                }
            )
        periods = tuple(parsed_periods)

    dataset = None
    raw_dataset = request_doc.get("dataset")
    if raw_dataset is not None:
        if (
            not isinstance(raw_dataset, dict)
            or not isinstance(raw_dataset.get("path"), str)
            or not raw_dataset["path"]
            or not isinstance(raw_dataset.get("manifest_sha256"), str)
            or not raw_dataset["manifest_sha256"]
        ):
            raise InputError("invalid dataset field in request.json")
        dataset = {
            "path": raw_dataset["path"],
            "manifest_sha256": raw_dataset["manifest_sha256"],
        }

    history_lookback_days = request_doc.get("history_lookback_days")
    if history_lookback_days is not None and (
        type(history_lookback_days) is not int or history_lookback_days <= 0
    ):
        raise InputError("invalid history_lookback_days in request.json")

    return {
        "origin": origin,
        "periods": periods,
        "dataset": dataset,
        "history_lookback_days": history_lookback_days,
    }


def load_run(run_dir: Path) -> SavedRun:
    root = Path(run_dir).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise InputError("run has no completed manifest")
    manifest = _read_json(manifest_path)
    _required(
        manifest,
        (
            "schema_version",
            "operation",
            "completion",
            "status",
            "run_id",
            "created_at",
            "files",
            "versions",
            "input_sources",
            "counts",
            "report_path",
        ),
        "manifest",
    )
    if type(manifest["schema_version"]) is int and manifest["schema_version"] == 1:
        raise InputError("forecast run uses schema 1; rerun the forecast")
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 2
        or manifest["operation"] != "forecast"
        or manifest["completion"] != "complete"
        or manifest["status"] not in ("complete", "partial")
    ):
        raise InputError("unsupported or incomplete forecast manifest")
    if not isinstance(manifest["files"], dict) or not manifest["files"]:
        raise InputError("manifest files must be a nonempty mapping")
    if (
        not isinstance(manifest["versions"], dict)
        or not isinstance(manifest["input_sources"], dict)
        or not isinstance(manifest["counts"], dict)
    ):
        raise InputError("invalid manifest mappings")
    _required(
        manifest["counts"],
        ("requested_scopes", "completed_scopes", "rejected_scopes"),
        "manifest counts",
    )
    for value in manifest["counts"].values():
        if type(value) is not int or value < 0:
            raise InputError("manifest counts must be nonnegative integers")
    if (
        manifest["counts"]["completed_scopes"] + manifest["counts"]["rejected_scopes"]
        != manifest["counts"]["requested_scopes"]
    ):
        raise InputError("manifest counts do not reconcile")
    for relative, metadata in manifest["files"].items():
        if (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("sha256"), str)
            or type(metadata.get("bytes")) is not int
            or metadata["bytes"] < 0
        ):
            raise InputError("invalid manifest file metadata")
        path = _inside(root, relative)
        if path.stat().st_size != metadata["bytes"] or _sha(path) != metadata["sha256"]:
            raise InputError(f"artifact checksum mismatch: {relative}")
    required_files = {
        "paths/index.json",
        "request.json",
        "resolved_config.yaml",
        "daily_expected.jsonl",
        "allocations.json",
        "period_summary.json",
        "diagnostics.json",
        "report.md",
    }
    if (
        not required_files <= set(manifest["files"])
        or manifest["report_path"] != "report.md"
    ):
        raise InputError("manifest omits required forecast artifacts")
    index = _read_json(root / "paths/index.json")
    _required(
        index,
        ("schema_version", "dates", "sample_ids", "scopes", "series"),
        "paths index",
    )
    if (
        type(index["schema_version"]) is not int
        or index["schema_version"] != 1
        or not isinstance(index["dates"], list)
        or not isinstance(index["sample_ids"], list)
        or not isinstance(index["scopes"], list)
        or not isinstance(index["series"], list)
    ):
        raise InputError("invalid paths index types")
    try:
        dates = [date.fromisoformat(value) for value in index["dates"]]
    except (TypeError, ValueError) as exc:
        raise InputError("invalid index dates") from exc
    if not dates or any(
        right - left != timedelta(days=1) for left, right in zip(dates, dates[1:])
    ):
        raise InputError("index dates must be consecutive")
    if (
        not index["sample_ids"]
        or any(type(value) is not int for value in index["sample_ids"])
        or index["sample_ids"] != list(range(len(index["sample_ids"])))
    ):
        raise InputError("invalid sample ordering")
    scope_ids = set()
    for scope in index["scopes"]:
        if not isinstance(scope, dict):
            raise InputError("invalid scope index entry")
        _required(
            scope,
            (
                "org_id",
                "scope_type",
                "scope_id",
                "scope_name",
                "status",
                "risk_ready",
                "assumptions",
                "reasons",
            ),
            "scope index entry",
        )
        if (
            not isinstance(scope["org_id"], str)
            or not scope["org_id"]
            or not isinstance(scope["scope_name"], str)
            or not scope["scope_name"]
            or scope["scope_type"] not in ("organization", "team", "project")
        ):
            raise InputError("invalid scope identity")
        if scope["scope_type"] == "organization":
            if scope["scope_id"] != scope["org_id"]:
                raise InputError("Organization scope_id must equal org_id")
        elif not isinstance(scope["scope_id"], str) or not scope["scope_id"]:
            raise InputError("Team/Project scope_id must be nonempty")
        key = (scope["org_id"], scope["scope_type"], scope["scope_id"])
        if (
            key in scope_ids
            or scope["scope_type"] not in ("organization", "team", "project")
            or scope["status"] not in SCOPE_STATUSES
            or type(scope["risk_ready"]) is not bool
            or not isinstance(scope["assumptions"], list)
            or not isinstance(scope["reasons"], list)
        ):
            raise InputError("invalid or duplicate scope index entry")
        if scope["risk_ready"] != (scope["status"] in ("complete", "conditional")):
            raise InputError("scope risk_ready contradicts status")
        scope_ids.add(key)
    series_ids = set()
    identities = set()
    for entry in index["series"]:
        if not isinstance(entry, dict):
            raise InputError("invalid series index entry")
        _required(
            entry,
            (
                "series_id",
                "org_id",
                "scope_type",
                "scope_id",
                "scope_name",
                "model",
                "status",
                "path",
                "shape",
                "dtype",
                "assumptions",
                "reasons",
            ),
            "series index entry",
        )
        if (
            not isinstance(entry["org_id"], str)
            or not entry["org_id"]
            or not isinstance(entry["model"], str)
            or not entry["model"]
            or not isinstance(entry["scope_name"], str)
            or not entry["scope_name"]
        ):
            raise InputError("invalid series identity fields")
        if entry["scope_type"] == "organization":
            if entry["scope_id"] != entry["org_id"]:
                raise InputError("Organization series scope_id must equal org_id")
        elif (
            entry["scope_type"] not in ("team", "project")
            or not isinstance(entry["scope_id"], str)
            or not entry["scope_id"]
        ):
            raise InputError("invalid series scope identity")
        ident = (
            entry["org_id"],
            entry["scope_type"],
            entry["scope_id"],
            entry["model"],
        )
        if (
            entry["series_id"] in series_ids
            or ident in identities
            or entry["series_id"] != identity(*ident)
        ):
            raise InputError("duplicate or invalid series identity")
        if (
            ident[:3] not in scope_ids
            or entry["status"] not in SERIES_STATUSES
            or not isinstance(entry["assumptions"], list)
            or not isinstance(entry["reasons"], list)
        ):
            raise InputError("invalid series identity/status")
        usable = entry["status"] in ("qualified", "inactive")
        if usable:
            if (
                not isinstance(entry["path"], str)
                or entry["shape"] != [len(dates), len(index["sample_ids"])]
                or entry["dtype"] != "float64"
                or entry["path"] not in manifest["files"]
            ):
                raise InputError("invalid usable-series array metadata")
            _inside(root, entry["path"])
        elif (
            entry["path"] is not None
            or entry["shape"] is not None
            or not entry["reasons"]
        ):
            raise InputError("invalid unavailable-series representation")
        series_ids.add(entry["series_id"])
        identities.add(ident)
    if any(
        not any(
            (e["org_id"], e["scope_type"], e["scope_id"]) == scope
            for e in index["series"]
        )
        for scope in scope_ids
    ):
        raise InputError("empty modeled scope")
    complete_scopes = 0
    for scope in index["scopes"]:
        key = (scope["org_id"], scope["scope_type"], scope["scope_id"])
        statuses = {
            entry["status"]
            for entry in index["series"]
            if (entry["org_id"], entry["scope_type"], entry["scope_id"]) == key
        }
        if statuses == {"qualified"}:
            expected = "complete"
        elif statuses == {"inactive"}:
            expected = "inactive"
        elif statuses <= {"qualified", "inactive"} and "qualified" in statuses:
            expected = "conditional"
        else:
            expected = "incomplete"
        if scope["status"] != expected or scope["risk_ready"] != (
            expected in ("complete", "conditional")
        ):
            raise InputError("scope status contradicts its series")
        complete_scopes += expected != "incomplete"
    rejected = len(index["scopes"]) - complete_scopes
    expected_overall = "partial" if rejected else "complete"
    if manifest["status"] != expected_overall or manifest["counts"] != {
        "requested_scopes": len(index["scopes"]),
        "completed_scopes": complete_scopes,
        "rejected_scopes": rejected,
    }:
        raise InputError("manifest status/counts contradict index")
    request_doc = _read_json(root / "request.json")
    facts = _run_facts(request_doc)
    if facts["dataset"] is not None:
        # `dataset.path` is recorded relative to the run directory (design §14.3);
        # an old, pre-relative absolute recording resolves to itself unchanged
        # (Path.__truediv__ with an absolute right-hand side discards `root`).
        recorded = Path(facts["dataset"]["path"])
        facts = {**facts, "dataset": {**facts["dataset"], "path": str((root / recorded).resolve())}}
    run = SavedRun(root, manifest, index, **facts)
    for entry in index["series"]:
        if entry["status"] in ("qualified", "inactive"):
            run.load_paths(entry)
    return run
