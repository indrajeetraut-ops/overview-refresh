"""The two doc generators in `utils/`.

`gen_test_report.py` used to be storage-specific and its test lived under
`tests/server/storage/`. Both are module-agnostic now, so the test moved here
with the script.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True,
                          text=True, cwd=REPO)


def test_report_generator_renders_markdown(tmp_path):
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuite><testcase classname="t" name="a" time="0.1"/>'
        '<testcase classname="t" name="b" time="0.2">'
        '<skipped message="needs a DSN"/></testcase></testsuite>'
    )
    out = tmp_path / "report.md"
    tests = "tests/server/storage/ --ignore=tests/server/storage/test_browser.py"
    r = _run("utils/gen_test_report.py", "--title", "Storage Module",
             "--junit", str(junit), "--out", str(out),
             "--tests", tests, "--note", "a footnote")
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert "# Storage Module — Test Report" in text
    assert "Total: 2" in text
    assert "Skipped: 1" in text
    # The skip *reason* is the part a reader needs; a bare count hides whether
    # a test was inapplicable or silently never run.
    assert "needs a DSN" in text
    assert "a footnote" in text
    assert f"--tests '{tests}'" in text


def test_report_generator_reports_failures(tmp_path):
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuite><testcase classname="t" name="a" time="0.1">'
        '<failure message="boom"/></testcase></testsuite>'
    )
    out = tmp_path / "report.md"
    r = _run("utils/gen_test_report.py", "--title", "T",
             "--junit", str(junit), "--out", str(out))
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert "Failed: 1" in text
    assert "## Failures" in text
    assert "boom" in text


def test_report_generator_missing_junit_is_an_error(tmp_path):
    r = _run("utils/gen_test_report.py", "--title", "T",
             "--junit", str(tmp_path / "nope.xml"),
             "--out", str(tmp_path / "o.md"))
    assert r.returncode == 1
    assert "missing" in r.stderr


def test_schema_blocks_in_docs_are_up_to_date():
    """The whole point of `gen_schema_blocks.py`: the JSON in the design doc
    and the README is derived from `schema/*.json`, so it cannot drift."""
    r = _run("utils/gen_schema_blocks.py", "--check")
    assert r.returncode == 0, (
        "doc JSON blocks are stale -- run "
        "`venv/bin/python utils/gen_schema_blocks.py`\n" + r.stdout + r.stderr)
