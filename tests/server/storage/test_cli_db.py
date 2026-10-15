import pytest

from server.storage import cli_db
from server.storage.dbm import operations
from server.storage.engine import dispose_engine
from server.storage.org_token_store import PgOrgTokenStore
from tests.server.storage.conftest import requires_db


#: The org-token verbs go through the ORM, so the CLI needs a config *file* pointing at the
#: test database -- `config/storage.yaml` reads TALIUM_DB_DSN, which these tests do not set.
@pytest.fixture
def db_config(tmp_path, cfg):
    operations.init(cfg, force=True)
    dispose_engine()          # the CLI builds its own engine from the file below
    path = tmp_path / "storage.yaml"
    path.write_text(f"storage:\n  dsn: {cfg.dsn}\n", encoding="utf-8")
    yield str(path)
    dispose_engine()


def test_help_exits_zero(capsys):
    assert cli_db.main(["--help"]) == 0


def test_unknown_verb_returns_error_code():
    assert cli_db.main(["bogus"]) == 1


def test_import_reports_deferred(capsys):
    code = cli_db.main(["--config", "config/storage.yaml", "import", "--source", "x"])
    captured = capsys.readouterr()
    assert code == 1
    assert "deferred" in (captured.out + captured.err).lower()


# ------------------------------------------- org-token (otel-intake design §4.5)

@requires_db
def test_org_token_issue_prints_the_key_once_and_stores_only_its_hash(db_config, capsys):
    code = cli_db.main(["--config", db_config, "org-token", "issue",
                        "--org", "org-acme", "--descriptor", '{"note": "fleet"}'])
    out = capsys.readouterr().out
    assert code == 0
    key = next(line.split()[1] for line in out.splitlines() if line.strip().startswith("key "))
    assert key.startswith("tk_org_")
    assert "cannot be recovered" in out

    # the issued key verifies, and the row holds only its hash
    from server.ingest.otlp.auth import OrgAuth
    from server.storage.engine import get_engine
    from server.storage.config import load_config

    get_engine(load_config(db_config))
    store = PgOrgTokenStore()
    assert OrgAuth(store).verify(f"Bearer {key}").org_id == "org-acme"
    assert all(key not in t.token_hash for t in store.list_for_org("org-acme"))


@requires_db
def test_org_token_revoke_by_hash_prefix(db_config, capsys):
    cli_db.main(["--config", db_config, "org-token", "issue", "--org", "org-acme"])
    hash_prefix = next(line.split()[1].rstrip("…")
                       for line in capsys.readouterr().out.splitlines()
                       if line.strip().startswith("hash "))

    assert cli_db.main(["--config", db_config, "org-token", "revoke",
                        "--hash-prefix", hash_prefix]) == 0
    from server.storage.engine import get_engine
    from server.storage.config import load_config

    get_engine(load_config(db_config))
    assert PgOrgTokenStore().list_for_org("org-acme")[0].revoked is True


@requires_db
def test_org_token_revoke_refuses_an_unknown_prefix(db_config, capsys):
    code = cli_db.main(["--config", db_config, "org-token", "revoke",
                        "--hash-prefix", "ffffffffffff"])
    assert code == 1
    assert "no org token" in capsys.readouterr().err


@requires_db
def test_org_token_list_shows_state(db_config, capsys):
    cli_db.main(["--config", db_config, "org-token", "issue", "--org", "org-acme"])
    capsys.readouterr()
    assert cli_db.main(["--config", db_config, "org-token", "list", "--org", "org-acme"]) == 0
    assert "active" in capsys.readouterr().out
