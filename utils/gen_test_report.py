"""Render a Markdown test report from a pytest JUnit XML (stdlib only).

Module-agnostic: every module-specific string is an argument, so the same
script serves `tests/server/storage/`, `tests/common/msging/` and whatever
comes next. It used to hardcode the storage title and read `STORAGE_*` env
vars; a second module needing a report is what made that a bug.

    venv/bin/python -m pytest tests/common/msging/ \
        --junitxml=docs/superpowers/reports/msging-junit.xml
    venv/bin/python utils/gen_test_report.py \
        --title "Event Messaging (msging)" \
        --junit docs/superpowers/reports/msging-junit.xml \
        --out   docs/superpowers/reports/msging-test-report.md \
        --tests tests/common/msging/

Reads nothing but the XML: no imports from the project, no DB, no network, so
a report can be regenerated from a stored artifact long after the run.
"""
from __future__ import annotations

import argparse
import shlex
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: One row per `<testcase>`: (file-ish group, test name, status, seconds, note).
Row = tuple[str, str, str, float, str]

_PASS, _FAIL, _SKIP = "pass", "FAIL", "skip"


def _parse(junit: Path) -> tuple[list[Row], str]:
    """Flatten every `<testcase>` in the file. Returns the rows and the run
    timestamp pytest recorded (empty when the writer omitted it)."""
    root = ET.parse(junit).getroot()
    suites = root.findall(".//testsuite") or [root]
    rows: list[Row] = []
    timestamp = ""
    for suite in suites:
        timestamp = timestamp or (suite.get("timestamp") or "")
        for case in suite.findall("testcase"):
            classname = case.get("classname") or ""
            name = f"{classname}.{case.get('name')}"
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            skipped = case.find("skipped")
            if failure is not None:
                status = _FAIL
                note = (failure.get("message") or "").strip()
            elif skipped is not None:
                status = _SKIP
                note = (skipped.get("message") or "").strip()
            else:
                status = _PASS
                note = ""
            try:
                seconds = float(case.get("time") or 0.0)
            except ValueError:
                seconds = 0.0
            rows.append((_group(classname), name, status, seconds, note))
    return rows, timestamp


def _group(classname: str) -> str:
    """The test module a case belongs to: `a.b.test_x.TestY` -> `a.b.test_x`.

    Grouping by module, not by class, because that is the unit a reader opens
    -- and a module with classes and bare functions in it must not split into
    two groups that look like two files.
    """
    parts = [p for p in classname.split(".") if p]
    for i, part in enumerate(parts):
        if part.startswith("test_"):
            return ".".join(parts[: i + 1])
    return classname or "(root)"


def _cell(text: str, limit: int = 160) -> str:
    """One-line, pipe-safe Markdown cell."""
    flat = " ".join(str(text).split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return flat.replace("|", "\\|")


def _render(rows: list[Row], *, title: str, timestamp: str, junit: Path,
            out: Path, tests: str | None, notes: list[str]) -> str:
    total = len(rows)
    fails = sum(1 for r in rows if r[2] == _FAIL)
    skips = sum(1 for r in rows if r[2] == _SKIP)
    passes = total - fails - skips
    duration = sum(r[3] for r in rows)

    lines = [f"# {title} — Test Report", ""]
    if timestamp:
        lines += [f"Run: `{timestamp}`", ""]
    lines += [
        f"- Total: {total}  Passed: {passes}  Failed: {fails}  "
        f"Skipped: {skips}",
        f"- Wall time (sum of cases): {duration:.2f}s",
        "",
    ]

    # Per-module summary first: on a suite of this size the flat table below is
    # a reference, not something anyone reads top to bottom.
    lines += ["## By test module", "",
              "| Module | Tests | Passed | Failed | Skipped | Time (s) |",
              "|---|---:|---:|---:|---:|---:|"]
    for group in sorted({r[0] for r in rows}):
        grp = [r for r in rows if r[0] == group]
        g_fail = sum(1 for r in grp if r[2] == _FAIL)
        g_skip = sum(1 for r in grp if r[2] == _SKIP)
        lines.append(
            f"| {_cell(group)} | {len(grp)} | {len(grp) - g_fail - g_skip} | "
            f"{g_fail} | {g_skip} | {sum(r[3] for r in grp):.2f} |")
    lines.append("")

    if fails:
        lines += ["## Failures", "", "| Test | Message |", "|---|---|"]
        lines += [f"| {_cell(n)} | {_cell(note)} |"
                  for _, n, s, _t, note in rows if s == _FAIL]
        lines.append("")

    if skips:
        # Why a test skipped is the part a reader actually needs: it is the
        # difference between "not applicable here" and "silently not run".
        lines += ["## Skipped", "", "| Test | Reason |", "|---|---|"]
        lines += [f"| {_cell(n)} | {_cell(note)} |"
                  for _, n, s, _t, note in rows if s == _SKIP]
        lines.append("")

    lines += ["## All tests", "", "| Test | Result | Time (s) |", "|---|---|---|"]
    lines += [f"| {_cell(n)} | {s} | {t:.3f} |" for _, n, s, t, _note in rows]
    lines.append("")

    if tests:
        lines += [
            "Reproduce:",
            "",
            "```bash",
            f"venv/bin/python -m pytest {tests} --junitxml={junit}",
            "venv/bin/python utils/gen_test_report.py \\",
            f'    --title "{title}" \\',
            f"    --junit {junit} \\",
            f"    --out   {out} \\",
            f"    --tests {shlex.quote(tests)}",
            "```",
            "",
        ]
    for note in notes:
        lines += [note, ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render a Markdown test report from a pytest JUnit XML.")
    ap.add_argument("--title", required=True,
                    help="report heading, e.g. \"Event Messaging (msging)\"")
    ap.add_argument("--junit", required=True, type=Path,
                    help="pytest --junitxml output to read")
    ap.add_argument("--out", required=True, type=Path,
                    help="Markdown file to write")
    ap.add_argument("--tests", default=None,
                    help="test path, used to print a reproduce command")
    ap.add_argument("--note", action="append", default=[],
                    help="footnote line; repeatable")
    args = ap.parse_args(argv)

    if not args.junit.exists():
        print(f"missing {args.junit}; run pytest --junitxml={args.junit} first",
              file=sys.stderr)
        return 1
    rows, timestamp = _parse(args.junit)
    text = _render(rows, title=args.title, timestamp=timestamp,
                   junit=args.junit, out=args.out, tests=args.tests,
                   notes=args.note)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    fails = sum(1 for r in rows if r[2] == _FAIL)
    print(f"wrote {args.out} ({len(rows)} tests, {fails} failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
