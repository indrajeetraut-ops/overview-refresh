import io

import pytest
from fixtures.uploader import archives

from server.uploader.errors import MalformedArchiveError, UploadTooLargeError
from server.uploader.unpack import stream_unpack


def test_normal_archive(local_store):
    files, total_bytes = stream_unpack(
        io.BytesIO(archives.build_normal_archive()),
        local_store,
        "up1",
        max_uncompressed_bytes=10_000_000,
    )
    rels = sorted(f.rel for f in files)
    assert rels == [
        "-Users-dev-repo/session-aaa.jsonl",
        "-Users-dev-repo/session-bbb.jsonl",
    ]
    for f in files:
        assert f.key == f"raw/projects/{f.rel}"
        assert local_store.exists(f"uploads/up1/{f.key}")
    assert total_bytes == sum(f.bytes for f in files)


def test_macos_metadata_entries_are_skipped(local_store):
    files, _ = stream_unpack(
        io.BytesIO(archives.build_archive_with_macos_metadata()),
        local_store,
        "up_macos",
        max_uncompressed_bytes=10_000_000,
    )
    rels = sorted(f.rel for f in files)
    assert rels == ["-Users-dev-repo/session-aaa.jsonl"]  # ._session-aaa.jsonl, .DS_Store dropped
    written_keys = local_store.list_prefix("uploads/up_macos")
    assert all("._" not in key and ".DS_Store" not in key for key in written_keys)


def test_subagents_archive_colocated(local_store):
    files, _ = stream_unpack(
        io.BytesIO(archives.build_subagents_archive()),
        local_store,
        "up2",
        max_uncompressed_bytes=10_000_000,
    )
    rels = sorted(f.rel for f in files)
    assert rels == [
        "-Users-dev-repo/session-parent.jsonl",
        "-Users-dev-repo/session-parent/subagents/agent-1.jsonl",
        "-Users-dev-repo/session-parent/subagents/agent-1.meta.json",
    ]


@pytest.mark.parametrize(
    "kind,expected_reason",
    [
        ("bad_gzip", "not_gzip"),
        ("empty", "empty_archive"),
        ("truncated", "truncated_tar"),
    ],
)
def test_malformed_archive(local_store, kind, expected_reason):
    with pytest.raises(MalformedArchiveError) as exc_info:
        stream_unpack(
            io.BytesIO(archives.build_malformed_archive(kind)),
            local_store,
            "up3",
            max_uncompressed_bytes=10_000_000,
        )
    assert exc_info.value.reason == expected_reason


@pytest.mark.parametrize(
    "kind,expected_reason",
    [
        ("absolute", "absolute_path"),
        ("traversal", "path_traversal"),
        ("symlink", "symlink_entry"),
        ("hardlink", "symlink_entry"),
    ],
)
def test_path_traversal_rejected(local_store, kind, expected_reason):
    with pytest.raises(MalformedArchiveError) as exc_info:
        stream_unpack(
            io.BytesIO(archives.build_path_traversal_archive(kind)),
            local_store,
            "up4",
            max_uncompressed_bytes=10_000_000,
        )
    assert exc_info.value.reason == expected_reason
    # Confinement (doc §7): nothing was ever written outside the upload's own prefix.
    written_keys = local_store.list_prefix("uploads/up4")
    assert all(key.startswith("uploads/up4/raw/projects/") for key in written_keys)


def test_uncompressed_size_cap(local_store):
    with pytest.raises(UploadTooLargeError):
        stream_unpack(
            io.BytesIO(archives.build_normal_archive()),
            local_store,
            "up5",
            max_uncompressed_bytes=1,
        )
