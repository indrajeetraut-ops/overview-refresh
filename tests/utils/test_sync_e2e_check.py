"""The pure halves of utils/sync_e2e_check.py: manifest discovery and summary."""
import importlib.util
import sys
from pathlib import Path

from common.manifest import Manifest

# Loaded by path: `utils` on the import path is tests/utils (this package), not the repo's
# top-level utils/ scripts directory.
_SPEC = importlib.util.spec_from_file_location(
    "sync_e2e_check", Path(__file__).resolve().parents[2] / "utils" / "sync_e2e_check.py")
_mod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _mod  # @dataclass resolves its module through sys.modules
_SPEC.loader.exec_module(_mod)
find_manifest_keys = _mod.find_manifest_keys
summarize_manifest = _mod.summarize_manifest


def _manifest(**over) -> Manifest:
    base = {
        "endpoint_id": "host-abc",
        "received_ts": "2026-09-23T00:00:00Z",
        "declared": {"org_id": "local-dev", "user_id": "anil"},
        "producer_id": "p",
        "file_count": 3,
        "uploader_version": "0.2.0",
        "files": [
            {"key": "claude/raw/-a-b/s1.jsonl", "rel": "-a-b/s1.jsonl", "bytes": 10,
             "vendor": "claude_code", "session_id": "s1",
             "repo": "o/r", "repo_url": "git@gitlab.com:o/r.git"},
            {"key": "claude/raw/-a-b/s2.jsonl", "rel": "-a-b/s2.jsonl", "bytes": 5,
             "vendor": "claude_code", "session_id": "s2"},
            {"key": "codex/raw/x.jsonl", "rel": "x.jsonl", "bytes": 1, "vendor": "codex"},
        ],
    }
    base.update(over)
    return Manifest.model_validate(base)


def test_find_manifest_keys_under_any_base_prefix():
    keys = [
        "cc/logs/uploads/ep-1/manifest.json",
        "uploads/u-2/manifest.json",
        "cc/logs/uploads/ep-1/claude/raw/-a/manifest.json.jsonl",
        "cc/logs/tus-tmp/abc",
        "cc/logs/uploads/ep-1/claude/raw/-a/nested/manifest.json",  # a session file, not ours
    ]
    assert find_manifest_keys(keys) == [
        ("cc/logs/uploads/ep-1/", "cc/logs/uploads/ep-1/manifest.json"),
        ("uploads/u-2/", "uploads/u-2/manifest.json"),
    ]


def test_summary_counts_claude_files_and_prefers_repo_url():
    s = summarize_manifest(_manifest())
    assert s.generation == "go" and s.scope_id == "host-abc"
    assert s.vendors == {"claude_code": 2, "codex": 1}
    assert s.claude_files == 2
    assert (s.with_repo_url, s.with_repo) == (1, 1)
    assert dict(s.repos) == {"git@gitlab.com:o/r.git": 1}
    assert s.session_ids == {"s1", "s2"}


def test_summary_treats_legacy_manifest_as_claude_code():
    m = _manifest(endpoint_id=None, upload_id="u-1",
                  files=[{"key": "raw/projects/-a/s.jsonl", "rel": "-a/s.jsonl", "bytes": 1}],
                  file_count=1)
    s = summarize_manifest(m)
    assert s.generation == "python" and s.scope_id == "u-1"
    assert s.claude_files == 1 and s.with_repo == 0
