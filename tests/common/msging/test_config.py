import pytest

from common.msging.config import load_config
from common.msging.errors import ConfigError

YAML = """
client:
  default_server: ${TALIUM_SERVER:-ingest_prod}
  servers:
    ingest_prod:
      base_path: "https://ingest.prod:8443"
      service_name: ingest
    ingest_dev:
      base_path: "https://ingest.dev:8443"
      service_name: ingest
      enabled: false
      req_timeout_s: 30
  defaults:
    req_timeout_s: 10
    max_in_flight: 64
server:
  queue_max: 500
  registration:
    mode: open
producer_store:
  backend: file
  file:
    path: /tmp/producers.json
"""


@pytest.fixture()
def cfg_path(tmp_path):
    p = tmp_path / "msging.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p


def test_env_default_applies_when_unset(cfg_path, monkeypatch):
    monkeypatch.delenv("TALIUM_SERVER", raising=False)
    cfg = load_config(cfg_path)
    assert cfg.client.default_server == "ingest_prod"


def test_env_overrides_default(cfg_path, monkeypatch):
    monkeypatch.setenv("TALIUM_SERVER", "ingest_dev")
    cfg = load_config(cfg_path)
    assert cfg.client.default_server == "ingest_dev"


def test_service_name_defaults_to_alias(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("client:\n  servers:\n    ingest:\n      base_path: https://h:1\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.client.servers["ingest"].service_name == "ingest"


def test_per_server_override_beats_defaults(cfg_path):
    cfg = load_config(cfg_path)
    assert cfg.client.settings_for("ingest_prod").req_timeout_s == 10
    assert cfg.client.settings_for("ingest_dev").req_timeout_s == 30


def test_defaults_fill_unspecified_keys(cfg_path):
    """A key absent from YAML falls back to the code default, not None."""
    assert cfg_path and load_config(cfg_path).client.settings_for("ingest_prod").msg_id_block == 1000


def test_unknown_alias_raises(cfg_path):
    with pytest.raises(ConfigError):
        load_config(cfg_path).client.settings_for("nope")


def test_typoed_per_server_override_is_rejected_not_ignored(tmp_path):
    """A typo'd override must fail loudly -- silently ignoring it means an
    operator believes they tuned a timeout they did not."""
    p = tmp_path / "m.yaml"
    p.write_text(
        "client:\n"
        "  servers:\n"
        "    ingest:\n"
        "      base_path: https://h:1\n"
        "      req_timout_s: 999\n",  # typo: missing the 'e'
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc:
        load_config(p).client.settings_for("ingest")
    assert "req_timout_s" in str(exc.value)


def test_registration_defaults_to_open(cfg_path):
    assert load_config(cfg_path).server.registration.mode == "open"


def test_server_defaults_present_when_stanza_absent(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("client:\n  servers: {}\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.server.queue_max == 10000
    assert cfg.server.max_msg_bytes == 33554432
    assert cfg.server.workers == 4


def test_server_workers_is_read_from_yaml(tmp_path):
    """§5.2 `workers`: concurrent handler tasks; in-flight msgs sit outside
    queue_max."""
    p = tmp_path / "m.yaml"
    p.write_text("server:\n  workers: 16\n", encoding="utf-8")
    assert load_config(p).server.workers == 16


def test_typoed_server_key_is_rejected_not_ignored(tmp_path):
    """`workrs: 16` reads like it works and silently runs 4 workers. Same rule
    as the client side: a typo must fail loudly."""
    p = tmp_path / "m.yaml"
    p.write_text("server:\n  workrs: 16\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "workrs" in str(exc.value)


@pytest.mark.parametrize(
    "yaml_text, bad_key",
    [
        # notify_buffer: -1 reaches deque(maxlen=-1) and makes publish() raise
        # ValueError inside a queue worker -- the one thing its docstring
        # promises it never does.
        ("server:\n  notify_buffer: -1\n", "notify_buffer"),
        # notify_ttl_s: 0 is a silent black hole: _expire's `>= ttl` is true
        # for every entry, so every notice drops as "expired" even with a live
        # subscriber attached.
        ("server:\n  notify_ttl_s: 0\n", "notify_ttl_s"),
        ("server:\n  queue_max: 0\n", "queue_max"),
        ("server:\n  workers: 0\n", "workers"),
        ("server:\n  heartbeat_s: 0\n", "heartbeat_s"),
        ("server:\n  max_msg_bytes: 0\n", "max_msg_bytes"),
        ("server:\n  retry_after_s: -1\n", "retry_after_s"),
    ],
)
def test_out_of_range_server_numerics_are_rejected(tmp_path, yaml_text, bad_key):
    """Same spirit as the extra="forbid" rule: a zero or negative here is a
    config bug that surfaces much later as a crash or a black hole, so it has
    to fail at load time and name the key."""
    p = tmp_path / "m.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert bad_key in str(exc.value)


def test_notify_buffer_zero_is_still_allowed(tmp_path):
    """`notify_buffer: 0` is "hold nothing", handled explicitly in the hub --
    it is a setting, not the bug the bound is there to catch."""
    p = tmp_path / "m.yaml"
    p.write_text("server:\n  notify_buffer: 0\n", encoding="utf-8")
    assert load_config(p).server.notify_buffer == 0


def test_shipped_config_loads(monkeypatch):
    """config/msging.yaml is what a real deployment reads. If the loader can't
    parse it, it's a trap for whoever deploys this."""
    monkeypatch.delenv("TALIUM_SERVER", raising=False)
    monkeypatch.delenv("TALIUM_ENROLL_KEY", raising=False)
    monkeypatch.delenv("TALIUM_DATA_DIR", raising=False)

    repo_root = __import__("pathlib").Path(__file__).resolve().parents[3]
    cfg = load_config(repo_root / "config" / "msging.yaml")

    assert cfg.client.default_server == "ingest_prod"
    assert cfg.client.servers["ingest_prod"].service_name == "ingest"
    assert cfg.client.servers["ingest_dev"].enabled is False
    assert cfg.client.settings_for("ingest_dev").req_timeout_s == 30
    assert cfg.server.queue_max == 10000
    assert cfg.server.workers == 4
    assert cfg.server.registration.mode == "open"
    assert cfg.server.registration.key is None
    assert cfg.producer_store.backend == "file"
    assert cfg.producer_store.file.path.endswith("/producers.json")
    assert cfg.producer_store.postgres == {}
