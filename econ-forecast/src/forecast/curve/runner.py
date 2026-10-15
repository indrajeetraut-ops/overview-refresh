"""Curve-artifact generation: deterministic shape config -> saved artifact.

Adapts (copies and trims, does not import) the `_operation`/`_check_output`/`_finish`/
`_completed` scaffolding pattern from `forecast.synthetic.runner`, with a curve-local
`CurveResult` and curve-specific manifest fields (no `org_id`, `start_date`, etc.).
Reuses `DatasetWriter`/`sha256` from `forecast.artifact_io`.
"""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import yaml
from common.msging.log import get_logger
from forecast.artifact_io import DatasetWriter, sha256
from forecast.synthetic.errors import ArtifactError as _WriterArtifactError
from forecast.synthetic.errors import InputDataError as _WriterInputDataError

from .config import CurveConfig
from .errors import ArtifactError, InputDataError
from .model import evaluate_curve, resolve_anchors
from .models import CurvePoint, CurveResult
from .preview import render_html, render_png
from .reporting import render_report

GENERATOR_VERSION = "1.0.0"
DEFAULT_SAMPLE_COUNT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config_bytes(config: CurveConfig) -> bytes:
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True).encode()


def config_digest(config: CurveConfig) -> str:
    """Full sha256 hex digest over the resolved config bytes (provenance)."""
    return hashlib.sha256(_config_bytes(config)).hexdigest()


def config_hash(config: CurveConfig) -> str:
    """Short, stable directory-naming id derived from the full config digest."""
    return config_digest(config)[:16]


@contextmanager
def _operation(run_id, log):
    state = {"run_id": run_id or str(uuid.uuid4()), "stage": "validation",
             "created_at": _now(), "started": perf_counter()}
    state["log"] = (log or get_logger(__name__)).bind(
        module=__name__, operation="curve-create", run_id=state["run_id"])
    try:
        yield state
    except Exception as exc:
        if not hasattr(exc, "stage"):
            exc.stage = state["stage"]
        if not hasattr(exc, "run_id"):
            exc.run_id = state["run_id"]
        if not hasattr(exc, "cleanup_outcome"):
            exc.cleanup_outcome = "not-created"
        raise


def _completed(state, result):
    state["log"].info("curve.run_completed", output_dir=str(result.output_dir),
                      duration_ms=round((perf_counter() - state["started"]) * 1000, 3))
    return result


def _is_complete_artifact(directory: Path) -> bool:
    """True only when `directory` holds a `manifest.json` recording a completed run."""
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("status") == "complete"


def _force_clear_output(target: Path, *, artifacts_root: Path, log=None) -> None:
    """Remove the computed `<name>/<hash>/` leaf directory before a forced recreate.

    Refuses (raising `ArtifactError`, the same type the non-force path raises) unless
    `target` is a plain directory and a genuine descendant of `artifacts_root` -- never
    the root itself, never an ancestor of it, and never `/`, the caller's home directory,
    or the current working directory. Deletion is confined to exactly this one leaf;
    other curves under the same `artifacts_root` are never touched.
    """
    if target.exists() and not target.is_dir():
        raise ArtifactError(f"output path exists and is not a directory; refusing --force: {target}")
    guarded = {Path("/"), Path.home().resolve(), Path.cwd().resolve()}
    if target in guarded:
        raise ArtifactError(f"refusing to --force a protected system path: {target}")
    if target == artifacts_root or artifacts_root not in target.parents:
        raise ArtifactError(
            f"refusing to --force a path outside the requested curve artifact leaf: {target}")
    if log:
        log.info("curve.force_recreate", output_dir=str(target))
    shutil.rmtree(target)


def run_curve(config: CurveConfig, artifacts_root: Path, *, log=None, run_id=None, force=False) -> CurveResult | None:
    """Generate the saved curve artifact for `config` under `artifacts_root`.

    The output directory is `<artifacts_root>/<config.name>/<config-hash>/`; the hash keys
    the directory on the *resolved* config bytes (including any `--preview-days` override
    the caller has already folded into `config.preview.days`), so an identical effective
    config always maps to the same directory (idempotent). If that directory already
    exists AND holds a complete `manifest.json`, this is a friendly no-op: nothing is read
    or written, and `None` is returned (the caller — `cli_curve.py` — reports the existing
    path and exits 0) -- unless `force` is set, in which case the leaf is always cleared
    and rebuilt (letting a renderer/template change take effect even when the config bytes
    are unchanged). If the directory exists but is incomplete or orphaned (no
    `manifest.json`, or one not recording `status: complete` — e.g. left behind by a
    hard-killed process), this raises `ArtifactError` rather than silently treating it as
    done -- unless `force` is set, in which case it is cleared and rebuilt too.
    """
    with _operation(run_id, log) as state:
        digest = config_digest(config)
        short_digest = digest[:16]
        artifacts_root_resolved = Path(artifacts_root).resolve()
        target = (artifacts_root_resolved / config.name / short_digest).resolve()
        if target.exists() or target.is_symlink():
            if force:
                _force_clear_output(target, artifacts_root=artifacts_root_resolved, log=state["log"])
            elif _is_complete_artifact(target):
                state["log"].info("curve.noop_existing_artifact", output_dir=str(target))
                return None
            else:
                raise ArtifactError(f"incomplete prior artifact at {target}; remove it and re-run")

        state["stage"] = "sampling"
        requested_days = config.preview.days
        sample_count = requested_days if requested_days else DEFAULT_SAMPLE_COUNT
        day_axis = requested_days is not None

        try:
            resolved = resolve_anchors(config.anchors, config.axis.start, config.axis.end)
            levels = evaluate_curve(resolved, sample_count)
        except ValueError as exc:
            # Config validation already exercises this path (see config.py:resolvable), so
            # reaching here should be unreachable; translate defensively rather than let a
            # bare ValueError escape the curve error hierarchy.
            raise InputDataError(str(exc)) from exc
        if min(levels) < 0.0:
            # Defensive belt-and-suspenders: cannot trip for PCHIP with anchors already
            # validated nonnegative (see design doc); guards a future non-PCHIP `interp`.
            raise InputDataError("sampled curve level fell below zero")

        points = [CurvePoint(pos=pos, level=level) for pos, level in
                  zip(_sample_positions(sample_count), levels)]

        state["stage"] = "artifacts"
        curve_log = state["log"].bind(module="forecast.curve.runner")
        try:
            with DatasetWriter(target, "curve", log=curve_log) as writer:
                writer.write_records("curve.jsonl", points)
                config_bytes = _config_bytes(config)
                writer.write_bytes("config.yaml", config_bytes)

                html_path = writer.output_dir / "preview.html"
                png_path = writer.output_dir / "preview.png"
                render_html(config, resolved, levels, html_path, day_axis=day_axis)
                render_png(config, resolved, levels, png_path, day_axis=day_axis)

                summary = {
                    "anchor_count": len(config.anchors),
                    "sample_count": sample_count,
                    "min_level": min(levels),
                    "max_level": max(levels),
                }
                report_bytes = render_report(
                    config, resolved, levels, run_id=state["run_id"],
                    created_at=state["created_at"], sample_count=sample_count,
                    day_axis=day_axis).encode()
                writer.write_bytes("report.md", report_bytes)

                state["stage"] = "manifest"
                files = {
                    **writer.files,
                    "config.yaml": {"sha256": sha256(writer.output_dir / "config.yaml")},
                    "preview.html": {"sha256": sha256(html_path)},
                    "preview.png": {"sha256": sha256(png_path)},
                }
                manifest = {
                    "schema_version": 1,
                    "generator_version": GENERATOR_VERSION,
                    "operation": "curve-create",
                    "status": "complete",
                    "run_id": state["run_id"],
                    "created_at": state["created_at"],
                    "name": config.name,
                    "config_sha256": digest,
                    "interp": config.interp,
                    "axis": {"start": config.axis.start, "end": config.axis.end},
                    "anchor_count": len(config.anchors),
                    "sample_count": sample_count,
                    "min_level": min(levels),
                    "max_level": max(levels),
                    "versions": {
                        "python": platform.python_version(),
                        "scipy": version("scipy"), "pydantic": version("pydantic"),
                        "pyyaml": version("PyYAML"), "plotly": version("plotly"),
                        "matplotlib": version("matplotlib"),
                    },
                    "files": files,
                    "report": {"path": "report.md",
                              "sha256": sha256(writer.output_dir / "report.md")},
                }
                writer.manifest(manifest)
                result = CurveResult(
                    output_dir=writer.output_dir, run_id=state["run_id"],
                    files=sorted({*files, "report.md", "manifest.json"}),
                    summary=summary, report_path=writer.output_dir / "report.md")
        except (_WriterArtifactError, _WriterInputDataError) as exc:
            error = ArtifactError(str(exc))
            error.cleanup_outcome = getattr(exc, "cleanup_outcome", "unknown")
            raise error from exc
        return _completed(state, result)


def _sample_positions(n: int) -> list[float]:
    if n <= 1:
        return [0.0]
    return [index / (n - 1) for index in range(n)]
