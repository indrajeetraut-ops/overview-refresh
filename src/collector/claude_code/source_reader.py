"""Claude Code source-reader: manifest -> grouped sessions -> SessionSource (spec:
docs/modules/source-adapter.md §2, §3, §8; batch-uploader.md §6 for the manifest/layout
this reads).

Split into two steps on purpose: group() is cheap (rel-path matching over the
already-in-memory manifest, no reads) so the engine can check a source's checkpoint/identity
*before* deciding whether read_session_source()'s actual I/O is worth doing at all (doc
§4.1's unchanged-source skip). Mirrors local_loader.py's recognition of the
`<session>.jsonl` + sibling `<session>/subagents/agent-<id>.jsonl`/`.meta.json` layout, but
matched against manifest `rel` strings instead of real filesystem globs, and reading via
SourceStore instead of Path.open() -- kept as a separate implementation rather than forcing
a shared abstraction over those two different listing mechanisms.

Discovering *which* subagents exist and their `meta` is eager (cheap: rel-path matching +
small `.meta.json` reads); each SubagentStream.records is lazy (functools.partial over the
store + key), so a session with many subagents never holds more than one branch's content in
memory at a time (normalizer/models.py's bounded-memory fix).

Defensively skips OS metadata entries (`.DS_Store`, AppleDouble `._*` sidecars) that
shouldn't reach a manifest at all now that the uploader filters them at unpack time
(server/uploader/unpack.py) -- kept here too since a manifest written before that fix still
lists them, and a vendor's archive could carry similar junk regardless.

Repo identity travels per-file on the manifest now (`entry.repo_url`/`entry.repo`, the new Go
uploader's `../upload`), not via a sidecar this reader used to read separately -- see
`group()`'s `repo=` line. A manifest is also now multi-vendor; `group()` filters to
`claude_code` entries (a `None` vendor means an old-generation manifest, treated as
`claude_code`) so a Codex file is never handed to this normalizer.
"""
from __future__ import annotations

import functools
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import structlog

from collector.normalizer.models import SessionSource, SubagentStream
from common.manifest import Manifest, ManifestFileEntry

from ..store import SourceStore

log = structlog.get_logger(module="collector.claude_code.source_reader")


@dataclass
class SessionGroup:
    """A main session file plus enough to find its checkpoint and, later, its subagents."""

    session_id: str  # Path(rel).stem, e.g. "session-parent"
    rel: str  # archive-relative path -- the logical-key basis (source-adapter.md §2)
    full_key: str  # store key including the manifest's own prefix -- for actual reads
    session_dir: str  # rel without ".jsonl" -- where this session's subagents/ would live
    repo: str | None  # entry.repo_url or entry.repo; None if neither was resolved


def _is_os_metadata_rel(rel: str) -> bool:
    basename = rel.rsplit("/", 1)[-1]
    return basename == ".DS_Store" or basename.startswith("._")


def _read_jsonl_via_store(store: SourceStore, key: str) -> list[dict]:
    with store.open(key, 0, None) as f:
        content = f.read()
    records = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _direct_subagent_files(
    all_files: list[ManifestFileEntry], parent_dir: str
) -> dict[str, dict[str, ManifestFileEntry]]:
    """{agent_id: {"jsonl": entry, "meta": entry|None}} for DIRECT children of parent_dir
    only -- a deeper-nested file's remainder still contains a "/" and is excluded, picked up
    only when the recursion reaches that child's own directory."""
    prefix = f"{parent_dir}/subagents/"
    result: dict[str, dict[str, ManifestFileEntry]] = {}
    for f in all_files:
        if not f.rel.startswith(prefix) or _is_os_metadata_rel(f.rel):
            continue
        remainder = f.rel[len(prefix) :]
        if "/" in remainder:
            continue
        if remainder.endswith(".meta.json"):
            result.setdefault(remainder[: -len(".meta.json")], {})["meta"] = f
        elif remainder.endswith(".jsonl"):
            result.setdefault(remainder[: -len(".jsonl")], {})["jsonl"] = f
    return result


def _build_subagent_streams(
    store: SourceStore, prefix: str, all_files: list[ManifestFileEntry], parent_dir: str
) -> dict[str, SubagentStream]:
    streams: dict[str, SubagentStream] = {}
    for agent_id, parts in _direct_subagent_files(all_files, parent_dir).items():
        jsonl_entry = parts.get("jsonl")
        if jsonl_entry is None:
            continue  # a stray .meta.json with no matching .jsonl -- nothing to normalize
        meta: dict = {}
        meta_entry = parts.get("meta")
        if meta_entry is not None:
            meta_key = prefix + meta_entry.key
            with store.open(meta_key, 0, None) as f:
                meta = json.loads(f.read())
        tool_use_id = meta.get("toolUseId")
        if not tool_use_id:
            log.warning("subagent_missing_tool_use_id", rel=jsonl_entry.rel)
            continue
        jsonl_key = prefix + jsonl_entry.key
        streams[tool_use_id] = SubagentStream(
            agent_id=meta.get("agentId", agent_id),
            tool_use_id=tool_use_id,
            meta=meta,
            records=functools.partial(_read_jsonl_via_store, store, jsonl_key),
            subagent_streams=_build_subagent_streams(
                store, prefix, all_files, f"{parent_dir}/subagents/{agent_id}"
            ),
        )
    return streams


class ClaudeCodeSourceReader:
    vendor = "claude_code"

    def __init__(self, store: SourceStore) -> None:
        self.store = store

    def group(self, manifest: Manifest, prefix: str) -> list[SessionGroup]:
        """prefix is everything before "manifest.json" in the manifest's own S3 key (e.g.
        "cc/logs/uploads/ep-1/") -- correct by construction under any base prefix, present or
        future, rather than reconstructed from a hardcoded "uploads/{id}/" join."""
        groups = []
        skipped_vendors: Counter[str] = Counter()
        for f in manifest.files:
            if not f.rel.endswith(".jsonl") or "/subagents/" in f.rel or _is_os_metadata_rel(f.rel):
                continue
            # A None vendor means an old-generation manifest (no per-file vendor field) --
            # treated as claude_code. A new-generation manifest's non-claude_code entries
            # (e.g. codex) are excluded here rather than handed to this normalizer.
            if f.vendor is not None and f.vendor != self.vendor:
                skipped_vendors[f.vendor] += 1
                continue
            # f.state ("live"/"stale") is deliberately unread: it means "did this file's
            # bytes change since the last manifest write," not "has the collector already
            # processed it." A file uploaded before the collector first ran would be stale
            # forever and would never be collected if state gated this. The checkpoint
            # identity (store.py's size+head-hash) is the authoritative "already processed"
            # signal and already handles re-runs correctly on its own.
            groups.append(
                SessionGroup(
                    session_id=Path(f.rel).stem,
                    rel=f.rel,
                    full_key=prefix + f.key,
                    session_dir=f.rel[: -len(".jsonl")],
                    repo=f.repo_url or f.repo,
                )
            )
        for vendor_name, count in skipped_vendors.items():
            log.info("manifest_vendor_skipped", vendor=vendor_name, count=count)
        return groups

    def identity(self, group: SessionGroup) -> str:
        # TODO(collector-meta-checkpoint): bytes-only. talium-sync >= dae4f26 rewrites a manifest
        # when only an entry's repo/repo_url/branch changes (the backfill for pre-Part-A uploads),
        # but that leaves this fingerprint unchanged, so the session is skipped and keeps its
        # `local:<cwd>` repo. Fold the entry's repo metadata in here so such a change re-ships.
        return self.store.identity(group.full_key)

    def read_session_source(
        self, manifest: Manifest, group: SessionGroup, prefix: str, *, source: str = "batch"
    ) -> SessionSource:
        records = _read_jsonl_via_store(self.store, group.full_key)
        agent_version = next((r["version"] for r in records if r.get("version")), None)
        return SessionSource(
            vendor=self.vendor,
            session_id=group.session_id,
            source=source,
            agent_version=agent_version,
            records=records,
            subagent_streams=_build_subagent_streams(
                self.store, prefix, manifest.files, group.session_dir
            ),
        )
