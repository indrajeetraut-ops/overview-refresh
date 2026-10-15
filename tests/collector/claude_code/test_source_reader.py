import json

import pytest

from collector.claude_code.source_reader import ClaudeCodeSourceReader
from collector.store import LocalFsSourceStore
from common.manifest import DeclaredFields, Manifest, ManifestFileEntry

UPLOAD_PREFIX = "uploads/u1/"


def _write(root, key: str, content: bytes) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _make_manifest(files: list[ManifestFileEntry], *, generation: str = "legacy", scope_id: str = "u1") -> Manifest:
    """generation="legacy" writes upload_id (the old Python uploader); generation="new" writes
    endpoint_id (the Go uploader, ../upload) -- every existing case below is parametrized over
    both so the collector's dual-generation compat (integration plan §B2) stays covered."""
    kwargs = dict(
        received_ts="2026-07-22T00:00:00Z",
        declared=DeclaredFields(org_id="acme", user_id="anil"),
        producer_id="prd1",
        file_count=len(files),
        files=files,
        uploader_version="0.1.0",
    )
    if generation == "legacy":
        kwargs["upload_id"] = scope_id
    elif generation == "new":
        kwargs["endpoint_id"] = scope_id
        kwargs["vendors"] = ["claude_code"]
    else:
        raise ValueError(generation)
    return Manifest(**kwargs)


@pytest.fixture(params=["legacy", "new"])
def generation(request):
    return request.param


def test_group_finds_the_main_session_file(tmp_path, generation):
    rel = "-Users-dev-repo/session-aaa.jsonl"
    key = f"raw/projects/{rel}"
    content = b'{"type": "user", "uuid": "evt-1", "message": {"role": "user", "content": "hi"}}\n'
    _write(tmp_path, f"uploads/u1/{key}", content)
    manifest = _make_manifest([ManifestFileEntry(key=key, rel=rel, bytes=len(content))], generation=generation)

    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    groups = reader.group(manifest, UPLOAD_PREFIX)

    assert len(groups) == 1
    assert groups[0].session_id == "session-aaa"
    assert groups[0].rel == rel
    assert groups[0].full_key == f"uploads/u1/{key}"


def test_group_skips_macos_metadata_entries(tmp_path, generation):
    """A manifest written before the uploader's AppleDouble/.DS_Store filter (unpack.py)
    still lists this junk -- the reader must skip it rather than trying to JSON-parse
    binary resource-fork data as a session file."""
    real_rel = "-Users-dev-repo/session-aaa.jsonl"
    junk_rel = "-Users-dev-repo/._session-aaa.jsonl"
    ds_store_rel = "-Users-dev-repo/.DS_Store"
    real_content = b'{"type": "user", "uuid": "evt-1"}\n'
    _write(tmp_path, f"uploads/u1/raw/projects/{real_rel}", real_content)
    _write(tmp_path, f"uploads/u1/raw/projects/{junk_rel}", b"\x00\x05\x16\x07not json")
    _write(tmp_path, f"uploads/u1/raw/projects/{ds_store_rel}", b"\x00\x00\x00\x01Bud1")
    files = [
        ManifestFileEntry(key=f"raw/projects/{real_rel}", rel=real_rel, bytes=len(real_content)),
        ManifestFileEntry(key=f"raw/projects/{junk_rel}", rel=junk_rel, bytes=9),
        ManifestFileEntry(key=f"raw/projects/{ds_store_rel}", rel=ds_store_rel, bytes=8),
    ]
    manifest = _make_manifest(files, generation=generation)

    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    groups = reader.group(manifest, UPLOAD_PREFIX)

    assert len(groups) == 1
    assert groups[0].rel == real_rel


def test_group_excludes_subagent_files(tmp_path, generation):
    main_rel = "-Users-dev-repo/session-parent.jsonl"
    sub_rel = "-Users-dev-repo/session-parent/subagents/agent-1.jsonl"
    meta_rel = "-Users-dev-repo/session-parent/subagents/agent-1.meta.json"
    files = [
        ManifestFileEntry(key=f"raw/projects/{main_rel}", rel=main_rel, bytes=1),
        ManifestFileEntry(key=f"raw/projects/{sub_rel}", rel=sub_rel, bytes=1),
        ManifestFileEntry(key=f"raw/projects/{meta_rel}", rel=meta_rel, bytes=1),
    ]
    manifest = _make_manifest(files, generation=generation)

    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    groups = reader.group(manifest, UPLOAD_PREFIX)

    assert len(groups) == 1
    assert groups[0].session_id == "session-parent"


def test_group_skips_non_claude_vendor(tmp_path, generation):
    """A multi-vendor manifest (the new Go uploader can carry Codex files too) must not hand
    a non-Claude entry to this normalizer. A None vendor (every legacy-generation entry, since
    the old Python uploader never wrote a per-file vendor) is treated as claude_code."""
    claude_rel = "-Users-dev-repo/session-aaa.jsonl"
    codex_rel = "2026/09/14/rollout-x.jsonl"
    files = [
        ManifestFileEntry(key=f"raw/projects/{claude_rel}", rel=claude_rel, bytes=1, vendor="claude_code"),
        ManifestFileEntry(key=f"codex/raw/{codex_rel}", rel=codex_rel, bytes=1, vendor="codex"),
    ]
    manifest = _make_manifest(files, generation=generation)

    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    groups = reader.group(manifest, UPLOAD_PREFIX)

    assert len(groups) == 1
    assert groups[0].rel == claude_rel


def test_group_none_vendor_entry_is_included(tmp_path):
    """An old-generation manifest's entries carry vendor=None (no per-file vendor at all) --
    must still be included, not mistaken for "not claude_code"."""
    rel = "-Users-dev-repo/session-aaa.jsonl"
    files = [ManifestFileEntry(key=f"raw/projects/{rel}", rel=rel, bytes=1, vendor=None)]
    manifest = _make_manifest(files, generation="legacy")

    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    groups = reader.group(manifest, UPLOAD_PREFIX)

    assert len(groups) == 1


def test_group_carries_repo_preferring_repo_url(tmp_path):
    rel_both = "-Users-dev-a/session-a.jsonl"
    rel_repo_only = "-Users-dev-b/session-b.jsonl"
    rel_neither = "-Users-dev-c/session-c.jsonl"
    files = [
        ManifestFileEntry(
            key=f"raw/projects/{rel_both}", rel=rel_both, bytes=1,
            repo="acme/widgets", repo_url="git@github.com:acme/widgets.git",
        ),
        ManifestFileEntry(key=f"raw/projects/{rel_repo_only}", rel=rel_repo_only, bytes=1, repo="acme/gadgets"),
        ManifestFileEntry(key=f"raw/projects/{rel_neither}", rel=rel_neither, bytes=1),
    ]
    manifest = _make_manifest(files, generation="new")

    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    groups = {g.rel: g.repo for g in reader.group(manifest, UPLOAD_PREFIX)}

    assert groups[rel_both] == "git@github.com:acme/widgets.git"
    assert groups[rel_repo_only] == "acme/gadgets"
    assert groups[rel_neither] is None


def test_reads_resolve_under_a_base_prefix(tmp_path):
    """A manifest discovered at cc/logs/uploads/ep-1/manifest.json (a base prefix configured on
    the uploader) must read its files from cc/logs/uploads/ep-1/<key> -- derived from the
    manifest's own discovered location, never a hardcoded uploads/{id}/ join."""
    prefix = "cc/logs/uploads/ep-1/"
    rel = "-Users-dev-repo/session-aaa.jsonl"
    key = f"raw/projects/{rel}"
    content = b'{"type": "user", "uuid": "evt-1"}\n'
    _write(tmp_path, f"{prefix}{key}", content)
    manifest = _make_manifest([ManifestFileEntry(key=key, rel=rel, bytes=len(content))], generation="new", scope_id="ep-1")

    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    groups = reader.group(manifest, prefix)

    assert len(groups) == 1
    assert groups[0].full_key == f"{prefix}{key}"
    assert reader.identity(groups[0]) == LocalFsSourceStore(tmp_path).identity(f"{prefix}{key}")

    session_source = reader.read_session_source(manifest, groups[0], prefix)
    assert session_source.session_id == "session-aaa"


def test_read_session_source_builds_lazy_subagent_tree_with_nesting(tmp_path, generation):
    main_rel = "-Users-dev-repo/session-parent.jsonl"
    main_content = (
        b'{"type": "user", "uuid": "evt-1", "cwd": "/repo", "gitBranch": "main", '
        b'"message": {"role": "user", "content": "go"}}\n'
    )

    child_rel = "-Users-dev-repo/session-parent/subagents/agent-1.jsonl"
    child_content = b'{"type": "user", "uuid": "sub-evt-1", "message": {"role": "user", "content": "explore"}}\n'
    child_meta_rel = "-Users-dev-repo/session-parent/subagents/agent-1.meta.json"
    child_meta = json.dumps({"agentId": "agent-1", "toolUseId": "toolu_1", "agentType": "Explore"}).encode()

    grandchild_rel = "-Users-dev-repo/session-parent/subagents/agent-1/subagents/agent-2.jsonl"
    grandchild_content = b'{"type": "user", "uuid": "gc-evt-1", "message": {"role": "user", "content": "deeper"}}\n'
    grandchild_meta_rel = "-Users-dev-repo/session-parent/subagents/agent-1/subagents/agent-2.meta.json"
    grandchild_meta = json.dumps({"agentId": "agent-2", "toolUseId": "toolu_2", "agentType": "Explore"}).encode()

    files = []
    for rel, content in (
        (main_rel, main_content),
        (child_rel, child_content),
        (child_meta_rel, child_meta),
        (grandchild_rel, grandchild_content),
        (grandchild_meta_rel, grandchild_meta),
    ):
        key = f"raw/projects/{rel}"
        _write(tmp_path, f"uploads/u1/{key}", content)
        files.append(ManifestFileEntry(key=key, rel=rel, bytes=len(content)))

    manifest = _make_manifest(files, generation=generation)
    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    group = reader.group(manifest, UPLOAD_PREFIX)[0]

    session_source = reader.read_session_source(manifest, group, UPLOAD_PREFIX)

    assert session_source.session_id == "session-parent"
    assert len(session_source.records) == 1
    assert set(session_source.subagent_streams) == {"toolu_1"}

    child = session_source.subagent_streams["toolu_1"]
    assert child.agent_id == "agent-1"
    assert len(child.records()) == 1  # lazy -- content only read on call
    assert set(child.subagent_streams) == {"toolu_2"}

    grandchild = child.subagent_streams["toolu_2"]
    assert grandchild.agent_id == "agent-2"
    assert len(grandchild.records()) == 1


def test_subagent_without_matching_jsonl_is_skipped(tmp_path, generation):
    main_rel = "-Users-dev-repo/session-parent.jsonl"
    _write(tmp_path, f"uploads/u1/raw/projects/{main_rel}", b'{"type": "user", "uuid": "evt-1"}\n')
    stray_meta_rel = "-Users-dev-repo/session-parent/subagents/agent-1.meta.json"
    _write(
        tmp_path,
        f"uploads/u1/raw/projects/{stray_meta_rel}",
        json.dumps({"agentId": "agent-1", "toolUseId": "toolu_1"}).encode(),
    )
    files = [
        ManifestFileEntry(key=f"raw/projects/{main_rel}", rel=main_rel, bytes=1),
        ManifestFileEntry(key=f"raw/projects/{stray_meta_rel}", rel=stray_meta_rel, bytes=1),
    ]
    manifest = _make_manifest(files, generation=generation)
    reader = ClaudeCodeSourceReader(LocalFsSourceStore(tmp_path))
    group = reader.group(manifest, UPLOAD_PREFIX)[0]

    session_source = reader.read_session_source(manifest, group, UPLOAD_PREFIX)

    assert session_source.subagent_streams == {}


def test_identity_matches_the_store_fingerprint_of_the_main_file(tmp_path, generation):
    rel = "-Users-dev-repo/session-aaa.jsonl"
    key = f"raw/projects/{rel}"
    content = b'{"type": "user", "uuid": "evt-1"}\n'
    _write(tmp_path, f"uploads/u1/{key}", content)
    manifest = _make_manifest([ManifestFileEntry(key=key, rel=rel, bytes=len(content))], generation=generation)

    store = LocalFsSourceStore(tmp_path)
    reader = ClaudeCodeSourceReader(store)
    group = reader.group(manifest, UPLOAD_PREFIX)[0]

    assert reader.identity(group) == store.identity(group.full_key)
