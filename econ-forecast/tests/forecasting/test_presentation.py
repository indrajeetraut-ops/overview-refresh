from __future__ import annotations

from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg

from forecast.forecasting.errors import InputError
from forecast.forecasting.presentation import build_figure, write_report
from forecast.forecasting.request import ForecastRequest
from forecast.forecasting.runner import run_forecast

VALUES = np.array([[100, 140, 80], [110, 90, 95], [90, 130, 85]], dtype=np.float64)
DATES = ["2026-09-01", "2026-09-02", "2026-09-03"]


def _entry(status="qualified", path="paths/team-payments__model-a.npy"):
    return {
        "series_id": "series-a",
        "org_id": "org-a",
        "scope_type": "team",
        "scope_id": "team-a",
        "scope_name": "Payments",
        "model": "model-a",
        "status": status,
        "path": path,
        "shape": [3, 3],
        "dtype": "float64",
        "assumptions": (
            ["continued_inactivity"]
            if status == "inactive"
            else ["fixed_allocation_shares"]
        ),
        "reasons": [],
    }


def test_build_figure_artists_match_independent_oracle():
    figure = build_figure(_entry(), DATES, VALUES)
    axis = figure.axes[0]
    np.testing.assert_allclose(
        axis.lines[0].get_ydata(), [320 / 3, 295 / 3, 305 / 3], rtol=1e-12, atol=1e-9
    )
    assert list(axis.lines[0].get_xdata()) == [
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    vertices = axis.collections[0].get_paths()[0].vertices
    for day, low, high in zip(DATES, [84, 91, 86], [132, 107, 122]):
        x = mdates.date2num(date.fromisoformat(day))
        ys = vertices[np.isclose(vertices[:, 0], x), 1]
        assert np.any(np.isclose(ys, low))
        assert np.any(np.isclose(ys, high))
    assert axis.get_ylabel() == "Total tokens"
    assert "qualified" in axis.get_title()
    assert figure.get_layout_engine() is not None


@pytest.mark.parametrize("days", [30, 90])
def test_date_locator_is_readable_for_real_horizons(days):
    dates = [
        (date(2026, 9, 1) + timedelta(days=offset)).isoformat()
        for offset in range(days)
    ]
    paths = np.tile(np.arange(days, dtype=np.float64)[:, None], (1, 4))
    figure = build_figure(_entry(), dates, paths)
    FigureCanvasAgg(figure).draw()
    labels = [
        item.get_text() for item in figure.axes[0].get_xticklabels() if item.get_text()
    ]
    assert 3 <= len(labels) <= 9
    assert len(set(labels)) == len(labels)


def test_inactive_title_and_invalid_arrays():
    figure = build_figure(_entry(status="inactive"), DATES, np.zeros_like(VALUES))
    assert "inactive" in figure.axes[0].get_title()
    assert "assumed continued inactivity" in figure.axes[0].get_title()
    with pytest.raises(InputError):
        build_figure(_entry(), DATES, VALUES[:, :0])
    invalid = VALUES.copy()
    invalid[0, 0] = np.nan
    with pytest.raises(InputError):
        build_figure(_entry(), DATES, invalid)


def test_thirty_day_chart_reserves_wrapped_readable_assumption_footer(tmp_path):
    dates = [(date(2026, 9, 1) + timedelta(days=offset)).isoformat() for offset in range(30)]
    values = np.column_stack([
        np.linspace(100_000, 900_000, 30),
        np.linspace(200_000, 1_000_000, 30),
        np.linspace(300_000, 1_100_000, 30),
    ]).astype(np.float64)
    entry = {**_entry(), "scope_name": "Payments and Platform Engineering Team",
             "model": "gpt-5.6-sol-production-catalog-name",
             "assumptions": ["uncalibrated_model_estimate", "fixed_allocation_shares",
                             "common_residual_window"]}
    figure = build_figure(entry, dates, values)
    target = tmp_path / "thirty-day-long-labels.png"
    FigureCanvasAgg(figure).print_png(target)
    footer = figure.texts[0]
    assert "_" not in footer.get_text()
    assert "uncalibrated model estimate" in footer.get_text()
    assert figure.axes[0].get_position().y0 > 0.15
    assert len([label for label in figure.axes[0].get_xticklabels() if label.get_text()]) <= 9
    assert target.stat().st_size > 20_000


def test_report_uses_saved_totals_windows_failures_and_actual_metrics(tmp_path):
    qualified = _entry()
    failed = {
        **_entry(),
        "series_id": "series-f",
        "model": "model-f",
        "status": "sampling_failed",
        "path": None,
        "shape": None,
        "assumptions": [],
        "reasons": ["weekday sampling failed"],
    }
    index = {
        "scopes": [
            {
                "org_id": "org-a",
                "scope_type": "team",
                "scope_id": "team-a",
                "scope_name": "Payments",
                "status": "incomplete",
                "risk_ready": False,
                "assumptions": ["fixed_allocation_shares"],
                "reasons": ["weekday sampling failed"],
            }
        ],
        "series": [qualified, failed],
    }
    diagnostics = {
        "series": [
            {
                "series_id": "series-a",
                "history_start": "2026-07-01",
                "history_end": "2026-08-29",
                "sampling_start": "2026-08-01",
                "sampling_end": "2026-08-29",
                "sampling_days": 29,
                "last_active_date": "2026-08-29",
            },
            {
                "series_id": "series-f",
                "history_start": "2026-08-01",
                "history_end": "2026-08-29",
                "sampling_start": None,
                "sampling_end": None,
                "sampling_days": None,
                "last_active_date": "2026-08-20",
            },
        ],
        "runtime": {
            "wall_seconds": 1.25,
            "cpu_user_seconds": 0.75,
            "cpu_system_seconds": 0.125,
            "peak_rss_bytes": 123456789,
            "output_bytes": 7654321,
            "workers": 2,
            "numerical_threads": 1,
            "memory_measurement": "summed process-tree RSS",
            "cpu_measurement": "process-tree deltas",
            "stage_seconds": {"preparation": 0.5},
            "workload": {
                "series": 2,
                "paths": 1000,
                "requested_days": 30,
                "internal_days": 61,
            },
        },
    }
    summaries = {
        "status": "partial",
        "entries": [
            {
                "org_id": "org-a",
                "scope_type": "team",
                "scope_id": "team-a",
                "model": "model-a",
                "period_type": "requested",
                "status": "qualified",
                "expected_tokens": 1000.25,
                "p10": 800.0,
                "p50": 990.0,
                "p90": 1250.0,
            },
        ],
    }
    target = write_report(
        tmp_path,
        index,
        diagnostics,
        summaries,
        {"requested_start": "2026-09-01", "requested_end": "2026-09-30"},
    )
    text = target.read_text(encoding="utf-8")
    for expected in [
        "Status: **partial**",
        "### org-a / team / Payments",
        "| Model / aggregate | Expected tokens | p10 | p50 | p90 | Status |",
        "| model-a | 1,000.250 | 800.000 | 990.000 | 1,250.000 | qualified |",
        "| model-a | qualified | 2026-07-01 to 2026-08-29 | 2026-08-01 to 2026-08-29 | 29 | 2026-08-29 |",
        "| model-f | sampling_failed | 2026-08-01 to 2026-08-29 | unavailable | unavailable | 2026-08-20 |",
        "| model-f | unavailable | unavailable | unavailable | unavailable | sampling_failed |",
        "| scope aggregate | unavailable | unavailable | unavailable | unavailable | incomplete |",
        "weekday sampling failed",
        "Peak RSS bytes: 123,456,789",
        "output bytes: 7,654,321",
        "preparation=0.500000s",
        "fixed over the forecast horizon",
        "Forecast probabilities are uncalibrated",
        "continued inactivity",
        "views overlap",
    ]:
        assert expected in text


def test_report_labels_unavailable_output_metric(tmp_path):
    hostile = "<img src=x onerror=globalThis.pwned=1>"
    target = write_report(
        tmp_path,
        {
            "scopes": [
                {
                    "org_id": "org",
                    "scope_type": "team",
                    "scope_id": "team",
                    "scope_name": hostile,
                    "status": "incomplete",
                    "risk_ready": False,
                    "assumptions": [],
                    "reasons": [],
                }
            ],
            "series": [],
        },
        {"series": [], "runtime": {"output_bytes": 0, "workload": {}}},
        {"status": "partial", "entries": []},
        {"requested_start": "2026-09-01", "requested_end": "2026-09-30"},
    )
    text = target.read_text(encoding="utf-8")
    assert "output bytes: unavailable until final artifact accounting" in text
    assert "&lt;img src=x onerror=globalThis.pwned=1&gt;" in text
    assert hostile not in text


def test_report_escapes_markdown_cells_and_keeps_inactive_aggregate_distinct(tmp_path):
    entry = {**_entry(status="inactive"), "org_id": "org|north\nwest",
             "scope_name": "Pay|ments\nOps", "model": "m|one"}
    scope = {key: entry[key] for key in ("org_id", "scope_type", "scope_id", "scope_name")}
    scope.update(status="inactive", risk_ready=False, assumptions=["continued_inactivity"], reasons=[])
    summary = {**scope, "model": None, "period_type": "requested", "status": "inactive",
               "expected_tokens": 0.0, "p10": 0.0, "p50": 0.0, "p90": 0.0,
               "assumptions": ["continued_inactivity"]}
    report = write_report(tmp_path, {"scopes": [scope], "series": [entry]},
                          {"series": [{"series_id": entry["series_id"], "sampling_days": None}],
                           "runtime": {}},
                          {"status": "complete", "entries": [summary]},
                          {"origin": "2026-08-29", "requested_start": "2026-09-01",
                           "requested_end": "2026-09-30"}).read_text()
    assert "Pay&#124;ments Ops" in report
    assert "org&#124;north west" in report
    assert "m&#124;one" in report
    assert "| scope aggregate | 0.000 | 0.000 | 0.000 | 0.000 | inactive |" in report
    assert "unavailable" in report
    assert "Resolved origin: `2026-08-29`" in report


def test_report_complete_qualified_scope_has_model_and_aggregate_totals(tmp_path):
    entry = _entry()
    scope = {key: entry[key] for key in ("org_id", "scope_type", "scope_id", "scope_name")}
    scope.update(status="complete", risk_ready=True, assumptions=[], reasons=[])
    model = {**scope, "model": "model-a", "period_type": "requested", "status": "qualified",
             "expected_tokens": 1234.5, "p10": 1000, "p50": 1200, "p90": 1500,
             "assumptions": []}
    aggregate = {**model, "model": None, "status": "complete"}
    report = write_report(tmp_path, {"scopes": [scope], "series": [entry]},
                          {"series": [{"series_id": entry["series_id"],
                                       "history_start": "2026-07-01", "history_end": "2026-08-29",
                                       "sampling_start": "2026-08-01", "sampling_end": "2026-08-29",
                                       "sampling_days": 29}], "runtime": {}},
                          {"status": "complete", "entries": [model, aggregate]},
                          {"origin": "2026-08-29", "requested_start": "2026-09-01",
                           "requested_end": "2026-09-30"}).read_text()
    assert "| model-a | 1,234.500 | 1,000.000 | 1,200.000 | 1,500.000 | qualified |" in report
    assert "| scope aggregate | 1,234.500 | 1,000.000 | 1,200.000 | 1,500.000 | complete |" in report
    assert report.count("### org-a / team / Payments") == 2


def test_real_partial_run_reports_unavailable_totals_without_zero(tmp_path):
    fixture_path = Path(__file__).parents[1] / "acceptance" / "conftest.py"
    spec = importlib.util.spec_from_file_location("_report_daily_fixture", fixture_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    dataset = fixture.make_daily_dataset(
        tmp_path / "daily", token_fn=lambda day, model, user: (100, 0, 0)
        if day == 0 else None,
        coverage_fn=lambda day, model, user: "partial" if day == 0 else "complete",
    )
    config = fixture.write_forecast_config(tmp_path / "forecast.yaml", dataset)
    output = tmp_path / "forecast"
    result = run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    assert result.status == "partial"
    assert json.loads((output / "period_summary.json").read_text())["entries"] == []
    report = (output / "report.md").read_text()
    assert "| model-a | unavailable | unavailable | unavailable | unavailable | insufficient_startup |" in report
    assert "| scope aggregate | unavailable | unavailable | unavailable | unavailable | incomplete |" in report
    assert "insufficient_startup" in report and "reasons:" in report
