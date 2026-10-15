"""CLI entry points (design §8, §10.1): `cli_update`, `cli_match`, `cli_show`,
`cli_versions`, called in-process via `main(argv)` against `tmp_path` catalog/config
roots -- no network, no touching the real `config/model_pricing` tree.

`cli_update` tests use its `_FETCHERS` injection seam (module docstring in
`cli_update.py`) to drive `update` off the same fixtures `test_build.py` uses, and to
force a source failure without any network access.

A final test runs `utils/model_pricing.sh` itself via `subprocess`, confirming the
wrapper's dispatch (`update --help`) actually reaches the Python CLI.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import common.model_pricing.cli_match as cli_match
import common.model_pricing.cli_show as cli_show
import common.model_pricing.cli_update as cli_update
import common.model_pricing.cli_versions as cli_versions
from common.model_pricing.build import build, write_catalog
from common.model_pricing.config import ModelPricingConfig

FIXTURES = Path(__file__).parent / "fixtures"
LITELLM_PAYLOAD = json.loads((FIXTURES / "litellm_subset.json").read_text())
AWS_PAYLOAD = json.loads((FIXTURES / "aws_us_east_1_subset.json").read_text())
BUILT_ON = date(2026, 9, 25)

REPO_ROOT = Path(__file__).resolve().parents[3]
WRAPPER = REPO_ROOT / "utils" / "model_pricing.sh"


@pytest.fixture(autouse=True)
def _no_gcp_key(monkeypatch):
    monkeypatch.delenv("GCP_BILLING_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _reset_fetchers_seam():
    """`cli_update._FETCHERS` is module-level state (the injection seam) -- reset it
    after every test so one test's override never leaks into the next."""
    yield
    cli_update._FETCHERS = None


def _litellm_fetcher(payload=None):
    payload = LITELLM_PAYLOAD if payload is None else payload

    def fetch(commit=None):
        return payload, {"commit_sha": "d3462c65", "commit_date": "2026-09-24T18:08:09Z"}

    return fetch


def _aws_fetcher(payload=None):
    payload = AWS_PAYLOAD if payload is None else payload

    def fetch(region):
        return payload, {"publication_date": "2026-09-22T16:44:18Z", "version": "1"}

    return fetch


@pytest.fixture
def roots(tmp_path):
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"
    return catalog_root, config_root


@pytest.fixture
def built_catalog(roots):
    """A catalog already on disk at `roots`, built directly through `build.py` (not
    through the CLI) -- what `match`/`show`/`versions` tests read from."""
    catalog_root, config_root = roots
    cfg = ModelPricingConfig.model_validate(
        {
            "channels": ["aws_bedrock:us-east-1:regional", "aws_bedrock:us-east-1:global"],
            "geo": {"us-east-1": "us"},
        }
    )
    result = build(
        cfg,
        fetchers={"litellm": _litellm_fetcher(), "aws_price_list": _aws_fetcher()},
        built_on=BUILT_ON,
    )
    write_catalog(result, catalog_root)
    return catalog_root, config_root, result


# ================================================================== --help (§8)


@pytest.mark.parametrize("mod", [cli_update, cli_match, cli_show, cli_versions])
def test_help_exits_0_without_network_config_or_catalog(mod, capsys):
    with pytest.raises(SystemExit) as exc:
        mod.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()


# ================================================================== update (§8)


def test_update_dry_run_writes_nothing(roots, capsys):
    catalog_root, config_root = roots
    cli_update._FETCHERS = {"litellm": _litellm_fetcher(), "aws_price_list": _aws_fetcher()}

    code = cli_update.main(
        [
            "--dry-run",
            "--config-root", str(config_root),
            "--catalog-root", str(catalog_root),
        ]
    )
    assert code == 0
    assert not catalog_root.exists()
    out = capsys.readouterr().out
    assert "Refresh report" in out


def test_update_dry_run_json_prints_refresh_detail(roots, capsys):
    catalog_root, config_root = roots
    cli_update._FETCHERS = {"litellm": _litellm_fetcher(), "aws_price_list": _aws_fetcher()}

    code = cli_update.main(
        [
            "--dry-run",
            "--json",
            "--config-root", str(config_root),
            "--catalog-root", str(catalog_root),
        ]
    )
    assert code == 0
    assert not catalog_root.exists()
    captured = capsys.readouterr()
    # stdout is reserved for the CLI's own result (§8): logs go to stderr
    # (`configure_logging(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))`
    # in cli_update.py), so the *entire* captured stdout is the JSON report -- no log
    # lines mixed in to strip out first.
    data = json.loads(captured.out)
    assert "added" in data and "changed" in data and "unmapped" in data
    assert "source_fetched" in captured.err  # confirms logs landed on stderr, not stdout


def test_update_writes_catalog_then_second_run_is_noop(roots, capsys):
    catalog_root, config_root = roots
    cli_update._FETCHERS = {"litellm": _litellm_fetcher(), "aws_price_list": _aws_fetcher()}

    code = cli_update.main(
        ["--config-root", str(config_root), "--catalog-root", str(catalog_root)]
    )
    assert code == 0
    files = list(catalog_root.iterdir())
    assert any(f.suffix == ".json" and f.name != "index.json" for f in files)
    capsys.readouterr()

    code2 = cli_update.main(
        ["--config-root", str(config_root), "--catalog-root", str(catalog_root)]
    )
    assert code2 == 0
    out2 = capsys.readouterr().out
    assert "no changes" in out2
    written = json.loads((catalog_root / "index.json").read_text())["versions"][-1]
    assert f"stays at {written}" in out2


def test_update_corrupt_catalog_exits_1(roots, capsys):
    catalog_root, config_root = roots
    catalog_root.mkdir(parents=True, exist_ok=True)
    (catalog_root / "index.json").write_text("{not json")
    cli_update._FETCHERS = {"litellm": _litellm_fetcher(), "aws_price_list": _aws_fetcher()}

    code = cli_update.main(
        ["--config-root", str(config_root), "--catalog-root", str(catalog_root)]
    )
    assert code == 1
    assert "update failed" in capsys.readouterr().err
    assert (catalog_root / "index.json").read_text() == "{not json"


def test_update_failing_source_exits_1_and_writes_nothing(roots, capsys):
    catalog_root, config_root = roots

    def failing_litellm(commit=None):
        raise RuntimeError("network down")

    # litellm is always fetched regardless of `model_pricing.yaml` (§6.1 step 1); a
    # cloud source like aws_price_list would only be called if a channel for it were
    # configured, which this empty `config_root` does not do.
    cli_update._FETCHERS = {"litellm": failing_litellm, "aws_price_list": _aws_fetcher()}

    code = cli_update.main(
        ["--config-root", str(config_root), "--catalog-root", str(catalog_root)]
    )
    assert code == 1
    assert not catalog_root.exists()
    err = capsys.readouterr().err
    assert "update failed" in err


# ================================================================== match (§8)


def test_match_all_matched_exits_0(built_catalog, capsys):
    catalog_root, config_root, _ = built_catalog
    code = cli_match.main(
        [
            "--org", "acme",
            "--models", "claude-sonnet-5",
            "--config-root", str(config_root),
            "--catalog-root", str(catalog_root),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "claude-sonnet-5" in out


def test_match_any_unmatched_exits_2(built_catalog, capsys):
    catalog_root, config_root, _ = built_catalog
    code = cli_match.main(
        [
            "--org", "acme",
            "--models", "claude-sonnet-5,totally-unknown-model",
            "--config-root", str(config_root),
            "--catalog-root", str(catalog_root),
        ]
    )
    assert code == 2


def test_match_json_format(built_catalog, capsys):
    catalog_root, config_root, _ = built_catalog
    code = cli_match.main(
        [
            "--org", "acme",
            "--models", "claude-sonnet-5",
            "--format", "json",
            "--config-root", str(config_root),
            "--catalog-root", str(catalog_root),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data[0]["model_id"] == "claude-sonnet-5"


def test_match_unknown_catalog_root_exits_1(tmp_path, capsys):
    code = cli_match.main(
        [
            "--org", "acme",
            "--models", "claude-sonnet-5",
            "--config-root", str(tmp_path / "config"),
            "--catalog-root", str(tmp_path / "no-such-catalog"),
        ]
    )
    assert code == 1


# ================================================================== show (§8)


def test_show_known_model_exits_0(built_catalog, capsys):
    catalog_root, config_root, _ = built_catalog
    code = cli_show.main(
        [
            "--model", "claude-sonnet-5",
            "--org", "acme",
            "--config-root", str(config_root),
            "--catalog-root", str(catalog_root),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "claude-sonnet-5" in out
    assert "anthropic" in out


def test_show_unknown_model_exits_2(built_catalog, capsys):
    catalog_root, config_root, _ = built_catalog
    code = cli_show.main(
        [
            "--model", "totally-unknown-model",
            "--config-root", str(config_root),
            "--catalog-root", str(catalog_root),
        ]
    )
    assert code == 2


# ================================================================== versions (§8)


def test_versions_lists_built_version(built_catalog, capsys):
    catalog_root, config_root, result = built_catalog
    code = cli_versions.main(["--catalog-root", str(catalog_root)])
    assert code == 0
    out = capsys.readouterr().out
    assert result.version.version in out
    assert "rows=" in out


def test_versions_limit(built_catalog, capsys):
    catalog_root, config_root, result = built_catalog
    code = cli_versions.main(["--catalog-root", str(catalog_root), "--limit", "1"])
    assert code == 0
    out = capsys.readouterr().out
    assert result.version.version in out


def test_versions_empty_catalog_exits_0(tmp_path, capsys):
    code = cli_versions.main(["--catalog-root", str(tmp_path / "no-such-catalog")])
    assert code == 0
    out = capsys.readouterr().out
    assert "no catalog versions" in out


# ================================================================== shell wrapper


def test_shell_wrapper_help(built_catalog):
    env = {"PATH": "/usr/bin:/bin", "PYTHON": sys.executable, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run(
        [str(WRAPPER), "update", "--help"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_shell_wrapper_unknown_command_exits_2():
    env = {"PATH": "/usr/bin:/bin", "PYTHON": sys.executable, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run(
        [str(WRAPPER), "bogus"], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_shell_wrapper_missing_command_exits_2():
    env = {"PATH": "/usr/bin:/bin", "PYTHON": sys.executable, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run([str(WRAPPER)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
