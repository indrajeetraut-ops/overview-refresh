"""Build interactive (Plotly) and static (matplotlib) figures from verified,
already-saved forecast paths, optionally overlaid with the real observed series.

Both renderers consume the same per-panel computed series (`_resolve_panels`)
so the HTML and PNG outputs for one plot entry show identical content, per
`plot-forecast-enhancements-design.md` §8. Each call draws exactly one scope
(organization, or one named team/project) -- "one file per scope" (design D16)
is the driver's job (`run.py`): it expands an omitted `scope_name` into one
call per scope of that type, never a combined multi-scope panel.
"""
from __future__ import annotations

import html
import os
import tempfile
import textwrap
from datetime import date
from pathlib import Path

import numpy as np
from plotly import graph_objects as go
from plotly.subplots import make_subplots

from forecast.forecasting.errors import ExecutionError, InputError
from forecast.forecasting.presentation import _render_png

_SCOPE_TYPES = {"organization", "team", "project"}
_PLOTTABLE = {"qualified", "inactive"}
_ACTUALS_COLOR = "#495057"


def _text(value: object) -> str:
    return html.escape(str(value), quote=True)


def _validate_band(band) -> tuple[int, int]:
    if (
        not isinstance(band, (tuple, list))
        or len(band) != 2
        or any(type(x) is not int for x in band)
    ):
        raise InputError("band must be a pair of integers")
    low, high = band
    if not (0 < low < high < 100):
        raise InputError("band must satisfy 0 < low < high < 100")
    return (low, high)


def _resolve_panels(
    saved_run,
    *,
    org_id: str,
    scope_type: str,
    scope_name: str | None = None,
    models: list[str] | None = None,
    samples: int = 0,
    band: tuple[int, int] = (10, 90),
    actuals: dict | None = None,
    actuals_source: str = "unavailable",
    actuals_note: str | None = None,
) -> tuple[list[dict], dict]:
    """Validate the selection and compute one series bundle per plotted panel,
    for exactly one scope (one row per model). Returns `(panels, summary)`.
    Never touches Plotly or matplotlib: this is the single source of numeric
    truth that both renderers draw from.
    """
    if not isinstance(org_id, str) or not org_id.strip():
        raise InputError("org_id must be a nonempty string")
    if scope_type not in _SCOPE_TYPES:
        raise InputError("scope_type must be organization, team, or project")
    band = _validate_band(band)
    actuals = actuals or {}
    if scope_type == "organization":
        if scope_name is not None:
            raise InputError("scope_name must be omitted for an organization")
        selected_scope = next(
            (scope for scope in saved_run.index.get("scopes", [])
             if scope.get("org_id") == org_id and scope.get("scope_type") == scope_type
             and scope.get("scope_id") == org_id),
            {"scope_id": org_id, "scope_name": org_id},
        )
    else:
        if not isinstance(scope_name, str) or not scope_name.strip():
            raise InputError(f"scope_name is required for {scope_type} scopes")
        matching_scopes = [scope for scope in saved_run.index.get("scopes", [])
                           if scope.get("org_id") == org_id and scope.get("scope_type") == scope_type
                           and scope.get("scope_name") == scope_name]
        if not matching_scopes:
            raise InputError(f"unknown {scope_type} name {scope_name!r} in Organization {org_id!r} for this run")
        if len(matching_scopes) != 1:
            raise InputError(f"ambiguous {scope_type} name {scope_name!r} in Organization {org_id!r} for this run")
        selected_scope = matching_scopes[0]
    if type(samples) is not int or samples < 0:
        raise InputError("samples must be a nonnegative integer")
    if models is not None:
        if not models or any(not isinstance(model, str) or not model.strip() for model in models):
            raise InputError("models must contain nonempty model IDs")
        if len(set(models)) != len(models):
            raise InputError("model selections must be unique")

    index = saved_run.index
    dates = list(index["dates"])
    sample_ids = list(index["sample_ids"])
    entries = [
        item for item in index["series"]
        if item["org_id"] == org_id
        and item["scope_type"] == scope_type
        and item.get("scope_id") == selected_scope["scope_id"]
    ]
    if not entries:
        raise InputError("selected scope is not present in the forecast run")
    available_models = list(dict.fromkeys(item["model"] for item in entries))
    selected_models = list(models) if models is not None else available_models
    missing = [model for model in selected_models if model not in available_models]
    if missing:
        raise InputError("selected model is not present in the selected scope: " + ", ".join(missing))

    selected_entries = [item for model in selected_models
                        for item in entries if item["model"] == model]
    plotted_entries = [item for item in selected_entries if item["status"] in _PLOTTABLE]
    unavailable_entries = [item for item in selected_entries if item["status"] not in _PLOTTABLE]
    unavailable = [item["model"] for item in unavailable_entries]
    if not plotted_entries:
        raise InputError("none of the selected models has representable forecast paths")

    scope_label = str(selected_scope.get("scope_name") or selected_scope["scope_id"])

    displayed_count = min(samples, len(sample_ids))
    low_pct, high_pct = band
    panels: list[dict] = []
    assumptions: list[str] = []
    actuals_overlaid = False
    for entry in plotted_entries:
        values = np.asarray(saved_run.load_paths(entry))
        if values.shape != (len(dates), len(sample_ids)):
            raise InputError("saved path axes do not match the run index")
        mean = np.mean(values, axis=1)
        band_low = np.quantile(values, low_pct / 100, axis=1, method="linear")
        band_high = np.quantile(values, high_pct / 100, axis=1, method="linear")
        sample_columns = [(sample_ids[column], values[:, column]) for column in range(displayed_count)]
        history = actuals.get((entry["scope_type"], entry["scope_id"], entry["model"]))
        actual_panel = None
        if history is not None and len(history.dates) and history.values.size:
            actual_panel = {
                "dates": [d.isoformat() for d in history.dates],
                "values": np.asarray(history.values, dtype=np.float64),
                "seam_date": history.dates[-1].isoformat(),
            }
            actuals_overlaid = True
        panels.append({
            "entry": entry,
            "scope_label": scope_label,
            "dates": dates,
            "mean": mean,
            "band_low": band_low,
            "band_high": band_high,
            "samples": sample_columns,
            "actuals": actual_panel,
        })
        for assumption in entry.get("assumptions", []):
            if assumption not in assumptions:
                assumptions.append(assumption)

    for assumption in selected_scope.get("assumptions", []):
        if assumption not in assumptions:
            assumptions.append(assumption)
    status = "partial" if unavailable_entries else "complete"

    summary = {
        "status": status,
        "selected_models": selected_models,
        "plotted_models": list(dict.fromkeys(item["model"] for item in plotted_entries)),
        "unavailable_models": list(dict.fromkeys(unavailable)),
        "full_sample_count": len(sample_ids),
        "displayed_sample_count": displayed_count,
        "org_id": org_id,
        "scope_type": scope_type,
        "scope_name": scope_label,
        "assumptions": assumptions,
        "band": [low_pct, high_pct],
        "actuals_source": actuals_source,
        "actuals_overlaid": actuals_overlaid,
        "actuals_note": actuals_note,
    }
    return panels, summary


def _subtitle(summary: dict, unavailable_text: str) -> str:
    low, high = summary["band"]
    actuals_bits = f"actuals: {summary['actuals_source']}"
    if summary["actuals_source"] != "unavailable":
        actuals_bits += " (overlaid)" if summary["actuals_overlaid"] else " (no matching series)"
    if summary.get("actuals_note"):
        actuals_bits += f"; {summary['actuals_note']}"
    assumption_text = ", ".join(summary["assumptions"]) or "none"
    return (
        f"Status: {summary['status']}; full samples: {summary['full_sample_count']}; "
        f"displayed samples/model: {summary['displayed_sample_count']}; "
        f"unavailable: {unavailable_text}; assumptions: {assumption_text}. "
        f"Bands are pointwise p{low}–p{high}. {actuals_bits}."
    )


def build_figure(
    saved_run,
    *,
    org_id: str,
    scope_type: str,
    scope_name: str | None = None,
    models: list[str] | None = None,
    samples: int = 0,
    band: tuple[int, int] = (10, 90),
    actuals: dict | None = None,
    actuals_source: str = "unavailable",
    actuals_note: str | None = None,
) -> tuple[go.Figure, dict]:
    """Return a self-contained Plotly figure and selection summary without changing the run."""
    panels, summary = _resolve_panels(
        saved_run, org_id=org_id, scope_type=scope_type, scope_name=scope_name,
        models=models, samples=samples, band=band, actuals=actuals,
        actuals_source=actuals_source, actuals_note=actuals_note,
    )
    low, high = summary["band"]
    subplot_titles = [
        f"{_text(panel['scope_label'])} · {_text(panel['entry']['model'])} · {_text(panel['entry']['status'])}"
        for panel in panels
    ]
    figure = make_subplots(
        rows=len(panels), cols=1, shared_xaxes=True,
        vertical_spacing=min(0.12, 0.35 / max(len(panels), 1)),
        subplot_titles=subplot_titles,
    )
    for row, panel in enumerate(panels, 1):
        entry = panel["entry"]
        dates = panel["dates"]
        series_label = _text(entry["model"])
        common_hover = "%{x}<br>" + series_label + "<br>%{y:,.3f} total tokens<extra></extra>"
        figure.add_trace(go.Scatter(
            x=dates, y=panel["band_low"], mode="lines", line={"width": 0},
            name=f"{series_label} p{low}", legendgroup=series_label, showlegend=False,
            hovertemplate=common_hover,
        ), row=row, col=1)
        figure.add_trace(go.Scatter(
            x=dates, y=panel["band_high"], mode="lines", line={"width": 0}, fill="tonexty",
            fillcolor="rgba(31,119,180,0.18)", name=f"{series_label} pointwise p{low}–p{high}",
            legendgroup=series_label, hovertemplate=common_hover,
        ), row=row, col=1)
        figure.add_trace(go.Scatter(
            x=dates, y=panel["mean"], mode="lines", line={"width": 3},
            name=f"{series_label} mean", legendgroup=series_label, hovertemplate=common_hover,
        ), row=row, col=1)
        for sample_id, column_values in panel["samples"]:
            figure.add_trace(go.Scatter(
                x=dates, y=column_values, mode="lines", opacity=0.42,
                line={"width": 1}, name=f"{series_label} sample {sample_id}",
                legendgroup=series_label,
                hovertemplate=("%{x}<br>" + series_label
                               + f"<br>sample {_text(sample_id)}"
                               + "<br>%{y:,.3f} total tokens<extra></extra>"),
            ), row=row, col=1)
        actual_panel = panel["actuals"]
        if actual_panel is not None:
            actual_hover = "%{x}<br>" + series_label + "<br>%{y:,.3f} observed tokens<extra></extra>"
            figure.add_trace(go.Scatter(
                x=actual_panel["dates"], y=actual_panel["values"], mode="lines",
                line={"width": 1.4, "color": _ACTUALS_COLOR}, name=f"{series_label} observed",
                legendgroup=series_label, hovertemplate=actual_hover,
            ), row=row, col=1)
            figure.add_vline(x=actual_panel["seam_date"], line_dash="dot",
                             line_color=_ACTUALS_COLOR, row=row, col=1)
        figure.update_yaxes(title_text="Total tokens", rangemode="tozero", row=row, col=1)

    unavailable_text = ", ".join(summary["unavailable_models"]) or "none"
    subtitle = _subtitle(summary, unavailable_text)
    figure.update_layout(
        title={"text": (
            f"Forecast · {_text(org_id)} · {_text(scope_type)} · {_text(summary['scope_name'])}"
            f"<br><sup>{_text(subtitle)}</sup>"
        )},
        template="plotly_white", hovermode="closest",
        height=max(460, 360 * len(panels)),
        margin={"t": 115, "r": 35, "b": 55, "l": 85},
        legend={"groupclick": "toggleitem"},
    )
    figure.update_xaxes(title_text="Date", row=len(panels), col=1)
    return figure, summary


def build_matplotlib_figure(
    saved_run,
    *,
    org_id: str,
    scope_type: str,
    scope_name: str | None = None,
    models: list[str] | None = None,
    samples: int = 0,
    band: tuple[int, int] = (10, 90),
    actuals: dict | None = None,
    actuals_source: str = "unavailable",
    actuals_note: str | None = None,
):
    """Return a matplotlib Figure (Agg-compatible) with the same content as `build_figure`."""
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.figure import Figure

    panels, summary = _resolve_panels(
        saved_run, org_id=org_id, scope_type=scope_type, scope_name=scope_name,
        models=models, samples=samples, band=band, actuals=actuals,
        actuals_source=actuals_source, actuals_note=actuals_note,
    )
    low, high = summary["band"]
    count = len(panels)
    figure = Figure(figsize=(9.5, max(4.8, 3.4 * count)), layout="constrained")
    figure.get_layout_engine().set(rect=(0, 0.08, 1, 0.90))
    axes = figure.subplots(nrows=count, ncols=1, sharex=True, squeeze=False)[:, 0]
    for axis, panel in zip(axes, panels):
        entry = panel["entry"]
        calendar_dates = [date.fromisoformat(value) for value in panel["dates"]]
        axis.plot(calendar_dates, panel["mean"], color="#087f5b", linewidth=2.2, label="Mean")
        axis.fill_between(calendar_dates, panel["band_low"], panel["band_high"],
                          color="#74c0fc", alpha=0.32, label=f"Pointwise p{low}–p{high}")
        for sample_id, column_values in panel["samples"]:
            axis.plot(calendar_dates, column_values, color="#adb5bd", linewidth=1, alpha=0.55)
        actual_panel = panel["actuals"]
        if actual_panel is not None:
            actual_dates = [date.fromisoformat(value) for value in actual_panel["dates"]]
            axis.plot(actual_dates, actual_panel["values"], color=_ACTUALS_COLOR,
                      linewidth=1.4, label="Observed (actual)")
            axis.axvline(date.fromisoformat(actual_panel["seam_date"]),
                         color=_ACTUALS_COLOR, linestyle=":", linewidth=1)
        axis.set_title(f"{entry['model']} · {entry['status']}")
        axis.set_ylabel("Total tokens")
        axis.set_ylim(bottom=0)
        axis.grid(axis="y", alpha=0.22)
        axis.legend(loc="best", fontsize=8)
    locator = AutoDateLocator(minticks=3, maxticks=9, interval_multiples=True)
    axes[-1].xaxis.set_major_locator(locator)
    axes[-1].xaxis.set_major_formatter(ConciseDateFormatter(locator))
    axes[-1].set_xlabel("Date")
    unavailable_text = ", ".join(summary["unavailable_models"]) or "none"
    subtitle = _subtitle(summary, unavailable_text)
    # matplotlib's suptitle does not wrap, so a long subtitle would clip off both
    # figure edges. Wrap it to the figure width and reserve proportional headroom
    # (rect top) so the multi-line title block never overlaps the top panel.
    title = f"Forecast · {org_id} · {scope_type} · {summary['scope_name']}"
    wrapped = textwrap.fill(subtitle, width=115)
    line_count = 1 + wrapped.count("\n") + 1  # title line + wrapped subtitle lines
    figure.suptitle(f"{title}\n{wrapped}", fontsize=9)
    top = max(0.80, 0.96 - 0.022 * line_count)
    figure.get_layout_engine().set(rect=(0, 0.08, 1, top))
    return figure, summary


def export_html(figure: go.Figure, output: Path, *, plotlyjs: str = True) -> Path:
    """Atomically publish an HTML file at a new path.

    `plotlyjs` is passed through to Plotly's `include_plotlyjs`: a relative path
    string (design §8: `<relative path>` to the shared `plotly.min.js`) so the
    HTML references one shared file instead of embedding it; the driver
    (`run.py`) computes that relative path. `True` (the default) embeds a full
    copy, kept only so this function still works standalone (e.g. ad hoc/test
    use); every production call site passes a relative path string.
    """
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise InputError("output already exists; choose a new HTML path")
    if not output.parent.exists() or not output.parent.is_dir():
        raise ExecutionError("output parent directory does not exist", stage="export")
    temporary: Path | None = None
    try:
        document = figure.to_html(full_html=True, include_plotlyjs=plotlyjs, auto_play=False)
        fd, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link publishes atomically and refuses to replace an existing path.
        os.link(temporary, output)
        temporary.unlink()
        return output
    except FileExistsError as exc:
        raise InputError("output already exists; choose a new HTML path") from exc
    except InputError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ExecutionError(f"cannot export HTML: {type(exc).__name__}", stage="export") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def export_png(figure, output: Path) -> Path:
    """Publish a PNG at a new external path via the shared matplotlib Agg helper.

    Not atomic, matching `forecast.curve.preview.render_png`'s convention: the
    presentation helper writes the target directly.
    """
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise InputError("output already exists; choose a new PNG path")
    if not output.parent.exists() or not output.parent.is_dir():
        raise ExecutionError("output parent directory does not exist", stage="export")
    try:
        _render_png(figure, output)
    except ExecutionError:
        raise
    return output
