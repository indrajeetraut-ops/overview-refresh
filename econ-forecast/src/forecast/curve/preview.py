"""Preview rendering for a saved curve: interactive Plotly + static matplotlib.

Both are rendered from the same sampled curve + resolved anchors, per design so the
operator eyeballs the exact same shape in either artifact. `render_html` follows the
offline (`include_plotlyjs="cdn"`), atomic-write conventions of
`forecast.utils.plot_forecast.export_html`; `render_png` reuses
`forecast.forecasting.presentation._render_png` (matplotlib Agg backend, no kaleido).
"""
from __future__ import annotations

import html
import os
import tempfile
from pathlib import Path

from plotly import graph_objects as go

from forecast.forecasting.errors import ExecutionError as _PresentationExecutionError
from forecast.forecasting.presentation import _render_png

from .config import CurveConfig
from .errors import ArtifactError


def _text(value) -> str:
    return html.escape(str(value), quote=True)


def _sample_x(n: int, day_axis: bool) -> tuple[list[float], str]:
    """Shared x-axis coordinates for the sampled curve line and the anchor markers."""
    if day_axis:
        return [index + 1 for index in range(n)], "Preview day"
    if n == 1:
        return [0.0], "Normalized position"
    return [index / (n - 1) for index in range(n)], "Normalized position"


def _anchor_x(positions: list[float], n: int, day_axis: bool) -> list[float]:
    if not day_axis:
        return positions
    scale = (n - 1) if n > 1 else 1
    return [pos * scale + 1 for pos in positions]


def _scaled(levels: list[float], baseline_tokens: float | None) -> list[float]:
    return [level * baseline_tokens for level in levels] if baseline_tokens else list(levels)


def _axes(config: CurveConfig, resolved: list[tuple[float, float]], levels: list[float],
          *, day_axis: bool):
    n = len(levels)
    x, x_title = _sample_x(n, day_axis)
    anchor_positions = [pos for pos, _ in resolved]
    anchor_levels = [level for _, level in resolved]
    baseline_tokens = config.preview.baseline_tokens
    y = _scaled(levels, baseline_tokens)
    anchor_y = _scaled(anchor_levels, baseline_tokens)
    anchor_x = _anchor_x(anchor_positions, n, day_axis)
    y_title = "Level x baseline tokens" if baseline_tokens else "Level (dimensionless multiplier)"
    return x, y, anchor_x, anchor_y, x_title, y_title


def build_figure(config: CurveConfig, resolved: list[tuple[float, float]], levels: list[float],
                  *, day_axis: bool = False) -> go.Figure:
    """Build the Plotly figure for `preview.html`: PCHIP line + anchor markers."""
    x, y, anchor_x, anchor_y, x_title, y_title = _axes(config, resolved, levels, day_axis=day_axis)
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=x, y=y, mode="lines", name="PCHIP curve", line={"width": 2.5, "color": "#1f77b4"},
        hovertemplate="%{x}<br>%{y:,.4f}<extra></extra>"))
    figure.add_trace(go.Scatter(
        x=anchor_x, y=anchor_y, mode="markers", name="Anchors",
        marker={"size": 9, "color": "#d62728", "symbol": "diamond"},
        hovertemplate="%{x}<br>%{y:,.4f}<extra></extra>"))
    figure.update_layout(
        title={"text": f"Curve &middot; {_text(config.name)} &middot; interp={_text(config.interp)}"},
        template="plotly_white", hovermode="closest",
        xaxis_title=x_title, yaxis_title=y_title,
        margin={"t": 70, "r": 35, "b": 55, "l": 70},
    )
    return figure


def render_html(config: CurveConfig, resolved: list[tuple[float, float]], levels: list[float],
                target: Path, *, day_axis: bool = False) -> Path:
    """Publish `preview.html`: offline Plotly (CDN), written atomically."""
    target = Path(target)
    figure = build_figure(config, resolved, levels, day_axis=day_axis)
    document = figure.to_html(full_html=True, include_plotlyjs="cdn", auto_play=False)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        # mkstemp creates the temp file 0600; match the 0644 of the other artifact files.
        os.chmod(target, 0o644)
        return target
    except OSError as exc:
        raise ArtifactError(f"cannot write {target.name}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def render_png(config: CurveConfig, resolved: list[tuple[float, float]], levels: list[float],
               target: Path, *, day_axis: bool = False) -> Path:
    """Publish `preview.png`: static matplotlib chart via the Agg backend (no kaleido)."""
    from matplotlib.figure import Figure

    target = Path(target)
    x, y, anchor_x, anchor_y, x_title, y_title = _axes(config, resolved, levels, day_axis=day_axis)
    figure = Figure(figsize=(9.5, 4.8), layout="constrained")
    axis = figure.subplots()
    axis.plot(x, y, color="#1f77b4", linewidth=2.2, label="PCHIP curve")
    axis.scatter(anchor_x, anchor_y, color="#d62728", marker="D", s=45, zorder=3, label="Anchors")
    axis.set_title(f"Curve - {config.name} - interp={config.interp}")
    axis.set_xlabel(x_title)
    axis.set_ylabel(y_title)
    axis.grid(axis="y", alpha=0.22)
    axis.legend(loc="best")
    try:
        _render_png(figure, target)
    except _PresentationExecutionError as exc:
        raise ArtifactError(str(exc)) from exc
    return target
