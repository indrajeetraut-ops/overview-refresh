import json
import stat

import pytest

from common.msging.clnt.binding import BindingRegistry
from common.msging.config import load_config
from common.msging.errors import ConfigError

YAML = """
client:
  default_server: ingest_prod
  servers:
    ingest_prod:
      base_path: "https://ingest.prod:8443"
      service_name: ingest
    ingest_dev:
      base_path: "https://ingest.dev:8443"
      enabled: false
"""


@pytest.fixture()
def reg(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(YAML, encoding="utf-8")
    return BindingRegistry(load_config(p).client, tmp_path / "store")


def test_none_resolves_to_default_server(reg):
    assert reg.resolve(None).alias == "ingest_prod"


def test_unknown_alias_raises(reg):
    with pytest.raises(ConfigError):
        reg.resolve("nope")


def test_disabled_alias_raises(reg):
    with pytest.raises(ConfigError):
        reg.resolve("ingest_dev")


def test_identity_is_written_0600(reg):
    reg.save_identity("ingest_prod", {"producer_id": "p1", "api_key": "tk_prod_x"})
    path = reg.dir_for("ingest_prod", "p1") / "identity.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_two_aliases_are_isolated_directories(reg):
    reg.save_identity("ingest_prod", {"producer_id": "p1", "api_key": "k1"})
    a = reg.dir_for("ingest_prod", "p1")
    b = reg.dir_for("ingest_dev", "p2")
    assert a != b and "ingest_prod" in str(a) and "ingest_dev" in str(b)


def test_load_all_rebuilds_producer_to_binding(reg):
    reg.save_identity("ingest_prod", {"producer_id": "p1", "api_key": "k1"})
    assert reg.load_all()["p1"].alias == "ingest_prod"


def test_stale_binding_detected_when_base_path_changed(reg):
    assert reg.check_stale("ingest_prod", "https://old.example:8443") is True
    assert reg.check_stale("ingest_prod", "https://ingest.prod:8443") is False


def test_load_all_skips_malformed_identity_file_not_fatal(reg):
    """A directory with a corrupt/truncated identity.json (bit rot, an
    out-of-band edit) must not blow up load_all() for every other producer
    -- it is logged loudly and skipped, matching store.py's corrupt-record
    policy (design §3)."""
    reg.save_identity("ingest_prod", {"producer_id": "good", "api_key": "k1"})
    bad_dir = reg.dir_for("ingest_prod", "bad")
    bad_dir.mkdir(parents=True)
    (bad_dir / "identity.json").write_text("{not valid json", encoding="utf-8")

    result = reg.load_all()
    assert set(result) == {"good"}
    assert result["good"].alias == "ingest_prod"


def test_load_all_skips_identity_missing_required_field(reg):
    """Well-formed JSON that is nonetheless missing a required key (e.g. a
    partially written or hand-edited record) is the same corrupt-record
    case, not a crash."""
    reg.save_identity("ingest_prod", {"producer_id": "good", "api_key": "k1"})
    bad_dir = reg.dir_for("ingest_prod", "incomplete")
    bad_dir.mkdir(parents=True)
    (bad_dir / "identity.json").write_text(
        json.dumps({"producer_id": "incomplete"}), encoding="utf-8"
    )

    result = reg.load_all()
    assert set(result) == {"good"}
