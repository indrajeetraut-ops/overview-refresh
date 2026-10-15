"""P2: `org.yaml` schema, defaults, team-override merge, and §3.5 cross-dataset validation."""
from pathlib import Path

import pytest
import yaml

from forecast.synthetic.errors import ConfigError
from forecast.synthetic.org.config import Defaults, validate_org_inputs


# --------------------------------------------------------------------------------------------
# Built-in defaults and team-override merge
# --------------------------------------------------------------------------------------------

def test_empty_defaults_yields_built_ins():
    defaults = Defaults()
    assert defaults.work.unlinked_share == 0.20
    assert defaults.work.feature_size == {"1": .35, "2": .25, "3": .20, "4": .10, "5": .05, "6": .05}
    assert defaults.sessions.mean_input_tokens == 1_200_000
    assert defaults.pr.merge_delay_hours.min == 0.2 and defaults.pr.merge_delay_hours.max == 8


def test_team_override_is_field_level(load_and_validate, org_config_dict):
    org_config_dict["defaults"] = {"work": {"unlinked_share": 0.5}}
    org_config_dict["teams"]["platform"] = {"work": {"parallel_items": 2}}
    resolved = load_and_validate(org_config_dict)
    platform = resolved.team_effective["platform"].work
    # The override only set parallel_items; unlinked_share still comes from the (overridden)
    # default, not the built-in default -- a field-level merge, not a whole-block replace.
    assert platform.parallel_items == 2
    assert platform.unlinked_share == 0.5
    payments = resolved.team_effective["payments"].work
    assert payments.parallel_items == 1
    assert payments.unlinked_share == 0.5


def test_mixture_override_replaces_whole(load_and_validate, org_config_dict):
    org_config_dict["teams"]["platform"] = {"work": {"change_mix": {"feature": 1.0}}}
    resolved = load_and_validate(org_config_dict)
    assert resolved.team_effective["platform"].work.change_mix == {"feature": 1.0}
    # Untouched team keeps the built-in mixture whole.
    assert resolved.team_effective["payments"].work.change_mix == {
        "feature": .45, "fix": .25, "refactor": .12, "test": .06, "docs": .06, "chore": .06}


# --------------------------------------------------------------------------------------------
# In-config validation (single-document, no cross-file inputs needed)
# --------------------------------------------------------------------------------------------

def test_module_weights_must_sum_to_one(build_org_config, org_config_dict):
    org_config_dict["repos"][0]["modules"] = [
        {"path": "src/", "weight": 0.4, "extensions": {".py": 1.0}}]
    with pytest.raises(ConfigError, match="modules"):
        build_org_config(org_config_dict)


def test_pr_size_min_must_be_below_max(build_org_config, org_config_dict):
    org_config_dict["defaults"] = {"pr": {"size_min_loc": 100, "size_max_loc": 50}}
    with pytest.raises(ConfigError, match="size_min_loc"):
        build_org_config(org_config_dict)


def test_min_max_pair_ordered(build_org_config, org_config_dict):
    org_config_dict["defaults"] = {"sessions": {"work_window_start_hours": {"min": 12, "max": 8}}}
    with pytest.raises(ConfigError, match="min"):
        build_org_config(org_config_dict)


def test_repo_names_unique(build_org_config, org_config_dict):
    org_config_dict["repos"][1]["name"] = org_config_dict["repos"][0]["name"]
    with pytest.raises(ConfigError, match=r"repos\[\]\.name"):
        build_org_config(org_config_dict)


def test_group_child_in_two_groups_rejected(build_org_config, org_config_dict):
    org_config_dict["org"]["groups"] = [
        {"team_id": "g1", "name": "G1", "children": ["platform"]},
        {"team_id": "g2", "name": "G2", "children": ["platform"]},
    ]
    with pytest.raises(ConfigError, match="two groups"):
        build_org_config(org_config_dict)


# --------------------------------------------------------------------------------------------
# Cross-dataset validation (plan §3.5), one test per rule, asserting the field is named
# --------------------------------------------------------------------------------------------

def test_series_dir_must_be_a_complete_series(load_and_validate, org_config_dict, series_inputs):
    tampered = dict(series_inputs, manifest=dict(series_inputs["manifest"], operation="roster"))
    with pytest.raises(ConfigError, match="operation"):
        load_and_validate(org_config_dict, inputs=tampered)


def test_series_dir_schema_1_is_rejected_with_a_regenerate_message(
        load_and_validate, org_config_dict, series_inputs):
    tampered = dict(series_inputs, manifest=dict(series_inputs["manifest"], schema_version=1))
    with pytest.raises(ConfigError, match="dataset uses schema 1; regenerate with this version"):
        load_and_validate(org_config_dict, inputs=tampered)


@pytest.mark.parametrize("version", [3, True, "2", None])
def test_series_dir_requires_schema_version_2(load_and_validate, org_config_dict, series_inputs,
                                              version):
    tampered = dict(series_inputs, manifest=dict(series_inputs["manifest"], schema_version=version))
    with pytest.raises(ConfigError, match="schema_version must be 2"):
        load_and_validate(org_config_dict, inputs=tampered)


def test_series_dir_must_be_status_complete(load_and_validate, org_config_dict, series_inputs):
    tampered = dict(series_inputs, manifest=dict(series_inputs["manifest"], status="running"))
    with pytest.raises(ConfigError, match="status"):
        load_and_validate(org_config_dict, inputs=tampered)


def test_project_repo_mapping_must_be_bijective(load_and_validate, org_config_dict):
    org_config_dict["repos"][0]["project"] = "does_not_exist"
    with pytest.raises(ConfigError, match=r"repos\[\]\.project"):
        load_and_validate(org_config_dict)


def test_every_rostered_team_needs_a_teams_entry(load_and_validate, org_config_dict):
    del org_config_dict["teams"]["payments"]
    with pytest.raises(ConfigError, match="teams"):
        load_and_validate(org_config_dict)


def test_teams_rejects_extra_names(load_and_validate, org_config_dict):
    org_config_dict["teams"]["ghost-team"] = {}
    with pytest.raises(ConfigError, match="teams"):
        load_and_validate(org_config_dict)


def test_leads_must_be_team_members(load_and_validate, org_config_dict, series_inputs):
    other_team_member = next(
        uid for team in series_inputs["teams"] if team.team_name == "payments" for uid in team.user_ids)
    org_config_dict["org"]["leads"] = {"platform": other_team_member}
    with pytest.raises(ConfigError, match="org.leads"):
        load_and_validate(org_config_dict)


def test_groups_children_must_reference_existing_teams(load_and_validate, org_config_dict):
    org_config_dict["org"]["groups"] = [{"team_id": "g1", "name": "G1", "children": ["no-such-team"]}]
    with pytest.raises(ConfigError, match="org.groups"):
        load_and_validate(org_config_dict)


def test_users_need_github_login(load_and_validate, org_config_dict, build_series_chain,
                                 read_series_inputs):
    # A second, independent-style series (no github_login) -- the shared `small_series` fixture
    # is consistent-style and must stay untouched.
    independent = build_series_chain(identity_style="independent", org_domain="no-login.test",
                                     label="independent-series")
    org_config_dict["org"]["series_dir"] = str(independent["series_dir"])
    inputs = read_series_inputs(independent["series_dir"])
    with pytest.raises(ConfigError, match="github_login"):
        load_and_validate(org_config_dict, inputs=inputs)


def test_admin_login_collides_with_a_user(load_and_validate, org_config_dict, series_inputs):
    org_config_dict["org"]["admin"]["github_login"] = series_inputs["users"][0].github_login
    with pytest.raises(ConfigError, match="admin.github_login"):
        load_and_validate(org_config_dict)


def test_non_claude_code_agent_rejected(load_and_validate, org_config_dict, series_inputs,
                                        small_series, read_series_inputs):
    cfg_path = small_series["series_dir"] / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    raw["agents"]["codex"] = {"billing_mode": "api", "usage_share": 0.1, "models": {"gpt-5.6-terra": 1.0}}
    raw["agents"]["claude_code"]["usage_share"] = 0.9
    cfg_path.write_text(yaml.safe_dump(raw))
    inputs = read_series_inputs(small_series["series_dir"])
    with pytest.raises(ConfigError, match="D1"):
        load_and_validate(org_config_dict, inputs=inputs)


def test_unpriced_model_rejected(load_and_validate, org_config_dict, series_inputs, small_series,
                                 read_series_inputs):
    cfg_path = small_series["series_dir"] / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    raw["agents"]["claude_code"]["models"] = {"claude-sonnet-5": 0.5, "not-a-real-model": 0.5}
    cfg_path.write_text(yaml.safe_dump(raw))
    inputs = read_series_inputs(small_series["series_dir"])
    with pytest.raises(ConfigError, match="unpriced"):
        load_and_validate(org_config_dict, inputs=inputs)


def test_allowances_section_is_rejected(load_and_validate, org_config_dict):
    """Token allowances were removed (code-econ docs/plan/2026-09-26-cost-based-seat-utilization.md):
    a seat's allowance is its team budget / members. A config still carrying the key must fail
    loudly, not generate a dataset that silently ignores it."""
    org_config_dict["allowances"] = {"teams": {"platform": 1_000_000}}
    with pytest.raises(ConfigError, match="allowances"):
        load_and_validate(org_config_dict)
