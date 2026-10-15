from server.api.config import ApiConfig, load_config


def test_defaults():
    cfg = ApiConfig()
    assert cfg.http.port == 8090
    assert cfg.storage_config == "config/storage.yaml"
    assert cfg.insights.min_sessions == 5
    assert cfg.story.min_sessions_for_band == 5
    assert cfg.dashboard.precedence.tokens == ["otel_measures", "otel_ledger", "jsonl_models"]
    assert cfg.dashboard.precedence.cost == ["otel_vendor", "estimated"]


def test_load_config_interpolates_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TALIUM_API_PORT_TEST", "9999")
    yaml_file = tmp_path / "api.yaml"
    yaml_file.write_text(
        "api:\n"
        "  http:\n"
        "    port: ${TALIUM_API_PORT_TEST}\n"
    )
    cfg = load_config(yaml_file)
    assert cfg.http.port == 9999


def test_shipped_default_config_parses():
    cfg = load_config("config/api.yaml")
    assert cfg.http.port == 8090
    assert cfg.storage_config == "config/storage.yaml"
    assert cfg.dashboard.flags.costly_usd_floor == 5.0


def test_pricing_fields_default_none_and_legacy_key_still_loads(tmp_path):
    from server.ingest.config import IngestConfig
    from server.worker.config import WorkerConfig

    for cls in (ApiConfig, WorkerConfig, IngestConfig):
        cfg = cls()
        assert cfg.pricing_org is None
        assert cfg.pricing_version is None
        # the legacy path key is gone from every service config (#68 T3/T4)
        assert not hasattr(cfg, "pricing_config")

    yaml_file = tmp_path / "api.yaml"
    yaml_file.write_text(
        "api:\n  pricing_config: whatever.yaml\n  pricing_org: talium.ai\n"
        "  pricing_version: 2026-09-25-d3e5c412\n"
    )
    cfg = load_config(yaml_file)
    assert cfg.pricing_org == "talium.ai"
    assert cfg.pricing_version == "2026-09-25-d3e5c412"
    assert load_config("config/api.yaml").pricing_org is None
