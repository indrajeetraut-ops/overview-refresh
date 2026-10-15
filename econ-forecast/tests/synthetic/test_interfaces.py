import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


ROOT=Path(__file__).parents[3]
PYTHON=ROOT/"venv/bin/python"
ENV={**os.environ,"PYTHONPATH":f"{ROOT/'econ-forecast/src'}:{ROOT/'src'}"}


def cli(*args,cwd=None):
    return subprocess.run([str(PYTHON),"-m","forecast.synthetic.cli_synt_gen",*map(str,args)],
                          cwd=cwd or ROOT,env=ENV,text=True,capture_output=True)


def write_yaml(path,data):
    path.write_text(yaml.safe_dump(data,sort_keys=False),encoding="utf-8")


def test_cli_help_and_invalid_arguments_do_no_io(tmp_path):
    help_result=cli("--help",cwd=tmp_path)
    assert help_result.returncode==0 and "user-create" in help_result.stdout
    assert list(tmp_path.iterdir())==[]
    missing=cli("--op","user-create",cwd=tmp_path)
    unknown=cli("--op","unknown",cwd=tmp_path)
    assert missing.returncode==2 and unknown.returncode==2


def test_validate_only_emits_one_summary_and_writes_nothing(tmp_path):
    cfg=tmp_path/"users.yaml"
    write_yaml(cfg,{"schema_version":1,"users":{"count":2,"org_domain":"example.test",
                                                  "locale":"en_US","seed":1}})
    output=tmp_path/"never-created"
    result=cli("--op","user-create","--config",cfg,"--output",output,"--validate-only")
    summary=json.loads(result.stdout)
    assert result.returncode==0 and summary["status"]=="valid"
    assert summary["files"]==[] and summary["report_path"] is None
    assert not output.exists()
    assert all(json.loads(line) for line in result.stderr.splitlines())


def test_reports_and_moved_series_replay(tmp_path):
    users_cfg=tmp_path/"users.yaml"
    write_yaml(users_cfg,{"schema_version":1,"users":{"count":3,"org_domain":"example.test",
                                                        "locale":"en_US","seed":1}})
    users_out=tmp_path/"users"
    assert cli("--op","user-create","--config",users_cfg,"--output",users_out).returncode==0
    roster_cfg=tmp_path/"roster.yaml"
    write_yaml(roster_cfg,{"schema_version":1,"roster":{"users_file":str(users_out/"users.jsonl"),
        "seed":2,"teams":[{"team_name":"team","count":3}]}})
    roster_out=tmp_path/"roster"
    assert cli("--op","roster-create","--config",roster_cfg,"--output",roster_out).returncode==0
    series_cfg=tmp_path/"series.yaml"
    write_yaml(series_cfg,{"schema_version":3,"series":{"users_file":str(users_out/"users.jsonl"),
        "roster_file":str(roster_out/"teams.jsonl"),"start_date":"2026-07-01","days":2,
        "timezone":"UTC","profile_seed":3,"usage_seed":4},
        "defaults":{"daily_variation_pct":0,"output_ratio":.03,"cache_hit_ratio":.8,
                    "cache_hit_variation_pp":0, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97,"mix_variation_pct":0,
                    "daily_zero_probability":0,"weekend_reduction_probability":0,
                    "weekend_reduction_factor":1},
        "profiles":{"p":{"daily_input_tokens":1000}},
        "team_profiles":{"team":{"profile_mix":{"p":1},"project_mix":{"X":1}}},
        "projects":[{"name":"X"}],
        "agents":{"agent":{"billing_mode":"api","usage_share":1,"models":{"model":1}}},
        "events":{"spike_rules":[],"zero_periods":[]}})
    series_out=tmp_path/"series"
    completed=cli("--op","series-create","--config",series_cfg,"--output",series_out)
    assert completed.returncode==0,completed.stderr
    summary=json.loads(completed.stdout)
    assert summary["report_path"]==str((series_out/"report.md").resolve())
    for directory,headings in ((users_out,["User creation"]),(roster_out,["Team roster","| team | 3 |"]),(series_out,["Series","Token breakdowns"])):
        report=(directory/"report.md").read_text()
        assert all(text in report for text in headings)
    moved=tmp_path/"moved";shutil.move(series_out,moved)
    replay=tmp_path/"replay"
    rerun=cli("--op","series-create","--config",moved/"config.yaml","--output",replay,cwd=tmp_path)
    assert rerun.returncode==0,rerun.stderr
    for name in ("usage.jsonl","coverage.jsonl","user_profiles.jsonl","generation_events.jsonl"):
        assert (moved/name).read_bytes()==(replay/name).read_bytes()
