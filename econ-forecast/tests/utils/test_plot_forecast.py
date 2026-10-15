from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from forecast.forecasting.errors import InputError
from forecast.utils.plot_forecast import (
    build_figure,
    build_matplotlib_figure,
    export_html,
    export_png,
)

_PAYMENTS = "Payments <script>globalThis.pwned=1</script>"


class FixedRun:
    def __init__(self, root: Path, arrays: dict[str, np.ndarray]):
        self.root = root
        self.arrays = arrays
        self.manifest = {"schema_version": 1, "status": "complete", "input_sources": {}}
        dates = ["2026-09-01", "2026-09-02", "2026-09-03"]
        self.index = {
            "dates": dates,
            "sample_ids": [0, 1, 2],
            "scopes": [{
                "org_id": "org-a", "scope_type": "team", "scope_id": "shared",
                "scope_name": _PAYMENTS,
                "status": "incomplete", "assumptions": ["uncalibrated_weekday_resampling"],
            }],
            "series": [
                {"series_id": "a", "org_id": "org-a", "scope_type": "team",
                 "scope_id": "shared", "scope_name": _PAYMENTS,
                 "model": "model-a", "status": "qualified", "assumptions": [], "path": "a.npy"},
                {"series_id": "b", "org_id": "org-a", "scope_type": "team",
                 "scope_id": "shared", "scope_name": "Payments", "model": "model-b",
                 "status": "inactive", "assumptions": ["continued_inactivity"], "path": "b.npy"},
                {"series_id": "double", "org_id": "org-a", "scope_type": "team",
                 "scope_id": "shared", "scope_name": "Payments", "model": "model-double",
                 "status": "qualified", "assumptions": [], "path": "double.npy"},
                {"series_id": "failed", "org_id": "org-a", "scope_type": "team",
                 "scope_id": "shared", "scope_name": "Payments", "model": "model-failed",
                 "status": "sampling_failed", "assumptions": [], "path": None},
                {"series_id": "other-org", "org_id": "org-b", "scope_type": "team",
                 "scope_id": "shared", "scope_name": "Payments", "model": "model-a",
                 "status": "qualified", "assumptions": [], "path": "other.npy"},
                {"series_id": "organization", "org_id": "org-a", "scope_type": "organization",
                 "scope_id": "org-a", "scope_name": "Org A", "model": "model-a",
                 "status": "qualified", "assumptions": [], "path": "organization.npy"},
            ],
        }

    def load_paths(self, entry):
        return self.arrays[entry["series_id"]]


@pytest.fixture
def fixed_run(tmp_path):
    values = np.array([[100, 140, 80], [110, 90, 95], [90, 130, 85]], dtype=np.float64)
    return FixedRun(tmp_path / "run", {
        "a": values, "b": np.zeros_like(values), "double": values * 2,
        "other-org": values * 99,
        "organization": values,
    })


def _traces(figure, suffix):
    return [trace for trace in figure.data if str(trace.name).endswith(suffix)]


def _base_summary(**overrides):
    summary = {
        "status": "complete", "selected_models": ["model-a"], "plotted_models": ["model-a"],
        "unavailable_models": [], "full_sample_count": 3, "displayed_sample_count": 1,
        "org_id": "org-a", "scope_type": "team", "scope_name": _PAYMENTS,
        "assumptions": ["uncalibrated_weekday_resampling"],
        "band": [10, 90], "actuals_source": "unavailable", "actuals_overlaid": False,
        "actuals_note": None,
    }
    summary.update(overrides)
    return summary


def test_hand_oracle_and_model_org_separation(fixed_run):
    figure, summary = build_figure(
        fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS,
        models=["model-a"], samples=1,
    )
    assert summary == _base_summary()
    np.testing.assert_allclose(_traces(figure, "mean")[0].y, [320 / 3, 295 / 3, 305 / 3], rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(_traces(figure, " p10")[0].y, [84, 91, 86], rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(_traces(figure, "p10–p90")[0].y, [132, 107, 122], rtol=1e-12, atol=1e-9)
    np.testing.assert_array_equal(_traces(figure, "sample 0")[0].y, [100, 110, 90])
    assert list(_traces(figure, "mean")[0].x) == ["2026-09-01", "2026-09-02", "2026-09-03"]

    double, _ = build_figure(
        fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS,
        models=["model-double"], samples=0,
    )
    np.testing.assert_allclose(_traces(double, "mean")[0].y,
                               np.array([320 / 3, 295 / 3, 305 / 3]) * 2)


@pytest.mark.parametrize("count,expected", [(0, 0), (1, 1), (10, 3), (99, 3)])
def test_display_count_does_not_change_all_sample_statistics(fixed_run, count, expected):
    figure, summary = build_figure(fixed_run, org_id="org-a", scope_type="team",
                                   scope_name=_PAYMENTS, models=["model-a"], samples=count)
    assert summary["displayed_sample_count"] == expected
    assert len([trace for trace in figure.data if " sample " in trace.name]) == expected
    np.testing.assert_allclose(_traces(figure, "mean")[0].y, [320 / 3, 295 / 3, 305 / 3])


def test_partial_and_inactive_are_explicit(fixed_run):
    figure, summary = build_figure(fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS)
    assert summary["status"] == "partial"
    assert summary["displayed_sample_count"] == 0
    assert not [trace for trace in figure.data if " sample " in trace.name]
    assert summary["plotted_models"] == ["model-a", "model-b", "model-double"]
    assert summary["unavailable_models"] == ["model-failed"]
    assert "continued_inactivity" in summary["assumptions"]
    assert "model-failed" in figure.layout.title.text
    assert "pointwise p10–p90" in figure.layout.title.text
    assert "actuals: unavailable" in figure.layout.title.text


def test_invalid_selections_and_axes(fixed_run):
    calls = [
        dict(org_id="missing", scope_type="team", scope_name=_PAYMENTS),
        dict(org_id="org-a", scope_type="team", scope_name=None),
        # Design D16: an omitted scope_name for team/project is no longer "all of
        # them in one file" -- that expansion is the driver's job (run.py), one
        # concrete scope per call. This function now requires a concrete name.
        dict(org_id="org-a", scope_type="project", scope_name=None),
        dict(org_id="org-a", scope_type="organization", scope_name="org-a"),
        dict(org_id="org-a", scope_type="team", scope_name=_PAYMENTS, models=["unknown"]),
        dict(org_id="org-a", scope_type="team", scope_name=_PAYMENTS, models=["model-failed"]),
        dict(org_id="org-a", scope_type="team", scope_name=_PAYMENTS, samples=-1),
        dict(org_id="org-a", scope_type="team", scope_name=_PAYMENTS, band=(90, 10)),
        dict(org_id="org-a", scope_type="team", scope_name=_PAYMENTS, band=(0, 100)),
        dict(org_id="org-a", scope_type="team", scope_name=_PAYMENTS, band=(10,)),
    ]
    for kwargs in calls:
        with pytest.raises(InputError):
            build_figure(fixed_run, **kwargs)


def test_organization_cli_omission_resolves_to_org_id(fixed_run):
    _, summary = build_figure(fixed_run, org_id="org-a", scope_type="organization", samples=0)
    assert summary["scope_name"] == "org-a"
    assert summary["plotted_models"] == ["model-a"]


def test_name_selection_rejects_unknown_and_ambiguous_saved_names(fixed_run):
    with pytest.raises(InputError, match="unknown team name"):
        build_figure(fixed_run, org_id="org-a", scope_type="team", scope_name="missing")
    fixed_run.index["scopes"].append({
        "org_id": "org-b", "scope_type": "team", "scope_id": "different",
        "scope_name": _PAYMENTS})
    build_figure(fixed_run, org_id="org-a", scope_type="team",
                scope_name=_PAYMENTS, models=["model-a"])
    fixed_run.index["scopes"].append({
        "org_id": "org-a", "scope_type": "team", "scope_id": "another",
        "scope_name": _PAYMENTS})
    with pytest.raises(InputError, match="ambiguous team name"):
        build_figure(fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS)


def test_band_is_configurable(fixed_run):
    figure, summary = build_figure(fixed_run, org_id="org-a", scope_type="team",
                                   scope_name=_PAYMENTS, models=["model-a"], band=(5, 95))
    assert summary["band"] == [5, 95]
    values = fixed_run.arrays["a"]
    expected_low = np.quantile(values, 0.05, axis=1, method="linear")
    expected_high = np.quantile(values, 0.95, axis=1, method="linear")
    np.testing.assert_allclose(_traces(figure, " p5")[0].y, expected_low)
    np.testing.assert_allclose(_traces(figure, "p5–p95")[0].y, expected_high)
    assert "Bands are pointwise p5–p95" in figure.layout.title.text


def test_actuals_overlay_draws_observed_line_and_seam(fixed_run):
    history = SimpleNamespace(
        dates=[date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)],
        values=np.array([10.0, 20.0, 30.0]),
    )
    actuals = {("team", "shared", "model-a"): history}
    figure, summary = build_figure(
        fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS,
        models=["model-a"], actuals=actuals, actuals_source="auto",
    )
    assert summary["actuals_source"] == "auto"
    assert summary["actuals_overlaid"] is True
    observed = _traces(figure, "observed")
    assert len(observed) == 1
    np.testing.assert_array_equal(observed[0].y, [10.0, 20.0, 30.0])
    assert list(observed[0].x) == ["2026-08-30", "2026-08-31", "2026-09-01"]
    assert len(figure.layout.shapes) == 1
    assert "actuals: auto (overlaid)" in figure.layout.title.text


def test_actuals_source_without_matching_series_is_noted(fixed_run):
    figure, summary = build_figure(
        fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS,
        models=["model-a"], actuals={}, actuals_source="auto",
        actuals_note="actuals may differ from the fitted input (hash mismatch)",
    )
    assert summary["actuals_overlaid"] is False
    assert not _traces(figure, "observed")
    assert "actuals: auto (no matching series)" in figure.layout.title.text
    assert "hash mismatch" in figure.layout.title.text


def test_project_scopes_are_drawn_one_at_a_time_by_name(fixed_run):
    """Design D16: "one file per project" is the driver's job (run.py expands
    an omitted scope_name into one call per project); this function itself
    always draws exactly one named project, never several combined."""
    project_scopes = [
        ("project-a", "Alpha", "qualified", "project-a", 1),
        ("project-b", "Beta", "qualified", "project-b", 2),
        ("project-c", "Gamma", "sampling_failed", "project-c", None),
    ]
    for scope_id, name, status, series_id, multiplier in project_scopes:
        fixed_run.index["scopes"].append({
            "org_id": "org-a", "scope_type": "project", "scope_id": scope_id,
            "scope_name": name, "status": "complete" if multiplier else "incomplete",
            "assumptions": [],
        })
        fixed_run.index["series"].append({
            "series_id": series_id, "org_id": "org-a", "scope_type": "project",
            "scope_id": scope_id, "scope_name": name, "model": "model-a",
            "status": status, "assumptions": [], "path": f"{series_id}.npy" if multiplier else None,
        })
        if multiplier:
            fixed_run.arrays[series_id] = fixed_run.arrays["a"] * multiplier

    one, one_summary = build_figure(fixed_run, org_id="org-a", scope_type="project", scope_name="Beta")
    assert one_summary["status"] == "complete"
    assert one_summary["scope_name"] == "Beta"
    assert len(_traces(one, "mean")) == 1
    np.testing.assert_allclose(_traces(one, "mean")[0].y, np.array([320 / 3, 295 / 3, 305 / 3]) * 2)

    other, other_summary = build_figure(fixed_run, org_id="org-a", scope_type="project", scope_name="Alpha")
    assert other_summary["status"] == "complete"
    np.testing.assert_allclose(_traces(other, "mean")[0].y, [320 / 3, 295 / 3, 305 / 3])

    with pytest.raises(InputError, match="none of the selected models"):
        build_figure(fixed_run, org_id="org-a", scope_type="project", scope_name="Gamma")
    with pytest.raises(InputError, match="unknown project name"):
        build_figure(fixed_run, org_id="org-a", scope_type="project", scope_name="Delta")

    fixed_run.index["series"].append({
        "series_id": "project-b-second-model", "org_id": "org-a", "scope_type": "project",
        "scope_id": "project-b", "scope_name": "Beta", "model": "model-b",
        "status": "qualified", "assumptions": [], "path": "project-b-second-model.npy",
    })
    fixed_run.arrays["project-b-second-model"] = fixed_run.arrays["a"] * 3
    both, both_summary = build_figure(fixed_run, org_id="org-a", scope_type="project", scope_name="Beta")
    assert both_summary["selected_models"] == ["model-a", "model-b"]
    assert "model-b mean" in [trace.name for trace in _traces(both, "mean")]
    filtered, filtered_summary = build_figure(
        fixed_run, org_id="org-a", scope_type="project", scope_name="Beta", models=["model-b"])
    assert [trace.name for trace in _traces(filtered, "mean")] == ["model-b mean"]
    assert filtered_summary["selected_models"] == ["model-b"]


def test_repeatable_and_source_preserved(fixed_run, tmp_path):
    fixed_run.root.mkdir()
    source = fixed_run.root / "marker"
    source.write_bytes(b"immutable")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    first, _ = build_figure(fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS,
                            models=["model-a"], samples=2)
    second, _ = build_figure(fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS,
                             models=["model-a"], samples=2)
    assert [list(trace.y) for trace in first.data] == [list(trace.y) for trace in second.data]
    output = tmp_path / "plot.html"
    assert export_html(first, output) == output
    assert "plotly" in output.read_text(encoding="utf-8").lower()
    assert not re.search(r"<script[^>]+src=[\"']https?://", output.read_text(encoding="utf-8"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with pytest.raises(InputError):
        export_html(first, output)


def test_export_html_references_a_shared_plotly_js_by_relative_path(fixed_run, tmp_path):
    """design section 8: passing a relative path as `plotlyjs` makes the HTML
    reference the shared file instead of embedding a copy -- a few KB, not
    the ~4 MB an embedded copy would be."""
    figure, _ = build_figure(fixed_run, org_id="org-a", scope_type="team",
                             scope_name=_PAYMENTS, models=["model-a"])
    output = tmp_path / "charts" / "plot.html"
    output.parent.mkdir()
    export_html(figure, output, plotlyjs="plotly.min.js")
    text = output.read_text(encoding="utf-8")
    assert re.search(r'<script[^>]+src="plotly\.min\.js"', text)
    assert len(text.encode("utf-8")) < 100_000


def test_matplotlib_figure_matches_plotly_content(fixed_run):
    figure, summary = build_figure(fixed_run, org_id="org-a", scope_type="team",
                                   scope_name=_PAYMENTS, models=["model-a"], samples=1)
    mpl_figure, mpl_summary = build_matplotlib_figure(
        fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS,
        models=["model-a"], samples=1,
    )
    assert mpl_summary == summary
    assert len(mpl_figure.axes) == 1
    mean_line = mpl_figure.axes[0].get_lines()[0]
    np.testing.assert_allclose(mean_line.get_ydata(), _traces(figure, "mean")[0].y)


def test_matplotlib_figure_draws_actuals_and_seam(fixed_run):
    history = SimpleNamespace(
        dates=[date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)],
        values=np.array([10.0, 20.0, 30.0]),
    )
    actuals = {("team", "shared", "model-a"): history}
    mpl_figure, summary = build_matplotlib_figure(
        fixed_run, org_id="org-a", scope_type="team", scope_name=_PAYMENTS,
        models=["model-a"], actuals=actuals, actuals_source="auto",
    )
    assert summary["actuals_overlaid"] is True
    axis = mpl_figure.axes[0]
    labels = [line.get_label() for line in axis.get_lines()]
    assert "Observed (actual)" in labels
    assert axis.lines  # axvline for the seam also drew a Line2D


def test_export_png_writes_a_valid_file_and_refuses_overwrite(fixed_run, tmp_path):
    figure, _ = build_matplotlib_figure(fixed_run, org_id="org-a", scope_type="team",
                                        scope_name=_PAYMENTS, models=["model-a"])
    output = tmp_path / "plot.png"
    assert export_png(figure, output) == output
    assert output.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(InputError):
        export_png(figure, output)
