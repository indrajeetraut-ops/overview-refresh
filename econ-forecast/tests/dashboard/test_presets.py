"""Presets: the shipped file matches the plan's table, every rule has a failing case,
and rendered scenarios load through the cost module's own `ScenarioConfig`."""

from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
import yaml

from forecast.cost.schemas import ScenarioConfig
from forecast.dashboard import (
    DEFAULT_PRESETS_PATH,
    ExecutionError,
    InputError,
    load_presets,
    render_scenario,
    write_scenario,
)

OPUS, SONNET, HAIKU = "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"
EFFECTIVE = date(2026, 10, 9)


def _transfer(src, dst, fraction):
    return ("model_transfer", {"from": src, "to": dst, "fraction": fraction, "token_factor": 1.0})


# Plan 2026-10-05 §2 preset table: id, label, group, changes in order.
PLAN_TABLE = [
    ("baseline", "Current trajectory", None, []),
    ("opus-to-sonnet-30", "Move 30% of Opus work to Sonnet", "Model mix", [_transfer(OPUS, SONNET, 0.3)]),
    ("opus-to-sonnet-60", "Move 60% of Opus work to Sonnet", "Model mix", [_transfer(OPUS, SONNET, 0.6)]),
    ("sonnet-to-haiku-30", "Move 30% of Sonnet work to Haiku", "Model mix", [_transfer(SONNET, HAIKU, 0.3)]),
    ("usage-up-20", "Usage grows 20%", "Usage", [("growth", {"factor": 1.2})]),
    ("usage-down-15", "Usage drops 15%", "Usage", [("growth", {"factor": 0.85})]),
    (
        "usage-up-20-opus-to-sonnet-50",
        "Grow 20%, move half of Opus to Sonnet",
        "Combined",
        [("growth", {"factor": 1.2}), _transfer(OPUS, SONNET, 0.5)],
    ),
]


def _valid() -> dict:
    return {
        "schema": "dashboard-presets/1.0",
        "presets": [
            {"id": "baseline", "label": "Current trajectory", "description": "As is."},
            {
                "id": "grow",
                "label": "Grow",
                "group": "Usage",
                "description": "More.",
                "changes": [{"growth": {"factor": 1.1}}],
            },
        ],
    }


def _load(tmp_path: Path, doc: dict):
    path = tmp_path / "presets.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return load_presets(path)


def _rejects(tmp_path: Path, doc: dict, fragment: str) -> None:
    with pytest.raises(InputError, match=fragment):
        _load(tmp_path, doc)


# --- shipped file -----------------------------------------------------------


def test_shipped_presets_match_the_plan_table():
    presets = load_presets(DEFAULT_PRESETS_PATH)
    assert [(p.id, p.label, p.group) for p in presets] == [(i, l, g) for i, l, g, _ in PLAN_TABLE]
    for preset, (_, _, _, changes) in zip(presets, PLAN_TABLE):
        assert len(preset.changes) == len(changes)
        for change, (kind, body) in zip(preset.changes, changes):
            dumped = change.model_dump(by_alias=True, exclude_none=True)
            assert dumped == {kind: body}
        assert preset.description.strip()


def test_shipped_presets_use_no_banned_tone_words():
    text = DEFAULT_PRESETS_PATH.read_text(encoding="utf-8").lower()
    assert not [w for w in ("waste", "burn rate", "lazy", "sloppy") if w in text]


# --- one test per rule ------------------------------------------------------


def test_schema_must_match(tmp_path):
    doc = _valid()
    doc["schema"] = "dashboard-presets/2.0"
    _rejects(tmp_path, doc, "dashboard-presets/1.0")


def test_unknown_keys_are_rejected(tmp_path):
    doc = _valid()
    doc["extra"] = 1
    _rejects(tmp_path, doc, "extra")
    doc = _valid()
    doc["presets"][1]["colour"] = "red"
    _rejects(tmp_path, doc, "colour")


def test_ids_must_be_unique(tmp_path):
    doc = _valid()
    doc["presets"].append(deepcopy(doc["presets"][1]))
    _rejects(tmp_path, doc, "unique")


@pytest.mark.parametrize("bad", ["Grow", "grow_up", "-grow", "grow up", "gröw", ""])
def test_ids_must_be_directory_safe(tmp_path, bad):
    doc = _valid()
    doc["presets"][1]["id"] = bad
    _rejects(tmp_path, doc, "must match")


def test_exactly_one_baseline(tmp_path):
    doc = _valid()
    doc["presets"] = doc["presets"][1:]
    _rejects(tmp_path, doc, "exactly one")
    doc = _valid()
    doc["presets"].append(deepcopy(doc["presets"][0]))
    _rejects(tmp_path, doc, "unique")


def test_baseline_must_be_first(tmp_path):
    doc = _valid()
    doc["presets"].reverse()
    _rejects(tmp_path, doc, "first")


def test_baseline_has_no_changes_or_group(tmp_path):
    doc = _valid()
    doc["presets"][0]["changes"] = [{"growth": {"factor": 1.1}}]
    _rejects(tmp_path, doc, "baseline")
    doc = _valid()
    doc["presets"][0]["group"] = "Usage"
    _rejects(tmp_path, doc, "baseline")


def test_other_presets_need_a_change(tmp_path):
    doc = _valid()
    doc["presets"][1]["changes"] = []
    _rejects(tmp_path, doc, "at least one change")
    del doc["presets"][1]["changes"]
    _rejects(tmp_path, doc, "at least one change")


@pytest.mark.parametrize("group", [None, "", "  "])
def test_other_presets_need_a_group(tmp_path, group):
    doc = _valid()
    if group is None:
        del doc["presets"][1]["group"]
    else:
        doc["presets"][1]["group"] = group
    _rejects(tmp_path, doc, "group")


def test_changes_use_the_cost_schema(tmp_path):
    doc = _valid()
    doc["presets"][1]["changes"] = [{"growth": {"factor": -1}}]
    _rejects(tmp_path, doc, "factor")
    doc["presets"][1]["changes"] = [{"model_transfer": {"from": OPUS, "to": SONNET, "fraction": 1.5}}]
    _rejects(tmp_path, doc, "fraction")
    doc["presets"][1]["changes"] = [{"growth": {"factor": 1.1}, "model_transfer": {"from": OPUS, "to": SONNET, "fraction": 0.5}}]
    _rejects(tmp_path, doc, "exactly one")


def test_agent_transfer_is_allowed(tmp_path):
    doc = _valid()
    doc["presets"][1]["changes"] = [{"agent_transfer": {"from": "codex", "to": "claude_code", "fraction": 0.2}}]
    assert _load(tmp_path, doc)[1].changes[0].agent_transfer is not None


def test_unknown_outer_change_key_is_rejected(tmp_path):
    doc = _valid()
    doc["presets"][1]["changes"] = [{"grow": {"factor": 1.1}}]
    _rejects(tmp_path, doc, "unknown change key")


def test_unknown_inner_change_key_is_rejected(tmp_path):
    doc = _valid()
    doc["presets"][1]["changes"] = [
        {"model_transfer": {"from": OPUS, "to": SONNET, "fraction": 0.3, "token_factr": 2.0}}
    ]
    _rejects(tmp_path, doc, "token_factr")


@pytest.mark.parametrize("value", [date(2026, 10, 9), None])
def test_effective_from_is_never_set_in_a_preset(tmp_path, value):
    doc = _valid()
    doc["presets"][1]["changes"] = [{"growth": {"factor": 1.1, "effective_from": value}}]
    _rejects(tmp_path, doc, "effective_from")


@pytest.mark.parametrize("field", ["label", "description"])
@pytest.mark.parametrize("value", ["", "   "])
def test_label_and_description_are_not_empty(tmp_path, field, value):
    doc = _valid()
    doc["presets"][1][field] = value
    _rejects(tmp_path, doc, field)


def test_missing_or_malformed_file_is_an_input_error(tmp_path):
    with pytest.raises(InputError):
        load_presets(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(InputError):
        load_presets(bad)


def _load_text(tmp_path: Path, text: str):
    path = tmp_path / "presets.yaml"
    path.write_text(text, encoding="utf-8")
    return load_presets(path)


def test_only_the_schema_alias_is_accepted(tmp_path):
    doc = _valid()
    doc["schema_"] = doc.pop("schema")
    _rejects(tmp_path, doc, "schema")


def test_transfer_uses_the_alias_not_the_python_field_name(tmp_path):
    doc = _valid()
    doc["presets"][1]["changes"] = [{"model_transfer": {"from_": OPUS, "to": SONNET, "fraction": 0.3}}]
    _rejects(tmp_path, doc, r"unknown key\(s\) \['from_'\]")
    # Both spellings at once: one used to be dropped silently.
    doc["presets"][1]["changes"] = [{"model_transfer": {"from": OPUS, "from_": HAIKU, "to": SONNET, "fraction": 0.3}}]
    _rejects(tmp_path, doc, "from_")


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    text = (DEFAULT_PRESETS_PATH.read_text() + "schema: dashboard-presets/1.0\n")
    with pytest.raises(InputError, match="duplicate"):
        _load_text(tmp_path, text)
    nested = (
        "schema: dashboard-presets/1.0\n"
        "presets:\n"
        "  - {id: baseline, label: A, description: B}\n"
        "  - {id: grow, label: G, group: U, description: D, changes: [{growth: {factor: 1.1, factor: 2.0}}]}\n"
    )
    with pytest.raises(InputError, match="duplicate"):
        _load_text(tmp_path, nested)


def test_an_unhashable_yaml_key_is_an_input_error(tmp_path):
    with pytest.raises(InputError, match="mapping key"):
        _load_text(tmp_path, "schema: dashboard-presets/1.0\n? [a, b]\n: x\n")


# 'inf' and 1e999 reach pydantic as strings (YAML 1.1 has no float form for 1e999).
@pytest.mark.parametrize("value", [".inf", "-.inf", ".nan", "'inf'", "'Infinity'", "1e999"])
def test_non_finite_numbers_are_rejected(tmp_path, value):
    text = (
        "schema: dashboard-presets/1.0\n"
        "presets:\n"
        "  - {id: baseline, label: A, description: B}\n"
        f"  - {{id: grow, label: G, group: U, description: D, changes: [{{growth: {{factor: {value}}}}}]}}\n"
    )
    with pytest.raises(InputError, match="finite"):
        _load_text(tmp_path, text)
    text = text.replace("growth: {factor: " + value + "}", "model_transfer: {from: a, to: b, fraction: 0.5, token_factor: " + value + "}")
    with pytest.raises(InputError, match="finite"):
        _load_text(tmp_path, text)


def test_unknown_keys_of_mixed_types_are_an_input_error_not_a_type_error(tmp_path):
    # YAML 1.1 reads `on` as a boolean, so the unknown keys are {True, "note"}.
    text = (
        "schema: dashboard-presets/1.0\n"
        "presets:\n"
        "  - {id: baseline, label: A, description: B}\n"
        "  - {id: grow, label: G, group: U, description: D, changes: [{growth: {factor: 1.1, on: 2026-10-09, note: x}}]}\n"
    )
    with pytest.raises(InputError, match="unknown key"):
        _load_text(tmp_path, text)
    outer = text.replace("{growth: {factor: 1.1, on: 2026-10-09, note: x}}", "{1: a, zzz: b}")
    with pytest.raises(InputError, match="unknown change key"):
        _load_text(tmp_path, outer)


# --- rendering --------------------------------------------------------------


def test_every_shipped_preset_renders_a_valid_scenario(tmp_path):
    presets = load_presets(DEFAULT_PRESETS_PATH)
    for preset in presets[1:]:
        doc = render_scenario(preset, EFFECTIVE)
        config = ScenarioConfig.model_validate(doc)
        assert config.name == preset.id
        assert config.scope.type == "organization" and config.scope.name is None
        assert len(config.changes) == len(preset.changes)
        for change in config.changes:
            (body,) = [b for b in (change.model_transfer, change.agent_transfer, change.growth) if b]
            assert body.effective_from == EFFECTIVE
        # Round-trips through a file exactly as the cost module reads it.
        path = write_scenario(preset, EFFECTIVE, tmp_path / "scenarios" / f"{preset.id}.yaml")
        assert ScenarioConfig.model_validate(yaml.safe_load(path.read_text())).changes == config.changes
        assert "&id" not in path.read_text()


def test_rendering_keeps_change_order_and_parameters():
    presets = {p.id: p for p in load_presets(DEFAULT_PRESETS_PATH)}
    doc = render_scenario(presets["usage-up-20-opus-to-sonnet-50"], EFFECTIVE)
    assert doc["schema"] == "scenario/1.0"
    assert doc["scope"] == {"type": "organization"}
    assert [list(c) for c in doc["changes"]] == [["growth"], ["model_transfer"]]
    assert doc["changes"][0]["growth"] == {"factor": 1.2, "effective_from": EFFECTIVE}
    assert doc["changes"][1]["model_transfer"] == {
        "from": OPUS, "to": SONNET, "fraction": 0.5, "token_factor": 1.0, "effective_from": EFFECTIVE,
    }


def test_an_unwritable_scenario_path_is_an_execution_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    preset = load_presets(DEFAULT_PRESETS_PATH)[1]
    with pytest.raises(ExecutionError, match="cannot write scenario"):
        write_scenario(preset, EFFECTIVE, blocker / "scenarios" / f"{preset.id}.yaml")


def test_baseline_has_no_scenario(tmp_path):
    baseline = load_presets(DEFAULT_PRESETS_PATH)[0]
    assert baseline.is_baseline
    with pytest.raises(InputError):
        render_scenario(baseline, EFFECTIVE)
