import pytest

from trace_utils import sources
from trace_utils.config import Dataset, Source
from trace_utils.sources import Selector, parse_repo, resolve


@pytest.mark.parametrize("url,repo,sub", [
    ("https://huggingface.co/datasets/org/name", "org/name", None),
    ("https://huggingface.co/datasets/org/name/tree/main/sessions/codex", "org/name", "sessions/codex"),
    ("hf://datasets/org/name/data", "org/name", "data"),
    ("org/name", "org/name", None),
])
def test_parse_repo(url, repo, sub):
    assert parse_repo(url) == (repo, sub)


@pytest.fixture
def fake_repo(monkeypatch):
    files = [
        "README.md", "manifest.jsonl",  # root-level non-data + metadata
        "data/a.jsonl", "data/b.jsonl",
        "sessions/codex/x.jsonl",
    ]

    class FakeApi:
        def __init__(self, *a, **k): ...
        def list_repo_files(self, repo_id, repo_type=None):
            return files

    monkeypatch.setattr(sources, "HfApi", FakeApi)
    return files


def test_subdir_preference_drops_root_manifest(fake_repo):
    ds = Dataset(name="t", url="org/name")
    rs = resolve(ds, Source(mode="stream"), Selector())
    # manifest.jsonl (root) excluded; only subdir files kept
    assert all("/" in f for f in rs.files)
    assert "manifest.jsonl" not in rs.files
    assert rs.fmt == "jsonl"
    assert rs.uris[0].startswith("hf://datasets/org/name/")


def test_glob_selector(fake_repo):
    ds = Dataset(name="t", url="org/name")
    rs = resolve(ds, Source(mode="stream"), Selector(glob="sessions/codex/*.jsonl"))
    assert rs.files == ["sessions/codex/x.jsonl"]


def test_session_selector_matches_filename(fake_repo):
    ds = Dataset(name="t", url="org/name")
    rs = resolve(ds, Source(mode="stream"), Selector(session="a"))
    assert rs.files == ["data/a.jsonl"]
    assert rs.session_filter is None


def test_subpath_scopes_and_no_match_raises(fake_repo):
    ds = Dataset(name="t", url="org/name", subpath="sessions/codex")
    rs = resolve(ds, Source(mode="stream"), Selector())
    assert rs.files == ["sessions/codex/x.jsonl"]
    with pytest.raises(ValueError):
        resolve(ds, Source(mode="stream"), Selector(glob="nope/*.jsonl"))
