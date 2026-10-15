"""T11 accept: report renders for baseline and scenario runs, in §8.3 order;
charts write as PNG and offline HTML with no network references."""

from datetime import date, timedelta
from pathlib import Path
import json

import numpy as np
import pytest
from common.model_pricing import ModelPriceEntry, RateSheet, Segment

from forecast.forecasting.dataset import PreparedDataset, ScopeMeta

from forecast.cost.artifacts import load_cost_run, write_cost_run
from forecast.cost.kernel import SeriesInput, price_one_series, price_scope
from forecast.cost.presentation import render_report

ORG, TEAM = "org-a", "team1"
MODEL = "claude-sonnet-5"
INACTIVE_MODEL = "idle-model"
UNPRICED_MODEL = "claude-ghost"
AGENT = "claude"
DATES = (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3))
ORIGIN = date(2026, 9, 30)
SAMPLES = 4
MAX_AGE_DAYS = 30


def _rates() -> dict:
    return {
        "input": "0.000003", "output": "0.000015", "cache_read": "0.0000003",
        "cache_write_5m": None, "cache_write_1h": None,
    }


def _sheet() -> RateSheet:
    return RateSheet(
        org_id=ORG, start=DATES[0], end=DATES[-1], price_version="v-test",
        models=[
            ModelPriceEntry(
                usage_model_id=MODEL, status="matched", model_id=MODEL, vendor="anthropic",
                channel="anthropic", match_step="normalized",
                segments=[Segment(from_=DATES[0], to=DATES[-1], rates=_rates())],
            ),
            ModelPriceEntry(
                usage_model_id=UNPRICED_MODEL, status="ambiguous",
                reason="normalizes to 2 registry models",
                candidates=["claude-sonnet-4-5", "claude-sonnet-5"],
            ),
        ],
    )


def _dataset() -> PreparedDataset:
    """Team1's own mix is thin (4 active days -> own_weight = 4/11 < 0.9, as in
    test_mix.py's design-example fixture); the Organization has a longer, separate
    history for the same model so its own mix (own_weight always 1.0) differs."""
    team_keys = frozenset([("u1", AGENT), ("u2", AGENT)])
    scopes = {
        (ORG, "organization", ORG): ScopeMeta(ORG, "organization", ORG, "Org A", team_keys),
        (ORG, "team", TEAM): ScopeMeta(ORG, "team", TEAM, "Team 1", team_keys),
    }
    models = {ORG: {MODEL: frozenset([AGENT]), UNPRICED_MODEL: frozenset([AGENT])}}
    usage: dict = {}
    coverage: dict = {}
    mix_days = [ORIGIN - timedelta(days=i) for i in range(4)]
    for day in mix_days:
        usage[(ORG, "team", TEAM, MODEL, day, AGENT, "output")] = 100.0
        usage[(ORG, "team", TEAM, MODEL, day, AGENT, "input")] = 900.0
        usage[(ORG, "organization", ORG, MODEL, day, AGENT, "output")] = 300.0
        usage[(ORG, "organization", ORG, MODEL, day, AGENT, "input")] = 9700.0
        for user in ("u1", "u2"):
            coverage[(day, ORG, user, AGENT)] = "complete"
    return PreparedDataset(
        start=date(2026, 1, 1), end=date(2026, 12, 31), timezone="UTC",
        models=models, scopes=scopes, usage=usage, coverage=coverage,
        startup=[], hashes={}, source_state={},
    )


def _request_doc(scenario: str) -> dict:
    return {
        "org_id": ORG,
        "forecast_run": {"name": "fc", "path": "artifacts/fc", "manifest_sha256": "a" * 64},
        "scenario": scenario,
        "periods": [{"type": "requested", "start": DATES[0].isoformat(), "end": DATES[-1].isoformat()}],
        "settings": {"half_life_days": 14, "own_mix_half_weight_days": 7, "max_age_days": MAX_AGE_DAYS},
    }


def _build_run(output_dir, *, scenario_series_assumptions: tuple[str, ...] = (), scenario: str = "baseline",
                scenario_yaml_bytes: bytes | None = None):
    dataset = _dataset()
    sheet = _sheet()

    org_qualified = SeriesInput(
        org_id=ORG, scope_type="organization", scope_id=ORG, scope_name="Org A",
        model=MODEL, status="qualified", tokens=np.full((len(DATES), SAMPLES), 1_000_000.0),
    )
    org_inactive = SeriesInput(
        org_id=ORG, scope_type="organization", scope_id=ORG, scope_name="Org A",
        model=INACTIVE_MODEL, status="inactive", tokens=np.zeros((len(DATES), SAMPLES)),
    )
    org_unpriced = SeriesInput(
        org_id=ORG, scope_type="organization", scope_id=ORG, scope_name="Org A",
        model=UNPRICED_MODEL, status="qualified", tokens=np.full((len(DATES), SAMPLES), 500.0),
    )
    team_qualified = SeriesInput(
        org_id=ORG, scope_type="team", scope_id=TEAM, scope_name="Team 1",
        model=MODEL, status="qualified", tokens=np.full((len(DATES), SAMPLES), 200_000.0),
        assumptions=scenario_series_assumptions,
    )

    series = [org_qualified, org_inactive, org_unpriced, team_qualified]
    series_costs = [
        price_one_series(s, dataset, sheet, DATES, ORIGIN, MAX_AGE_DAYS) for s in series
    ]
    org_costs = [c for c in series_costs if c.scope_type == "organization"]
    team_costs = [c for c in series_costs if c.scope_type == "team"]
    scope_costs = [
        price_scope(ORG, "organization", ORG, "Org A", org_costs, DATES, SAMPLES),
        price_scope(ORG, "team", TEAM, "Team 1", team_costs, DATES, SAMPLES),
    ]

    write_cost_run(
        output_dir, request_doc=_request_doc(scenario), sheet=sheet, dataset=dataset, org_id=ORG,
        dates=DATES, samples=SAMPLES,
        periods=({"type": "requested", "start": DATES[0], "end": DATES[-1]},),
        series_costs=series_costs, scope_costs=scope_costs,
        origin=ORIGIN, max_age_days=MAX_AGE_DAYS,
        scenario_yaml_bytes=scenario_yaml_bytes,
    )
    return load_cost_run(output_dir)


_SCENARIO_YAML = b"""\
schema: scenario/1.0
name: team1-opus-to-sonnet
scope:
  type: team
  name: team1
changes:
  - model_transfer:
      from: claude-opus-5
      to: claude-sonnet-5
      fraction: 0.3
      token_factor: 1.0
      effective_from: 2026-10-01
"""


def _section_index(text: str, heading: str) -> int:
    index = text.find(heading)
    assert index != -1, f"missing section: {heading}"
    return index


def test_report_sections_present_in_order(tmp_path):
    output = tmp_path / "run"
    run = _build_run(output)

    written = render_report(run, output)
    assert written == [Path("report.md")]
    report_text = (output / "report.md").read_text(encoding="utf-8")

    order = [
        "## Blocking problems", "## Cost table",
        "## Thin-evidence mix", "## Price mapping", "## Assumptions",
    ]
    indices = [_section_index(report_text, heading) for heading in order]
    assert indices == sorted(indices)
    # No scenario section on a baseline run.
    assert "## Scenario" not in report_text
    # This run has never been plotted (no charts/index.json): the Charts
    # section is omitted entirely, not shown empty (design §14.5).
    assert "## Charts" not in report_text


def test_unpriced_model_listed_with_reason_and_candidates(tmp_path):
    output = tmp_path / "run"
    run = _build_run(output)
    render_report(run, output)
    report_text = (output / "report.md").read_text(encoding="utf-8")

    blocking = report_text[report_text.index("## Blocking problems"):report_text.index("## Cost table")]
    assert UNPRICED_MODEL in blocking
    assert "claude-sonnet-4-5" in blocking  # a listed candidate
    assert "normalizes to 2 registry models" in blocking  # the ambiguity reason
    # The unpriced series also makes the whole organization scope incomplete.
    assert "organization Org A" in blocking

    cost_table = report_text[report_text.index("## Cost table"):report_text.index("## Thin-evidence mix")]
    assert "organization Org A" not in cost_table  # incomplete scope: no total
    assert "team Team 1" in cost_table


def test_thin_evidence_mix_lists_team_series(tmp_path):
    output = tmp_path / "run"
    run = _build_run(output)
    render_report(run, output)
    report_text = (output / "report.md").read_text(encoding="utf-8")

    thin = report_text[report_text.index("## Thin-evidence mix"):report_text.index("## Price mapping")]
    assert TEAM in thin or "Team 1" in thin
    assert MODEL in thin


def _write_charts_index(output: Path, plots: list[dict]) -> None:
    (output / "charts").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1, "operation": "plot", "created_at": "2026-10-01T00:00:00Z",
        "status": "complete", "plots": plots,
    }
    (output / "charts" / "index.json").write_text(json.dumps(payload), encoding="utf-8")


def test_render_report_no_longer_draws_charts_itself(tmp_path):
    """design §14.5: cost runs stop drawing charts; `render_report` only ever
    writes `report.md`. Chart drawing moved to `plot_cost` (T6)."""
    output = tmp_path / "run"
    run = _build_run(output)
    written = render_report(run, output)
    assert written == [Path("report.md")]
    assert not (output / "charts").exists()


def test_charts_section_lists_exactly_the_charts_index_json_entries(tmp_path):
    """design §14.5/T2: once `plot_cost` has drawn charts (`charts/index.json`
    present), re-rendering the report links exactly those files, labeled from
    scope_type/scope_name/view/model."""
    output = tmp_path / "run"
    run = _build_run(output)
    render_report(run, output)  # first render: no charts yet
    _write_charts_index(output, [
        {"scope_type": "team", "scope_name": "Team 1", "view": "total", "measure": "cost",
         "model": None, "files": ["team-team-1__total__cost.html", "team-team-1__total__cost.png"],
         "status": "complete"},
        {"scope_type": "team", "scope_name": "Team 1", "view": "claude-sonnet-5", "measure": "cost",
         "model": "claude-sonnet-5", "files": ["team-team-1__claude-sonnet-5__cost.html"],
         "status": "complete"},
    ])

    render_report(run, output)  # re-render after plot_cost drew charts
    report_text = (output / "report.md").read_text(encoding="utf-8")
    charts_section = report_text[report_text.index("## Charts"):report_text.index("## Thin-evidence mix")]
    assert "### team Team 1" in charts_section
    assert "[interactive](charts/team-team-1__total__cost.html)" in charts_section
    assert "[PNG](charts/team-team-1__total__cost.png)" in charts_section
    assert "claude-sonnet-5" in charts_section
    assert "[interactive](charts/team-team-1__claude-sonnet-5__cost.html)" in charts_section
    assert "[PNG]" not in charts_section.split("claude-sonnet-5", 1)[1].split("\n", 1)[0]


def test_scenario_section_only_for_scenario_runs(tmp_path):
    baseline_dir = tmp_path / "baseline"
    baseline_run = _build_run(baseline_dir)
    render_report(baseline_run, baseline_dir)
    baseline_text = (baseline_dir / "report.md").read_text(encoding="utf-8")
    assert "## Scenario" not in baseline_text

    scenario_dir = tmp_path / "scenario"
    scenario_run = _build_run(
        scenario_dir,
        scenario="team1-opus-to-sonnet",
        scenario_series_assumptions=("scenario_constant_tokens",),
        scenario_yaml_bytes=_SCENARIO_YAML,
    )
    render_report(scenario_run, scenario_dir)
    scenario_text = (scenario_dir / "report.md").read_text(encoding="utf-8")

    idx_scenario = scenario_text.index("## Scenario")
    idx_thin = scenario_text.index("## Thin-evidence mix")
    assert idx_scenario < idx_thin

    scenario_section = scenario_text[idx_scenario:idx_thin]
    assert "team1-opus-to-sonnet" in scenario_section
    assert "model_transfer" not in scenario_section  # rendered in plain language, not raw YAML keys
    assert "claude-opus-5" in scenario_section
    assert "scenario_constant_tokens" in scenario_section


def test_render_report_output_matches_output_dir_argument(tmp_path):
    """The interface takes `output_dir` explicitly; `render_report` must write
    there rather than assuming it always equals `run.root` (even though the
    fixtures above happen to pass the same directory the run lives in)."""
    output = tmp_path / "run"
    run = _build_run(output)

    written = render_report(run, output)
    for rel in written:
        assert not rel.is_absolute()
        assert (output / rel).exists()


def test_scope_order_follows_the_scope_tree_and_status_is_derived_from_the_index():
    from types import SimpleNamespace

    from forecast.cost.presentation import _run_status, _scope_order

    keys = [("project", "a"), ("team", "b"), ("organization", "o"), ("team", "a")]
    assert sorted(keys, key=_scope_order) == [
        ("organization", "o"), ("team", "a"), ("team", "b"), ("project", "a"),
    ]
    run = SimpleNamespace(index={"scopes": [{"status": "complete"}, {"status": "conditional"}]})
    assert _run_status(run) == "complete"
    run.index["scopes"].append({"status": "incomplete"})
    assert _run_status(run) == "incomplete"
