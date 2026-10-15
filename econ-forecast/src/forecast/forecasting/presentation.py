"""Minimal report and PNG presentation built only from saved forecast results."""
from __future__ import annotations

from datetime import date
from collections import defaultdict
import html
from pathlib import Path
import textwrap
import warnings

import numpy as np

from common.msging.log import get_logger
from .errors import ExecutionError, InputError

LIMITATIONS = [
    "Forecast probabilities are uncalibrated; the fitted model has not been evaluated against held-out real data.",
    "Agent/category allocation shares are fixed over the forecast horizon.",
    "Organization, Team, and Project views overlap and must not be added together.",
    "Models in a scope jointly sample their common historical error dates; errors are drawn independently across future days, so multi-day bursts are underrepresented.",
    "Negative simulated values are corrected to zero without feeding back into the model state; a large corrected fraction is reported but does not change the series status.",
    "Inactive paths assume continued inactivity and are not an empirical uncertainty distribution.",
]
_LOG = get_logger("forecast.forecasting.presentation")


def _safe(value) -> str:
    if value is None:
        return "unavailable"
    escaped = html.escape(str(value), quote=True)
    return (escaped.replace("\\", "\\\\").replace("`", "\\`")
            .replace("|", "&#124;").replace("\r", " ").replace("\n", " "))


def _number(value, decimals=3) -> str:
    if value is None or isinstance(value, bool):
        return "unavailable"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "unavailable"
    return f"{number:,.{decimals}f}" if np.isfinite(number) else "unavailable"


def build_figure(entry: dict, dates: list[str], paths: np.ndarray):
    """Build one Agg-compatible Matplotlib figure from one saved path matrix."""
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.figure import Figure

    values = np.asarray(paths)
    if values.ndim != 2 or values.shape[0] != len(dates) or values.shape[1] == 0:
        raise InputError("presentation path axes do not match the index")
    if values.dtype != np.dtype("float64") or not np.isfinite(values).all() or np.any(values < 0):
        raise InputError("presentation paths must be finite nonnegative float64 values")
    try:
        calendar_dates = [date.fromisoformat(value) for value in dates]
    except (TypeError, ValueError) as exc:
        raise InputError("presentation dates must be ISO calendar dates") from exc
    mean = np.mean(values, axis=1)
    p10 = np.quantile(values, 0.10, axis=1, method="linear")
    p90 = np.quantile(values, 0.90, axis=1, method="linear")
    figure = Figure(figsize=(9.5, 4.8), layout="constrained")
    figure.get_layout_engine().set(rect=(0, 0.15, 1, 0.85))
    axis = figure.subplots()
    axis.plot(calendar_dates, mean, color="#087f5b", linewidth=2.2, label="Daily mean")
    axis.fill_between(calendar_dates, p10, p90, color="#74c0fc", alpha=0.32,
                      label="Pointwise p10–p90")
    inactive = entry.get("status") == "inactive"
    suffix = " · assumed continued inactivity" if inactive else ""
    axis.set_title(f"{entry.get('scope_name', entry.get('scope_id'))} · {entry.get('model')} · "
                   f"{entry.get('status')}{suffix}")
    axis.set_xlabel("Forecast date")
    axis.set_ylabel("Total tokens")
    axis.set_ylim(bottom=0)
    locator = AutoDateLocator(minticks=3, maxticks=9, interval_multiples=True)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    axis.grid(axis="y", alpha=0.22)
    axis.legend(loc="best")
    assumptions = list(entry.get("assumptions", []))
    if assumptions:
        readable = ", ".join(str(item).replace("_", " ") for item in assumptions)
        footer = textwrap.fill("Assumptions: " + readable, width=105,
                               subsequent_indent="             ")
        figure.text(0.01, 0.025, footer, fontsize=8, color="#495057",
                    va="bottom", linespacing=1.25)
    return figure


def _render_png(figure, target: Path) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.filterwarnings("always", message=".*constrained_layout.*", category=UserWarning)
            FigureCanvasAgg(figure).print_png(target)
        for warning in caught:
            _LOG.warning("forecast.presentation_warning", category=warning.category.__name__,
                         message=str(warning.message), artifact=target.name)
    except OSError as exc:
        raise ExecutionError(f"cannot write chart {target.name}", stage="presentation") from exc


def write_report(root: Path, index: dict, diagnostics: dict, summaries: dict, request: dict) -> Path:
    """Write the minimal truthful Markdown report from finalized staging metadata."""
    root = Path(root)
    runtime = diagnostics.get("runtime", {})
    workload = runtime.get("workload", {})
    by_series = {item.get("series_id"): item for item in diagnostics.get("series", [])}
    scopes = {(item.get("org_id"), item.get("scope_type"), item.get("scope_id")): item
              for item in index.get("scopes", [])}

    def scope_name(item: dict) -> str:
        scope = scopes.get((item.get("org_id"), item.get("scope_type"), item.get("scope_id")), item)
        return _safe(scope.get("scope_name") or
                     (item.get("org_id") if item.get("scope_type") == "organization" else None))

    def row(*cells) -> str:
        return "| " + " | ".join(str(cell) for cell in cells) + " |"

    def key(item: dict) -> tuple:
        return item.get("org_id"), item.get("scope_type"), item.get("scope_id")

    def grouped(items: list[dict]) -> dict[tuple, list[dict]]:
        result = defaultdict(list)
        for item in items:
            result[key(item)].append(item)
        return result

    def scope_heading(items: list[dict]) -> str:
        item = items[0]
        return f"### {_safe(item.get('org_id'))} / {_safe(item.get('scope_type'))} / {scope_name(item)}"

    def window(start, end) -> str:
        if start is None or end is None:
            return "unavailable"
        return f"{_safe(start)} to {_safe(end)}"

    lines = ["# Forecast run", "", f"Status: **{_safe(summaries.get('status'))}**", "",
             f"Requested window: `{_safe(request.get('requested_start'))}` through `{_safe(request.get('requested_end'))}`",
             f"Resolved origin: `{_safe(request.get('origin'))}`", "",
             "## Scope status", "",
             row("Organization", "Scope type", "Scope name", "Status", "Risk ready"),
             row("---", "---", "---", "---", "---")]
    scope_notes = []
    for scope in index.get("scopes", []):
        assumptions = ", ".join(map(_safe, scope.get("assumptions", []))) or "none"
        reasons = ", ".join(map(_safe, scope.get("reasons", []))) or "none"
        lines.append(row(_safe(scope.get("org_id")), _safe(scope.get("scope_type")),
                         scope_name(scope), _safe(scope.get("status")), _safe(scope.get("risk_ready"))))
        scope_notes.append(f"- {_safe(scope.get('scope_type'))} {scope_name(scope)}: assumptions: {assumptions}; reasons: {reasons}")
    lines += ["", *scope_notes]
    lines += ["", "## Models and evidence windows", ""]
    for entries in grouped(index.get("series", [])).values():
        lines += [scope_heading(entries), "",
                  row("Model", "Status", "History window", "Sampling window", "Shared days", "Last active"),
                  row("---", "---", "---", "---", "---:", "---")]
        notes = []
        for entry in entries:
            diag = by_series.get(entry.get("series_id"), {})
            assumptions = ", ".join(map(_safe, entry.get("assumptions", []))) or "none"
            reasons = ", ".join(map(_safe, entry.get("reasons", []))) or "none"
            lines.append(row(_safe(entry.get("model")), _safe(entry.get("status")),
                             window(diag.get("history_start"), diag.get("history_end")),
                             window(diag.get("sampling_start"), diag.get("sampling_end")),
                             _safe(diag.get("sampling_days")), _safe(diag.get("last_active_date"))))
            notes.append(f"- {_safe(entry.get('model'))}: assumptions: {assumptions}; reasons: {reasons}")
        lines += ["", *notes, ""]
    lines += ["## Requested-window expected totals (tokens)", ""]
    requested = [item for item in summaries.get("entries", []) if item.get("period_type") == "requested"]
    represented = {(key(item), item.get("model")) for item in requested}
    for entry in index.get("series", []):
        if (key(entry), entry.get("model")) not in represented:
            requested.append({**entry, "expected_tokens": None, "p10": None,
                              "p50": None, "p90": None})
    for scope in index.get("scopes", []):
        if (key(scope), None) not in represented:
            requested.append({**scope, "model": None, "expected_tokens": None,
                              "p10": None, "p50": None, "p90": None})
    if not requested:
        lines.append("No representable requested-window totals.")
    for entries in grouped(requested).values():
        lines += [scope_heading(entries), "",
                  row("Model / aggregate", "Expected tokens", "p10", "p50", "p90", "Status"),
                  row("---", "---:", "---:", "---:", "---:", "---")]
        notes = []
        for item in entries:
            label = "scope aggregate" if item.get("model") is None else _safe(item.get("model"))
            lines.append(row(label, _number(item.get("expected_tokens")),
                             _number(item.get("p10")), _number(item.get("p50")),
                             _number(item.get("p90")), _safe(item.get("status"))))
            if item.get("assumptions") or item.get("reasons"):
                assumptions = ", ".join(map(_safe, item.get("assumptions", []))) or "none"
                reasons = ", ".join(map(_safe, item.get("reasons", []))) or "none"
                notes.append(f"- {label}: assumptions: {assumptions}; reasons: {reasons}")
        lines += ["", *notes, ""]
    stages = runtime.get("stage_seconds", {})
    stage_text = ", ".join(f"{_safe(name)}={_number(value, 6)}s" for name, value in stages.items()) or "unavailable"
    output_bytes = runtime.get("output_bytes")
    output_text = _number(output_bytes, 0) if isinstance(output_bytes, (int, float)) and output_bytes > 0 else "unavailable until final artifact accounting"
    lines += ["", "## Runtime and workload", "",
              f"- Series: {_safe(workload.get('series'))}; paths: {_safe(workload.get('paths'))}; requested days: {_safe(workload.get('requested_days'))}; internal days: {_safe(workload.get('internal_days'))}",
              f"- Workers: {_safe(runtime.get('workers'))}; numerical threads: {_safe(runtime.get('numerical_threads'))}",
              f"- Wall seconds: {_number(runtime.get('wall_seconds'), 6)}; CPU user/system seconds: {_number(runtime.get('cpu_user_seconds'), 6)} / {_number(runtime.get('cpu_system_seconds'), 6)}",
              f"- Peak RSS bytes: {_number(runtime.get('peak_rss_bytes'), 0)}; output bytes: {output_text}",
              f"- Stage seconds: {stage_text}",
              f"- Memory measurement: {_safe(runtime.get('memory_measurement'))}",
              f"- CPU measurement: {_safe(runtime.get('cpu_measurement'))}",
              "", "## Interpretation limitations", "", *[f"- {item}" for item in LIMITATIONS]]
    target = root / "report.md"
    try:
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ExecutionError("cannot write forecast report", stage="presentation") from exc
    _LOG.info("forecast.report_written", artifact=target.name, status=summaries.get("status"),
              scope_count=len(index.get("scopes", [])), series_count=len(index.get("series", [])))
    return target
