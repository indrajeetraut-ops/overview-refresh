"""Preview rendering smoke tests: files get produced with the right shape/permissions.
Deliberately no browser automation (Playwright is out of scope here, per the brief)."""
from __future__ import annotations

from forecast.curve.config import CurveConfig
from forecast.curve.model import evaluate_curve, resolve_anchors
from forecast.curve.preview import render_html, render_png

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _config(**overrides) -> CurveConfig:
    data = {
        "schema_version": 1, "name": "preview-test", "interp": "pchip",
        "axis": {"start": 1, "end": 10}, "anchors": [[1, "1.0"], [10, "3.0"]],
        "preview": {}, "description": None,
    }
    data.update(overrides)
    return CurveConfig.model_validate(data)


def _sample(config, n):
    resolved = resolve_anchors(config.anchors, config.axis.start, config.axis.end)
    levels = evaluate_curve(resolved, n)
    return resolved, levels


def test_render_html_writes_a_readable_file_with_0644_permissions(tmp_path):
    config = _config()
    resolved, levels = _sample(config, 5)
    target = tmp_path / "preview.html"
    render_html(config, resolved, levels, target)
    assert target.exists()
    assert target.stat().st_mode & 0o777 == 0o644
    text = target.read_text(encoding="utf-8")
    assert "<html" in text.lower()


def test_render_png_writes_a_valid_png(tmp_path):
    config = _config()
    resolved, levels = _sample(config, 5)
    target = tmp_path / "preview.png"
    render_png(config, resolved, levels, target)
    assert target.exists()
    assert target.read_bytes()[: len(PNG_MAGIC)] == PNG_MAGIC


def test_render_html_and_png_succeed_on_both_axis_modes(tmp_path):
    config = _config()
    resolved, levels = _sample(config, 6)
    for day_axis in (False, True):
        html_target = tmp_path / f"preview-{day_axis}.html"
        png_target = tmp_path / f"preview-{day_axis}.png"
        render_html(config, resolved, levels, html_target, day_axis=day_axis)
        render_png(config, resolved, levels, png_target, day_axis=day_axis)
        assert html_target.exists() and png_target.exists()


def test_no_baseline_tokens_uses_dimensionless_axis_label(tmp_path):
    config = _config()
    resolved, levels = _sample(config, 5)
    target = tmp_path / "preview.html"
    render_html(config, resolved, levels, target)
    assert "Level (dimensionless multiplier)" in target.read_text(encoding="utf-8")


def test_baseline_tokens_uses_scaled_axis_label(tmp_path):
    config = _config(preview={"baseline_tokens": 1000.0})
    resolved, levels = _sample(config, 5)
    target = tmp_path / "preview.html"
    render_html(config, resolved, levels, target)
    assert "Level x baseline tokens" in target.read_text(encoding="utf-8")


def test_single_sample_point_does_not_crash(tmp_path):
    config = _config()
    resolved, levels = _sample(config, 1)
    html_target = tmp_path / "preview.html"
    png_target = tmp_path / "preview.png"
    render_html(config, resolved, levels, html_target)
    render_png(config, resolved, levels, png_target)
    assert html_target.exists() and png_target.exists()
