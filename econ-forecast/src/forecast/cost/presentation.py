"""Cost run report and charts (design §8.3): a terse Markdown report plus PNG
(matplotlib, Agg) and self-contained offline HTML (Plotly, inlined `plotly.js`)
charts, built only from an already-loaded `CostRun` -- mirrors
`forecast.forecasting.presentation` and `forecast.utils.plot_forecast`'s own
idioms, in USD instead of tokens.

`render_report(run, output_dir)` is the one entry point (design §10): it writes
`report.md` and `charts/*` into `output_dir` (the cost run's own directory) and
returns the written paths, relative to `output_dir`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
import json
import math

import numpy as np
import yaml

from common.msging.log import get_logger

from .artifacts import CostRun
from .errors import ExecutionError
from .schemas import TOKEN_TYPES

_LOG = get_logger("forecast.cost.presentation")

#: Notes carried on every report regardless of content (design §8.3 "Notes").
_STANDARD_NOTES = [
    "Fan edges shown in charts are per-date quantiles across samples, not one coherent future path.",
    "Organization, Team, and Project scopes overlap; their totals must not be added together.",
    "Prices are the latest known at the time this run was priced; a later price change is not reflected here.",
    "Long-context pricing tiers are ignored.",
    "The agent x token-type split used here may differ from the forecast report's own split "
    "(forecasting's category allocation uses a different, fixed 28-day plain-sum estimator) -- a known error.",
]

#: Plain-language text for assumption labels a cost run can carry (design §9); a
#: label not listed here (e.g. one carried through unchanged from the forecast)
#: falls back to its own words with underscores turned to spaces.
_ASSUMPTION_TEXT = {
    "fixed_mix_estimate": (
        "Its token-type mix (input / cache-write 5m / cache-write 1h / cache-read / output) is held fixed at the estimated blend "
        "for the whole horizon."
    ),
    "continued_inactivity": (
        "It was inactive in history; the forecast, and this cost run, assume it stays inactive."
    ),
    "scenario_constant_tokens": (
        "Tokens moved to it by a scenario change keep the destination series' own mix "
        "(token_factor = 1.0)."
    ),
    "scenario_token_factor_manual": (
        "Tokens moved to it by a scenario change are scaled by a manually set token_factor != 1.0."
    ),
    "scenario_mix_borrowed": (
        "It has no mix evidence of its own for this run; it borrows the source series' mix."
    ),
    "scenario_added_series": (
        "It did not exist in the forecast; the scenario created it to receive transferred tokens."
    ),
    "scenario_propagated_by_share": (
        "Its change at this scope is propagated from a child scope by attribution share, not "
        "recomputed independently; exact in expectation, approximate per sample."
    ),
    "uncalibrated_model_estimate": "Its forecast probabilities are uncalibrated against held-out data.",
    "excess_negative_correction": (
        "A notable fraction of its simulated values were negative and corrected to zero."
    ),
}



_SCOPE_RANK = {"organization": 0, "team": 1, "project": 2}


def _scope_order(key: tuple[str, str]) -> tuple[int, str]:
    """Organization, then teams, then projects -- the scope tree, not alphabetical."""
    return (_SCOPE_RANK.get(key[0], 3), key[1])


def _run_status(run) -> str:
    """`incomplete` if any scope is; else `complete`. Derived from the index because
    the report is rendered before the manifest (which records the same rule) exists."""
    return "incomplete" if any(s["status"] == "incomplete" for s in run.index["scopes"]) else "complete"

def _assumption_text(label: str) -> str:
    return _ASSUMPTION_TEXT.get(label, label.replace("_", " "))


def _usd(value) -> str:
    if value is None or isinstance(value, bool):
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"${number:,.2f}" if math.isfinite(number) else "n/a"


def _number(value, decimals: int = 1) -> str:
    if value is None or isinstance(value, bool):
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:,.{decimals}f}" if math.isfinite(number) else "n/a"


def _scope_label(scope_type: str, scope_name: str) -> str:
    return f"{scope_type} {scope_name}"


def _row(*cells) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _md_table(headers: list[str], rows: list[tuple]) -> list[str]:
    if not rows:
        return []
    lines = [_row(*headers), _row(*(["---"] * len(headers)))]
    lines += [_row(*row) for row in rows]
    return lines


# ---------------------------------------------------------------------------
# Chart figures -- matplotlib (PNG) and Plotly (offline HTML)
# ---------------------------------------------------------------------------


def _fan_matplotlib(dates: list[date], values: np.ndarray, title: str, *,
                    band: tuple[int, int] = (10, 90), samples: int = 0):
    """Mean line + pointwise band (default p10-p90, configurable, design §4/§8.3),
    plus up to `samples` individual raw path lines, one series or one scope total.
    Mirrors `forecast.forecasting.presentation.build_figure`."""
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.figure import Figure

    low_pct, high_pct = band
    mean = np.mean(values, axis=1)
    low = np.quantile(values, low_pct / 100, axis=1, method="linear")
    high = np.quantile(values, high_pct / 100, axis=1, method="linear")
    figure = Figure(figsize=(9.5, 4.8), layout="constrained")
    figure.get_layout_engine().set(rect=(0, 0.12, 1, 0.88))
    axis = figure.subplots()
    axis.plot(dates, mean, color="#087f5b", linewidth=2.2, label="Daily mean")
    axis.fill_between(dates, low, high, color="#74c0fc", alpha=0.32,
                      label=f"Pointwise p{low_pct}-p{high_pct}")
    for column in range(min(samples, values.shape[1])):
        axis.plot(dates, values[:, column], color="#adb5bd", linewidth=1, alpha=0.55)
    axis.set_title(title)
    axis.set_xlabel("Date")
    axis.set_ylabel("Daily cost (USD)")
    axis.set_ylim(bottom=0)
    locator = AutoDateLocator(minticks=3, maxticks=9, interval_multiples=True)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    axis.grid(axis="y", alpha=0.22)
    axis.legend(loc="best")
    return figure


def _fan_plotly(dates: list[str], values: np.ndarray, title: str, *,
                band: tuple[int, int] = (10, 90), samples: int = 0):
    from plotly import graph_objects as go

    low_pct, high_pct = band
    mean = np.mean(values, axis=1)
    low = np.quantile(values, low_pct / 100, axis=1, method="linear")
    high = np.quantile(values, high_pct / 100, axis=1, method="linear")
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=dates, y=low, mode="lines", line={"width": 0}, name=f"p{low_pct}",
        showlegend=False, hoverinfo="skip",
    ))
    figure.add_trace(go.Scatter(
        x=dates, y=high, mode="lines", line={"width": 0}, fill="tonexty",
        fillcolor="rgba(31,119,180,0.18)", name=f"Pointwise p{low_pct}-p{high_pct}",
        hovertemplate=f"%{{x}}<br>p{low_pct}-p{high_pct}<extra></extra>",
    ))
    figure.add_trace(go.Scatter(
        x=dates, y=mean, mode="lines", line={"width": 3}, name="Daily mean",
        hovertemplate="%{x}<br>$%{y:,.2f}<extra></extra>",
    ))
    for column in range(min(samples, values.shape[1])):
        figure.add_trace(go.Scatter(
            x=dates, y=values[:, column], mode="lines", opacity=0.42, line={"width": 1},
            name=f"sample {column}",
            hovertemplate=f"%{{x}}<br>sample {column}<br>$%{{y:,.2f}}<extra></extra>",
        ))
    figure.update_layout(
        title=title, template="plotly_white", xaxis_title="Date", yaxis_title="Daily cost (USD)",
        margin={"t": 70, "r": 25, "b": 45, "l": 70},
    )
    figure.update_yaxes(rangemode="tozero")
    return figure


def _breakdown_matplotlib(dates: list[date], model_series: dict[str, list[float]], title: str):
    """Stacked expected daily cost by model, one scope (design §2.2/§8.3)."""
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.figure import Figure

    models = sorted(model_series)
    figure = Figure(figsize=(9.5, 4.8), layout="constrained")
    figure.get_layout_engine().set(rect=(0, 0.12, 1, 0.88))
    axis = figure.subplots()
    axis.stackplot(dates, *(model_series[m] for m in models), labels=models, alpha=0.85)
    axis.set_title(title)
    axis.set_xlabel("Date")
    axis.set_ylabel("Expected daily cost (USD)")
    axis.set_ylim(bottom=0)
    locator = AutoDateLocator(minticks=3, maxticks=9, interval_multiples=True)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    axis.grid(axis="y", alpha=0.22)
    axis.legend(loc="best", fontsize=8)
    return figure


def _breakdown_plotly(dates: list[str], model_series: dict[str, list[float]], title: str):
    from plotly import graph_objects as go

    figure = go.Figure()
    for model in sorted(model_series):
        figure.add_trace(go.Scatter(
            x=dates, y=model_series[model], mode="lines", stackgroup="models", name=model,
            hovertemplate="%{x}<br>" + model + "<br>$%{y:,.2f}<extra></extra>",
        ))
    figure.update_layout(
        title=title, template="plotly_white", xaxis_title="Date",
        yaxis_title="Expected daily cost (USD)", margin={"t": 70, "r": 25, "b": 45, "l": 70},
    )
    figure.update_yaxes(rangemode="tozero")
    return figure


# ---------------------------------------------------------------------------
# Data shaping from CostRun
# ---------------------------------------------------------------------------


def _daily_expected_by_scope_model(run: CostRun) -> dict[tuple[str, str], dict[str, dict[str, float]]]:
    """`(scope_type, scope_name) -> model -> {date_iso: usd}`, summed over token
    type and agent (§8.2 `daily_expected.jsonl`: one row per date/series/agent) --
    the expected (mean) daily cost per model a scope's breakdown chart stacks."""
    totals: dict[tuple[str, str], dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    for row in run.daily_expected:
        key = (row["scope_type"], row["scope_name"])
        usd_total = sum(row["usd"].get(tt, 0.0) for tt in TOKEN_TYPES)
        totals[key][row["model"]][row["date"]] += usd_total
    return totals


def _scenario_series_tokens(run: CostRun) -> dict[tuple[str, str, str], float]:
    """`(scope_type, scope_name, model) -> total expected tokens` over the whole
    run, summed over date/agent/token-type from `daily_expected.jsonl` -- the
    resulting token level of a series after a scenario's changes were applied."""
    totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in run.daily_expected:
        key = (row["scope_type"], row["scope_name"], row["model"])
        totals[key] += sum(row["tokens"].get(tt, 0.0) for tt in TOKEN_TYPES)
    return totals


# ---------------------------------------------------------------------------
# Report sections (design §8.3 order)
# ---------------------------------------------------------------------------


def _blocking_problems(run: CostRun) -> list[str]:
    sheet_by_model = {m.usage_model_id: m for m in run.rate_sheet.models}
    unpriced_rows = []
    for entry in run.index["series"]:
        if entry["status"] != "unpriced":
            continue
        reasons = "; ".join(entry.get("reasons", [])) or "no reason recorded"
        sheet_entry = sheet_by_model.get(entry["model"])
        candidates = "-"
        reason_text = reasons
        if sheet_entry is not None and sheet_entry.status != "matched":
            reason_text = sheet_entry.reason or reasons
            if sheet_entry.candidates:
                candidates = ", ".join(sheet_entry.candidates)
        unpriced_rows.append((
            _scope_label(entry["scope_type"], entry["scope_name"]), entry["model"], reason_text, candidates,
        ))

    incomplete_rows = []
    for scope in run.index["scopes"]:
        if scope["status"] != "incomplete":
            continue
        reasons = ", ".join(scope.get("reasons", [])) or "none"
        assumptions = ", ".join(scope.get("assumptions", [])) or "none"
        incomplete_rows.append((_scope_label(scope["scope_type"], scope["scope_name"]), reasons, assumptions))

    lines = ["## Blocking problems", ""]
    if not unpriced_rows and not incomplete_rows:
        lines += ["No blocking problems.", ""]
        return lines
    if unpriced_rows:
        lines += ["### Unpriced models", ""]
        lines += _md_table(["Scope", "Model", "Reason", "Candidates"], unpriced_rows) + [""]
    if incomplete_rows:
        lines += ["### Incomplete scopes", ""]
        lines += _md_table(["Scope", "Reasons", "Assumptions"], incomplete_rows) + [""]
    return lines


def _cost_table(run: CostRun) -> list[str]:
    by_scope: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for entry in run.period_summary.get("entries", []):
        if entry["model"] is None:
            by_scope[(entry["scope_type"], entry["scope_name"])].append(entry)

    lines = ["## Cost table (per scope and period)", ""]
    if not by_scope:
        lines += ["No scope totals (every scope is incomplete).", ""]
        return lines
    for key in sorted(by_scope, key=_scope_order):
        entries = sorted(by_scope[key], key=lambda e: (e["period_type"], e["start"]))
        rows = [
            (e["period_type"], f"{e['start']} to {e['end']}", e["status"],
             _usd(e["expected"]), _usd(e["p10"]), _usd(e["p50"]), _usd(e["p90"]))
            for e in entries
        ]
        lines += [f"### {_scope_label(*key)}", ""]
        lines += _md_table(["Period", "Range", "Status", "Expected", "p10", "p50", "p90"], rows) + [""]
    return lines


def _read_charts_index(path: Path) -> dict | None:
    """This run does not draw charts itself (design §14.5); if `plot_cost` has
    already drawn some into `charts/`, its `index.json` is the sole source of
    truth for what the report's Charts section links. Absent, unreadable, or
    malformed -> `None` (the section is omitted, not an error)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("plots"), list) else None


def _chart_entry_label(entry: dict) -> str:
    if entry.get("model"):
        return str(entry["model"])
    return {"total": "Total daily cost fan", "breakdown": "Model breakdown (expected)"}.get(
        entry.get("view"), str(entry.get("view")))


def _charts_section(charts_index: dict | None) -> list[str]:
    """Design §14.5/T2: lists exactly the files in `charts/index.json` (labels
    from `scope_type`, `scope_name`, `view`, `model`) when that file exists;
    omitted (no heading at all) otherwise -- there is nothing to hide when this
    run has never been plotted."""
    if not charts_index or not charts_index["plots"]:
        return []
    lines = ["## Charts", ""]
    by_scope: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for entry in charts_index["plots"]:
        by_scope[(entry["scope_type"], entry["scope_name"])].append(entry)
    for key in sorted(by_scope, key=_scope_order):
        lines.append(f"### {_scope_label(*key)}")
        for entry in sorted(by_scope[key], key=lambda e: (e.get("view") or "", e.get("model") or "")):
            links = []
            for name in entry.get("files", []):
                if name.endswith(".html"):
                    links.append(f"[interactive](charts/{name})")
                elif name.endswith(".png"):
                    links.append(f"[PNG](charts/{name})")
            lines.append(f"- {_chart_entry_label(entry)}: " + " · ".join(links))
        lines.append("")
    return lines


def _scenario_section(run: CostRun, output_dir: Path) -> list[str]:
    scenario_name = run.request.get("scenario")
    if not scenario_name or scenario_name == "baseline":
        return []

    lines = [f"## Scenario: `{scenario_name}`", ""]
    scenario_path = output_dir / "scenario.yaml"
    if scenario_path.is_file():
        try:
            config = yaml.safe_load(scenario_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            config = {}
        scope = config.get("scope") or {"type": "organization"}
        lines.append(f"- Declared scope: {scope.get('type', 'organization')} {scope.get('name') or ''}".rstrip())
        for change in config.get("changes", []):
            if "model_transfer" in change:
                c = change["model_transfer"]
                lines.append(
                    f"- Model transfer: {(c.get('fraction') or 0) * 100:.0f}% of `{c.get('from')}` "
                    f"to `{c.get('to')}` (token_factor={c.get('token_factor', 1.0)}, effective "
                    f"{c.get('effective_from') or 'the first forecast date'})"
                )
            elif "agent_transfer" in change:
                c = change["agent_transfer"]
                lines.append(
                    f"- Agent transfer: {(c.get('fraction') or 0) * 100:.0f}% of `{c.get('from')}`'s "
                    f"tokens to `{c.get('to')}` (token_factor={c.get('token_factor', 1.0)}, effective "
                    f"{c.get('effective_from') or 'the first forecast date'})"
                )
            elif "growth" in change:
                c = change["growth"]
                lines.append(
                    f"- Growth: tokens x{c.get('factor')} (effective "
                    f"{c.get('effective_from') or 'the first forecast date'})"
                )
        lines.append("")
    else:
        lines += ["- `scenario.yaml` not found alongside this run; changes not shown.", ""]

    token_totals = _scenario_series_tokens(run)
    changed_rows = []
    for entry in run.index["series"]:
        tags = [a for a in entry.get("assumptions", []) if a.startswith("scenario_")]
        if not tags:
            continue
        key = (entry["scope_type"], entry["scope_name"], entry["model"])
        changed_rows.append((
            _scope_label(entry["scope_type"], entry["scope_name"]), entry["model"],
            ", ".join(tags), _number(token_totals.get(key, 0.0)),
        ))
    lines += ["### Token shifts (post-scenario expected tokens, from daily_expected)", ""]
    if changed_rows:
        lines += _md_table(
            ["Scope", "Model", "Scenario effect", "Expected tokens (run total)"], changed_rows
        ) + [""]
    else:
        lines += ["No series carries a scenario assumption tag.", ""]
    return lines


def _thin_evidence_mix(run: CostRun) -> list[str]:
    lines = ["## Thin-evidence mix (own_weight < 0.9)", ""]
    thin = [e for e in run.mix.get("entries", []) if e["own_weight"] < 0.9]
    if not thin:
        lines += ["No series has thin evidence (every own_weight is >= 0.9).", ""]
        return lines
    rows = [
        (_scope_label(e["scope_type"], e["scope_name"]), e["model"], f"{e['own_weight']:.3f}",
         e["active_days"], f"{e['window']['start']} to {e['window']['end']}")
        for e in thin
    ]
    lines += _md_table(["Scope", "Model", "own_weight", "Active days", "Window"], rows) + [""]
    return lines


def _price_mapping(run: CostRun) -> list[str]:
    lines = ["## Price mapping", "", f"Price version: `{run.rate_sheet.price_version}`", ""]
    matched = [m for m in run.rate_sheet.models if m.status == "matched"]
    if not matched:
        lines += ["No matched models.", ""]
        return lines
    rows = [
        (m.usage_model_id, m.vendor or "-", m.channel or "-", m.model_id or "-", m.match_step or "-")
        for m in matched
    ]
    lines += _md_table(["Usage model", "Vendor", "Channel", "Model ID", "Match step"], rows) + [""]
    return lines


def _assumptions(run: CostRun) -> list[str]:
    labels: set[str] = set()
    for scope in run.index["scopes"]:
        labels.update(scope.get("assumptions", []))
    for series in run.index["series"]:
        labels.update(series.get("assumptions", []))

    lines = ["## Assumptions", ""]
    if labels:
        lines += [f"- **{label}**: {_assumption_text(label)}" for label in sorted(labels)]
        lines.append("")
    lines += [f"- {note}" for note in _STANDARD_NOTES]
    lines.append("")
    return lines


def _header(run: CostRun) -> list[str]:
    dates = run.index["dates"]
    request = run.request
    return [
        "# Cost run", "",
        f"Organization: `{request.get('org_id')}`  ",
        f"Scenario: `{request.get('scenario')}`  ",
        f"Status: **{_run_status(run)}**  ",
        f"Dates: `{dates[0]}` through `{dates[-1]}`; samples: {run.index.get('samples')}  ",
        f"Price version: `{run.rate_sheet.price_version}`",
        "",
    ]


def render_report(run: CostRun, output_dir: Path) -> list[Path]:
    """Write `report.md` into `output_dir` (design §14.5). This run does not draw
    charts itself; if `plot_cost` has already drawn some into `charts/` (its
    `index.json` present), the Charts section links exactly those files,
    otherwise the section is omitted -- so re-rendering after `plot_cost` runs
    (or before it ever has) both work from the same function. `output_dir` is
    the cost run's own directory -- `scenario.yaml`, if this is a scenario run,
    is read from there. Returns `[Path("report.md")]`: neither `report.md` nor
    `charts/` is listed in the cost manifest (design §14.5), so this return
    value is informational only."""
    output_dir = Path(output_dir)
    charts_index = _read_charts_index(output_dir / "charts" / "index.json")

    lines: list[str] = []
    lines += _header(run)
    lines += _blocking_problems(run)
    lines += _cost_table(run)
    lines += _charts_section(charts_index)
    lines += _scenario_section(run, output_dir)
    lines += _thin_evidence_mix(run)
    lines += _price_mapping(run)
    lines += _assumptions(run)

    report_path = output_dir / "report.md"
    try:
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ExecutionError("cannot write cost report") from exc

    _LOG.info("cost.report_written", scopes=len(run.index["scopes"]), series=len(run.index["series"]),
              charts=len(charts_index["plots"]) if charts_index else 0)
    return [Path("report.md")]
