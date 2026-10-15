from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import psutil
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "econ-forecast" / "utils" / "plot_forecast.sh"
_helper_spec = importlib.util.spec_from_file_location(
    "plot_cli_fixture", Path(__file__).with_name("test_plot_forecast_cli.py")
)
_helper = importlib.util.module_from_spec(_helper_spec)
assert _helper_spec.loader is not None
_helper_spec.loader.exec_module(_helper)
make_run = _helper.make_run
make_project_run = _helper.make_project_run


def _export(run: Path, *, measure_memory=False, samples=None, scope_name=None):
    """Draws into `<run>/charts/` (design §7); HTML loads the shared
    `charts/plotly.min.js` by relative path (§8)."""
    command = [str(WRAPPER), "--artifact", str(run), "--scope-type", "team",
               "--scope-name", scope_name or (
                   "<img src=x onerror=globalThis.pwned=1>" if "hostile" in str(run) else "Payments"),
               "--format", "html"]
    if samples is not None:
        command.extend(["--samples", str(samples)])
    peak_rss = 0
    if measure_memory:
        process = subprocess.Popen(command, cwd=run.parent, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        monitored = psutil.Process(process.pid)
        while process.poll() is None:
            try:
                live = [monitored, *monitored.children(recursive=True)]
                peak_rss = max(peak_rss, sum(item.memory_info().rss for item in live))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(0.005)
        stdout, stderr = process.communicate(timeout=60)
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    else:
        result = subprocess.run(command, cwd=run.parent, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), peak_rss


def test_offline_render_hover_zoom_reset_legend_and_hostile_text(tmp_path):
    run = make_run(tmp_path / "hostile run", hostile=True, models=2)
    result, _ = _export(run, samples=10)
    html_path = run / "charts" / result["plots"][0]["outputs"]["html"]
    assert (run / "charts" / "plotly.min.js").exists()
    assert html_path.stat().st_size < 1_000_000
    screenshot = tmp_path / "offline-plot.png"
    browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    assert browser_path, "PLAYWRIGHT_BROWSERS_PATH must point to a provisioned Chromium"
    external_requests = []
    page_errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.route("http://**/*", lambda route: (external_requests.append(route.request.url), route.abort()))
        page.route("https://**/*", lambda route: (external_requests.append(route.request.url), route.abort()))
        page.goto(html_path.as_uri(), wait_until="load")
        page.wait_for_selector(".plotly-graph-div .scatterlayer .trace", state="visible")
        assert page.locator(".plotly-graph-div").count() == 1
        assert page.locator(".subplot").count() >= 2
        assert page.evaluate("globalThis.pwned") is None
        assert not external_requests
        assert not page_errors

        plot = page.locator(".plotly-graph-div")
        before = page.evaluate("el => [el.layout.xaxis.range, el.layout.xaxis.autorange]", plot.element_handle())
        graph = page.locator(".subplot.xy").first
        box = graph.bounding_box()
        assert box
        page.locator(".modebar-btn[data-title='Zoom']").first.click()
        page.mouse.move(box["x"] + box["width"] * 0.15, box["y"] + box["height"] * 0.2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.65, box["y"] + box["height"] * 0.8, steps=8)
        page.mouse.up()
        page.wait_for_timeout(200)
        zoomed = page.evaluate("el => [el.layout.xaxis.range, el.layout.xaxis.autorange]", plot.element_handle())
        assert zoomed != before and zoomed[0] is not None
        page.locator(".modebar-btn[data-title='Pan']").first.click()
        page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.5)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.35, box["y"] + box["height"] * 0.5, steps=8)
        page.mouse.up()
        page.wait_for_timeout(200)
        panned = page.evaluate("el => el.layout.xaxis.range", plot.element_handle())
        assert panned != zoomed[0]
        page.locator(".modebar-btn[data-title='Reset axes']").first.click()
        reset = page.evaluate("el => el.layout.xaxis.autorange", plot.element_handle())
        assert reset is True

        legend_item = page.locator(".legend .traces").first
        # Plotly does not consistently expose a curve number attribute, so assert visual opacity changes.
        opacity_before = legend_item.evaluate("el => getComputedStyle(el).opacity")
        legend_item.click()
        page.wait_for_timeout(300)
        opacity_after = legend_item.evaluate("el => getComputedStyle(el).opacity")
        assert opacity_after != opacity_before

        point = page.evaluate("""el => {
            const rect = el.getBoundingClientRect();
            return {
              x: rect.left + el._fullLayout.xaxis._offset + el._fullLayout.xaxis.d2p('2026-09-01'),
              y: rect.top + el._fullLayout.yaxis._offset + el._fullLayout.yaxis.d2p(2.0)
            };
        }""", plot.element_handle())
        page.mouse.move(point["x"], point["y"])
        page.wait_for_selector(".hoverlayer .hovertext", state="visible")
        hover = page.locator(".hoverlayer").text_content()
        assert "Sep 1, 2026" in hover and "model-0" in hover and "2.000 total tokens" in hover
        page.screenshot(path=str(screenshot), full_page=True)
        assert not external_requests
        assert not page_errors
        browser.close()
    assert screenshot.stat().st_size > 10_000


def test_default_main_graph_has_no_individual_sample_lines(tmp_path):
    run = make_run(tmp_path / "default run")
    result, _ = _export(run)
    plot = result["plots"][0]
    assert plot["samples"] == 0
    html_path = run / "charts" / plot["outputs"]["html"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(html_path.as_uri(), wait_until="load")
        page.wait_for_selector(".plotly-graph-div .scatterlayer .trace", state="visible")
        names = page.evaluate("""() => document.querySelector('.plotly-graph-div').data.map(trace => trace.name)""")
        assert len(names) == 3
        assert any(name.endswith(" mean") for name in names)
        assert any("p10–p90" in name for name in names)
        assert not any(" sample " in name for name in names)
        browser.close()


def test_all_projects_render_one_file_per_project(tmp_path):
    """`--scope-type project` without a name: one file per project (design §5);
    the failed project gets no file and the run exits 2."""
    run = make_project_run(tmp_path / "projects")
    result = subprocess.run(
        [str(WRAPPER), "--artifact", str(run), "--scope-type", "project", "--format", "html"],
        cwd=tmp_path, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 2, result.stderr
    charts = run / "charts"
    assert not (charts / "project-gamma__models__tokens.html").exists()
    page_errors = []
    external_requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.route("http://**/*", lambda route: (external_requests.append(route.request.url), route.abort()))
        page.route("https://**/*", lambda route: (external_requests.append(route.request.url), route.abort()))
        for name in ("Alpha", "Beta"):
            html_path = charts / f"project-{name.lower()}__models__tokens.html"
            page.goto(html_path.as_uri(), wait_until="load")
            page.wait_for_selector(".plotly-graph-div .scatterlayer .trace", state="attached")
            names = page.evaluate("""() => document.querySelector('.plotly-graph-div').data.map(trace => trace.name)""")
            assert any(trace.endswith("model-0 mean") for trace in names)
        assert not page_errors and not external_requests
        browser.close()


def test_reference_plot_workload_four_models_90_days_1000_samples(tmp_path):
    run = make_run(tmp_path / "benchmark run", models=4, dates=90, samples=1000)
    started = time.perf_counter()
    result, peak_rss = _export(run, measure_memory=True, samples=10)
    generation_seconds = time.perf_counter() - started
    plot = result["plots"][0]
    assert plot["samples"] == 10
    assert plot["plotted_models"] == ["model-0", "model-1", "model-2", "model-3"]
    assert generation_seconds < 30
    assert peak_rss < 1024 ** 3
    html_path = run / "charts" / plot["outputs"]["html"]
    assert html_path.stat().st_size < 1_000_000
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1400})
        load_started = time.perf_counter()
        page.goto(html_path.as_uri(), wait_until="load")
        page.wait_for_selector(".plotly-graph-div .scatterlayer .trace", state="visible")
        browser_seconds = time.perf_counter() - load_started
        assert browser_seconds < 15
        assert page.locator(".subplot").count() >= 4
        assert page.locator(".legend .traces").count() >= 48
        values = page.evaluate("""() => {
            const el = document.querySelector('.plotly-graph-div');
            return [0, 13, 26, 39].map(i => ({p10: el.calcdata[i][0].y, p90: el.calcdata[i+1][0].y, mean: el.calcdata[i+2][0].y}));
        }""")
        for multiplier, item in enumerate(values, 1):
            assert abs(item["mean"] - 500.5 * multiplier) < 1e-9
            assert abs(item["p10"] - 100.9 * multiplier) < 1e-9
            assert abs(item["p90"] - 900.1 * multiplier) < 1e-9
        interaction_started = time.perf_counter()
        page.locator(".legend .traces").first.click()
        page.wait_for_timeout(100)
        assert time.perf_counter() - interaction_started < 5
        browser.close()
    assert peak_rss > 0
    print(json.dumps({"generation_seconds": generation_seconds, "browser_seconds": browser_seconds,
                      "peak_process_rss_bytes": peak_rss,
                      "output_bytes": html_path.stat().st_size}, sort_keys=True))
