"""Atomic cost-run writer and reader (design §8). `manifest.json` is written last
(publication marker, mirrors `forecasting.artifacts`); `load_cost_run` checks schema
majors, per-file integrity, array shape/dtype/finiteness, and takes identity only
from `paths/index.json` (§8.1: "Identity comes from index.json only").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

import numpy as np

from common.model_pricing import RateSheet
from forecast.forecasting.dataset import PreparedDataset
from forecast.forecasting.schemas import canonical_bytes, slug

from .errors import ExecutionError, InputError
from .kernel import ScopeCost, SeriesCost
from .mix import ancestors_of, attribution_share
from .schemas import (
    CostDailyExpectedRow,
    CostIndex,
    CostManifest,
    CostMix,
    CostPeriodSummary,
    CostRequestDoc,
    schema_major,
)
from .summaries import daily_expected_rows, period_summary_entries

REQUIRED_FILES = {
    "paths/index.json",
    "request.json",
    "rate_sheet.json",
    "mix.json",
    "daily_expected.jsonl",
    "period_summary.json",
}
#: `<file>: expected major` (§8.1) -- a minor bump is accepted, a major bump is not.
SCHEMA_MAJORS = {
    "paths/index.json": ("1", "cost-index"),
    "request.json": ("1", "cost-request"),
    "rate_sheet.json": ("1", "rate-sheet"),
    "mix.json": ("2", "cost-mix"),
    "daily_expected.jsonl": ("2", "cost-daily-expected"),
    "period_summary.json": ("2", "cost-period-summary"),
    "scenario.yaml": ("1", "scenario"),
}


def _code_version() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unknown"


def _npy_name(scope_type: str, scope_name: str, model: str, used: set[str]) -> str:
    """§8.1: `<scope_type>-<scope_name>` and model, lowercased/slugged; `-2` on
    collision (distinct series that happen to slug identically)."""
    base = f"{slug(scope_type)}-{slug(scope_name)}__{slug(model)}"
    name = f"{base}.npy"
    counter = 2
    while name in used:
        name = f"{base}-{counter}.npy"
        counter += 1
    used.add(name)
    return name


def _mix_entries(
    dataset: PreparedDataset,
    org_id: str,
    series_costs: list[SeriesCost],
    origin: date,
    max_age_days: int,
) -> list[dict]:
    """`mix.json` entries (§8.2): one per priced series. A `scenario_mix_borrowed`
    series (§6.2) reports the borrowed `shares`, `active_days: 0`, `own_weight: 0.0`,
    and the *source* series' window -- `kernel.price_one_series` already builds that
    synthetic `MixResult` for it, so this function does not distinguish the two
    cases."""
    entries = []
    for s in series_costs:
        if s.status != "qualified" or s.mix_result is None:
            continue  # defensive: every qualified series carries a mix_result
        mr = s.mix_result
        attribution = {
            anc_type: attribution_share(
                dataset, org_id, s.scope_type, s.scope_id, anc_type, anc_id, s.model,
                origin, max_age_days,
            )
            for anc_type, anc_id in ancestors_of(dataset, org_id, s.scope_type, s.scope_id)
        }
        entries.append(
            {
                "scope_type": s.scope_type,
                "scope_name": s.scope_name,
                "model": s.model,
                "window": {
                    "start": (mr.window_start or origin).isoformat(),
                    "end": (mr.window_end or origin).isoformat(),
                },
                "active_days": mr.active_days,
                "own_weight": mr.own_weight,
                "shares": [dict(row) for row in mr.shares],
                "attribution": attribution,
            }
        )
    return entries


def _risk_ready(status: str) -> bool:
    return status in ("complete", "conditional")


def build_index(
    org_id: str,
    dates: tuple[date, ...],
    samples: int,
    scope_costs: list[ScopeCost],
    series_costs: list[SeriesCost],
    path_names: dict[tuple[str, str, str], str],
) -> dict:
    return {
        "org_id": org_id,
        "dates": [d.isoformat() for d in dates],
        "samples": samples,
        "scopes": [
            {
                "scope_type": sc.scope_type,
                "scope_name": sc.scope_name,
                "status": sc.status,
                "risk_ready": _risk_ready(sc.status),
                "assumptions": list(sc.assumptions),
                "reasons": list(sc.reasons),
            }
            for sc in scope_costs
        ],
        "series": [
            {
                "scope_type": s.scope_type,
                "scope_name": s.scope_name,
                "model": s.model,
                "status": s.status,
                "path": path_names.get((s.scope_type, s.scope_id, s.model)),
                "assumptions": list(s.assumptions),
                "reasons": list(s.reasons),
            }
            for s in series_costs
        ],
    }


def write_cost_run(
    output_dir: Path,
    *,
    request_doc: dict,
    sheet: RateSheet,
    dataset: PreparedDataset,
    org_id: str,
    dates: tuple[date, ...],
    samples: int,
    periods: tuple[dict, ...],
    series_costs: list[SeriesCost],
    scope_costs: list[ScopeCost],
    origin: date,
    max_age_days: int,
    scenario_yaml_bytes: bytes | None = None,
    report_renderer=None,
) -> None:
    """Write one cost run (§8.1). The caller (runner.py, T10) owns `--force`/refusal
    of an existing `output_dir`; this function only ever creates a brand new one and
    removes it on failure, mirroring `forecasting.runner`'s own cleanup contract.

    `report_renderer`, if given, is called as `report_renderer(run, output_dir)`
    (presentation.py's `render_report` signature) after every data artifact is on
    disk but before the manifest -- `run` is a `CostRun` built in memory from what
    was just written (not read back through `load_cost_run`, since the manifest
    does not exist yet). It writes `report.md`; neither it nor `charts/` (drawn
    later, separately, by `plot_cost`) is listed in the manifest (design §14.5),
    so a later re-render of `report.md` never requires a manifest rewrite."""
    out = Path(output_dir)
    created = False
    try:
        out.mkdir(parents=True, exist_ok=False)
        created = True
        (out / "paths").mkdir()
        files: dict[str, dict] = {}

        def _write(relative: str, data: bytes, schema: str) -> None:
            path = out / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files[relative] = {
                "schema": schema,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }

        used_names: set[str] = set()
        path_names: dict[tuple[str, str, str], str] = {}
        for s in series_costs:
            if s.status not in ("qualified", "inactive") or s.cost is None:
                continue
            name = _npy_name(s.scope_type, s.scope_name, s.model, used_names)
            relative = f"paths/{name}"
            arr = np.ascontiguousarray(s.cost, dtype=np.float64)
            np.save(out / relative, arr, allow_pickle=False)
            data = (out / relative).read_bytes()
            files[relative] = {
                "schema": "cost-path/1.0",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            path_names[(s.scope_type, s.scope_id, s.model)] = relative

        index = build_index(org_id, dates, samples, scope_costs, series_costs, path_names)
        _write("paths/index.json", canonical_bytes(index), "cost-index/1.0")
        _write("request.json", canonical_bytes(request_doc), "cost-request/1.0")
        _write("rate_sheet.json", sheet.to_json().encode("utf-8"), "rate-sheet/1.0")

        mix_entries = _mix_entries(dataset, org_id, series_costs, origin, max_age_days)
        _write("mix.json", canonical_bytes({"entries": mix_entries}), "cost-mix/2.0")

        daily_rows = daily_expected_rows(series_costs, sheet, dates)
        daily_bytes = b"".join(canonical_bytes(row) for row in daily_rows)
        _write("daily_expected.jsonl", daily_bytes, "cost-daily-expected/2.0")

        period_entries = period_summary_entries(series_costs, scope_costs, dates, periods)
        _write("period_summary.json", canonical_bytes({"entries": period_entries}), "cost-period-summary/2.0")

        if scenario_yaml_bytes is not None:
            _write("scenario.yaml", scenario_yaml_bytes, "scenario/1.0")

        if report_renderer is not None:
            in_memory_run = CostRun(
                root=out.resolve(),
                manifest={},
                request=request_doc,
                index=index,
                rate_sheet=sheet,
                mix={"entries": mix_entries},
                period_summary={"entries": period_entries},
                _daily_expected=daily_rows,
            )
            # design §14.5: report.md/charts/ are presentation, regenerable by
            # `plot_cost` re-rendering the report -- neither is listed in the
            # manifest, so a later re-render never needs a manifest rewrite.
            report_renderer(in_memory_run, out)

        status = "complete" if all(sc.status != "incomplete" for sc in scope_costs) else "incomplete"
        manifest = {
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "code_version": _code_version(),
            "files": files,
        }
        temporary = out / ".manifest.tmp"
        temporary.write_bytes(canonical_bytes(manifest))
        os.replace(temporary, out / "manifest.json")
    except InputError:
        if created:
            import shutil

            shutil.rmtree(out, ignore_errors=True)
        raise
    except Exception as exc:
        if created:
            import shutil

            shutil.rmtree(out, ignore_errors=True)
        if isinstance(exc, ExecutionError):
            raise
        raise ExecutionError(f"cost write failed: {type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                digest.update(block)
    except OSError as exc:
        raise ExecutionError(f"cannot read artifact: {path.name}") from exc
    return digest.hexdigest()


def _inside(root: Path, relative: str) -> Path:
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


def _read_json_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read artifact: {path.name}") from exc


def _check_schema(relative: str, schema_id: str) -> None:
    expected = SCHEMA_MAJORS.get(relative)
    if expected is None:
        return
    expected_major, expected_name = expected
    name, _, version = schema_id.rpartition("/")
    if name != expected_name or schema_major(schema_id) != expected_major:
        raise InputError(f"unsupported schema for {relative}: {schema_id!r}")


@dataclass
class CostRun:
    """A completed cost run (§3.2). `request`, `index`, `rate_sheet`, `mix`, and
    `period_summary` are parsed eagerly; `daily_expected` is parsed lazily (only on
    first access) so opening a run stays cheap for a large one."""

    root: Path
    manifest: dict
    request: dict
    index: dict
    rate_sheet: RateSheet
    mix: dict
    period_summary: dict
    _daily_expected: list[dict] | None = field(default=None, repr=False, compare=False)

    @property
    def daily_expected(self) -> list[dict]:
        if self._daily_expected is None:
            path = _inside(self.root, "daily_expected.jsonl")
            rows = []
            for number, line in enumerate(path.read_bytes().splitlines(), 1):
                if not line.strip():
                    raise InputError(f"blank daily_expected row at line {number}")
                try:
                    CostDailyExpectedRow.model_validate_json(line)
                except Exception as exc:  # pydantic ValidationError
                    raise InputError(f"invalid daily_expected row at line {number}") from exc
                rows.append(json.loads(line))
            self._daily_expected = rows
        return self._daily_expected

    def paths(self, entry: dict) -> np.ndarray:
        """Mirrors `forecasting.artifacts.SavedRun.load_paths`: mmap, dtype/shape/
        finiteness/nonnegativity, inactive-is-all-zero -- USD instead of tokens."""
        if not any(entry is item for item in self.index["series"]):
            raise InputError("series entry does not belong to this run")
        if entry["status"] not in ("qualified", "inactive"):
            raise InputError("series has no usable paths")
        path = _inside(self.root, entry["path"])
        try:
            array = np.load(path, mmap_mode="r", allow_pickle=False)
        except (OSError, ValueError, TypeError) as exc:
            raise InputError("invalid NumPy path artifact") from exc
        expected = [len(self.index["dates"]), self.index["samples"]]
        if array.dtype != np.dtype("float64") or not array.flags.c_contiguous or list(array.shape) != expected:
            raise InputError("path array contract mismatch")
        for start in range(0, array.shape[0], 32):
            block = np.asarray(array[start : start + 32])
            if not np.isfinite(block).all() or np.any(block < 0):
                raise InputError("path array contains nonfinite or negative values")
            if entry["status"] == "inactive" and np.any(block != 0):
                raise InputError("inactive path array must be all zero")
        return array

    def scope_paths(self, scope_entry: dict) -> np.ndarray | None:
        """Sum of the scope's series paths; `None` for an `incomplete` scope (§8.2:
        no scope total is ever computed for one)."""
        if scope_entry["status"] == "incomplete":
            return None
        total = np.zeros((len(self.index["dates"]), self.index["samples"]), dtype=np.float64)
        for entry in self.index["series"]:
            if (
                entry["scope_type"] == scope_entry["scope_type"]
                and entry["scope_name"] == scope_entry["scope_name"]
                and entry["path"]
            ):
                total = total + self.paths(entry)
        return total


def load_cost_run(run_dir: Path) -> CostRun:
    root = Path(run_dir).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise InputError("run has no completed manifest")
    try:
        manifest_raw = json.loads(_read_json_bytes(manifest_path))
    except json.JSONDecodeError as exc:
        raise InputError("invalid JSON artifact: manifest.json") from exc
    try:
        manifest = CostManifest.model_validate(manifest_raw)
    except Exception as exc:  # pydantic ValidationError
        raise InputError(f"invalid manifest: {exc}") from exc

    if not REQUIRED_FILES <= set(manifest.files):
        raise InputError("manifest omits required cost artifacts")
    for relative, meta in manifest.files.items():
        # report.md/charts/ are never listed here (design §14.5: presentation,
        # regenerable by `plot_cost` re-rendering the report) -- nothing to
        # tolerate; every listed file is a real, checked data artifact.
        path = _inside(root, relative)
        if path.stat().st_size != meta.bytes or _sha(path) != meta.sha256:
            raise InputError(f"artifact checksum mismatch: {relative}")
        _check_schema(relative, meta.schema_)

    def _load(relative: str, model_cls):
        raw = json.loads(_read_json_bytes(root / relative))
        try:
            return model_cls.model_validate(raw), raw
        except Exception as exc:  # pydantic ValidationError
            raise InputError(f"invalid {relative}: {exc}") from exc

    _, request_raw = _load("request.json", CostRequestDoc)
    index_model, index_raw = _load("paths/index.json", CostIndex)
    _, mix_raw = _load("mix.json", CostMix)
    _, period_summary_raw = _load("period_summary.json", CostPeriodSummary)
    try:
        rate_sheet = RateSheet.model_validate_json(_read_json_bytes(root / "rate_sheet.json"))
    except Exception as exc:  # pydantic ValidationError
        raise InputError(f"invalid rate_sheet.json: {exc}") from exc

    # Identity/shape cross-checks (§8.1: identity comes from index.json only).
    dates = index_raw["dates"]
    if not dates or len(set(dates)) != len(dates):
        raise InputError("invalid or duplicate index dates")
    identities = set()
    for entry in index_raw["series"]:
        identity = (entry["scope_type"], entry["scope_name"], entry["model"])
        if identity in identities:
            raise InputError("duplicate series identity in index")
        identities.add(identity)
        usable = entry["status"] in ("qualified", "inactive")
        if usable and not entry["path"]:
            raise InputError("usable series missing a path")
        if not usable and entry["path"] is not None:
            raise InputError("unusable series must not carry a path")

    run = CostRun(
        root=root,
        manifest=manifest_raw,
        request=request_raw,
        index=index_raw,
        rate_sheet=rate_sheet,
        mix=mix_raw,
        period_summary=period_summary_raw,
    )
    for entry in index_raw["series"]:
        if entry["status"] in ("qualified", "inactive"):
            run.paths(entry)
    return run
