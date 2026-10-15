"""Tests for dated, indexed forecasting test evidence."""

import json
from pathlib import Path
import subprocess

from forecast.testing import cli_test_report
from forecast.testing.cli_test_report import _index_markdown, _junit_counts, _update_index


ROOT = Path(__file__).parents[3]


def test_junit_counts_and_index_preserve_pass_fail_skip_and_run_identity(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite><testcase classname="t" name="pass"/>'
        '<testcase classname="t" name="fail"><failure message="x"/></testcase>'
        '<testcase classname="t" name="skip"><skipped message="y"/></testcase>'
        '</testsuite>',
        encoding="utf-8",
    )
    assert _junit_counts(junit) == {
        "total": 3, "passed": 1, "failed": 1, "skipped": 1,
    }
    entry = {
        "run_id": "run-1", "started_at": "2026-09-19T00:00:00+00:00",
        "completed_at": "2026-09-19T00:01:00+00:00", "status": "failed",
        "counts": _junit_counts(junit), "unverified_tests": ["browser::test"],
        "report_path": "runs/run-1/report.md", "junit_path": "runs/run-1/junit.xml",
        "result_path": "runs/run-1/result.json",
    }
    index = _update_index(tmp_path, entry)
    assert json.loads((tmp_path / "index.json").read_text()) == index
    rendered = _index_markdown(index)
    assert "run-1" in rendered and "| failed | 1 | 1 | 1 | 1 |" in rendered
    assert (tmp_path / "index.md").read_text() == rendered


def test_index_can_record_a_report_failure_without_junit():
    index = {"schema_version": 1, "latest_run_id": "broken", "runs": [{
        "run_id": "broken", "started_at": "2026-09-19T00:00:00+00:00",
        "completed_at": "2026-09-19T00:00:01+00:00", "status": "report_failed",
        "counts": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
        "unverified_tests": [], "report_path": "runs/broken/report.md",
        "junit_path": None, "result_path": "runs/broken/result.json",
    }]}
    rendered = _index_markdown(index)
    assert "[report](runs/broken/report.md)" in rendered
    assert "| unavailable |" in rendered


def test_failed_pytest_is_persisted_and_returned_to_shell(tmp_path, monkeypatch):
    def run(command, **kwargs):
        if "--collect-only" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout="browser.py::test_browser\n", stderr=""
            )
        if command[1:3] == ["-m", "pytest"]:
            junit = Path(next(x.split("=", 1)[1] for x in command if x.startswith("--junitxml=")))
            junit.write_text(
                '<testsuite><testcase classname="t" name="broken">'
                '<failure message="expected failure"/></testcase></testsuite>',
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 1)
        out = Path(command[command.index("--out") + 1])
        out.write_text("# generated report\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="generated", stderr="")

    monkeypatch.setattr(cli_test_report.subprocess, "run", run)
    assert cli_test_report.main(["--output-root", str(tmp_path)]) == 1
    index = json.loads((tmp_path / "index.json").read_text())
    latest = index["runs"][0]
    assert latest["status"] == "failed"
    assert latest["counts"] == {"total": 1, "passed": 0, "failed": 1, "skipped": 0}
    assert latest["unverified_tests"] == ["browser.py::test_browser"]
    result = json.loads((tmp_path / latest["result_path"]).read_text())
    assert result["exit_code"] == 1 and result["status"] == "failed"


def test_test_forecast_wrapper_help_is_side_effect_free(tmp_path):
    result = subprocess.run(
        [str(ROOT / "econ-forecast/utils/test_forecast.sh"), "--help"],
        cwd=tmp_path, text=True, capture_output=True,
    )
    assert result.returncode == 0
    assert "dated JUnit report" in result.stdout
    assert list(tmp_path.iterdir()) == []
