import os

from server.storage.config import StorageConfig, load_config


def test_defaults():
    cfg = StorageConfig(dsn="postgresql+psycopg://x/db")
    assert cfg.pool_size == 5
    assert cfg.pool_pre_ping is True
    assert cfg.queries_dir == "config/queries"


def test_load_interpolates_env(tmp_path):
    os.environ["TALIUM_TEST_DSN_X"] = "postgresql+psycopg://u@h/econ"
    f = tmp_path / "storage.yaml"
    f.write_text("storage:\n  dsn: ${TALIUM_TEST_DSN_X}\n  pool_size: 9\n")
    cfg = load_config(f)
    assert cfg.dsn == "postgresql+psycopg://u@h/econ"
    assert cfg.pool_size == 9
