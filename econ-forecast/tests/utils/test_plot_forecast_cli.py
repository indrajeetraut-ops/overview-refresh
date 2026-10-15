from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import yaml


ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "econ-forecast" / "utils" / "plot_forecast.sh"


def _identity(org_id, scope_type, scope_id, model):
    raw = json.dumps([org_id, scope_type, scope_id, model], separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()


def make_run(root: Path, *, partial=False, hostile=False, models=1, dates=3, samples=3):
    root.mkdir()
    paths = root / "paths"
    paths.mkdir()
    date_values = [(date(2026, 9, 1) + timedelta(days=offset)).isoformat()
                   for offset in range(dates)]
    sample_ids = list(range(samples))
    name = "<img src=x onerror=globalThis.pwned=1>" if hostile else "Payments"
    series = []
    for number in range(models):
        model = f"model-{number}"
        sid = _identity("org-a", "team", "team-a", model)
        values = (np.arange(dates * samples, dtype=np.float64).reshape(dates, samples) + 1) * (number + 1)
        relative = f"paths/{sid}.npy"
        np.save(root / relative, values, allow_pickle=False)
        series.append({
            "series_id": sid, "org_id": "org-a", "scope_type": "team", "scope_id": "team-a",
            "scope_name": name, "model": model, "status": "qualified", "path": relative,
            "shape": [dates, samples], "dtype": "float64", "assumptions": [], "reasons": [],
        })
    if partial:
        model = "unavailable"
        series.append({
            "series_id": _identity("org-a", "team", "team-a", model), "org_id": "org-a",
            "scope_type": "team", "scope_id": "team-a", "scope_name": name, "model": model,
            "status": "sampling_failed", "path": None, "shape": None, "dtype": "float64",
            "assumptions": [], "reasons": ["injected numerical failure"],
        })
    index = {
        "schema_version": 1, "dates": date_values, "sample_ids": sample_ids,
        "scopes": [{"org_id": "org-a", "scope_type": "team", "scope_id": "team-a",
                    "scope_name": name, "status": "incomplete" if partial else "complete",
                    "risk_ready": not partial, "assumptions": [], "reasons": []}],
        "series": series,
    }
    (paths / "index.json").write_text(json.dumps(index), encoding="utf-8")
    fixture_files = {
        "request.json": "{}\n", "resolved_config.yaml": "schema_version: 1\n",
        "daily_expected.jsonl": "", "allocations.json": "{}\n",
        "period_summary.json": "{}\n", "diagnostics.json": "{}\n", "report.md": "# fixture\n",
    }
    for relative, content in fixture_files.items():
        (root / relative).write_text(content, encoding="utf-8")
    files = {}
    for item in sorted(path for path in root.rglob("*") if path.is_file()):
        relative = item.relative_to(root).as_posix()
        data = item.read_bytes()
        files[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    manifest = {
        "schema_version": 2, "operation": "forecast", "completion": "complete",
        "status": "partial" if partial else "complete", "run_id": "plot-fixture",
        "created_at": "2026-09-13T00:00:00Z", "files": files,
        "versions": {"python": "fixture"}, "input_sources": {},
        "counts": {"requested_scopes": 1, "completed_scopes": 0 if partial else 1,
                   "rejected_scopes": 1 if partial else 0},
        "report_path": "report.md",
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def make_project_run(root: Path, *, all_failed=False):
    make_run(root)
    index_path = root / "paths" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for number, name in enumerate(("Alpha", "Beta", "Gamma"), 1):
        scope_id = f"project-{number}"
        status = "sampling_failed" if name == "Gamma" or all_failed else "qualified"
        series_id = _identity("org-a", "project", scope_id, "model-0")
        relative = f"paths/{series_id}.npy" if status == "qualified" else None
        if relative:
            np.save(root / relative, np.full((3, 3), number * 10.0), allow_pickle=False)
        index["scopes"].append({
            "org_id": "org-a", "scope_type": "project", "scope_id": scope_id,
            "scope_name": name, "status": "incomplete" if status == "sampling_failed" else "complete",
            "risk_ready": status == "qualified", "assumptions": [], "reasons": [],
        })
        index["series"].append({
            "series_id": series_id, "org_id": "org-a", "scope_type": "project",
            "scope_id": scope_id, "scope_name": name, "model": "model-0",
            "status": status, "path": relative,
            "shape": [3, 3] if relative else None, "dtype": "float64",
            "assumptions": [], "reasons": ["injected failure"] if not relative else [],
        })
    index_path.write_text(json.dumps(index), encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "partial"
    manifest["counts"] = {"requested_scopes": 4,
                          "completed_scopes": 1 if all_failed else 3,
                          "rejected_scopes": 3 if all_failed else 1}
    for item in (index_path, *sorted((root / "paths").glob("*.npy"))):
        data = item.read_bytes()
        manifest["files"][item.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _run(*args, cwd=None):
    return subprocess.run([str(WRAPPER), *map(str, args)], cwd=cwd, text=True,
                          capture_output=True, env={**os.environ, "NO_COLOR": "1"}, timeout=60)


def test_help_outside_repository_reads_no_run(tmp_path):
    working = tmp_path / "working directory with spaces"
    working.mkdir()
    result = _run("--help", cwd=working)
    assert result.returncode == 0
    assert "--samples" in result.stdout
    assert "--band" in result.stdout
    assert "--config" in result.stdout
    assert "--output" not in result.stdout
    assert result.stderr == ""

    invalid = _run("--samples", "-1", "--artifact", "x", "--scope-type", "organization", cwd=working)
    assert invalid.returncode == 1  # argparse type error -> "could not start"
    assert invalid.stdout == ""


def test_missing_required_and_mutual_exclusion(tmp_path):
    missing_artifact = _run("--scope-type", "organization")
    assert missing_artifact.returncode == 1
    assert json.loads(missing_artifact.stderr)["error_type"] == "input"

    missing_selection = _run("--artifact", str(tmp_path / "run"))
    assert missing_selection.returncode == 1
    assert missing_selection.stdout == ""

    mutual = _run("--artifact", str(tmp_path / "run"), "--config", "x.yaml", "--scope-type", "organization")
    assert mutual.returncode == 1
    assert "mutually exclusive" in json.loads(mutual.stderr)["message"]

    neither = _run()
    assert neither.returncode == 1


def test_bad_band_and_scope_name_on_organization_are_rejected_before_writing(tmp_path):
    run = make_run(tmp_path / "run")
    bad_band = _run("--artifact", run, "--scope-type", "organization", "--band", "90", "10")
    assert bad_band.returncode == 1
    assert not (run / "charts").exists()

    bad_org = _run("--artifact", run, "--scope-type", "organization", "--scope-name", "x")
    assert bad_org.returncode == 1
    assert not (run / "charts").exists()


def test_single_plot_creates_charts_dir_with_index_and_result(tmp_path):
    working = tmp_path / "working directory with spaces"
    working.mkdir()
    make_run(working / "saved run")
    result = _run("--artifact", "saved run", "--scope-type", "team", "--scope-name", "Payments",
                  "--model", "model-0", "--samples", "2", cwd=working)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "complete"
    assert payload["counts"] == {"requested": 1, "succeeded": 1, "failed": 0}
    assert payload["forecast_data"] == {"source": "unavailable", "dataset": None}
    plot = payload["plots"][0]
    assert plot["stem"] == "team-payments__models__tokens"
    assert plot["outputs"] == {"html": "team-payments__models__tokens.html",
                               "png": "team-payments__models__tokens.png"}
    charts = working / "saved run" / "charts"
    assert (charts / "team-payments__models__tokens.html").exists()
    assert (charts / "team-payments__models__tokens.png").exists()
    assert (charts / "plotly.min.js").exists()
    index = json.loads((charts / "index.json").read_text())
    assert index["plots"][0]["files"] == ["team-payments__models__tokens.html",
                                          "team-payments__models__tokens.png"]
    logs = [json.loads(line) for line in result.stderr.splitlines()]
    assert logs[0]["event"] == "plot.selection"
    assert logs[-1]["event"] == "plot.completed"


def test_format_flag_produces_only_the_requested_file(tmp_path):
    run = make_run(tmp_path / "run")
    result = _run("--artifact", run, "--scope-type", "team", "--scope-name", "Payments", "--format", "html")
    assert result.returncode == 0, result.stderr
    charts = run / "charts"
    assert (charts / "team-payments__models__tokens.html").exists()
    assert not (charts / "team-payments__models__tokens.png").exists()


def test_existing_charts_dir_is_refused_without_force(tmp_path):
    run = make_run(tmp_path / "run")
    (run / "charts").mkdir()
    (run / "charts" / "stray.txt").write_text("x", encoding="utf-8")
    result = _run("--artifact", run, "--scope-type", "team", "--scope-name", "Payments")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "use --force" in json.loads(result.stderr.splitlines()[-1])["message"]
    assert (run / "charts" / "stray.txt").exists()

    forced = _run("--artifact", run, "--scope-type", "team", "--scope-name", "Payments", "--force")
    assert forced.returncode == 0, forced.stderr
    assert not (run / "charts" / "stray.txt").exists()


def test_corrupt_artifact_is_a_structural_failure(tmp_path):
    run = make_run(tmp_path / "run")
    array = next((run / "paths").glob("*.npy"))
    array.write_bytes(array.read_bytes() + b"corrupt")
    result = _run("--artifact", run, "--scope-type", "team", "--scope-name", "Payments")
    assert result.returncode == 1
    assert not (run / "charts").exists()
    assert "checksum mismatch" in result.stderr


def test_all_selected_entries_failing_is_partial_batch_not_a_crash(tmp_path):
    run = make_run(tmp_path / "run", partial=True)
    result = _run("--artifact", run, "--scope-type", "team", "--scope-name", "Payments",
                  "--model", "unavailable")
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["counts"] == {"requested": 1, "succeeded": 0, "failed": 1}
    assert payload["plots"][0]["error"]["type"] == "input"
    assert payload["plots"][0]["outputs"] == {}
    charts = run / "charts"
    assert (charts / "result.json").exists()
    assert (charts / "index.json").exists()
    assert not (charts / "team-payments__models__tokens.html").exists()


def test_project_name_omission_plots_one_file_per_project(tmp_path):
    """Design D16: omitted --scope-name for team/project draws one file per
    scope of that type -- never a single combined "all projects" file."""
    run = make_project_run(tmp_path / "project run")
    result = _run("--artifact", run, "--scope-type", "project")
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    stems = {plot["stem"]: plot for plot in payload["plots"]}
    assert set(stems) == {
        "project-alpha__models__tokens", "project-beta__models__tokens",
        "project-gamma__models__tokens",
    }
    assert stems["project-alpha__models__tokens"]["status"] == "complete"
    assert stems["project-beta__models__tokens"]["status"] == "complete"
    assert stems["project-gamma__models__tokens"]["status"] == "failed"
    charts = run / "charts"
    assert (charts / "project-alpha__models__tokens.html").exists()
    assert (charts / "project-beta__models__tokens.html").exists()
    assert not (charts / "project-gamma__models__tokens.html").exists()


def test_hostile_scope_name_is_escaped_in_html_and_slugified_safely(tmp_path):
    run = make_run(tmp_path / "hostile run", hostile=True)
    result = _run("--artifact", run, "--scope-type", "team",
                  "--scope-name", "<img src=x onerror=globalThis.pwned=1>")
    assert result.returncode == 0, result.stderr
    charts = run / "charts"
    html_files = [p for p in charts.glob("*.html")]
    assert len(html_files) == 1, "hostile scope name must still produce exactly one safely slugged file"
    assert html_files[0].name == "team-img-src-x-onerror-globalthis-pwned-1__models__tokens.html"
    html = html_files[0].read_text(encoding="utf-8")
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=globalThis.pwned=1&gt;" in html


def test_config_mode_resilient_batch_and_output_contract(tmp_path):
    run = make_run(tmp_path / "run")
    config = tmp_path / "plot.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": 1,
        "defaults": {"format": "html", "band": {"low": 5, "high": 95}},
        "forecast": [
            {"scope_type": "team", "scope_name": "Payments"},
            {"scope_type": "team", "scope_name": "does-not-exist"},
        ],
    }), encoding="utf-8")
    result = _run("--artifact", run, "--config", config)
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    assert payload["counts"] == {"requested": 2, "succeeded": 1, "failed": 1}
    statuses = {plot["stem"]: plot["status"] for plot in payload["plots"]}
    assert statuses["team-payments__models__tokens"] == "complete"
    assert statuses["team-does-not-exist__models__tokens"] == "failed"
    charts = run / "charts"
    assert (charts / "team-payments__models__tokens.html").exists()
    assert not (charts / "team-payments__models__tokens.png").exists()  # format: html only
    assert not (charts / "team-does-not-exist__models__tokens.html").exists()


def test_single_plot_and_config_mode_write_the_same_file_for_the_same_entry(tmp_path):
    single_run = make_run(tmp_path / "single-run")
    single = _run("--artifact", single_run, "--scope-type", "team", "--scope-name", "Payments")
    assert single.returncode == 0, single.stderr

    config_run = make_run(tmp_path / "config-run")
    config = tmp_path / "plot.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": 1, "forecast": [{"scope_type": "team", "scope_name": "Payments"}],
    }), encoding="utf-8")
    batch = _run("--artifact", config_run, "--config", config)
    assert batch.returncode == 0, batch.stderr

    name = "team-payments__models__tokens.html"
    single_html = (single_run / "charts" / name).read_text(encoding="utf-8")
    batch_html = (config_run / "charts" / name).read_text(encoding="utf-8")
    # Plotly embeds a fresh random div id per render; strip it before comparing
    # so the comparison is about content, not that one incidental detail.
    uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    assert uuid.sub("UUID", single_html) == uuid.sub("UUID", batch_html)


def test_config_with_stem_collision_is_rejected_before_writing(tmp_path):
    run = make_run(tmp_path / "run")
    config = tmp_path / "plot.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": 1,
        "forecast": [
            {"scope_type": "team", "scope_name": "Payments"},
            {"scope_type": "team", "scope_name": "PAYMENTS"},
        ],
    }), encoding="utf-8")
    result = _run("--artifact", run, "--config", config)
    assert result.returncode == 1
    assert not (run / "charts").exists()
    assert "stem" in json.loads(result.stderr.splitlines()[-1])["message"]


def test_unreadable_config_is_a_structural_failure(tmp_path):
    run = make_run(tmp_path / "run")
    config = tmp_path / "bad.yaml"
    config.write_text("not: [valid, plot config", encoding="utf-8")
    result = _run("--artifact", run, "--config", config)
    assert result.returncode == 1
    assert result.stdout == ""


def test_config_without_forecast_section_leaves_charts_untouched(tmp_path):
    run = make_run(tmp_path / "run")
    config = tmp_path / "plot.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": 1, "cost": [{"scope_type": "organization"}],
    }), encoding="utf-8")
    result = _run("--artifact", run, "--config", config)
    assert result.returncode == 0, result.stderr
    assert not (run / "charts").exists()


def test_configured_plotly_js_is_written_once_when_missing(tmp_path):
    """design section 8: "either file is written from get_plotlyjs() when
    missing" -- a `plotly_js` pointing at a path that does not exist yet is
    created (parent directories included), and the chart references it by a
    relative path from `<run>/charts/`."""
    run = make_run(tmp_path / "run")
    js_path = tmp_path / "assets" / "plotly.min.js"
    config = tmp_path / "plot.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": 1, "plotly_js": "assets/plotly.min.js",
        "forecast": [{"scope_type": "team", "scope_name": "Payments"}],
    }), encoding="utf-8")

    assert not js_path.exists()
    result = _run("--artifact", run, "--config", config)
    assert result.returncode == 0, result.stderr
    assert js_path.is_file()
    assert len(js_path.read_text(encoding="utf-8")) > 1000  # real get_plotlyjs() content

    html = (run / "charts" / "team-payments__models__tokens.html").read_text(encoding="utf-8")
    assert re.search(r'<script[^>]+src="\.\./\.\./assets/plotly\.min\.js"', html)


def test_configured_plotly_js_is_not_rewritten_when_it_already_exists(tmp_path):
    js_path = tmp_path / "assets" / "plotly.min.js"
    js_path.parent.mkdir()
    js_path.write_text("stale-marker", encoding="utf-8")
    run = make_run(tmp_path / "run")
    config = tmp_path / "plot.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": 1, "plotly_js": "assets/plotly.min.js",
        "forecast": [{"scope_type": "team", "scope_name": "Payments"}],
    }), encoding="utf-8")

    result = _run("--artifact", run, "--config", config)
    assert result.returncode == 0, result.stderr
    assert js_path.read_text(encoding="utf-8") == "stale-marker"
