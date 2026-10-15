"""End-to-end forecast coordination and completed-artifact publication."""

from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib.metadata import version
import json
import os
import platform
from pathlib import Path
import shutil
import threading
import time
import uuid

import numpy as np
import psutil
from threadpoolctl import threadpool_limits
import yaml

from common.msging.log import get_logger

from .allocation import TOKEN_TYPES, allocation, quantiles
from .artifacts import load_run
from .config import load_config
from .dataset import load_dataset
from .errors import ExecutionError, InputError
from .ets import fit_ets
from .history import build_histories
from .known_events import apply_known_events, team_names_by_user, validate_known_events
from .paths import simulate_joint
from .request import ForecastRequest, resolve_window
from .runtime import ResourceMonitor
from .schemas import canonical_bytes, identity, path_name


@dataclass
class ForecastResult:
    status: str
    exit_code: int
    requested_scopes: int
    completed_scopes: int
    rejected_scopes: int
    output_dir: Path | None
    report_path: Path | None
    diagnostics: dict
    origin: str | None = None

    def to_dict(self):
        value = asdict(self)
        value["output_dir"] = str(self.output_dir) if self.output_dir else None
        value["report_path"] = str(self.report_path) if self.report_path else None
        return value


def _selected(cfg, data):
    result = []
    seen = set()
    for requested in cfg.scopes:
        org = requested.org_id or cfg.default_org_id
        if requested.type == "organization":
            sid = org
        else:
            matches = [scope for scope in data.scopes.values()
                       if scope.org_id == org and scope.scope_type == requested.type
                       and scope.name == requested.name]
            if not matches:
                raise InputError(f"unknown {requested.type} name {requested.name!r} in Organization {org!r}; use a saved scope name")
            if len(matches) != 1:
                raise InputError(f"ambiguous {requested.type} name {requested.name!r} in Organization {org!r}")
            sid = matches[0].scope_id
        key = (org, requested.type, sid)
        if key in seen:
            raise InputError("duplicate requested scope")
        if key not in data.scopes:
            raise InputError(f"unknown requested Organization scope: {key}")
        seen.add(key)
        result.append(data.scopes[key])
    return result


def _force_clear_output(path: Path, *, protected: tuple[Path, ...] = (), log=None) -> None:
    """Remove an existing output path before a forced recreate.

    Refuses (raising `InputError`, the same type the non-force path raises) unless
    `path` resolves to a plain directory that is not `/`, the caller's home directory,
    the current working directory, and is neither equal to nor an ancestor of any path
    in `protected` (this run's own input dataset). Deletion is confined to exactly the
    resolved directory; nothing outside it is ever touched.
    """
    if path.is_symlink():
        raise InputError(f"output path is a symlink; refusing --force: {path}")
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise InputError(f"output path exists and is not a directory; refusing --force: {resolved}")
    guarded = {Path("/"), Path.home().resolve(), Path.cwd().resolve()}
    if resolved in guarded:
        raise InputError(f"refusing to --force a protected system path: {resolved}")
    for raw in protected:
        try:
            guard = Path(raw).resolve()
        except OSError:
            continue
        if resolved == guard or resolved in guard.parents:
            raise InputError(
                f"refusing to --force an output path that is, or contains, a required input: {resolved}")
    if log:
        log.info("forecast.force_recreate", output_dir=str(resolved))
    shutil.rmtree(resolved)


def _json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def _file_meta(root, path, rows=None):
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    d = {"sha256": h, "bytes": path.stat().st_size}
    if rows is not None:
        d["rows"] = rows
    return d


def _scope_status(entries):
    statuses = {x["status"] for x in entries}
    if statuses == {"qualified"}:
        return "complete", True
    if statuses <= {"qualified", "inactive"} and "qualified" in statuses:
        return "conditional", True
    if statuses == {"inactive"}:
        return "inactive", False
    return "incomplete", False


def _diag(history, sid, recent, attempts=None):
    return {
        "series_id": sid,
        "history_start": history.dates[0].isoformat() if history.dates else None,
        "history_end": history.dates[-1].isoformat() if history.dates else None,
        "covered_days": len(history.dates),
        "startup_start": (
            history.startup_start.isoformat() if history.startup_start else None
        ),
        "startup_source": history.startup_source,
        "last_active_date": (
            history.last_active.isoformat() if history.last_active else None
        ),
        "active_day_average_tokens": (
            float(np.mean(history.values[history.values > 0]))
            if np.any(history.values > 0)
            else 0.0
        ),
        "recent_total": recent,
        "fit_attempts": attempts or [],
        "sampling_start": None,
        "sampling_end": None,
        "sampling_days": None,
        "correction": None,
        "assumptions": [],
        "reasons": list(history.reasons),
    }


def _run_forecast(
    config_path: Path,
    request: ForecastRequest,
    output_dir: Path | None = None,
    *,
    validate_only: bool = False,
    monitor: ResourceMonitor | None = None,
    force: bool = False,
) -> ForecastResult:
    started = time.perf_counter()
    stages = {}
    run_id = str(uuid.uuid4())
    log = get_logger(__name__).bind(run_id=run_id, module=__name__)
    mark = time.perf_counter()
    cfg = load_config(config_path)
    if request.origin is not None:
        window = resolve_window(request)
    stages["config"] = time.perf_counter() - mark
    log.info("forecast.config.validated", elapsed_seconds=stages["config"])
    if not validate_only and output_dir is None:
        raise InputError("output_dir is required unless validate_only")
    if output_dir is not None and (
        Path(output_dir).exists() or Path(output_dir).is_symlink()
    ):
        if not force:
            raise InputError("output path already exists")
        _force_clear_output(Path(output_dir), protected=(cfg.input.dataset_dir,), log=log)
    mark = time.perf_counter()
    data = load_dataset(cfg.input.dataset_dir)
    # Recorded so a cost run can verify the same dataset was priced (cost design
    # §3.1); independent of PreparedDataset.hashes, which covers only usage/coverage.
    dataset_manifest_sha256 = hashlib.sha256(
        (Path(cfg.input.dataset_dir).resolve() / "manifest.json").read_bytes()
    ).hexdigest()
    team_by_user = team_names_by_user(data)
    defaulted_origin = request.origin is None
    if defaulted_origin:
        request = ForecastRequest(data.end, request.unit, request.offset, request.periods)
        window = resolve_window(request)
    log.info("forecast.origin.resolved", origin=request.origin.isoformat(),
             source="dataset_end" if defaulted_origin else "explicit")
    # Known-events name/date cross-checks need the loaded dataset and resolved window,
    # so they cannot live in config.py's static validation; they must also run on the
    # validate_only path (that path exists precisely to surface config errors early).
    validate_known_events(cfg.known_events, data, cfg.default_org_id, window.requested_dates)
    selected = _selected(cfg, data)
    histories, not_applicable = build_histories(
        data,
        selected,
        request.origin,
        lookback=cfg.history.lookback_days,
        minimum=cfg.history.minimum_days,
        active_days=cfg.history.minimum_active_days,
        allocation_days=cfg.allocation.lookback_days,
    )
    stages["input"] = time.perf_counter() - mark
    log.info(
        "forecast.input.validated",
        elapsed_seconds=stages["input"],
        scope_count=len(selected),
        series_count=len(histories),
    )
    log.info(
        "forecast.history.classified",
        counts={
            status: sum(h.status == status for h in histories)
            for status in sorted({h.status for h in histories})
        },
    )
    if len(histories) > 1000:
        raise InputError("selected scope/model count exceeds 1000")
    cells = len(histories) * len(window.internal_dates) * cfg.uncertainty.paths
    if cells * 8 > 512 * 1024**2:
        raise InputError("path workload exceeds 512 MiB")
    if any(not any(h.scope == s for h in histories) for s in selected):
        raise InputError("requested scope has no applicable models")
    base_diag = {
        "schema_version": 1,
        "series": [],
        "runtime": {
            "wall_seconds": 0.0,
            "stage_seconds": stages,
            "peak_rss_bytes": psutil.Process().memory_info().rss,
            "memory_measurement": "process RSS sample at completion",
            "output_bytes": 0,
            "workers": cfg.runtime.workers,
            "numerical_threads": 1,
            "workload": {
                "series": len(histories),
                "internal_days": len(window.internal_dates),
                "requested_days": len(window.requested_dates),
                "paths": cfg.uncertainty.paths,
            },
            "platform": platform.platform(),
            "versions": {},
        },
        "not_applicable_models": not_applicable,
        "known_events": [],
    }
    if validate_only:
        base_diag["numerical_eligibility"] = "not_run"
        invalid_statuses = {"insufficient_history", "insufficient_startup"}
        invalid_scopes = 0
        for scope in selected:
            scoped = [history for history in histories if history.scope == scope]
            rejected = [
                history for history in scoped if history.status in invalid_statuses
            ]
            if rejected:
                invalid_scopes += 1
            for history in scoped:
                series_id = identity(
                    history.scope.org_id,
                    history.scope.scope_type,
                    history.scope.scope_id,
                    history.model,
                )
                _, _, recent = allocation(history)
                base_diag["series"].append(_diag(history, series_id, recent))
        status = "invalid" if invalid_scopes else "valid"
        if monitor is not None:
            measured = monitor.finish()
            measured["stage_seconds"] = stages
            base_diag["runtime"].update(measured)
        else:
            base_diag["runtime"]["wall_seconds"] = time.perf_counter() - started
        log.info(
            "forecast.validation.completed",
            status=status,
            rejected_scopes=invalid_scopes,
        )
        return ForecastResult(
            status,
            2 if invalid_scopes else 0,
            len(selected),
            len(selected) - invalid_scopes,
            invalid_scopes,
            None,
            None,
            base_diag,
            request.origin.isoformat(),
        )
    out = Path(output_dir).absolute()
    created = False
    try:
        out.mkdir(parents=True, exist_ok=False)
        created = True
        (out / "paths").mkdir()
        internal = list(window.internal_dates)
        requested_idx = np.asarray([internal.index(d) for d in window.requested_dates])
        items = []
        mark = time.perf_counter()
        worker_stage_lock = threading.Lock()
        # These two values sum work across workers and may exceed run wall time.
        worker_stages = {"preparation": 0.0, "simulation": 0.0}
        # Dedup key is (level, name): the factor for a given key is a pure function of
        # it (see known_events.py), so entries from different scopes agree exactly.
        known_event_provenance: dict[tuple[str, str], dict] = {}

        def process_scope(scope):
            preparation_started = time.perf_counter()
            scoped_items = []
            for h in [value for value in histories if value.scope == scope]:
                sid = identity(
                    h.scope.org_id, h.scope.scope_type, h.scope.scope_id, h.model
                )
                shares, tokens, recent = allocation(h)
                item = {
                    "history": h,
                    "series_id": sid,
                    "status": h.status,
                    "reasons": list(h.reasons),
                    "shares": shares,
                    "historical_tokens": tokens,
                    "recent": recent,
                    "requested_idx": requested_idx,
                    "fit": None,
                    "attempts": [],
                    "paths": None,
                }
                if h.status == "qualified":
                    fit, attempts = fit_ets(
                        h.values,
                        len(internal),
                        seasonal=cfg.model.seasonal,
                        trend=cfg.model.trend,
                    )
                    item["fit"] = fit
                    item["attempts"] = attempts
                    if fit is None:
                        item["status"] = "sampling_failed"
                        item["reasons"] = [
                            "all allowed ETS configurations failed numerical acceptance"
                        ]
                elif h.status == "inactive":
                    item["paths"] = np.zeros(
                        (len(window.requested_dates), cfg.uncertainty.paths),
                        dtype=np.float64,
                    )
                scoped_items.append(item)
            preparation_elapsed = time.perf_counter() - preparation_started
            simulation_started = time.perf_counter()
            simulate_joint(
                scoped_items,
                internal,
                cfg.uncertainty.paths,
                cfg.uncertainty.seed,
                cfg.uncertainty.max_negative_correction_ratio,
            )
            simulation_elapsed = time.perf_counter() - simulation_started
            # Post-simulation, pre-persistence: the dated known-event overlay (design
            # §7). Mutates qualified/inactive items' paths in place; zero (inactive)
            # paths stay zero under any multiplier.
            applied = apply_known_events(
                scoped_items,
                scope,
                data,
                cfg.known_events,
                team_by_user,
                window.requested_dates,
                cfg.uncertainty.seed,
            )
            with worker_stage_lock:
                worker_stages["preparation"] += preparation_elapsed
                worker_stages["simulation"] += simulation_elapsed
                for entry in applied:
                    known_event_provenance[(entry["level"], entry["name"])] = entry
            for item in scoped_items:
                item["fit"] = None
                if item["paths"] is not None:
                    history = item["history"]
                    name = path_name(
                        history.scope.org_id,
                        history.scope.scope_type,
                        history.scope.scope_id,
                        history.scope.name,
                        history.model,
                    )
                    relative = f"paths/{name}"
                    np.save(
                        out / relative,
                        np.ascontiguousarray(item["paths"], dtype=np.float64),
                        allow_pickle=False,
                    )
                    item["prewritten_path"] = relative
                    item["paths"] = np.load(
                        out / relative, mmap_mode="r", allow_pickle=False
                    )
            return scoped_items

        with threadpool_limits(limits=1):
            if cfg.runtime.workers == 1:
                groups = [process_scope(scope) for scope in selected]
            else:
                with ThreadPoolExecutor(
                    max_workers=cfg.runtime.workers, thread_name_prefix="forecast-scope"
                ) as executor:
                    groups = list(executor.map(process_scope, selected))
        for group in groups:
            items.extend(group)
        base_diag["known_events"] = sorted(
            known_event_provenance.values(), key=lambda e: (e["level"], e["name"])
        )
        for entry in base_diag["known_events"]:
            log.info("forecast.known_events.applied", **entry)
        stages["model_and_paths"] = time.perf_counter() - mark
        stages.update(worker_stages)
        base_diag["runtime"]["stage_measurement"] = (
            "preparation and simulation are summed worker wall time; other stages are coordinator wall time"
        )
        log.info(
            "forecast.preparation.completed",
            elapsed_seconds=stages["preparation"],
            workers=cfg.runtime.workers,
            series_count=len(items),
        )
        log.info(
            "forecast.simulation.completed",
            elapsed_seconds=stages["simulation"],
            qualified_count=sum(x["status"] == "qualified" for x in items),
            failed_count=sum(
                x["status"] not in ("qualified", "inactive") for x in items
            ),
        )
        index = {
            "schema_version": 1,
            "dates": [d.isoformat() for d in window.requested_dates],
            "sample_ids": list(range(cfg.uncertainty.paths)),
            "scopes": [],
            "series": [],
        }
        allocations = []
        daily = []
        summaries = []
        diag_series = []
        summary_started = time.perf_counter()
        for item in items:
            h = item["history"]
            status = item["status"]
            assumptions = (
                ["continued_inactivity"]
                if status == "inactive"
                else (
                    [
                        "uncalibrated_model_estimate",
                        "fixed_allocation_shares",
                        "common_residual_window",
                    ]
                    + (["known_event_applied"] if item.get("known_event_applied") else [])
                    + (
                        ["excess_negative_correction"]
                        if item.get("correction") and not item["correction"]["passed"]
                        else []
                    )
                    if status == "qualified"
                    else []
                )
            )
            entry = {
                "series_id": item["series_id"],
                "org_id": h.scope.org_id,
                "scope_type": h.scope.scope_type,
                "scope_id": h.scope.scope_id,
                "scope_name": h.scope.name,
                "model": h.model,
                "status": status,
                "path": None,
                "shape": None,
                "dtype": "float64",
                "assumptions": assumptions,
                "reasons": item["reasons"],
            }
            if status in ("qualified", "inactive"):
                rel = item["prewritten_path"]
                arr = item["paths"]
                entry["path"] = rel
                entry["shape"] = list(arr.shape)
            index["series"].append(entry)
            allocations.append(
                {
                    "series_id": item["series_id"],
                    "status": status,
                    "window_start": (
                        h.category_window_start.isoformat()
                        if h.category_window_start
                        else None
                    ),
                    "window_end": h.dates[-1].isoformat() if h.dates else None,
                    "historical_total": item["recent"],
                    "shares": item["shares"] if status == "qualified" else [],
                    "historical_tokens": (
                        item["historical_tokens"] if status == "qualified" else []
                    ),
                    "assumptions": assumptions,
                }
            )
            diagnostic = _diag(h, item["series_id"], item["recent"], item.get("attempts"))
            diagnostic["reasons"] = item["reasons"]
            diagnostic["assumptions"] = assumptions
            if item.get("sampling"):
                diagnostic.update(
                    sampling_start=item["sampling"]["start"].isoformat(),
                    sampling_end=item["sampling"]["end"].isoformat(),
                    sampling_days=item["sampling"]["days"],
                )
            diagnostic["correction"] = item.get("correction")
            diag_series.append(diagnostic)
            if status in ("qualified", "inactive"):
                arr = item["paths"]
                means = np.mean(arr, axis=1)
                if status == "qualified":
                    for day, total in zip(window.requested_dates, means):
                        for share in item["shares"]:
                            cats = {
                                c: float(total * share[c])
                                for c in TOKEN_TYPES
                            }
                            daily.append(
                                {
                                    "schema_version": 2,
                                    "date": day.isoformat(),
                                    "org_id": h.scope.org_id,
                                    "scope_type": h.scope.scope_type,
                                    "scope_id": h.scope.scope_id,
                                    "model": h.model,
                                    "agent_type": share["agent_type"],
                                    "tokens": cats,
                                    "total_tokens": float(sum(cats.values())),
                                    "status": status,
                                }
                            )
                else:
                    for day in window.requested_dates:
                        daily.append(
                            {
                                "schema_version": 2,
                                "date": day.isoformat(),
                                "org_id": h.scope.org_id,
                                "scope_type": h.scope.scope_type,
                                "scope_id": h.scope.scope_id,
                                "model": h.model,
                                "agent_type": None,
                                "tokens": {c: 0.0 for c in TOKEN_TYPES},
                                "total_tokens": 0.0,
                                "status": status,
                            }
                        )
                ranges = [
                    ("requested", window.requested_dates[0], window.requested_dates[-1])
                ] + [("calendar", a, b) for a, b in window.periods]
                for typ, a, b in ranges:
                    mask = [a <= d <= b for d in window.requested_dates]
                    mean, p10, p50, p90 = quantiles(arr[mask])
                    summaries.append(
                        {
                            "org_id": h.scope.org_id,
                            "scope_type": h.scope.scope_type,
                            "scope_id": h.scope.scope_id,
                            "model": h.model,
                            "start": a.isoformat(),
                            "end": b.isoformat(),
                            "period_type": typ,
                            "status": status,
                            "expected_tokens": mean,
                            "p10": p10,
                            "p50": p50,
                            "p90": p90,
                            "assumptions": assumptions,
                        }
                    )
                item["paths"] = None
        for scope in selected:
            entries = [
                e
                for e in index["series"]
                if (e["org_id"], e["scope_type"], e["scope_id"])
                == (scope.org_id, scope.scope_type, scope.scope_id)
            ]
            state, risk = _scope_status(entries)
            s_assumptions = sorted(set(a for e in entries for a in e["assumptions"]))
            reasons = [r for e in entries for r in e["reasons"]]
            index["scopes"].append(
                {
                    "org_id": scope.org_id,
                    "scope_type": scope.scope_type,
                    "scope_id": scope.scope_id,
                    "scope_name": scope.name,
                    "status": state,
                    "risk_ready": risk,
                    "assumptions": s_assumptions,
                    "reasons": reasons,
                }
            )
            if state != "incomplete":
                agg = np.zeros(
                    (len(window.requested_dates), cfg.uncertainty.paths),
                    dtype=np.float64,
                )
                for entry in entries:
                    if entry["path"] is not None:
                        agg += np.load(
                            out / entry["path"], mmap_mode="r", allow_pickle=False
                        )
                for typ, a, b in [
                    ("requested", window.requested_dates[0], window.requested_dates[-1])
                ] + [("calendar", a, b) for a, b in window.periods]:
                    mask = [a <= d <= b for d in window.requested_dates]
                    mean, p10, p50, p90 = quantiles(agg[mask])
                    summaries.append(
                        {
                            "org_id": scope.org_id,
                            "scope_type": scope.scope_type,
                            "scope_id": scope.scope_id,
                            "model": None,
                            "start": a.isoformat(),
                            "end": b.isoformat(),
                            "period_type": typ,
                            "status": state,
                            "expected_tokens": mean,
                            "p10": p10,
                            "p50": p50,
                            "p90": p90,
                            "assumptions": s_assumptions,
                        }
                    )
                del agg
        stages["summaries"] = time.perf_counter() - summary_started
        overall = (
            "partial"
            if any(s["status"] == "incomplete" for s in index["scopes"])
            else "complete"
        )
        completed = sum(s["status"] != "incomplete" for s in index["scopes"])
        rejected = len(selected) - completed
        request_doc = {
            "schema_version": 1,
            "origin": request.origin.isoformat(),
            "unit": request.unit,
            "offset": request.offset,
            "periods": request.periods,
            "requested_start": window.requested_dates[0].isoformat(),
            "requested_end": window.requested_dates[-1].isoformat(),
            "internal_start": window.internal_dates[0].isoformat(),
            "internal_end": window.internal_dates[-1].isoformat(),
            "dates": [d.isoformat() for d in window.requested_dates],
            "periods_windows": [
                {"start": a.isoformat(), "end": b.isoformat()}
                for a, b in window.periods
            ],
            # Cost design §3.1/§8.2: requested + calendar periods in one typed list,
            # distinct from "periods" above (that key is the request's period *count*).
            "period_ranges": [
                {
                    "type": "requested",
                    "start": window.requested_dates[0].isoformat(),
                    "end": window.requested_dates[-1].isoformat(),
                },
            ]
            + [
                {"type": "calendar", "start": a.isoformat(), "end": b.isoformat()}
                for a, b in window.periods
            ],
            "dataset": {
                # Relative to the run directory (design §14.3) so a workspace + its
                # dataset can move together; `load_run` resolves this back to an
                # absolute path against the run root.
                "path": os.path.relpath(Path(cfg.input.dataset_dir).resolve(), start=out.resolve()),
                "manifest_sha256": dataset_manifest_sha256,
            },
            "history_lookback_days": cfg.history.lookback_days,
            "resolved_scopes": [
                {"org_id": s.org_id, "scope_type": s.scope_type, "scope_id": s.scope_id}
                for s in selected
            ],
        }
        write_started = time.perf_counter()
        _json_write(out / "paths/index.json", index)
        _json_write(out / "request.json", request_doc)
        _json_write(
            out / "allocations.json", {"schema_version": 2, "entries": allocations}
        )
        _json_write(
            out / "period_summary.json", {"schema_version": 1, "entries": summaries}
        )
        with (out / "daily_expected.jsonl").open("wb") as f:
            for row in daily:
                f.write(canonical_bytes(row))
        resolved = cfg.model_dump(mode="json")
        # Relative to the run directory (design §14.3), matching request.json's dataset.path.
        resolved["input"]["dataset_dir"] = os.path.relpath(
            Path(cfg.input.dataset_dir).resolve(), start=out.resolve()
        )
        (out / "resolved_config.yaml").write_text(
            yaml.safe_dump(resolved, sort_keys=True), encoding="utf-8"
        )
        stages["artifact_staging"] = time.perf_counter() - write_started
        data.verify_unchanged()
        base_diag["series"] = diag_series
        from .presentation import write_report

        if monitor is not None:
            measured = monitor.finish()
            measured["stage_seconds"] = stages
            base_diag["runtime"].update(measured)
            base_diag["runtime"]["measurement_limitations"] = [
                "resource timing excludes final diagnostics/report serialization and atomic manifest publication; output_bytes includes every manifest-listed artifact and excludes the manifest"
            ]
        else:
            base_diag["runtime"]["stage_seconds"] = stages
            base_diag["runtime"]["wall_seconds"] = time.perf_counter() - started
            base_diag["runtime"]["peak_rss_bytes"] = psutil.Process().memory_info().rss
        for package in (
            "numpy",
            "scipy",
            "pandas",
            "statsmodels",
            "matplotlib",
            "plotly",
            "psutil",
            "threadpoolctl",
        ):
            base_diag["runtime"]["versions"][package] = version(package)
        base_diag["runtime"]["output_bytes"] = 0
        for _ in range(10):
            _json_write(out / "diagnostics.json", base_diag)
            write_report(
                out,
                index,
                base_diag,
                {"status": overall, "entries": summaries},
                request_doc,
            )
            measured_output = sum(
                path.stat().st_size for path in out.rglob("*") if path.is_file()
            )
            if measured_output == base_diag["runtime"]["output_bytes"]:
                break
            base_diag["runtime"]["output_bytes"] = measured_output
        else:
            raise ExecutionError("forecast output byte accounting did not stabilize")
        log.info(
            "forecast.report.completed",
            status=overall,
        )
        files = {}
        for p in sorted(x for x in out.rglob("*") if x.is_file()):
            rel = p.relative_to(out).as_posix()
            files[rel] = _file_meta(
                out, p, len(daily) if rel == "daily_expected.jsonl" else None
            )
        output_bytes = sum(x["bytes"] for x in files.values())
        manifest = {
            "schema_version": 2,
            "operation": "forecast",
            "completion": "complete",
            "status": overall,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
            "versions": base_diag["runtime"]["versions"],
            "input_sources": data.hashes,
            "counts": {
                "requested_scopes": len(selected),
                "completed_scopes": completed,
                "rejected_scopes": rejected,
            },
            "report_path": "report.md",
        }
        temporary = out / ".manifest.tmp"
        _json_write(temporary, manifest)
        os.replace(temporary, out / "manifest.json")
        load_run(out)
        log.info(
            "forecast.write.completed",
            file_count=len(files),
            output_bytes=output_bytes,
            elapsed_seconds=time.perf_counter() - started,
        )
        return ForecastResult(
            overall,
            2 if overall == "partial" else 0,
            len(selected),
            completed,
            rejected,
            out,
            out / "report.md",
            base_diag,
            request.origin.isoformat(),
        )
    except InputError:
        if created:
            shutil.rmtree(out, ignore_errors=True)
        raise
    except Exception as exc:
        if created:
            shutil.rmtree(out, ignore_errors=True)
        if isinstance(exc, ExecutionError):
            raise
        raise ExecutionError(
            f"forecast execution failed: {type(exc).__name__}: {exc}"
        ) from exc


def run_forecast(
    config_path: Path,
    request: ForecastRequest,
    output_dir: Path | None = None,
    *,
    validate_only: bool = False,
    force: bool = False,
) -> ForecastResult:
    """Run forecasting while recording process-tree resources for the invocation."""
    monitor = ResourceMonitor()
    with monitor:
        return _run_forecast(
            config_path,
            request,
            output_dir,
            validate_only=validate_only,
            monitor=monitor,
            force=force,
        )
