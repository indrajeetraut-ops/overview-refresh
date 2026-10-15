import pytest
from pydantic import ValidationError

from common.manifest import DeclaredFields, Manifest, ManifestFileEntry


def _base_kwargs(**overrides) -> dict:
    kwargs = dict(
        received_ts="2026-09-22T00:00:00Z",
        declared=DeclaredFields(org_id="acme", user_id="anil"),
        producer_id="prd1",
        file_count=0,
        files=[],
        uploader_version="0.1.0",
    )
    kwargs.update(overrides)
    return kwargs


def test_manifest_accepts_endpoint_id():
    m = Manifest(**_base_kwargs(endpoint_id="ep-1"))
    assert m.upload_id is None
    assert m.endpoint_id == "ep-1"
    assert m.scope_id == "ep-1"


def test_manifest_accepts_legacy_upload_id():
    m = Manifest(**_base_kwargs(upload_id="u1"))
    assert m.endpoint_id is None
    assert m.upload_id == "u1"
    assert m.scope_id == "u1"


def test_manifest_rejects_neither_id():
    with pytest.raises(ValidationError):
        Manifest(**_base_kwargs())


def test_manifest_ignores_unknown_fields():
    data = _base_kwargs(endpoint_id="ep-1")
    data["some_future_field"] = "whatever"
    m = Manifest.model_validate(data)
    assert m.endpoint_id == "ep-1"


def test_manifest_file_entry_new_fields_are_optional():
    entry = ManifestFileEntry(key="claude/raw/a.jsonl", rel="a.jsonl", bytes=10)
    assert entry.vendor is None
    assert entry.session_id is None
    assert entry.repo is None
    assert entry.repo_url is None
    assert entry.branch is None
    assert entry.state is None
    assert entry.active is False


def test_manifest_file_entry_new_generation_fields_round_trip():
    entry = ManifestFileEntry(
        key="claude/raw/a.jsonl",
        rel="a.jsonl",
        bytes=10,
        vendor="claude_code",
        session_id="s1",
        repo="acme/widgets",
        repo_url="git@github.com:acme/widgets.git",
        branch="main",
        state="live",
        active=True,
        mtime="2026-09-22T00:00:00Z",
    )
    assert entry.repo_url == "git@github.com:acme/widgets.git"
    assert entry.active is True
