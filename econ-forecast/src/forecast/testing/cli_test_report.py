"""Run the forecasting test suite and publish dated, indexed evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

import structlog

from common.msging.log import configure_logging, get_logger


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_ROOT = ROOT / "econ-forecast" / "docs" / "test-results"
TEST_ROOT = ROOT / "econ-forecast" / "tests"
BROWSER_TEST = TEST_ROOT / "utils" / "test_plot_forecast_browser.py"


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_text(
        path,
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
    )


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    failed = sum(
        case.find("failure") is not None or case.find("error") is not None
        for case in cases
    )
    skipped = sum(case.find("skipped") is not None for case in cases)
    return {
        "total": len(cases),
        "passed": len(cases) - failed - skipped,
        "failed": failed,
        "skipped": skipped,
    }


def _index_markdown(index: dict) -> str:
    lines = [
        "# Econ Forecast recorded test runs",
        "",
        "Each row points to immutable evidence for one invocation of "
        "`econ-forecast/utils/test_forecast.sh`.",
        "",
        "| Completed (UTC) | Run ID | Status | Passed | Failed | Skipped | Unverified | Report | JUnit |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for run in index["runs"]:
        counts = run["counts"]
        report_link = (
            f"[report]({run['report_path']})" if run.get("report_path") else "unavailable"
        )
        junit_link = (
            f"[JUnit]({run['junit_path']})" if run.get("junit_path") else "unavailable"
        )
        lines.append(
            f"| {run['completed_at']} | `{run['run_id']}` | {run['status']} | "
            f"{counts['passed']} | {counts['failed']} | {counts['skipped']} | "
            f"{len(run['unverified_tests'])} | {report_link} | {junit_link} |"
        )
    lines += ["", "Machine-readable metadata: [`index.json`](index.json).", ""]
    return "\n".join(lines)


def _update_index(output_root: Path, entry: dict) -> dict:
    path = output_root / "index.json"
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        runs = current.get("runs", []) if isinstance(current, dict) else []
    else:
        runs = []
    runs = [entry, *(run for run in runs if run.get("run_id") != entry["run_id"])]
    index = {
        "schema_version": 1,
        "latest_run_id": entry["run_id"],
        "runs": runs,
    }
    _atomic_json(path, index)
    _atomic_text(output_root / "index.md", _index_markdown(index))
    return index


def _collect_browser_tests() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(BROWSER_TEST)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return [str(BROWSER_TEST.relative_to(ROOT))]
    return [line.strip() for line in result.stdout.splitlines() if "::" in line]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        prog="test_forecast.sh",
        description=(
            "Run econ-forecast tests and save a dated JUnit report, readable "
            "Markdown report, JSON result, and cumulative indexes."
        ),
    )
    value.add_argument(
        "--include-browser",
        action="store_true",
        help="also run the four Playwright browser tests; failures remain recorded",
    )
    value.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="report root (default: econ-forecast/docs/test-results)",
    )
    return value


def main(argv=None) -> int:
    configure_logging(
        json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr)
    )
    log = get_logger(__name__)
    args = parser().parse_args(argv)
    output_root = args.output_root.resolve()
    started = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    directory_name = f"{started:%Y%m%dT%H%M%SZ}_{run_id[:8]}"
    run_dir = output_root / "runs" / directory_name
    run_dir.mkdir(parents=True, exist_ok=False)
    junit_path = run_dir / "junit.xml"
    report_path = run_dir / "report.md"
    result_path = run_dir / "result.json"

    unverified = [] if args.include_browser else _collect_browser_tests()
    pytest_command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        str(TEST_ROOT.relative_to(ROOT)),
    ]
    if not args.include_browser:
        pytest_command.append(f"--ignore={BROWSER_TEST.relative_to(ROOT)}")
    pytest_command.append(f"--junitxml={junit_path}")
    log.info(
        "forecast.tests.started",
        run_id=run_id,
        include_browser=args.include_browser,
        report_dir=str(run_dir),
    )
    completed = subprocess.run(pytest_command, cwd=ROOT)

    if not junit_path.is_file():
        completed_at = datetime.now(timezone.utc)
        counts = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
        error = "pytest did not produce JUnit XML"
        _atomic_text(
            report_path,
            "# Econ Forecast — Test Report\n\n"
            f"Run ID: `{run_id}`\n\nStatus: **report_failed**\n\n{error}.\n",
        )
        summary = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "report_failed",
            "exit_code": completed.returncode or 1,
            "started_at": started.isoformat(),
            "completed_at": completed_at.isoformat(),
            "counts": counts,
            "unverified_tests": unverified,
            "error": error,
            "report_dir": str(run_dir),
            "report_path": str(report_path),
            "junit_path": None,
        }
        _atomic_json(result_path, summary)
        relative_run = run_dir.relative_to(output_root)
        _update_index(output_root, {
            "run_id": run_id,
            "started_at": summary["started_at"],
            "completed_at": summary["completed_at"],
            "status": summary["status"],
            "counts": counts,
            "unverified_tests": unverified,
            "report_path": f"{relative_run.as_posix()}/report.md",
            "junit_path": None,
            "result_path": f"{relative_run.as_posix()}/result.json",
        })
        print(json.dumps(summary, sort_keys=True))
        return summary["exit_code"]

    counts = _junit_counts(junit_path)
    status = (
        "failed"
        if completed.returncode != 0 or counts["failed"]
        else "passed_with_unverified"
        if unverified
        else "passed"
    )
    completed_at = datetime.now(timezone.utc)
    test_selection = str(TEST_ROOT.relative_to(ROOT))
    if not args.include_browser:
        test_selection += f" --ignore={BROWSER_TEST.relative_to(ROOT)}"
    notes = [
        f"Run ID: `{run_id}`. Completed UTC: `{completed_at.isoformat()}`.",
        "Each test name states its purpose; bracketed suffixes record parameterized cases. "
        "The source test contains exact fixtures, inputs, and assertions.",
    ]
    if unverified:
        notes.append(
            f"Overall status is `{status}`: {len(unverified)} browser tests were excluded "
            "from this run and are unverified, not passed or skipped."
        )
    generator_command = [
        sys.executable,
        str(ROOT / "utils" / "gen_test_report.py"),
        "--title",
        "Econ Forecast",
        "--junit",
        str(junit_path),
        "--out",
        str(report_path),
        "--tests",
        test_selection,
    ]
    for note in notes:
        generator_command.extend(["--note", note])
    generated = subprocess.run(generator_command, cwd=ROOT, text=True, capture_output=True)
    if generated.returncode != 0:
        status = "report_failed"
        _atomic_text(
            report_path,
            "# Econ Forecast — Test Report\n\n"
            f"Run ID: `{run_id}`\n\nStatus: **report_failed**\n\n"
            "The JUnit artifact exists, but Markdown generation failed.\n",
        )

    relative_run = run_dir.relative_to(output_root)
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "exit_code": completed.returncode if generated.returncode == 0 else 1,
        "started_at": started.isoformat(),
        "completed_at": completed_at.isoformat(),
        "counts": counts,
        "unverified_tests": unverified,
        "pytest_command": pytest_command,
        "report_dir": str(run_dir),
        "report_path": str(report_path) if report_path.exists() else None,
        "junit_path": str(junit_path),
    }
    _atomic_json(result_path, summary)
    entry = {
        "run_id": run_id,
        "started_at": summary["started_at"],
        "completed_at": summary["completed_at"],
        "status": status,
        "counts": counts,
        "unverified_tests": unverified,
        "report_path": f"{relative_run.as_posix()}/report.md",
        "junit_path": f"{relative_run.as_posix()}/junit.xml",
        "result_path": f"{relative_run.as_posix()}/result.json",
    }
    _update_index(output_root, entry)
    log.info("forecast.tests.completed", **summary)
    print(json.dumps(summary, sort_keys=True))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
