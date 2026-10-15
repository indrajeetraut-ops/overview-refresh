"""Small shared helpers the segments library needs, vendored from the retired fold's `metrics.py`
(`event_tokens`, `git_command_counts`, `segment_repo_key`) plus the interrupt sentinel -- kept here
so the library is self-contained and doesn't depend on the deleted fold package."""
from __future__ import annotations

import re

from common import git_ident

INTERRUPT_SENTINEL = "[Request interrupted by user]"
_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def event_tokens(event: dict) -> int:
    payload = event.get("payload", {})
    return sum(payload.get(f) or 0 for f in _USAGE_FIELDS)


def git_command_counts(events: list[dict], subcommands: list[str]) -> dict[str, int]:
    """Counts of git subcommands run via Bash, keyed by subcommand (`checkout`/`switch` collapse
    into one `checkout` bucket). Best-effort: skips up to 3 tokens after `git` before the first
    recognized subcommand; a chained command counts every recognized invocation."""
    if not subcommands:
        return {}
    alternation = "|".join(re.escape(s) for s in subcommands)
    pattern = re.compile(rf"\bgit\s+(?:\S+\s+){{0,3}}?(?P<subcommand>{alternation})\b")

    counts: dict[str, int] = {}
    for event in events:
        if event["event_type"] != "tool_call":
            continue
        payload = event["payload"]
        if payload.get("tool_name") != "Bash":
            continue
        command = (payload.get("input") or {}).get("command")
        if not isinstance(command, str):
            continue
        for match in pattern.finditer(command):
            sub = match.group("subcommand")
            bucket = "checkout" if sub == "switch" else sub
            counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def segment_repo_key(seg_events: list[dict], repo: dict | None) -> str | None:
    """Which project a segment's own file activity belongs to (the per-segment project column).
    Reuses repo_info's root-matching against file_change `path`; a segment with no file activity
    falls back to the session's primary repo_key. None only when the session resolved no repo."""
    if not repo or repo.get("repo_id") is None:
        return None
    stripped_cwd = git_ident.strip_worktree_suffix(repo["cwd"]).rstrip("/") if repo.get("cwd") else None

    tallies: dict[str, int] = {}
    for e in seg_events:
        if e["event_type"] != "file_change":
            continue
        path = e["payload"].get("path")
        if not path or not stripped_cwd:
            continue
        if path == stripped_cwd or path.startswith(stripped_cwd + "/"):
            tallies[repo["repo_key"]] = tallies.get(repo["repo_key"], 0) + 1
            continue
        sibling = git_ident.sibling_project_root(stripped_cwd, path)
        if sibling is not None:
            key = sibling.rsplit("/", 1)[-1]
            tallies[key] = tallies.get(key, 0) + 1

    if tallies:
        return max(tallies.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return repo.get("repo_key")
