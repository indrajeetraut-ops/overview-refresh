"""Scope mapping (plan §3.2): organization, team and project ids from the org dataset, each
way it can fail, and the package-boundary rule for `forecast.synthetic`."""

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

import forecast.dashboard as dashboard_package
from forecast.dashboard import DashboardScope, InputError, RunScope, map_scopes

ORG = RunScope("organization", "fixture.dev")
ALPHA = RunScope("team", "Alpha")
BETA = RunScope("team", "Beta")
CHECKOUT = RunScope("project", "checkout_flow")
SEARCH = RunScope("project", "search_index")
BILLING = RunScope("project", "billing_sync")
RUN_SCOPES = (ORG, ALPHA, BETA, CHECKOUT, SEARCH, BILLING)


@pytest.fixture
def demo(tmp_path, make_org_demo):
    return make_org_demo(tmp_path / "demo")


def _map(demo, scopes=RUN_SCOPES, org_id="fixture.dev"):
    return map_scopes(demo, scopes, org_id=org_id)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_rows(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


# --- the mapping -------------------------------------------------------------


def test_maps_every_scope_in_run_order(demo):
    mapped = _map(demo)
    assert all(isinstance(m, DashboardScope) for m in mapped)
    assert [(m.run_scope, m.scope_type, m.scope_id, m.name) for m in mapped] == [
        (ORG, "org", "default", "fixture.dev"),
        (ALPHA, "team", "alpha", "Alpha"),
        (BETA, "team", "beta", "Beta"),
        (CHECKOUT, "repo", "github:acme/checkout-flow", "acme/checkout-flow"),
        (SEARCH, "repo", "github:acme/search-index", "acme/search-index"),
        (BILLING, "repo", "github:acme/billing-sync", "acme/billing-sync"),
    ]


def test_order_follows_the_run_not_the_files(demo):
    mapped = _map(demo, (BILLING, ALPHA, ORG))
    assert [m.scope_id for m in mapped] == ["github:acme/billing-sync", "alpha", "default"]


def test_a_team_whose_directory_name_differs_from_the_layer_1_name(demo):
    # The directory spells it `Qa`; Layer 1 says `QA`. The id is the slug of the Layer 1 name.
    (mapped,) = _map(demo, (RunScope("team", "QA"),))
    assert (mapped.scope_type, mapped.scope_id, mapped.name) == ("team", "qa", "QA")


def test_a_team_name_with_spaces_and_underscores_is_slugged(demo):
    (mapped,) = _map(demo, (RunScope("team", "Data Platform_X"),))
    assert (mapped.scope_id, mapped.name) == ("data-platform-x", "Data Platform_X")


def test_the_parent_directory_team_is_not_exported(demo):
    ids = {m.scope_id for m in _map(demo)}
    assert "eng" not in ids


def test_an_empty_budgets_file_is_valid(demo):
    (demo / "org" / "budgets.jsonl").write_text("")
    assert _map(demo)[0].scope_id == "default"


def test_blank_lines_in_jsonl_files_are_ignored(demo):
    path = demo / "org" / "repositories.jsonl"
    path.write_text("\n" + path.read_text() + "\n\n")
    assert len(_map(demo)) == 6


def test_a_repository_label_is_matched_exactly(demo):
    path = demo / "org" / "repositories.jsonl"
    rows = _rows(path)
    for row in rows:
        row["label"] = row["label"].replace("acme/", "Acme/")  # the owner's case differs
    _write_rows(path, rows)
    with pytest.raises(InputError, match="acme/checkout-flow"):
        _map(demo, (CHECKOUT,))


# --- errors ------------------------------------------------------------------


def _mutate_rows(path: Path, change) -> None:
    rows = _rows(path)
    change(rows)
    _write_rows(path, rows)


def _fails(demo, fragment, scopes=RUN_SCOPES, org_id="fixture.dev"):
    with pytest.raises(InputError, match=fragment) as caught:
        _map(demo, scopes, org_id)
    assert caught.value.stage == "scopes"
    return caught.value


def test_a_team_without_a_directory_row(demo):
    error = _fails(demo, "Gamma", (RunScope("team", "Gamma"),))
    assert "'gamma'" in str(error)


def test_a_project_without_a_repo_entry(demo):
    _fails(demo, "orphan_project", (RunScope("project", "orphan_project"),))


def test_a_repo_entry_without_a_repository_row(demo):
    _mutate_rows(demo / "org" / "repositories.jsonl", lambda rows: rows.pop(0))
    _fails(demo, "acme/checkout-flow", (CHECKOUT,))


def test_an_org_dataset_of_another_org(demo):
    _fails(demo, "other.dev", org_id="other.dev")


def test_a_budgets_org_row_that_disagrees(demo):
    _mutate_rows(demo / "org" / "budgets.jsonl", lambda rows: rows[0].update(scope_id="elsewhere"))
    _fails(demo, "elsewhere")


def test_a_directory_team_of_another_org(demo):
    _mutate_rows(demo / "org" / "directory_teams.jsonl", lambda rows: rows[1].update(org_id="elsewhere"))
    _fails(demo, "elsewhere")


def test_two_teams_with_one_slug(demo):
    _fails(demo, "both map to team 'alpha'", (ALPHA, RunScope("team", " ALPHA ")))


def test_two_projects_in_one_repository(demo):
    config = demo / "configs" / "org.yaml"
    config.write_text(config.read_text().replace("name: search-index", "name: checkout-flow"))
    _fails(demo, "both map to repo", (CHECKOUT, SEARCH))


def test_a_repository_label_that_appears_twice(demo):
    path = demo / "org" / "repositories.jsonl"
    rows = _rows(path)
    _write_rows(path, [*rows, {**rows[0], "repo_id": "github:acme/other"}])
    _fails(demo, "appears 2 times", (CHECKOUT,))


def test_a_project_with_two_repo_entries(demo):
    config = demo / "configs" / "org.yaml"
    config.write_text(config.read_text() + "- project: checkout_flow\n  name: checkout-flow-2\n")
    _fails(demo, "several repos entries", (CHECKOUT,))


def test_an_unknown_forecast_scope_type(demo):
    _fails(demo, "scope type", (RunScope("portfolio", "x"),))


@pytest.mark.parametrize("name", ["budgets.jsonl", "directory_teams.jsonl", "repositories.jsonl", "manifest.json"])
def test_a_missing_org_file(demo, name):
    (demo / "org" / name).unlink()
    _fails(demo, name)


def test_a_missing_org_config(demo):
    (demo / "configs" / "org.yaml").unlink()
    _fails(demo, "org.yaml")


@pytest.mark.parametrize("name", ["budgets.jsonl", "directory_teams.jsonl", "repositories.jsonl"])
def test_a_garbage_jsonl_line_names_the_file_and_line(demo, name):
    path = demo / "org" / name
    path.write_text(path.read_text() + "{not json\n")
    error = _fails(demo, name)
    assert "line" in str(error)


def test_a_jsonl_line_that_is_not_an_object(demo):
    path = demo / "org" / "directory_teams.jsonl"
    path.write_text(path.read_text() + "[1, 2]\n")
    _fails(demo, "directory_teams.jsonl line 6 is not a JSON object")


def test_a_non_object_manifest(demo):
    (demo / "org" / "manifest.json").write_text("[]")
    _fails(demo, "manifest.json")


@pytest.mark.parametrize("text", ["[]", "org: {}\nrepos: []\n", "org: {github_owner: acme}\n", "{"])
def test_a_malformed_org_config(demo, text):
    (demo / "configs" / "org.yaml").write_text(text)
    _fails(demo, "org.yaml")


def test_a_repos_entry_without_a_name(demo):
    (demo / "configs" / "org.yaml").write_text("org: {github_owner: acme}\nrepos:\n- project: checkout_flow\n")
    _fails(demo, "without project and name")


def test_a_manifest_without_a_directory_org_id(demo):
    (demo / "org" / "manifest.json").write_text(json.dumps({"org_id": "fixture.dev"}))
    _fails(demo, "directory_org_id")


# --- package boundary ----------------------------------------------------------


def test_only_scopes_imports_from_forecast_synthetic():
    package = Path(dashboard_package.__file__).parent
    found = {}
    for path in sorted(package.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                if name == "forecast.synthetic" or name.startswith("forecast.synthetic."):
                    found.setdefault(path.name, []).append(name)
    assert found == {"scopes.py": ["forecast.synthetic.org.ids"]}


def test_importing_the_package_does_not_load_the_synthetic_org_package():
    code = "import sys, forecast.dashboard; sys.exit(1 if 'forecast.synthetic.org' in sys.modules else 0)"
    root = Path(dashboard_package.__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", code], env={"PYTHONPATH": f"{root}:{root.parents[1] / 'src'}", "PATH": ""},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
