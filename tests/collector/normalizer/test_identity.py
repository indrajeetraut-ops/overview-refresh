"""Account identity, opt-in (spec: docs/modules/normalizer.md §5.1).

The transcript carries no email, account, or organization. OTEL stamps all three on every span,
reading them from the agent's own config file; a JSONL-only pipeline has no identity at all
unless it is given one.

Opt-in on purpose. Everything else in the normalizer works to keep identity and content *out*
of the output, so putting it in is an operator decision made per run -- there is no default
path and no implicit read.
"""
import json

import pytest

from collector import cli_normalize
from collector.normalizer.identity import load_account_identity
from fixtures.normalizer import recorded_sessions

SESSION = recorded_sessions.RECORDED_DIR / "session-regular.jsonl"

FULL = {
    "oauthAccount": {
        "emailAddress": "dev@example.com",
        "accountUuid": "b97e5644-1658-448c-a8d1-adcc2d303974",
        "organizationName": "Acme Engineering",
        "organizationUuid": "ab5d9cff-baff-418d-8a76-9e1744ff4dd4",
        "organizationRole": "admin",          # entitlement, not identity -- must not be read
        "userRateLimitTier": "default_claude_max_20x",
        "accountCreatedAt": "2026-01-01T00:00:00Z",
    },
    "userID": "9de5d01f9dc1e7ba390afaffa690fa03",
    "autoUpdates": True,
}


def _write(tmp_path, data, name="claude.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# -- extraction ------------------------------------------------------------------------------


def test_extracts_the_four_identity_fields(tmp_path):
    assert load_account_identity(_write(tmp_path, FULL)) == {
        "email": "dev@example.com",
        "account_uuid": "b97e5644-1658-448c-a8d1-adcc2d303974",
        "organization_name": "Acme Engineering",
        "organization_uuid": "ab5d9cff-baff-418d-8a76-9e1744ff4dd4",
    }


def test_entitlement_fields_are_not_read(tmp_path):
    """organizationRole, rate-limit tiers, accountCreatedAt sit in the same block and are not
    identity. Reading the whole block would quietly widen what leaves the machine."""
    identity = load_account_identity(_write(tmp_path, FULL))
    assert set(identity) == {"email", "account_uuid", "organization_name", "organization_uuid"}


def test_personal_account_yields_null_org_not_missing_keys(tmp_path):
    """A personal account has no organization. Every key is still present, so a consumer never
    has to tell 'absent' from 'not looked for'."""
    personal = {"oauthAccount": {"emailAddress": "solo@example.com", "accountUuid": "uuid-1"}}
    assert load_account_identity(_write(tmp_path, personal)) == {
        "email": "solo@example.com",
        "account_uuid": "uuid-1",
        "organization_name": None,
        "organization_uuid": None,
    }


def test_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(tmp_path, FULL, name=".claude.json")
    assert load_account_identity("~/.claude.json")["email"] == "dev@example.com"


# -- failures name the path ------------------------------------------------------------------


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="user config not found"):
        load_account_identity(tmp_path / "nope.json")


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_account_identity(path)


def test_wrong_file_shape_raises(tmp_path):
    """Pointing -u at settings.json instead of .claude.json is the likely mistake. Failing
    beats returning four nulls, which reads as a personal account."""
    with pytest.raises(ValueError, match="no `oauthAccount` block"):
        load_account_identity(_write(tmp_path, {"permissions": {}, "model": "opus"}))


# -- meta_info wiring ------------------------------------------------------------------------


def _dump(tmp_path, *extra):
    out = tmp_path / "out.json"
    cli_normalize.main([str(SESSION), "--out", str(out), *extra])
    return json.loads(out.read_text(encoding="utf-8"))


def test_user_is_null_without_the_flag(tmp_path):
    """Null means identity was never requested -- distinct from an object with null members,
    which means it was requested and the account has no org."""
    assert _dump(tmp_path)["meta_info"]["user"] is None


def test_flag_populates_meta_info(tmp_path):
    cfg = _write(tmp_path, FULL)
    meta = _dump(tmp_path, "--user-config", str(cfg))["meta_info"]
    assert meta["user"]["email"] == "dev@example.com"
    assert meta["user"]["organization_name"] == "Acme Engineering"


def test_short_flag_works(tmp_path):
    cfg = _write(tmp_path, FULL)
    assert _dump(tmp_path, "-u", str(cfg))["meta_info"]["user"]["account_uuid"] == FULL["oauthAccount"]["accountUuid"]


def test_identity_stays_out_of_the_sessions(tmp_path):
    """meta_info only. It is a property of who ran the session, not of anything inside it --
    and per-event identity is what makes OTEL spans PII-bearing."""
    cfg = _write(tmp_path, FULL)
    dump = _dump(tmp_path, "-u", str(cfg))
    assert "dev@example.com" not in json.dumps(dump["sessions"])


def test_bad_user_config_exits_without_a_traceback(tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        _dump(tmp_path, "-u", str(tmp_path / "absent.json"))
    assert "user config not found" in str(excinfo.value)
    assert "Traceback" not in capsys.readouterr().err
