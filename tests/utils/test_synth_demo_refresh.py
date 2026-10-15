"""utils/synth_demo_refresh.sh: the generate -> forecast -> (load) -> (dump) chain.

Docker-free and database-free. The script finds its wrappers relative to its own location, so each
test copies it into a throwaway tree whose four wrappers are stubs. A stub appends its arguments
to a log and creates the files the real wrapper would; a stub env variable makes one of them fail.
"""
import datetime
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REAL_SCRIPT = Path(__file__).resolve().parents[2] / "utils" / "synth_demo_refresh.sh"
SHELL = "/bin/bash" if os.path.exists("/bin/bash") else "bash"  # macOS ships bash 3.2: the compat target
DSN = "postgresql+psycopg://postgres@localhost:5432/t8_load_synth"
OWNED = ("configs", "pricing", "org", "roster", "series", "users", "forecast")

FORECAST_LINE = json.dumps({
    "status": "complete", "exit_code": 0, "bytes": 123, "org_id": "default",
    "origin": "2026-09-30", "data_through": "2026-10-09", "scope_count": 10,
    "preset_count": 7, "generated_at": "2026-10-09T01:00:00Z"})

GENERATE_STUB = """#!/usr/bin/env bash
echo "generate $*" >> "$STUB_LOG"
out=""
while [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done
[ -e "$out" ] && { echo "output path already exists" >&2; exit 1; }
if [ "${STUB_FAIL:-}" = generate ]; then
  mkdir -p "$out/configs"                      # a partial output, as a crashed generator leaves
  [ -n "${STUB_STRAY:-}" ] && echo x > "$out/stray.txt"
  exit 7
fi
mkdir -p "$out"
[ -n "${STUB_SLEEP_GENERATE:-}" ] && sleep "$STUB_SLEEP_GENERATE"
for d in configs pricing org roster series users; do mkdir -p "$out/$d"; done
[ -z "${STUB_NO_MANIFEST:-}" ] && echo '{}' > "$out/org/manifest.json"
echo '{"status":"complete"}'
"""

FORECAST_STUB = """#!/usr/bin/env bash
echo "forecast $*" >> "$STUB_LOG"
echo "stdin=[$(cat)]" >> "$STUB_LOG"
[ "${STUB_FAIL:-}" = forecast ] && exit 7
org=""
while [ $# -gt 0 ]; do [ "$1" = --org-demo ] && org="$2"; shift; done
[ -n "${STUB_SLEEP_FORECAST:-}" ] && sleep "$STUB_SLEEP_FORECAST"
mkdir -p "$org/forecast"
[ -z "${STUB_NO_EXPORT:-}" ] && echo '{"stub": true}' > "$org/forecast/forecast_export.json"
echo "${STUB_FORECAST_LINE}"
"""

LOAD_STUB = """#!/usr/bin/env bash
echo "load $*" >> "$STUB_LOG"
echo "pythonpath=$PYTHONPATH" >> "$STUB_LOG"
echo "path=$PATH" >> "$STUB_LOG"
[ "${STUB_FAIL:-}" = load ] && exit 7
echo '{"status":"complete"}'
"""

DUMP_STUB = """#!/usr/bin/env bash
echo "dump $*" >> "$STUB_LOG"
echo "dump_out=${OUT:-}" >> "$STUB_LOG"
[ "${STUB_FAIL:-}" = dump ] && exit 7
if [ -z "${STUB_NO_DUMP:-}" ]; then
  mkdir -p "$(dirname "${OUT:-$PWD/dump.sql.gz}")"
  echo dump > "${OUT:-$PWD/dump.sql.gz}"
fi
"""


def _write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _make_config(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name in ("users.yaml", "roster.yaml", "series.yaml", "org.yaml"):
        (path / name).write_text("{}\n")


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "repo"
    _write_exec(root / "utils" / "synth_demo_refresh.sh", REAL_SCRIPT.read_text())
    _write_exec(root / "econ-forecast" / "utils" / "synth_org.sh", GENERATE_STUB)
    _write_exec(root / "econ-forecast" / "utils" / "dashboard_forecast.sh", FORECAST_STUB)
    _write_exec(root / "utils" / "load_synthetic.sh", LOAD_STUB)
    _write_exec(root / "utils" / "synth_dump.sh", DUMP_STUB)
    _make_config(root / "econ-forecast" / "config" / "synthetic" / "org")
    _write_exec(tmp_path / "bin" / "docker", "#!/usr/bin/env bash\nexit 0\n")  # `--dump` checks docker exists
    return root


class Run:
    def __init__(self, root: Path, tmp_path: Path):
        self.root = root
        self.tmp = tmp_path
        self.log = tmp_path / "stub.log"
        self.out = tmp_path / "org-demo"

    def lines(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def calls(self) -> list[str]:
        """Stage names in the order the stubs ran."""
        return [l.split()[0] for l in self.lines() if l.split()[0] in ("generate", "forecast", "load", "dump")]

    def call(self, name: str) -> str:
        return next(l for l in self.lines() if l.startswith(name + " "))

    def run(self, *args, env=None, cwd=None, path=None, input=None):
        e = dict(os.environ)
        e.update({"STUB_LOG": str(self.log), "STUB_FORECAST_LINE": FORECAST_LINE,
                  "PATH": path or f"{self.tmp / 'bin'}:{e['PATH']}"})
        e.pop("OUT", None)
        e.pop("STUB_FAIL", None)
        e.update(env or {})
        proc = subprocess.run([SHELL, str(self.root / "utils" / "synth_demo_refresh.sh"), *map(str, args)],
                              capture_output=True, text=True, env=e, cwd=str(cwd or self.tmp), input=input)
        self.proc = proc
        lines = proc.stdout.strip().splitlines()
        self.json = json.loads(lines[-1]) if lines else None
        return proc


@pytest.fixture
def run(tree, tmp_path):
    return Run(tree, tmp_path)


def _snapshot(path: Path) -> dict:
    return {str(p.relative_to(path)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(path.rglob("*"))}


def _populate_owned(path: Path, extra: str | None = None) -> None:
    for name in OWNED:
        (path / name).mkdir(parents=True)
        (path / name / "old.txt").write_text("old")
    if extra:
        (path / extra).write_text("mine")


# --- the script itself ------------------------------------------------------------------------------

def test_script_is_valid_bash():
    assert subprocess.run([SHELL, "-n", str(REAL_SCRIPT)], capture_output=True).returncode == 0
    assert subprocess.run(["bash", "-n", str(REAL_SCRIPT)], capture_output=True).returncode == 0
    assert os.access(REAL_SCRIPT, os.X_OK)


def test_help_exits_zero_and_lists_the_options(run):
    proc = run.run("--help")
    assert proc.returncode == 0
    for flag in ("--output", "--end-date", "--config-dir", "--presets", "--force", "--dsn", "--dump", "--dry-run"):
        assert flag in proc.stderr
    assert run.calls() == []


# --- usage and validation errors: exit 2, nothing run -----------------------------------------------

@pytest.mark.parametrize("args", [
    ["--bogus"],
    ["--output"],
    ["--dsn"],
    ["--end-date", "yesterday"],
    ["--end-date", "2026-13-01"],
    ["--end-date", "2026-1-5"],
    ["--end-date", "2026-02-30"],
    ["--end-date", "2026-04-31"],
    ["--end-date", "2025-02-29"],
    ["--end-date", "2026-00-10"],
    ["--output", ""],
    ["--dsn", ""],
    ["--config-dir", ""],
    ["--presets", ""],
    ["--end-date", ""],
    ["--config-dir", "does-not-exist"],
    ["--presets", "does-not-exist.yaml"],
])
def test_usage_errors_exit_2_before_any_stage(run, args):
    proc = run.run(*args)
    assert proc.returncode == 2
    assert run.json["status"] == "invalid" and run.json["exit_code"] == 2 and run.json["error"]
    assert run.calls() == []


@pytest.mark.parametrize("dsn", [
    "postgresql+psycopg://postgres@localhost:5432/econ_synth_test",
    "postgresql+psycopg://postgres@localhost:5432/econ",
    "postgresql+psycopg://postgres@localhost:5432/econ_synth/",
    "postgresql+psycopg://postgres@localhost:5432/",
    "postgresql+psycopg://postgres@localhost:5432/econ?application_name=x_synth",
    "postgresql+psycopg://postgres@localhost:5432/econsynth",
    "postgresql+psycopg://postgres@localhost:5432/_synth",
])
def test_dsn_whose_database_is_not_synth_exits_2_before_any_stage(run, dsn):
    proc = run.run("--output", run.out, "--dsn", dsn)
    assert proc.returncode == 2
    assert "_synth" in run.json["error"]
    assert run.calls() == [] and not run.out.exists()


def test_dsn_with_query_string_is_accepted(run):
    dsn = "postgresql+psycopg://u:pw@h:5432/t8_load_synth?sslmode=require"
    assert run.run("--output", run.out, "--dsn", dsn, "--end-date", "2026-10-09").returncode == 0
    assert "load" in run.calls()


def test_there_is_no_default_database(run):
    assert run.run("--output", run.out, "--end-date", "2026-10-09").returncode == 0
    assert "load" not in run.calls()
    assert not any("econ_synth" in l for l in run.lines())


def test_dump_without_docker_exits_2_before_any_stage(run, tmp_path):
    safe = tmp_path / "nodocker"
    safe.mkdir()
    for tool in ("sed", "date", "find", "ls", "mktemp", "tail", "cat", "rm", "rmdir", "python3", "dirname", "sort", "bash", "env"):
        found = shutil.which(tool)
        if found:
            (safe / tool).symlink_to(found)
    proc = run.run("--output", run.out, "--dump", path=str(safe))
    assert proc.returncode == 2
    assert "docker" in run.json["error"]
    assert run.calls() == [] and not run.out.exists()


# --- the output directory ----------------------------------------------------------------------------

def test_non_empty_output_without_force_is_refused_and_untouched(run):
    _populate_owned(run.out, extra="notes.txt")
    before = _snapshot(run.out)
    proc = run.run("--output", run.out)
    assert proc.returncode == 2
    assert "--force" in run.json["error"]
    assert run.calls() == []
    assert _snapshot(run.out) == before


def test_force_removes_exactly_the_owned_entries_and_regenerates(run):
    _populate_owned(run.out)
    proc = run.run("--output", run.out, "--force", "--end-date", "2026-10-09")
    assert proc.returncode == 0, proc.stderr
    assert run.json["status"] == "complete"
    assert run.calls() == ["generate", "forecast"]
    # The old markers are gone: the directory was recreated by the (stub) generator.
    assert not list(run.out.rglob("old.txt"))
    assert (run.out / "forecast" / "forecast_export.json").is_file()


def test_force_refuses_a_directory_holding_anything_else(run):
    _populate_owned(run.out, extra="notes.txt")
    before = _snapshot(run.out)
    proc = run.run("--output", run.out, "--force")
    assert proc.returncode == 2
    assert "notes.txt" in run.json["error"]
    assert run.calls() == []
    assert _snapshot(run.out) == before


def test_force_refuses_a_hidden_extra_entry(run):
    _populate_owned(run.out, extra=".keep")
    assert run.run("--output", run.out, "--force").returncode == 2
    assert (run.out / ".keep").exists() and (run.out / "org" / "old.txt").exists()


def test_force_with_only_some_owned_entries_is_allowed(run):
    (run.out / "series").mkdir(parents=True)
    (run.out / "series" / "old.txt").write_text("old")
    assert run.run("--output", run.out, "--force").returncode == 0
    assert not (run.out / "series" / "old.txt").exists()


def test_empty_existing_output_directory_needs_no_force(run):
    run.out.mkdir()
    proc = run.run("--output", run.out, "--end-date", "2026-10-09")
    assert proc.returncode == 0, proc.stderr
    assert run.calls() == ["generate", "forecast"]


def test_output_that_is_a_file_or_a_symlink_is_refused(run, tmp_path):
    run.out.write_text("a file")
    assert run.run("--output", run.out, "--force").returncode == 2
    assert run.out.read_text() == "a file"
    run.out.unlink()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    run.out.symlink_to(target)
    assert run.run("--output", run.out, "--force").returncode == 2
    assert (target / "keep.txt").read_text() == "keep"
    assert run.calls() == []


def test_force_does_not_follow_a_symlinked_entry(run, tmp_path):
    other = tmp_path / "precious"
    other.mkdir()
    (other / "keep.txt").write_text("keep")
    run.out.mkdir()
    (run.out / "users").symlink_to(other)
    assert run.run("--output", run.out, "--force").returncode == 0
    assert (other / "keep.txt").read_text() == "keep"


def test_relative_output_is_relative_to_the_callers_directory(run, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    proc = run.run("--output", "rel-demo", "--end-date", "2026-10-09", cwd=work)
    assert proc.returncode == 0, proc.stderr
    assert (work / "rel-demo" / "forecast" / "forecast_export.json").is_file()
    assert str(work / "rel-demo") in run.call("generate")
    assert run.json["output"] == str(work / "rel-demo")


# --- dry run -----------------------------------------------------------------------------------------

def test_dry_run_prints_every_stage_with_its_command_and_changes_nothing(run, tmp_path):
    dsn = "postgresql+psycopg://postgres:s3cret@localhost:5432/t8_load_synth"
    proc = run.run("--output", run.out, "--end-date", "2026-10-09", "--dsn", dsn, "--dump", "--dry-run",
                   env={"OUT": str(tmp_path / "d.sql.gz")})
    assert proc.returncode == 0, proc.stderr
    for n, name in enumerate(("generate", "forecast", "load", "dump"), 1):
        assert f"[synth-refresh] stage {n}/4 {name}" in proc.stderr
    assert "synth_org.sh --config-dir" in proc.stderr and "--end-date 2026-10-09" in proc.stderr
    assert "dashboard_forecast.sh --org-demo" in proc.stderr
    assert "load_synthetic.sh --dataset" in proc.stderr and "--replace" in proc.stderr
    assert "synth_dump.sh --dataset" in proc.stderr
    assert str(tmp_path / "d.sql.gz") in proc.stderr
    assert "s3cret" not in proc.stderr and "s3cret" not in proc.stdout
    assert run.json["status"] == "dry_run"
    assert [s["name"] for s in run.json["stages"]] == ["generate", "forecast", "load", "dump"]
    assert all(s["status"] == "planned" and s["command"] for s in run.json["stages"])
    assert run.calls() == []
    assert not run.out.exists() and not (tmp_path / "d.sql.gz").exists()


def test_dry_run_without_dsn_or_dump_lists_two_stages(run):
    assert run.run("--output", run.out, "--dry-run").returncode == 0
    assert [s["name"] for s in run.json["stages"]] == ["generate", "forecast"]
    assert "stage 2/2 forecast" in run.proc.stderr


def test_dry_run_still_validates_and_refuses_a_non_empty_output(run):
    _populate_owned(run.out)
    before = _snapshot(run.out)
    assert run.run("--output", run.out, "--dry-run").returncode == 2
    assert _snapshot(run.out) == before


def test_dry_run_with_force_does_not_remove_anything(run):
    _populate_owned(run.out)
    before = _snapshot(run.out)
    assert run.run("--output", run.out, "--force", "--dry-run").returncode == 0
    assert _snapshot(run.out) == before and run.calls() == []


def test_dry_run_default_dump_path_is_under_the_repo_seed_directory(run, tree):
    assert run.run("--output", run.out, "--dump", "--dry-run").returncode == 0
    assert run.json["dump_path"] == str(tree / "deploy" / "seed" / "econ_synth.sql.gz")


# --- the stages ------------------------------------------------------------------------------------------

def test_stage_order_and_arguments(run, tree, tmp_path):
    cfg = tmp_path / "cfg"
    _make_config(cfg)
    presets = tmp_path / "presets.yaml"
    presets.write_text("presets: []\n")
    out_file = tmp_path / "t8.sql.gz"
    proc = run.run("--output", run.out, "--end-date", "2026-10-09", "--config-dir", cfg, "--presets", presets,
                   "--dsn", DSN, "--dump", env={"OUT": str(out_file)})
    assert proc.returncode == 0, proc.stderr
    assert run.calls() == ["generate", "forecast", "load", "dump"]
    assert run.call("generate") == f"generate --config-dir {cfg} --output {run.out} --end-date 2026-10-09"
    assert run.call("forecast") == f"forecast --org-demo {run.out} --presets {presets}"
    assert run.call("load") == (f"load --dataset {run.out}/org --dsn {DSN} --replace "
                                f"--forecast-export {run.out}/forecast/forecast_export.json")
    assert run.call("dump") == f"dump --dataset {run.out}/org"
    assert "dump_out=" + str(out_file) in run.lines()
    for n, name in enumerate(("generate", "forecast", "load", "dump"), 1):
        assert f"[synth-refresh] stage {n}/4 {name} ..." in proc.stderr


def test_presets_flag_is_not_passed_when_unset_and_defaults_are_used(run, tree):
    assert run.run("--output", run.out).returncode == 0
    assert run.call("forecast") == f"forecast --org-demo {run.out}"
    expected_cfg = tree / "econ-forecast" / "config" / "synthetic" / "org"
    assert f"--config-dir {expected_cfg} " in run.call("generate")


def test_end_date_today_is_resolved_once_to_a_date(run):
    before = datetime.date.today()
    assert run.run("--output", run.out).returncode == 0
    after = datetime.date.today()
    arg = run.call("generate").rsplit(" ", 1)[1]
    assert arg in (before.isoformat(), after.isoformat())
    assert run.json["end_date"] == arg


def test_load_stage_only_with_dsn_and_dump_stage_only_with_dump(run, tmp_path):
    assert run.run("--output", run.out).returncode == 0
    assert run.calls() == ["generate", "forecast"]
    assert "stage 2/2 forecast" in run.proc.stderr

    shutil.rmtree(run.out)
    run.log.unlink()
    assert run.run("--output", run.out, "--dsn", DSN).returncode == 0
    assert run.calls() == ["generate", "forecast", "load"]

    shutil.rmtree(run.out)
    run.log.unlink()
    assert run.run("--output", run.out, "--dump", env={"OUT": str(tmp_path / "o.gz")}).returncode == 0
    assert run.calls() == ["generate", "forecast", "dump"]
    assert "load" not in run.calls()


def test_the_loader_gets_the_repo_python_environment(run, tree):
    (tree / "venv" / "bin").mkdir(parents=True)
    assert run.run("--output", run.out, "--dsn", DSN).returncode == 0
    path = next(l for l in run.lines() if l.startswith("path="))
    pythonpath = next(l for l in run.lines() if l.startswith("pythonpath="))
    assert path.startswith(f"path={tree}/venv/bin:")
    assert f"{tree}/src" in pythonpath and f"{tree}/econ-forecast/src" in pythonpath


def test_neither_dsn_nor_dump_prints_how_to_continue(run):
    proc = run.run("--output", run.out)
    assert proc.returncode == 0
    assert "load_synthetic.sh --dataset" in proc.stderr and "synth_dump.sh --dataset" in proc.stderr
    assert "econ_synth" not in proc.stderr


def test_dump_hint_says_the_dump_must_be_reloaded_with_replace(run, tmp_path):
    proc = run.run("--output", run.out, "--dump", env={"OUT": str(tmp_path / "o.gz")})
    assert "aws_synth_load.sh --replace" in proc.stderr
    assert "forecast_runs" in proc.stderr and "forecast_scope_series" in proc.stderr


# --- failures --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stage, after", [
    ("generate", ["generate"]),
    ("forecast", ["generate", "forecast"]),
    ("load", ["generate", "forecast", "load"]),
    ("dump", ["generate", "forecast", "load", "dump"]),
])
def test_a_failing_stage_stops_the_run(run, tmp_path, stage, after):
    proc = run.run("--output", run.out, "--dsn", DSN, "--dump", env={"STUB_FAIL": stage, "OUT": str(tmp_path / "o.gz")})
    assert proc.returncode == 1
    assert run.calls() == after
    assert run.json["status"] == "failed"
    assert run.json["failed_stage"] == stage
    assert run.json["exit_code"] == 7
    assert [s["name"] for s in run.json["stages"]] == after
    assert run.json["stages"][-1]["status"] == "failed"
    assert all(s["status"] == "ok" for s in run.json["stages"][:-1])
    assert f"stage {stage} failed" in proc.stderr
    if stage != "generate":
        assert (run.out / "org").is_dir()  # completed outputs stay in place
    else:
        assert not run.out.exists()  # the generator's partial output is removed


def test_a_forecast_line_that_is_not_json_fails_the_forecast_stage(run):
    proc = run.run("--output", run.out, env={"STUB_FORECAST_LINE": "not json"})
    assert proc.returncode == 1
    assert run.json["failed_stage"] == "forecast"


def test_a_forecast_that_did_not_complete_fails_the_forecast_stage(run):
    line = json.dumps({**json.loads(FORECAST_LINE), "status": "failed"})  # otherwise complete-looking
    proc = run.run("--output", run.out, env={"STUB_FORECAST_LINE": line})
    assert proc.returncode == 1 and run.json["failed_stage"] == "forecast"


# --- the closing JSON ------------------------------------------------------------------------------------

def test_last_stdout_line_is_one_json_object_with_the_documented_keys(run, tmp_path):
    out_file = tmp_path / "o.gz"
    proc = run.run("--output", run.out, "--end-date", "2026-10-09", "--dsn", DSN, "--dump", env={"OUT": str(out_file)})
    assert proc.returncode == 0
    last = proc.stdout.strip().splitlines()[-1]
    doc = json.loads(last)
    assert doc["status"] == "complete"
    assert doc["output"] == str(run.out) and doc["end_date"] == "2026-10-09"
    assert doc["origin"] == "2026-09-30" and doc["data_through"] == "2026-10-09"
    assert doc["scope_count"] == 10 and doc["preset_count"] == 7
    assert doc["export_path"] == str(run.out / "forecast" / "forecast_export.json")
    assert doc["dump_path"] == str(out_file)
    assert [s["name"] for s in doc["stages"]] == ["generate", "forecast", "load", "dump"]
    for s in doc["stages"]:
        assert s["status"] == "ok" and isinstance(s["seconds"], int) and s["seconds"] >= 0
    assert proc.stdout.strip().count("\n") == 0 or all(not l.startswith("{") for l in proc.stdout.strip().splitlines()[:-1])


def test_dump_path_is_null_without_dump(run):
    assert run.run("--output", run.out).returncode == 0
    assert run.json["dump_path"] is None and run.json["status"] == "complete"


def test_stage_stdout_is_forwarded_to_stderr_not_stdout(run):
    proc = run.run("--output", run.out)
    assert '{"status":"complete"}' in proc.stderr
    assert proc.stdout.strip().count("\n") == 0  # only the closing JSON line


# --- review fixes: dates, empty values, config, masking, paths --------------------------------------------

def test_a_leap_day_is_a_real_date(run):
    assert run.run("--output", run.out, "--end-date", "2024-02-29", "--dry-run").returncode == 0
    assert run.run("--output", run.out, "--end-date", "2000-02-29", "--dry-run").returncode == 0
    assert run.run("--output", run.out, "--end-date", "1900-02-29", "--dry-run").returncode == 2


@pytest.mark.parametrize("missing", ["users.yaml", "roster.yaml", "series.yaml", "org.yaml"])
def test_a_config_dir_without_one_of_the_four_yamls_exits_2(run, tmp_path, missing):
    cfg = tmp_path / "cfg"
    _make_config(cfg)
    (cfg / missing).unlink()
    assert run.run("--output", run.out, "--config-dir", cfg).returncode == 2
    assert missing in run.json["error"]
    assert run.calls() == [] and not run.out.exists()


def test_the_default_config_dir_is_checked_too(run, tree):
    (tree / "econ-forecast" / "config" / "synthetic" / "org" / "org.yaml").unlink()
    assert run.run("--output", run.out).returncode == 2
    assert run.calls() == []


@pytest.mark.parametrize("password_dsn", [
    "postgresql+psycopg://postgres:p@ss w0rd@localhost:5432/t8_load_synth",
    "postgresql+psycopg://postgres:s3cr3t@localhost:5432/t8_load_synth?password=qq77&sslpassword=zz88",
    "postgresql+psycopg://postgres@localhost:5432/t8_load_synth?sslpassword=zz88&password=qq77",
])
def test_the_dsn_password_is_masked_in_every_form(run, password_dsn):
    proc = run.run("--output", run.out, "--dsn", password_dsn, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    shown = proc.stderr + proc.stdout
    for secret in ("p@ss", "w0rd", "s3cr3t", "qq77", "zz88"):
        assert secret not in shown
    assert "t8_load_synth" in shown and "REDACTED" in shown


def test_the_real_run_does_not_print_the_password_either(run):
    dsn = "postgresql+psycopg://postgres:s3cr3t@localhost:5432/t8_load_synth"
    proc = run.run("--output", run.out, "--dsn", dsn)
    assert proc.returncode == 0
    assert "s3cr3t" not in proc.stdout + proc.stderr.replace(f"--dsn {dsn}", "")
    assert f"--dsn {dsn}" in run.call("load")  # the stub got the real DSN


def test_dot_components_in_the_output_path_are_normalised(run, tmp_path):
    out = run.out
    proc = run.run("--output", f"{tmp_path}/side/../org-demo", "--end-date", "2026-10-09")
    assert proc.returncode == 0, proc.stderr
    assert run.json["output"] == str(out) and f"--output {out} " in run.call("generate")

    proc = run.run("--output", f"{out}/.", "--force", "--end-date", "2026-10-09")
    assert proc.returncode == 0, proc.stderr
    assert run.json["output"] == str(out)
    proc = run.run("--output", f"{tmp_path}/./org-demo/../org-demo/", "--force", "--dry-run")
    assert proc.returncode == 0 and run.json["output"] == str(out)


def test_output_with_quote_backslash_and_newline_round_trips_through_the_json(run, tmp_path):
    weird = tmp_path / 'we"ird\\na\nme'
    proc = run.run("--output", weird, "--end-date", "2026-10-09")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout.strip().splitlines()[-1])
    assert doc["output"] == str(weird)
    assert doc["export_path"] == str(weird / "forecast" / "forecast_export.json")
    assert (weird / "forecast" / "forecast_export.json").is_file()


# --- review fixes: more symlink, docker, relative OUT, cleanup, checks ---------------------------------

def test_a_symlinked_output_is_refused_whatever_it_points_at(run, tmp_path):
    empty = tmp_path / "empty-target"
    empty.mkdir()
    run.out.symlink_to(empty)
    assert run.run("--output", run.out, "--force").returncode == 2
    assert empty.exists() and not list(empty.iterdir()) and run.out.is_symlink()
    run.out.unlink()
    owned = tmp_path / "owned-target"
    _populate_owned(owned)
    before = _snapshot(owned)
    run.out.symlink_to(owned)
    assert run.run("--output", run.out, "--force").returncode == 2
    assert _snapshot(owned) == before and run.calls() == []


def _path_without_docker(tmp_path: Path) -> str:
    safe = tmp_path / "nodocker"
    safe.mkdir(exist_ok=True)
    for tool in ("sed", "date", "find", "ls", "mktemp", "tail", "cat", "rm", "rmdir", "mv", "tee",
                 "kill", "sleep", "python3", "dirname", "sort", "bash", "env"):
        found = shutil.which(tool)
        if found and not (safe / tool).exists():
            (safe / tool).symlink_to(found)
    return str(safe)


def test_dump_dry_run_without_docker_only_notes_it(run, tmp_path):
    proc = run.run("--output", run.out, "--dump", "--dry-run", path=_path_without_docker(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "docker is not installed" in proc.stderr
    assert run.json["status"] == "dry_run" and [s["name"] for s in run.json["stages"]][-1] == "dump"
    assert run.calls() == []


def test_a_relative_out_is_relative_to_the_repo_root(run, tree):
    proc = run.run("--output", run.out, "--dump", env={"OUT": "rel/dump.sql.gz"}, cwd=run.tmp)
    assert proc.returncode == 0, proc.stderr
    assert run.json["dump_path"] == str(tree / "rel" / "dump.sql.gz")
    assert (tree / "rel" / "dump.sql.gz").is_file()
    assert f"will be written to {tree}/rel/dump.sql.gz" in proc.stderr


def test_the_temporary_stage_file_is_removed_on_success_and_on_failure(run, tmp_path):
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    assert run.run("--output", run.out, env={"TMPDIR": str(scratch)}).returncode == 0
    assert not list(scratch.iterdir())
    shutil.rmtree(run.out)
    assert run.run("--output", run.out, env={"TMPDIR": str(scratch), "STUB_FAIL": "forecast"}).returncode == 1
    assert not list(scratch.iterdir())
    assert run.run("--output", run.out, "--end-date", "bad", env={"TMPDIR": str(scratch)}).returncode == 2
    assert not list(scratch.iterdir())


def test_forecast_success_without_an_export_file_fails_the_forecast_stage(run):
    proc = run.run("--output", run.out, env={"STUB_NO_EXPORT": "1"})
    assert proc.returncode == 1
    assert run.json["failed_stage"] == "forecast"
    assert "is missing" in proc.stderr
    assert run.json["stages"][-1] == {"name": "forecast", "status": "failed", "seconds": run.json["stages"][-1]["seconds"]}


def test_dump_success_without_a_dump_file_fails_the_dump_stage(run, tmp_path):
    proc = run.run("--output", run.out, "--dump", env={"STUB_NO_DUMP": "1", "OUT": str(tmp_path / "o.gz")})
    assert proc.returncode == 1
    assert run.json["failed_stage"] == "dump" and run.json["dump_path"] is None
    assert "is missing" in proc.stderr


def test_generate_success_without_a_manifest_fails_the_generate_stage(run):
    proc = run.run("--output", run.out, env={"STUB_NO_MANIFEST": "1"})
    assert proc.returncode == 1
    assert run.json["failed_stage"] == "generate"
    assert run.calls() == ["generate"]
    assert not run.out.exists()  # nothing to restore: the partial output is removed


def test_seconds_is_a_real_measurement(run):
    proc = run.run("--output", run.out, env={"STUB_SLEEP_FORECAST": "2"})
    assert proc.returncode == 0
    seconds = {s["name"]: s["seconds"] for s in run.json["stages"]}
    assert seconds["forecast"] >= 1
    assert seconds["generate"] < seconds["forecast"] + 1


def test_stage_output_is_streamed_live(run):
    """The first stage's output reaches stderr while the second stage is still running."""
    proc = subprocess.Popen(
        [SHELL, str(run.root / "utils" / "synth_demo_refresh.sh"), "--output", str(run.out)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "STUB_LOG": str(run.log), "STUB_FORECAST_LINE": FORECAST_LINE,
             "STUB_SLEEP_FORECAST": "3", "PATH": f"{run.tmp / 'bin'}:{os.environ['PATH']}"})
    seen_early = False
    deadline = datetime.datetime.now() + datetime.timedelta(seconds=2.5)
    buf = ""
    import select
    while datetime.datetime.now() < deadline:
        ready, _, _ = select.select([proc.stderr], [], [], 0.2)
        if ready:
            buf += os.read(proc.stderr.fileno(), 65536).decode()
            if '{"status":"complete"}' in buf and "stage 2/2 forecast" in buf:
                seen_early = True
                break
    proc.communicate()
    assert seen_early


# --- review fixes: rename aside, restore, interruption ----------------------------------------------------

def _aside_dirs(run) -> list[Path]:
    return sorted(run.tmp.glob("org-demo.prev-*"))


def test_force_success_deletes_the_aside_copy(run):
    _populate_owned(run.out)
    proc = run.run("--output", run.out, "--force", "--end-date", "2026-10-09")
    assert proc.returncode == 0, proc.stderr
    assert _aside_dirs(run) == []
    assert not list(run.out.rglob("old.txt"))
    assert "aside" in proc.stderr


def test_a_failed_generate_with_force_keeps_the_old_demo(run):
    _populate_owned(run.out)
    before = _snapshot(run.out)
    proc = run.run("--output", run.out, "--force", env={"STUB_FAIL": "generate"})
    assert proc.returncode == 1
    assert run.json["failed_stage"] == "generate"
    assert _snapshot(run.out) == before  # the partial output is gone, the old demo is back
    assert _aside_dirs(run) == []
    assert "the previous demo is back" in proc.stderr
    assert run.calls() == ["generate"]


def test_a_generate_without_manifest_with_force_keeps_the_old_demo(run):
    _populate_owned(run.out)
    before = _snapshot(run.out)
    assert run.run("--output", run.out, "--force", env={"STUB_NO_MANIFEST": "1"}).returncode == 1
    assert _snapshot(run.out) == before and _aside_dirs(run) == []


def test_an_empty_existing_directory_is_restored_after_a_failed_generate(run):
    run.out.mkdir()
    assert run.run("--output", run.out, env={"STUB_FAIL": "generate"}).returncode == 1
    assert run.out.is_dir() and not list(run.out.iterdir()) and _aside_dirs(run) == []


def test_a_failure_after_generate_keeps_the_new_demo_and_deletes_the_old(run):
    _populate_owned(run.out)
    proc = run.run("--output", run.out, "--force", env={"STUB_FAIL": "forecast"})
    assert proc.returncode == 1 and run.json["failed_stage"] == "forecast"
    assert (run.out / "org" / "manifest.json").is_file() and not list(run.out.rglob("old.txt"))
    assert _aside_dirs(run) == []


def test_when_the_restore_fails_the_message_says_where_the_old_copy_is(run):
    _populate_owned(run.out)
    proc = run.run("--output", run.out, "--force", env={"STUB_FAIL": "generate", "STUB_STRAY": "1"})
    assert proc.returncode == 1
    aside = _aside_dirs(run)
    assert len(aside) == 1 and (aside[0] / "org" / "old.txt").is_file()
    assert str(aside[0]) in proc.stderr and "kept at" in proc.stderr
    assert (run.out / "stray.txt").exists()  # not ours to delete


def test_dry_run_with_an_existing_output_says_it_would_move_it_aside(run):
    _populate_owned(run.out)
    proc = run.run("--output", run.out, "--force", "--dry-run")
    assert proc.returncode == 0
    assert "would move" in proc.stderr and ".prev-" in proc.stderr
    assert _aside_dirs(run) == []


def _default_signals():
    """The interrupt tests send SIGINT: a pytest started in the background (nohup ... &) has it
    ignored, and a signal ignored on entry cannot be trapped by the script."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)


def _unique_sleep() -> str:
    """A duration no other test or process uses, so `ps` can be searched for exactly this sleep."""
    import secrets
    return f"47.{secrets.randbelow(10**9):09d}"


def _start(run, *args, env=None):
    e = {**os.environ, "STUB_LOG": str(run.log), "STUB_FORECAST_LINE": FORECAST_LINE,
         "PATH": f"{run.tmp / 'bin'}:{os.environ['PATH']}", **(env or {})}
    return subprocess.Popen([SHELL, str(run.root / "utils" / "synth_demo_refresh.sh"), *map(str, args)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e,
                            start_new_session=True, preexec_fn=_default_signals)


def _wait_for_log(run, text, seconds=15):
    import time
    end = time.time() + seconds
    while time.time() < end:
        if any(text in l for l in run.lines()):
            return
        time.sleep(0.1)
    raise AssertionError(f"{text!r} never reached the stub log")


def _leftover_sleepers(marker: str) -> bool:
    """True when a `sleep <marker>` is still alive after a few seconds' grace."""
    import time
    for _ in range(30):
        out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
        if not any(l.strip() == f"sleep {marker}" for l in out.splitlines()):
            return False
        time.sleep(0.1)
    return True


@pytest.mark.parametrize("sig, code", [(15, 143), (2, 130), (1, 129), (3, 131)])
def test_an_interrupt_during_generate_restores_the_old_demo_and_reports_it(run, sig, code):
    import time
    marker = _unique_sleep()
    _populate_owned(run.out)
    before = _snapshot(run.out)
    proc = _start(run, "--output", run.out, "--force", env={"STUB_SLEEP_GENERATE": marker})
    _wait_for_log(run, "generate ")
    time.sleep(0.5)
    started = time.time()
    os.kill(proc.pid, sig)
    out, err = proc.communicate(timeout=20)
    assert time.time() - started < 15  # the stage child was stopped, not waited for
    assert proc.returncode == code, err
    doc = json.loads(out.strip().splitlines()[-1])
    assert doc["status"] == "failed" and doc["failed_stage"] == "generate"
    assert doc["error"] == "interrupted" and doc["exit_code"] == code
    assert _snapshot(run.out) == before and _aside_dirs(run) == []
    assert not _leftover_sleepers(marker)


def test_an_interrupt_during_a_later_stage_keeps_the_new_demo(run):
    import time
    marker = _unique_sleep()
    proc = _start(run, "--output", run.out, env={"STUB_SLEEP_FORECAST": marker})
    _wait_for_log(run, "forecast ")
    time.sleep(0.5)
    os.kill(proc.pid, 15)
    out, err = proc.communicate(timeout=20)
    assert proc.returncode == 143
    doc = json.loads(out.strip().splitlines()[-1])
    assert doc["failed_stage"] == "forecast" and doc["error"] == "interrupted"
    assert [s["name"] for s in doc["stages"]] == ["generate", "forecast"]
    assert (run.out / "org" / "manifest.json").is_file()
    assert not _leftover_sleepers(marker)


def test_the_venv_is_put_on_the_path_after_generate_creates_it(run, tree):
    """A fresh checkout has no venv until the generator wrapper makes one."""
    gen = tree / "econ-forecast" / "utils" / "synth_org.sh"
    gen.write_text(gen.read_text().replace("mkdir -p \"$out\"\n", "mkdir -p \"$out\"\nmkdir -p \"$STUB_ROOT/venv/bin\"\n", 1))
    assert not (tree / "venv").exists()
    proc = run.run("--output", run.out, "--dsn", DSN, env={"STUB_ROOT": str(tree)})
    assert proc.returncode == 0, proc.stderr
    path = next(l for l in run.lines() if l.startswith("path="))
    assert path.startswith(f"path={tree}/venv/bin:")


# --- second review round ------------------------------------------------------------------------------------

FAKE_MV = """#!/usr/bin/env bash
# A `mv` that misbehaves on chosen calls (counted in $FAKE_MV_COUNT), and is the real one otherwise.
n=$(( $(cat "$FAKE_MV_COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$FAKE_MV_COUNT"
if [ "$n" = "${FAKE_MV_SIGNAL_ON:-0}" ]; then
  kill -TERM "$PPID"; sleep 0.3; exit 1         # signalled before the move happens, then fails
fi
if [ "$n" = "${FAKE_MV_FAIL_ON:-0}" ]; then exit 1; fi
exec "$REAL_MV" "$@"
"""


def _fake_mv(run, **env):
    _write_exec(run.tmp / "bin" / "mv", FAKE_MV)
    return {"FAKE_MV_COUNT": str(run.tmp / "mv.count"), "REAL_MV": shutil.which("mv"), **env}


def test_a_signal_before_the_move_never_deletes_the_users_demo(run):
    _populate_owned(run.out)
    before = _snapshot(run.out)
    proc = run.run("--output", run.out, "--force", env=_fake_mv(run, FAKE_MV_SIGNAL_ON="1"))
    assert proc.returncode == 143, proc.stderr
    assert run.json["error"] == "interrupted"
    assert _snapshot(run.out) == before and _aside_dirs(run) == []
    assert run.calls() == []


def test_a_failed_move_aside_changes_nothing(run):
    _populate_owned(run.out)
    before = _snapshot(run.out)
    proc = run.run("--output", run.out, "--force", env=_fake_mv(run, FAKE_MV_FAIL_ON="1"))
    assert proc.returncode == 2 and "could not move" in run.json["error"]
    assert _snapshot(run.out) == before and run.calls() == []


def test_when_moving_the_old_demo_back_fails_the_message_says_where_it_is(run):
    _populate_owned(run.out)
    proc = run.run("--output", run.out, "--force",
                   env=_fake_mv(run, FAKE_MV_FAIL_ON="2", STUB_FAIL="generate"))
    assert proc.returncode == 1
    aside = _aside_dirs(run)
    assert len(aside) == 1 and (aside[0] / "org" / "old.txt").is_file()
    assert "could not move it back" in proc.stderr and str(aside[0]) in proc.stderr


def test_an_existing_aside_name_is_never_overwritten(run, tmp_path):
    """The aside name carries the script's pid; make that exact name exist, then run as that pid."""
    _populate_owned(run.out)
    before = _snapshot(run.out)
    e = {**os.environ, "STUB_LOG": str(run.log), "STUB_FORECAST_LINE": FORECAST_LINE,
         "PATH": f"{run.tmp / 'bin'}:{os.environ['PATH']}"}
    proc = subprocess.run(
        [SHELL, "-c", 'mkdir -p "$1.prev-$$" && echo precious > "$1.prev-$$/f" && shift && exec "$@"',
         "sh", str(run.out), SHELL, str(run.root / "utils" / "synth_demo_refresh.sh"),
         "--output", str(run.out), "--force"],
        capture_output=True, text=True, env=e)
    assert proc.returncode == 2, proc.stderr
    assert "already exists" in json.loads(proc.stdout.strip().splitlines()[-1])["error"]
    assert _snapshot(run.out) == before and run.calls() == []
    (precious,) = _aside_dirs(run)
    assert (precious / "f").read_text().strip() == "precious"


def test_an_owned_name_that_is_a_broken_symlink_is_removed_with_the_old_demo(run):
    run.out.mkdir()
    (run.out / "users").symlink_to(run.tmp / "nowhere")
    (run.out / "org").mkdir()
    proc = run.run("--output", run.out, "--force", "--end-date", "2026-10-09")
    assert proc.returncode == 0, proc.stderr
    assert _aside_dirs(run) == [] and not (run.out / "users").is_symlink()


def test_a_failed_generate_removes_a_partial_output_that_is_a_broken_symlink(run):
    run.out.mkdir()
    (run.out / "users").symlink_to(run.tmp / "nowhere")
    before = _snapshot(run.out)
    assert run.run("--output", run.out, "--force", env={"STUB_FAIL": "generate"}).returncode == 1
    assert (run.out / "users").is_symlink() and sorted(p.name for p in run.out.iterdir()) == ["users"]
    assert before == _snapshot(run.out) and _aside_dirs(run) == []


def test_the_stage_stdin_is_dev_null(run):
    proc = run.run("--output", run.out, input="typed by someone\n")
    assert proc.returncode == 0
    assert "stdin=[]" in run.lines()


def test_config_dir_or_presets_inside_the_output_are_refused_before_any_change(run):
    run.out.mkdir()
    cfg = run.out / "my-config"
    _make_config(cfg)
    presets = run.out / "presets.yaml"
    presets.write_text("presets: []\n")
    before = _snapshot(run.out)
    assert run.run("--output", run.out, "--force", "--config-dir", cfg).returncode == 2
    assert "inside --output" in run.json["error"]
    assert run.run("--output", run.out, "--force", "--presets", presets).returncode == 2
    assert "inside --output" in run.json["error"]
    assert run.run("--output", run.out, "--force", "--config-dir", run.out).returncode == 2
    assert _snapshot(run.out) == before and run.calls() == []


def test_config_dir_inside_the_output_through_a_symlink_is_refused(run, tmp_path):
    run.out.mkdir()
    _make_config(run.out / "cfg")
    link = tmp_path / "cfg-link"
    link.symlink_to(run.out / "cfg")
    assert run.run("--output", run.out, "--force", "--config-dir", link).returncode == 2
    assert (run.out / "cfg" / "users.yaml").exists() and run.calls() == []


def test_config_next_to_the_output_is_fine(run, tmp_path):
    cfg = tmp_path / "org-demo-config"
    _make_config(cfg)
    assert run.run("--output", run.out, "--config-dir", cfg, "--dry-run").returncode == 0


@pytest.mark.parametrize("dsn", [
    "user:s3cr3t@host",
    "s3cr3t",
    "postgresql+psycopg://user:s3cr3t@host:5432/db?x=s3cr3t",
    "postgres://u:s3cr3t@h/db wi th space",
])
def test_a_refused_dsn_never_echoes_a_password(run, dsn):
    proc = run.run("--output", run.out, "--dsn", dsn)
    assert proc.returncode == 2
    assert "s3cr3t" not in proc.stdout + proc.stderr


def test_a_plain_database_name_is_still_shown_in_the_refusal(run):
    run.run("--output", run.out, "--dsn", "postgresql+psycopg://u:pw@h:5432/econ")
    assert "'econ'" in run.json["error"] and "pw" not in run.json["error"].replace("must", "")


def test_an_unknown_argument_that_is_a_dsn_is_masked(run):
    proc = run.run("postgresql+psycopg://u:s3cr3t@h/t8_synth")
    assert proc.returncode == 2
    assert "s3cr3t" not in proc.stdout + proc.stderr


def test_force_works_when_the_output_path_contains_a_newline(run, tmp_path):
    weird = tmp_path / "we\nird"
    _populate_owned(weird)
    proc = run.run("--output", weird, "--force", "--end-date", "2026-10-09")
    assert proc.returncode == 0, proc.stderr
    assert not list(weird.rglob("old.txt")) and (weird / "org" / "manifest.json").is_file()
    assert len(list(tmp_path.glob("we*prev*"))) == 0


def test_force_still_refuses_an_extra_entry_whose_name_has_a_newline(run):
    _populate_owned(run.out)
    (run.out / "ex\ntra").write_text("x")
    assert run.run("--output", run.out, "--force").returncode == 2
    assert (run.out / "org" / "old.txt").exists()


def test_a_signal_while_the_temp_file_is_made_leaves_the_demo_alone(run):
    """Signalled while the temporary file is being made, i.e. before anything is moved or flagged."""
    _write_exec(run.tmp / "bin" / "mktemp",
                '#!/usr/bin/env bash\n'
                '# signal the refresh script: the nearest ancestor whose command line names it\n'
                'p=$PPID\n'
                'while [ "$p" -gt 1 ]; do\n'
                '  case "$(ps -o command= -p "$p")" in\n'
                '    *synth_demo_refresh.sh*) kill -TERM "$p"; break ;;\n'
                '  esac\n'
                '  p=$(ps -o ppid= -p "$p" | tr -d " ")\n'
                'done\n'
                'sleep 0.3\n'
                'exec "$REAL_MKTEMP" "$@"\n')
    _populate_owned(run.out)
    before = _snapshot(run.out)
    proc = run.run("--output", run.out, "--force", env={"REAL_MKTEMP": shutil.which("mktemp")})
    assert proc.returncode == 143, proc.stderr
    assert _snapshot(run.out) == before and _aside_dirs(run) == [] and run.calls() == []
