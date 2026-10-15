"""`plot_cost.sh` (design §5, §7, §8; plan T6): config-only CLI drawing a saved
cost run's `cost:` section into `<cost-run>/charts/` and re-rendering
`report.md`. The fixture prices a small multi-scope run through the real cost
kernel and `write_cost_run` (the `tests/cost/test_presentation.py` pattern), so
`load_cost_run` reads it exactly as it reads a production run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
import structlog

from common.model_pricing import ModelPriceEntry, RateSheet, Segment
from forecast.cost.artifacts import load_cost_run, write_cost_run
from forecast.cost.kernel import SeriesInput, price_one_series, price_scope
from forecast.forecasting.dataset import PreparedDataset, ScopeMeta
from forecast.utils import cli_plot_cost

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "econ-forecast" / "utils" / "plot_cost.sh"

ORG, AGENT = "org-a", "claude"
MODELS = ("model-a", "model-b")
DATES = (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3))
ORIGIN = date(2026, 9, 30)
SAMPLES = 8
MAX_AGE_DAYS = 30
TEAMS = ("team1", "team2", "team3", "team4")
PROJECTS = ("proj1", "proj2", "proj3", "proj4")


def _sheet() -> RateSheet:
    rates = {"input": "0.000003", "output": "0.000015", "cache_read": "0.0000003",
             "cache_write_5m": None, "cache_write_1h": None}
    return RateSheet(
        org_id=ORG, start=DATES[0], end=DATES[-1], price_version="v-test",
        models=[
            ModelPriceEntry(usage_model_id=model, status="matched", model_id=model,
                            vendor="anthropic", channel="anthropic", match_step="normalized",
                            segments=[Segment(from_=DATES[0], to=DATES[-1], rates=rates)])
            for model in MODELS
        ],
    )


def _scopes() -> list[tuple[str, str]]:
    return ([("organization", ORG)] + [("team", name) for name in TEAMS]
            + [("project", name) for name in PROJECTS])


def _dataset() -> PreparedDataset:
    keys = frozenset([("u1", AGENT)])
    scopes = {(ORG, scope_type, scope_id): ScopeMeta(ORG, scope_type, scope_id, scope_id, keys)
              for scope_type, scope_id in _scopes()}
    usage: dict = {}
    coverage: dict = {}
    for offset in range(4):
        day = ORIGIN - timedelta(days=offset)
        coverage[(day, ORG, "u1", AGENT)] = "complete"
        for scope_type, scope_id in _scopes():
            for model in MODELS:
                usage[(ORG, scope_type, scope_id, model, day, AGENT, "input")] = 900.0
                usage[(ORG, scope_type, scope_id, model, day, AGENT, "output")] = 100.0
    return PreparedDataset(
        start=date(2026, 1, 1), end=date(2026, 12, 31), timezone="UTC",
        models={ORG: {model: frozenset([AGENT]) for model in MODELS}},
        scopes=scopes, usage=usage, coverage=coverage, startup=[], hashes={}, source_state={},
    )


def make_cost_run(output: Path) -> Path:
    """Nine complete scopes (1 organization, 4 teams, 4 projects), each with both
    models qualified; sample paths vary so bands/samples are visible."""
    dataset, sheet = _dataset(), _sheet()
    rng = np.random.default_rng(7)
    series_costs, scope_costs = [], []
    for scope_type, scope_id in _scopes():
        costs = [
            price_one_series(
                SeriesInput(org_id=ORG, scope_type=scope_type, scope_id=scope_id,
                            scope_name=scope_id, model=model, status="qualified",
                            tokens=rng.uniform(1e5, 1e6, size=(len(DATES), SAMPLES))),
                dataset, sheet, DATES, ORIGIN, MAX_AGE_DAYS)
            for model in MODELS
        ]
        series_costs += costs
        scope_costs.append(price_scope(ORG, scope_type, scope_id, scope_id, costs, DATES, SAMPLES))
    request_doc = {
        "org_id": ORG,
        "forecast_run": {"name": "fc", "path": "../fc", "manifest_sha256": "a" * 64},
        "scenario": "baseline",
        "periods": [{"type": "requested", "start": DATES[0].isoformat(), "end": DATES[-1].isoformat()}],
        "settings": {"half_life_days": 14, "own_mix_half_weight_days": 7, "max_age_days": MAX_AGE_DAYS},
    }
    write_cost_run(
        output, request_doc=request_doc, sheet=sheet, dataset=dataset, org_id=ORG,
        dates=DATES, samples=SAMPLES,
        periods=({"type": "requested", "start": DATES[0], "end": DATES[-1]},),
        series_costs=series_costs, scope_costs=scope_costs,
        origin=ORIGIN, max_age_days=MAX_AGE_DAYS,
    )
    return output


def _plot_yaml(path: Path, body: str) -> Path:
    path.write_text("schema_version: 1\n" + body, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def pristine_run(tmp_path_factory) -> Path:
    return make_cost_run(tmp_path_factory.mktemp("cost") / "run")


@pytest.fixture
def cost_run(pristine_run, tmp_path) -> Path:
    import shutil

    target = tmp_path / "run"
    shutil.copytree(pristine_run, target)
    return target


@pytest.fixture(autouse=True)
def _restore_structlog():
    """`main()` points structlog at the (captured) stderr; restore the prior
    configuration so later tests never log into a closed capture stream."""
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


def _main(capsys, *args) -> tuple[int, dict | None]:
    code = cli_plot_cost.main([str(arg) for arg in args])
    out = capsys.readouterr().out.strip().splitlines()
    return code, (json.loads(out[-1]) if out else None)


def _stems(charts: Path) -> set[str]:
    return {p.stem for p in charts.iterdir() if p.suffix in (".html", ".png")}


def _index_files(charts: Path) -> list[str]:
    index = json.loads((charts / "index.json").read_text(encoding="utf-8"))
    return [name for item in index["plots"] for name in item["files"]]


def _report_links(run: Path) -> set[str]:
    text = (run / "report.md").read_text(encoding="utf-8")
    return set(re.findall(r"\]\(charts/([^)]+)\)", text))


def test_help_reads_no_data(tmp_path):
    result = subprocess.run([str(WRAPPER), "--help"], cwd=tmp_path, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0
    assert "--cost-run" in result.stdout and "--config" in result.stdout
    assert "required" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("args", [["--config", "plot.yaml"], ["--cost-run", "run"]])
def test_missing_required_argument_exits_1(tmp_path, args):
    with pytest.raises(SystemExit) as info:
        cli_plot_cost.main(args)
    assert info.value.code == 1


def test_default_views_on_nine_scopes_give_two_stems_per_scope(cost_run, tmp_path, capsys):
    config = _plot_yaml(tmp_path / "plot.yaml",
                        "cost:\n  - scope_type: organization\n  - scope_type: team\n"
                        "  - scope_type: project\n")
    code, summary = _main(capsys, "--cost-run", cost_run, "--config", config)
    assert code == 0 and summary["status"] == "complete"
    complete_scopes = [s for s in load_cost_run(cost_run).index["scopes"] if s["status"] == "complete"]
    assert len(complete_scopes) == 9
    charts = cost_run / "charts"
    stems = _stems(charts)
    assert len(stems) == 2 * len(complete_scopes) == 18
    assert "organization-org-a__total__cost" in stems
    assert "team-team3__breakdown__cost" in stems
    assert "project-proj4__total__cost" in stems
    assert summary["counts"] == {"requested": 18, "succeeded": 18, "failed": 0}
    # index.json matches the directory exactly; report.md links exactly index.json's files.
    listed = _index_files(charts)
    on_disk = {p.name for p in charts.iterdir() if p.suffix in (".html", ".png")}
    assert sorted(listed) == sorted(on_disk)
    assert _report_links(cost_run) == set(listed)
    assert (charts / "plotly.min.js").exists()
    # the run is still a valid cost run after plotting + re-rendering report.md
    load_cost_run(cost_run)


def test_per_model_on_organization_one_file_per_model(cost_run, tmp_path, capsys):
    config = _plot_yaml(tmp_path / "plot.yaml",
                        "defaults:\n  format: html\n"
                        "cost:\n  - scope_type: organization\n    views: [per-model]\n")
    code, _ = _main(capsys, "--cost-run", cost_run, "--config", config)
    assert code == 0
    charts = cost_run / "charts"
    assert _stems(charts) == {f"organization-org-a__{model}__cost" for model in MODELS}
    index = json.loads((charts / "index.json").read_text(encoding="utf-8"))
    result = json.loads((charts / "result.json").read_text(encoding="utf-8"))
    assert sorted(item["model"] for item in index["plots"]) == list(MODELS)
    assert sorted(item["model"] for item in result["plots"]) == list(MODELS)
    for item in index["plots"]:
        assert item["view"] == item["model"]
    assert _report_links(cost_run) == set(_index_files(charts))


def test_breakdown_ignores_band_and_samples(cost_run, tmp_path, capsys):
    """design §6: `breakdown` is expected cost only -- band/samples are accepted
    in the config but do not change the chart."""
    htmls = []
    for index, extra in enumerate(("", "    samples: 5\n    band: {low: 5, high: 95}\n")):
        config = _plot_yaml(tmp_path / f"plot{index}.yaml",
                            "defaults:\n  format: html\ncost:\n  - scope_type: team\n"
                            "    scope_name: team1\n    views: [breakdown]\n" + extra)
        code, _ = _main(capsys, "--cost-run", cost_run, "--config", config, "--force")
        assert code == 0
        htmls.append((cost_run / "charts" / "team-team1__breakdown__cost.html").read_text(encoding="utf-8"))
    strip = lambda text: re.sub(r'id="[^"]+"|"[0-9a-f-]{36}"', "", text)  # noqa: E731
    assert strip(htmls[0]) == strip(htmls[1])


def test_total_honors_band_and_samples(cost_run, tmp_path, capsys):
    config = _plot_yaml(tmp_path / "plot.yaml",
                        "defaults:\n  format: html\n  samples: 3\n  band: {low: 5, high: 95}\n"
                        "cost:\n  - scope_type: team\n    scope_name: team2\n    views: [total]\n")
    code, _ = _main(capsys, "--cost-run", cost_run, "--config", config)
    assert code == 0
    html = (cost_run / "charts" / "team-team2__total__cost.html").read_text(encoding="utf-8")
    assert "p5" in html and "p95" in html
    record = json.loads((cost_run / "charts" / "result.json").read_text(encoding="utf-8"))["plots"][0]
    assert record["band"] == [5, 95] and record["samples"] == 3


def test_existing_charts_refused_without_force_and_replaced_with_it(cost_run, tmp_path, capsys):
    config = _plot_yaml(tmp_path / "plot.yaml",
                        "defaults:\n  format: png\ncost:\n  - scope_type: organization\n")
    assert _main(capsys, "--cost-run", cost_run, "--config", config)[0] == 0
    (cost_run / "charts" / "stray.txt").write_text("x", encoding="utf-8")
    report_before = (cost_run / "report.md").read_text(encoding="utf-8")
    assert _main(capsys, "--cost-run", cost_run, "--config", config)[0] == 1
    assert (cost_run / "charts" / "stray.txt").exists()
    assert (cost_run / "report.md").read_text(encoding="utf-8") == report_before
    assert _main(capsys, "--cost-run", cost_run, "--config", config, "--force")[0] == 0
    assert not (cost_run / "charts" / "stray.txt").exists()


def test_shared_plotly_js_is_referenced_relatively(cost_run, tmp_path, capsys):
    config = _plot_yaml(tmp_path / "plot.yaml",
                        "plotly_js: assets/plotly.min.js\ndefaults:\n  format: html\n"
                        "cost:\n  - scope_type: organization\n    views: [total]\n")
    assert _main(capsys, "--cost-run", cost_run, "--config", config)[0] == 0
    shared = tmp_path / "assets" / "plotly.min.js"
    assert shared.exists()
    assert not (cost_run / "charts" / "plotly.min.js").exists()
    html_path = cost_run / "charts" / "organization-org-a__total__cost.html"
    expected = Path(os.path.relpath(shared, start=html_path.parent)).as_posix()
    html = html_path.read_text(encoding="utf-8")
    assert re.findall(r'<script[^>]*\ssrc="([^"]+)"', html) == [expected]
    assert html_path.stat().st_size < 1_000_000


def test_absent_cost_section_leaves_charts_untouched(cost_run, tmp_path, capsys):
    config = _plot_yaml(tmp_path / "plot.yaml", "forecast:\n  - scope_type: organization\n")
    code, summary = _main(capsys, "--cost-run", cost_run, "--config", config)
    assert code == 0 and summary["status"] == "complete"
    assert not (cost_run / "charts").exists()


def test_unknown_scope_is_a_per_entry_failure_exit_2(cost_run, tmp_path, capsys):
    config = _plot_yaml(tmp_path / "plot.yaml",
                        "defaults:\n  format: png\ncost:\n  - scope_type: organization\n"
                        "    views: [total]\n  - scope_type: team\n    scope_name: nope\n"
                        "    views: [total]\n")
    code, summary = _main(capsys, "--cost-run", cost_run, "--config", config)
    assert code == 2 and summary["status"] == "partial"
    assert _report_links(cost_run) == set(_index_files(cost_run / "charts"))


def test_wrapper_runs_end_to_end(cost_run, tmp_path):
    config = _plot_yaml(tmp_path / "plot.yaml",
                        "defaults:\n  format: html\ncost:\n  - scope_type: organization\n"
                        "    views: [total]\n")
    result = subprocess.run([str(WRAPPER), "--cost-run", str(cost_run), "--config", str(config)],
                            text=True, capture_output=True, timeout=120,
                            env={**os.environ, "NO_COLOR": "1"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["status"] == "complete"
