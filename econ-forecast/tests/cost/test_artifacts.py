"""T9 accept: tampered file rejected; unknown minor accepted; unknown major
rejected; slug collision handled; write/read round trip."""

from datetime import date
from pathlib import Path

import json
import numpy as np
import pytest
from common.model_pricing import ModelPriceEntry, RateSheet, Segment

from forecast.forecasting.dataset import PreparedDataset, ScopeMeta

from forecast.cost.artifacts import load_cost_run, write_cost_run
from forecast.cost.errors import InputError
from forecast.cost.kernel import SeriesInput, price_one_series, price_scope

ORG, MODEL, AGENT = "org-a", "opus", "agent-a"
HISTORY_DAY = date(2026, 9, 1)
DATES = (date(2026, 10, 1),)
SAMPLES = 2


def _dataset(extra_model: str | None = None) -> PreparedDataset:
    scopes = {
        (ORG, "organization", ORG): ScopeMeta(ORG, "organization", ORG, "Org A", frozenset([("u1", AGENT)])),
    }
    models = {ORG: {MODEL: frozenset([AGENT])}}
    usage = {
        (ORG, "organization", ORG, MODEL, HISTORY_DAY, AGENT, "input"): 200_000.0,
        (ORG, "organization", ORG, MODEL, HISTORY_DAY, AGENT, "cache_read"): 700_000.0,
        (ORG, "organization", ORG, MODEL, HISTORY_DAY, AGENT, "output"): 100_000.0,
    }
    if extra_model:
        models[ORG][extra_model] = frozenset([AGENT])
        usage[(ORG, "organization", ORG, extra_model, HISTORY_DAY, AGENT, "output")] = 500.0
    coverage = {(HISTORY_DAY, ORG, "u1", AGENT): "complete"}
    return PreparedDataset(
        start=date(2026, 1, 1), end=date(2026, 12, 31), timezone="UTC",
        models=models, scopes=scopes, usage=usage, coverage=coverage,
        startup=[], hashes={}, source_state={},
    )


def _sheet(model=MODEL) -> RateSheet:
    return RateSheet(
        org_id=ORG, start=DATES[0], end=DATES[0], price_version="v",
        models=[
            ModelPriceEntry(
                usage_model_id=model, status="matched", model_id=model, vendor="anthropic",
                channel="anthropic", match_step="normalized",
                segments=[
                    Segment(
                        from_=DATES[0], to=DATES[0],
                        rates={"input": "0.000003", "output": "0.000015", "cache_read": "0.0000003",
                               "cache_write_5m": None, "cache_write_1h": None},
                    )
                ],
            )
        ],
    )


def _write_run(output_dir, *, extra_model=None):
    dataset = _dataset(extra_model)
    sheet = _sheet()
    qualified = SeriesInput(
        org_id=ORG, scope_type="organization", scope_id=ORG, scope_name="Org A",
        model=MODEL, status="qualified", tokens=np.full((1, SAMPLES), 1_000_000.0),
    )
    inactive = SeriesInput(
        org_id=ORG, scope_type="organization", scope_id=ORG, scope_name="Org A",
        model="idle-model", status="inactive", tokens=np.zeros((1, SAMPLES)),
    )
    series = [qualified, inactive]
    if extra_model:
        series.append(
            SeriesInput(
                org_id=ORG, scope_type="organization", scope_id=ORG, scope_name="Org A",
                model=extra_model, status="qualified", tokens=np.full((1, SAMPLES), 500.0),
            )
        )
        sheet = _sheet_multi([MODEL, extra_model])

    series_costs = [
        price_one_series(s, dataset, sheet, DATES, HISTORY_DAY, max_age_days=30) for s in series
    ]
    scope_cost = price_scope(ORG, "organization", ORG, "Org A", series_costs, DATES, SAMPLES)

    request_doc = {
        "org_id": ORG,
        "forecast_run": {"name": "fc", "path": "artifacts/fc", "manifest_sha256": "a" * 64},
        "scenario": "baseline",
        "periods": [{"type": "requested", "start": DATES[0].isoformat(), "end": DATES[0].isoformat()}],
        "settings": {"half_life_days": 14, "own_mix_half_weight_days": 7, "max_age_days": 30},
    }
    write_cost_run(
        output_dir, request_doc=request_doc, sheet=sheet, dataset=dataset, org_id=ORG,
        dates=DATES, samples=SAMPLES,
        periods=({"type": "requested", "start": DATES[0], "end": DATES[0]},),
        series_costs=series_costs, scope_costs=[scope_cost],
        origin=HISTORY_DAY, max_age_days=30,
    )
    return series_costs, scope_cost


def _sheet_multi(models) -> RateSheet:
    return RateSheet(
        org_id=ORG, start=DATES[0], end=DATES[0], price_version="v",
        models=[
            ModelPriceEntry(
                usage_model_id=m, status="matched", model_id=m, vendor="anthropic",
                channel="anthropic", match_step="normalized",
                segments=[
                    Segment(
                        from_=DATES[0], to=DATES[0],
                        rates={"input": "0.000003", "output": "0.000015", "cache_read": "0.0000003",
                               "cache_write_5m": None, "cache_write_1h": None},
                    )
                ],
            )
            for m in models
        ],
    )


def test_write_and_load_round_trip(tmp_path):
    output = tmp_path / "run"
    _write_run(output)

    run = load_cost_run(output)
    assert run.manifest["status"] == "complete"  # manifest status: no *incomplete* scope
    assert run.index["org_id"] == ORG
    assert run.index["samples"] == SAMPLES
    scope_entry = run.index["scopes"][0]
    assert scope_entry["status"] == "conditional"  # one qualified + one inactive
    qualified_entry = next(e for e in run.index["series"] if e["model"] == MODEL)
    assert qualified_entry["status"] == "qualified"
    arr = run.paths(qualified_entry)
    assert arr.shape == (1, SAMPLES)
    assert np.all(arr > 0)

    scope_total = run.scope_paths(scope_entry)
    np.testing.assert_allclose(scope_total, arr)  # inactive series is all zero

    rows = run.daily_expected
    assert len(rows) >= 1
    assert any(r["model"] == MODEL for r in rows)

    assert run.mix["entries"], "expected a mix.json entry for the qualified series"
    mix_entry = run.mix["entries"][0]
    assert mix_entry["model"] == MODEL
    assert mix_entry["own_weight"] == 1.0  # organization: no parent to blend with

    assert run.rate_sheet.models[0].usage_model_id == MODEL


def test_tampered_file_is_rejected(tmp_path):
    output = tmp_path / "run"
    _write_run(output)
    (output / "mix.json").write_bytes(b'{"entries": []}')
    with pytest.raises(InputError):
        load_cost_run(output)


def test_unknown_minor_schema_is_accepted(tmp_path):
    output = tmp_path / "run"
    _write_run(output)
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["files"]["mix.json"]["schema"] = "cost-mix/2.7"
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    run = load_cost_run(output)  # does not raise
    assert run.mix is not None


def test_unknown_major_schema_is_rejected(tmp_path):
    output = tmp_path / "run"
    _write_run(output)
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["files"]["mix.json"]["schema"] = "cost-mix/3.0"
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(InputError):
        load_cost_run(output)


@pytest.mark.parametrize("name,old", [("mix.json", "cost-mix/1.0"),
                                      ("daily_expected.jsonl", "cost-daily-expected/1.0"),
                                      ("period_summary.json", "cost-period-summary/1.0")])
def test_version_1_cost_schema_ids_are_rejected(tmp_path, name, old):
    output = tmp_path / "run"
    _write_run(output)
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["files"][name]["schema"] = old
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(InputError, match="unsupported schema"):
        load_cost_run(output)


def test_writer_emits_version_2_ids_for_the_changed_artifacts(tmp_path):
    output = tmp_path / "run"
    _write_run(output)
    files = json.loads((output / "manifest.json").read_text())["files"]
    assert files["mix.json"]["schema"] == "cost-mix/2.0"
    assert files["daily_expected.jsonl"]["schema"] == "cost-daily-expected/2.0"
    assert files["period_summary.json"]["schema"] == "cost-period-summary/2.0"
    for name, schema in (("paths/index.json", "cost-index/1.0"), ("request.json", "cost-request/1.0"),
                         ("rate_sheet.json", "rate-sheet/1.0")):
        assert files[name]["schema"] == schema


def test_slug_collision_gets_a_numbered_suffix(tmp_path):
    output = tmp_path / "run"
    # "Model A" and "model_a" both slug to "model-a".
    series_costs, scope_cost = _write_run(output, extra_model="dummy")
    # Directly exercise the naming helper for the exact collision case (cheaper than
    # threading two colliding model names through the full pricing pipeline).
    from forecast.cost.artifacts import _npy_name

    used: set[str] = set()
    first = _npy_name("organization", "Org A", "Model A", used)
    second = _npy_name("organization", "Org A", "model_a", used)
    assert first == "organization-org-a__model-a.npy"
    assert second == "organization-org-a__model-a-2.npy"
    assert first != second

    run = load_cost_run(output)
    assert len(run.index["series"]) == 3


def test_borrowed_mix_series_gets_a_mix_json_entry(tmp_path):
    """Fix 3: mix.json carries an entry for a `scenario_mix_borrowed` series too --
    the borrowed shares, `active_days: 0`, `own_weight: 0.0`, the source window."""
    dataset = _dataset()
    sheet = _sheet()
    borrowed_shares = ({"agent_type": AGENT, "input": 0.5, "cache_write_5m": 0.0,
                        "cache_write_1h": 0.0, "cache_read": 0.3, "output": 0.2},)
    source_window = (date(2026, 7, 1), date(2026, 7, 31))
    series = SeriesInput(
        org_id=ORG, scope_type="organization", scope_id=ORG, scope_name="Org A",
        model=MODEL, status="qualified", tokens=np.full((1, SAMPLES), 200.0),
        assumptions=("scenario_mix_borrowed",),
        mix_override=borrowed_shares, mix_override_window=source_window,
    )
    series_cost = price_one_series(series, dataset, sheet, DATES, HISTORY_DAY, max_age_days=30)
    scope_cost = price_scope(ORG, "organization", ORG, "Org A", [series_cost], DATES, SAMPLES)

    output = tmp_path / "run"
    request_doc = {
        "org_id": ORG,
        "forecast_run": {"name": "fc", "path": "artifacts/fc", "manifest_sha256": "a" * 64},
        "scenario": "borrow-test",
        "periods": [{"type": "requested", "start": DATES[0].isoformat(), "end": DATES[0].isoformat()}],
        "settings": {"half_life_days": 14, "own_mix_half_weight_days": 7, "max_age_days": 30},
    }
    write_cost_run(
        output, request_doc=request_doc, sheet=sheet, dataset=dataset, org_id=ORG,
        dates=DATES, samples=SAMPLES,
        periods=({"type": "requested", "start": DATES[0], "end": DATES[0]},),
        series_costs=[series_cost], scope_costs=[scope_cost],
        origin=HISTORY_DAY, max_age_days=30,
    )

    run = load_cost_run(output)
    assert len(run.mix["entries"]) == 1
    entry = run.mix["entries"][0]
    assert entry["active_days"] == 0
    assert entry["own_weight"] == 0.0
    assert entry["shares"] == [dict(row) for row in borrowed_shares]
    assert entry["window"] == {"start": "2026-07-01", "end": "2026-07-31"}


def _write_run_with_renderer(output: Path, renderer):
    dataset = _dataset()
    sheet = _sheet()
    series = SeriesInput(
        org_id=ORG, scope_type="organization", scope_id=ORG, scope_name="Org A",
        model=MODEL, status="qualified", tokens=np.full((1, SAMPLES), 1_000_000.0),
    )
    series_cost = price_one_series(series, dataset, sheet, DATES, HISTORY_DAY, max_age_days=30)
    scope_cost = price_scope(ORG, "organization", ORG, "Org A", [series_cost], DATES, SAMPLES)
    request_doc = {
        "org_id": ORG,
        "forecast_run": {"name": "fc", "path": "artifacts/fc", "manifest_sha256": "a" * 64},
        "scenario": "baseline",
        "periods": [{"type": "requested", "start": DATES[0].isoformat(), "end": DATES[0].isoformat()}],
        "settings": {"half_life_days": 14, "own_mix_half_weight_days": 7, "max_age_days": 30},
    }
    write_cost_run(
        output, request_doc=request_doc, sheet=sheet, dataset=dataset, org_id=ORG,
        dates=DATES, samples=SAMPLES,
        periods=({"type": "requested", "start": DATES[0], "end": DATES[0]},),
        series_costs=[series_cost], scope_costs=[scope_cost],
        origin=HISTORY_DAY, max_age_days=30, report_renderer=renderer,
    )


def test_report_renderer_hook_runs_but_is_not_listed_in_the_manifest(tmp_path):
    """design §14.5: `write_cost_run`'s `report_renderer` hook runs before the
    manifest and writes `report.md`, but neither `report.md` nor `charts/` is
    folded into `manifest.files` -- they are presentation, regenerable later by
    `plot_cost` re-rendering the report without touching the manifest."""
    output = tmp_path / "run"

    def _fake_renderer(run, out):
        (out / "report.md").write_text("# fixture report\n", encoding="utf-8")
        return [Path("report.md")]

    _write_run_with_renderer(output, _fake_renderer)

    manifest = json.loads((output / "manifest.json").read_text())
    assert "report.md" not in manifest["files"]
    assert not any(name.startswith("charts/") for name in manifest["files"])
    assert (output / "report.md").read_text() == "# fixture report\n"
    load_cost_run(output)  # unaffected by report.md's presence or content


def test_reader_never_checks_report_md_present_absent_or_tampered(tmp_path):
    """report.md is not in the manifest at all (design §14.5): loading a run does
    not depend on it existing, matching its recorded bytes, or existing unaltered."""
    output = tmp_path / "run"

    def _fake_renderer(run, out):
        (out / "report.md").write_text("# fixture report\n", encoding="utf-8")
        return [Path("report.md")]

    _write_run_with_renderer(output, _fake_renderer)
    report = output / "report.md"

    report.write_text("tampered\n", encoding="utf-8")
    load_cost_run(output)  # no checksum kept for report.md; tampering is invisible to the reader

    report.unlink()
    load_cost_run(output)
