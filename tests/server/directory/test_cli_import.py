"""cli_directory_import against a real Postgres (IDENT §3.3, plan G4). Skips without a DSN."""
from pathlib import Path

from sqlalchemy import text

from server.directory.cli_directory_import import main
from server.storage.engine import session_scope

from .conftest import requires_db

FIXTURE = str(Path("tests/server/directory/fixtures/csv_roster"))
_ARGS = ["--source", FIXTURE, "--storage-config", "config/storage.yaml"]


def _counts() -> dict[str, int]:
    with session_scope() as s:
        return {
            t: s.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            for t in ("users", "teams", "team_members", "role_assignments", "identity_links")
        }


@requires_db
def test_dry_run_writes_nothing(engine, capsys):
    rc = main(_ARGS)  # --dry-run is the default
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "created  5" in out  # 4 users.csv rows + erin bootstrapped from members.csv
    assert all(v == 0 for v in _counts().values())


@requires_db
def test_apply_then_reapply_is_idempotent(engine, capsys):
    rc = main([*_ARGS, "--apply"])
    assert rc == 0
    first = capsys.readouterr().out
    assert "APPLIED" in first
    after_first = _counts()
    assert after_first["users"] == 5
    assert after_first["teams"] == 3
    assert after_first["team_members"] == 5
    # anil + cy carry an org-level lead grant; the other 3 get the default dev@org
    assert after_first["role_assignments"] == 5

    rc = main([*_ARGS, "--apply"])
    assert rc == 0
    second = capsys.readouterr().out
    assert "created  0" in second
    assert "updated  0" in second
    assert _counts() == after_first


@requires_db
def test_bootstrap_warning_surfaced(engine, capsys):
    main(_ARGS)
    out = capsys.readouterr().out
    assert "erin@acme.com" in out and "bootstrap" in out
