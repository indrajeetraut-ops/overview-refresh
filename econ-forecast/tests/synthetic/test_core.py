from collections import Counter
from pathlib import Path
import hashlib
import json
import yaml
import pytest
from forecast.artifact_io import read_records
from forecast.synthetic.allocation import allocate_integer
from forecast.synthetic.config import load_roster_config,load_series_config,load_users_config
from forecast.synthetic.errors import ConfigError,InputDataError
from forecast.synthetic.models import TeamRecord,UserRecord
from forecast.synthetic.profiles import assign_profiles
from forecast.synthetic.roster import create_roster
from forecast.synthetic.runner import run_roster,run_series,run_users
from forecast.synthetic.users import create_users
from forecast.forecasting.dataset import load_dataset

ROOT=Path(__file__).parents[2]
def test_allocation_exact_and_ties():
    assert allocate_integer(3,{"b":.5,"a":.5})=={"b":1,"a":2}
    assert sum(allocate_integer(10,{"a":.2,"b":.3,"z":0,"c":.5}).values())==10
def test_shipped_configs_parse():
    base=ROOT/"config/synthetic"
    assert load_users_config(base/"users.yaml").users.count==100
    assert len(load_roster_config(base/"roster.yaml").roster.teams)==3
    assert load_series_config(base/"series.yaml").series.days==60
def test_boolean_count_and_duplicate_yaml_rejected(tmp_path):
    p=tmp_path/"x.yaml";p.write_text("schema_version: 1\nusers:\n  count: true\n  org_domain: x.com\n  seed: 1\n")
    with pytest.raises(ConfigError):load_users_config(p)
    p.write_text("schema_version: 1\nschema_version: 1\nusers: {}\n")
    with pytest.raises(ConfigError):load_users_config(p)
def test_users_and_roster_are_deterministic():
    base=ROOT/"config/synthetic";uc=load_users_config(base/"users.yaml");users=create_users(uc)
    assert users==create_users(uc) and len({u.email for u in users})==100
    rc=load_roster_config(base/"roster.yaml");teams=create_roster(rc,users)
    assert [(team.team_name, team.team_id) for team in teams] == [
        (team.team_name, team.team_id) for team in create_roster(rc, users)]
    assert [len(t.user_ids) for t in teams]==[10,15,25]
    assert len({x for t in teams for x in t.user_ids})==50 and any(t.team_name=="team1" for t in teams)
def test_workflow_and_replay(tmp_path):
    base=ROOT/"config/synthetic";users_dir=tmp_path/"users";r1=run_users(load_users_config(base/"users.yaml"),users_dir)
    roster_yaml=(base/"roster.yaml").read_text().replace("../../artifacts/users/users.jsonl",str(users_dir/"users.jsonl"));rp=tmp_path/"roster.yaml";rp.write_text(roster_yaml);roster_dir=tmp_path/"roster";run_roster(load_roster_config(rp),roster_dir)
    sy=(base/"series.yaml").read_text().replace("../../artifacts/users/users.jsonl",str(users_dir/"users.jsonl")).replace("../../artifacts/roster/teams.jsonl",str(roster_dir/"teams.jsonl"));sp=tmp_path/"series.yaml";sp.write_text(sy);series_dir=tmp_path/"series";result=run_series(load_series_config(sp),series_dir)
    replay=tmp_path/"replay";run_series(load_series_config(series_dir/"config.yaml"),replay)
    for name in ("usage.jsonl","coverage.jsonl","user_profiles.jsonl","generation_events.jsonl"):assert (series_dir/name).read_bytes()==(replay/name).read_bytes()
    first_projects = {(scope.name, scope.scope_id) for scope in load_dataset(series_dir).scopes.values()
                      if scope.scope_type == "project"}
    replay_projects = {(scope.name, scope.scope_id) for scope in load_dataset(replay).scopes.values()
                       if scope.scope_type == "project"}
    assert first_projects == replay_projects and len(first_projects) == 5
    # A saved legacy series snapshot has ID-keyed project mixtures; it remains
    # readable by name without accepting IDs in fresh operator configuration.
    saved = yaml.safe_load((replay / "config.yaml").read_text())
    name_to_id = {scope.name: scope.scope_id for scope in load_dataset(replay).scopes.values()
                  if scope.scope_type == "project"}
    legacy_ids = {project["name"]: str(12 + i) for i, project in enumerate(saved["projects"])}
    for project in saved["projects"]:
        project["project_id"] = legacy_ids[project["name"]]
    for team in saved["team_profiles"].values():
        team["project_mix"] = {legacy_ids[name]: share for name, share in team["project_mix"].items()}
    old_to_legacy = {name_to_id[name]: legacy for name, legacy in legacy_ids.items()}
    usage_rows = [json.loads(line) for line in (replay / "usage.jsonl").read_text().splitlines()]
    for row in usage_rows:
        row["project_id"] = old_to_legacy[row["project_id"]]
    usage_payload = b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
                             for row in usage_rows)
    (replay / "usage.jsonl").write_bytes(usage_payload)
    payload = yaml.safe_dump(saved, sort_keys=True).encode()
    (replay / "config.yaml").write_bytes(payload)
    manifest = json.loads((replay / "manifest.json").read_text())
    manifest["config_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest["files"]["usage.jsonl"].update(sha256=hashlib.sha256(usage_payload).hexdigest(),
                                                 bytes=len(usage_payload))
    (replay / "manifest.json").write_text(json.dumps(manifest))
    assert {(scope.name, scope.scope_id) for scope in load_dataset(replay).scopes.values()
            if scope.scope_type == "project"} == set(legacy_ids.items())
    assert result.summary_counts["coverage_rows"]==50*60*2
    assert (series_dir/"report.md").exists()
    with pytest.raises(Exception):run_users(load_users_config(base/"users.yaml"),users_dir)
