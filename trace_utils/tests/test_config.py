import pytest

from trace_utils.config import load_config


def _write(tmp_path, text):
    p = tmp_path / "cfg.yaml"
    p.write_text(text)
    return p


def test_load_and_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret123")
    cfg = _write(tmp_path, """
datasets:
  - name: a
    url: https://huggingface.co/datasets/org/name
  - url: hf://datasets/org2/name2
    subpath: sessions/codex
source:
  mode: download
  cache_dir: /tmp/x
  hf_token_env: ${MY_TOKEN}
""")
    c = load_config(cfg)
    assert len(c.datasets) == 2
    assert c.datasets[0].name == "a"
    assert c.datasets[1].name == "hf://datasets/org2/name2"  # name defaults to url
    assert c.datasets[1].subpath == "sessions/codex"
    assert c.source.mode == "download"
    assert c.source.hf_token_env == "secret123"  # ${MY_TOKEN} interpolated


def test_missing_url_raises(tmp_path):
    cfg = _write(tmp_path, "datasets:\n  - name: a\n")
    with pytest.raises(ValueError):
        load_config(cfg)


def test_bad_mode_raises(tmp_path):
    cfg = _write(tmp_path, "source:\n  mode: bogus\n")
    with pytest.raises(ValueError):
        load_config(cfg)


def test_dataset_lookup(tmp_path):
    cfg = _write(tmp_path, "datasets:\n  - name: a\n    url: org/name\n")
    c = load_config(cfg)
    assert c.dataset("a").url == "org/name"
    assert c.dataset("missing") is None
