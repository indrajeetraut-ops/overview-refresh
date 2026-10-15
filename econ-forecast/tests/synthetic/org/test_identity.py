"""P1: Layer 1 `users.identity_style` (independent vs. consistent) and its byte-identity guarantee."""
import re
import unicodedata

from forecast.artifact_io import sha256
from forecast.synthetic.config import UserOptions, UsersConfig
from forecast.synthetic.runner import run_users
from forecast.synthetic.users import create_users

LOGIN_RE = re.compile(r"^[a-z0-9-]{1,39}$")


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()


def _config(**overrides) -> UsersConfig:
    options = {"count": 20, "org_domain": "acme-identity.test", "locale": "en_US", "seed": 4242}
    options.update(overrides)
    return UsersConfig(schema_version=1, users=UserOptions(**options))


def test_consistent_email_matches_name():
    users = create_users(_config(identity_style="consistent"))
    assert users
    for user in users:
        local = user.email.split("@", 1)[0]
        first = user.name.split(" ", 1)[0]
        assert local.startswith(_fold(first)), (user.name, user.email)
        assert user.github_login is not None


def test_login_unique_and_valid():
    users = create_users(_config(identity_style="consistent", count=500))
    logins = [user.github_login for user in users]
    assert len(logins) == len(users)
    assert len(set(logins)) == len(logins), "github_login must be unique"
    for login in logins:
        assert LOGIN_RE.fullmatch(login), login


def test_consistent_deterministic():
    config = _config(identity_style="consistent")
    first = create_users(config)
    second = create_users(config)
    assert first == second


def test_user_ids_style_independent():
    independent = create_users(_config(identity_style="independent"))
    consistent = create_users(_config(identity_style="consistent"))
    assert [u.user_id for u in independent] == [u.user_id for u in consistent]
    assert len(independent) == len(consistent) == 20


def test_independent_bytes_unchanged(tmp_path):
    # Hash computed before P1 touched users.py/models.py, from the then-current
    # (identity_style-free) create_users(), with:
    #   schema_version: 1
    #   users: {count: 20, org_domain: baseline-check.test, seed: 999}
    # via:
    #   PYTHONPATH=econ-forecast/src:src venv/bin/python -m forecast.synthetic.cli_synt_gen \
    #     --op user-create --config <that config> --output <tmp dir>
    #   sha256sum <tmp dir>/users.jsonl
    expected = "e6f9540e9701d07727fe2cb24f15c09b621c65eb1c9423e01670aa02d0e8a150"
    config = UsersConfig(schema_version=1, users=UserOptions(
        count=20, org_domain="baseline-check.test", locale="en_US", seed=999))
    result = run_users(config, tmp_path / "out")
    assert sha256(result.output_dir / "users.jsonl") == expected
