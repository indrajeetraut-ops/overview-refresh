"""Fixture-prep tool: turn a *real* Claude Code session tree into a committable,
sanitized normalizer fixture (spec: docs/modules/normalizer.md §12 "recorded CC sessions").

Real `~/.claude/projects/<...>/<session>.jsonl` files carry the developer's actual work --
file contents, absolute home paths, and the occasional credential -- so they are never
committed verbatim. This script produces a structure-preserving, content-scrubbed copy:

  * Every record keeps its exact keys, ordering, and structural ids (`uuid`, `parentUuid`,
    `tool_use.id`, `tool_use_id`, subagent `toolUseId`) -- the normalizer's correlation and
    shape depend on those, so they must survive untouched.
  * Redaction runs on every *string leaf*: home paths, e-mail addresses, and common
    credential shapes (sk-/ghp_/AWS/Slack/bearer/`key=value` secrets) collapse to a
    placeholder. Over-redaction is safe here; under-redaction is not.
  * Oversized string leaves are truncated to CAP chars. Real sessions bury a few
    100-KB pasted blobs that would bloat the repo and widen the content-exposure surface
    for no test value -- the *shape* they carry is unchanged by trimming their bytes.

Re-run it whenever the fixtures need refreshing:

    python tests/fixtures/normalizer/recorded/sanitize.py \
        --source-root /Users/anil/Downloads/sumandra-projects-talium-code-econ

It writes into this same directory. `git diff` the result before committing.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# --- what the fixtures are built from -------------------------------------------------------
# (destination name -> source session id). The regular session is the one normalizer.md §6
# already uses as its worked example; the subagent session is the smallest real spawner.
_FIXTURES = {
    "session-regular": "05c3d17f-3d62-4739-9355-0f03939c6003",
    "session-subagents": "0f5e45b9-ecc1-4773-913c-86cd341369d1",
    # 27-task run with many turns + subagent spawns -- the worker plan's turn-heavy fixture
    # (docs/plan/2026-07-27-worker-session-analytics.md §7 P4-P6), used by the fold module's
    # tests, not the normalizer's own RECORDED_NAMES/shape-contract set.
    "session-turns": "1cc0fed2-e532-4611-89cc-aa96d3af0e72",
    # Current-client shape: record 0 is `queue-operation` (no cwd/gitBranch/timestamp), unlike
    # the v2.1.214-era fixtures above where record 0 is a `user` record. Regression fixture for
    # the session_start-context fix (normalizer.md §6.2) -- without it, every fixture in this
    # set happened to hit the case that always worked.
    "session-queue-op-first": "b1b61b7d-8feb-4cc0-872c-3641d1aefb7a",
}

_CAP = 300  # max chars kept per string leaf; longer values are trimmed with a marker.
_LIST_CAP = 40  # max elements kept per list; a real session's giant result arrays (a 500-line
#                 dir listing, a 300-hunk patch) carry no *shape* past their first few entries.

# structural ids that redaction/truncation must never touch (correlation + dedup keys).
_STRUCTURAL_KEYS = {"uuid", "parentUuid", "id", "tool_use_id", "toolUseId", "sessionId", "agentId"}

# Applied in order to every non-structural string leaf. Broad on purpose (source-adapter §7:
# scrubbing is a deliberate, err-toward-removal choice).
_REDACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "redacted@example.com"),
    (re.compile(r"(/Users/)[^/\s\"']+"), r"\1user"),
    (re.compile(r"(/home/)[^/\s\"']+"), r"\1user"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[REDACTED]"),
    (re.compile(r"gh[posru]_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|secret|access[_-]?token|auth[_-]?token|password|passwd|client[_-]?secret)\b"
            r"(\s*[\":=]+\s*)\"?[A-Za-z0-9/_+.\-]{6,}\"?"
        ),
        r"\1\2[REDACTED]",
    ),
]


def _scrub_string(value: str) -> str:
    for pattern, repl in _REDACTIONS:
        value = pattern.sub(repl, value)
    if len(value) > _CAP:
        value = value[:_CAP] + f" …[+{len(value) - _CAP} chars]"
    return value


def _scrub_key(key: str) -> str:
    # Dict keys are occasionally data, not schema (e.g. `trackedFileBackups` is keyed by
    # absolute file path, not by a fixed field name) -- redact the same patterns as a value,
    # but skip the length cap: key identity/uniqueness matters in a way a string *value*'s
    # content never does, so truncating risks silently colliding two distinct keys.
    for pattern, repl in _REDACTIONS:
        key = pattern.sub(repl, key)
    return key


def _scrub(value, *, key: str | None = None):
    if isinstance(value, str):
        # structural ids travel byte-for-byte -- correlation and dedup depend on them.
        return value if key in _STRUCTURAL_KEYS else _scrub_string(value)
    if isinstance(value, dict):
        return {_scrub_key(k): _scrub(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, key=key) for v in value[:_LIST_CAP]]
    return value


def _sanitize_jsonl(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with src.open("r", encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            fout.write(json.dumps(_scrub(json.loads(line))) + "\n")
            n += 1
    return n


def _collect_tool_use_ids(session_jsonl: Path) -> set[str]:
    ids: set[str] = set()
    for line in session_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
        for block in content or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in ("Task", "Agent"):
                ids.add(block.get("id"))
    return ids


def _sanitize_session(source_root: Path, dest_dir: Path, dest_name: str, session_id: str) -> None:
    src_jsonl = source_root / f"{session_id}.jsonl"
    dst_jsonl = dest_dir / f"{dest_name}.jsonl"
    n = _sanitize_jsonl(src_jsonl, dst_jsonl)
    print(f"  {dest_name}.jsonl  <- {session_id}.jsonl  ({n} records)")

    src_subagents = source_root / session_id / "subagents"
    if not src_subagents.is_dir():
        return
    dst_subagents = dest_dir / dest_name / "subagents"
    spawn_ids = _collect_tool_use_ids(src_jsonl)
    for meta_path in sorted(src_subagents.glob("agent-*.meta.json")):
        stem = meta_path.name[: -len(".meta.json")]  # agent-<hash> (Path.stem only strips .json)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # meta.json holds only structural correlation data -- copy it through unscrubbed.
        (dst_subagents).mkdir(parents=True, exist_ok=True)
        (dst_subagents / f"{stem}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        child_jsonl = src_subagents / f"{stem}.jsonl"
        if child_jsonl.is_file():
            cn = _sanitize_jsonl(child_jsonl, dst_subagents / f"{stem}.jsonl")
            print(f"    subagents/{stem}.jsonl  ({cn} records)")
        tool_use_id = meta.get("toolUseId")
        assert tool_use_id in spawn_ids, (
            f"correlation would break: subagent {stem} toolUseId={tool_use_id!r} "
            f"not found among parent spawn ids {spawn_ids}"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("/Users/anil/Downloads/sumandra-projects-talium-code-econ"),
        help="Directory holding the raw <session>.jsonl files and <session>/subagents/ trees.",
    )
    args = parser.parse_args(argv)

    dest_dir = Path(__file__).resolve().parent
    print(f"sanitizing into {dest_dir}")
    for dest_name, session_id in _FIXTURES.items():
        _sanitize_session(args.source_root, dest_dir, dest_name, session_id)
    print("done. Review `git diff` before committing.")


if __name__ == "__main__":
    main()
