from server.uploader.config import UploaderConfig, load_config


def test_defaults():
    cfg = UploaderConfig(
        object_store={"backend": "local", "local": {"root": "/tmp/x"}}
    )
    assert cfg.limits.max_upload_bytes == 5 * 1024**3
    assert cfg.limits.upload_timeout_s == 900.0
    assert cfg.http.port == 8080


def test_load_config_interpolates_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TALIUM_DATA_DIR", str(tmp_path))
    yaml_file = tmp_path / "uploader.yaml"
    yaml_file.write_text(
        "object_store:\n"
        "  backend: local\n"
        "  local:\n"
        "    root: ${TALIUM_DATA_DIR}/uploads\n"
    )
    cfg = load_config(yaml_file)
    assert cfg.object_store.backend == "local"
    assert str(cfg.object_store.local.root) == f"{tmp_path}/uploads"


def test_shipped_default_config_parses(monkeypatch):
    monkeypatch.setenv("TALIUM_DATA_DIR", "/tmp/talium")
    cfg = load_config("config/uploader.yaml")
    assert cfg.object_store.backend == "local"
    assert cfg.limits.max_upload_bytes == 5368709120
