"""Generate the `session_info` baseline over a local Claude Code corpus (default
`~/.claude/projects`), for use as the A/B oracle in Steps 3-5.

This writes **private** data (real paths, titles, PR urls from the developer's own sessions), so its
output dir is gitignored by default and never committed -- unlike the sanitized-fixture golden in
`test_differential_runner.py`, which is the committed CI baseline. One JSON file per master family
plus a `manifest.json` recording per-family category coverage.

    venv/bin/python tests/graph_migration/cli_baseline.py                       # ~/.claude/projects -> .local-baseline/
    venv/bin/python tests/graph_migration/cli_baseline.py --corpus DIR --out DIR
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from tests.graph_migration.harness import load_family, graph_snapshot_family

DEFAULT_CORPUS = Path.home() / ".claude" / "projects"
DEFAULT_OUT = Path(__file__).resolve().parent / ".local-baseline"

# Categories the plan wants covered, and whether a single local JSONL family can exhibit them.
# resume (cross-session continuation) and otel-true-up (a separate OTEL source) are NOT derivable
# from one jsonl file -- reported so coverage gaps are explicit, not silently assumed.
def _categories(events_by_sid: dict[str, list]) -> list[str]:
    cats: list[str] = []
    all_events = [e for evs in events_by_sid.values() for e in evs]
    types = {e["event_type"] for e in all_events}
    if len(events_by_sid) == 1:
        cats.append("single")
    if len(events_by_sid) > 1:
        cats.append("multi_subagent")
    if "compaction" in types:
        cats.append("compaction")
    if "api_error" in types:
        cats.append("api_error")
    if "pr_link" in types:
        cats.append("pr_link")
    if any(
        e["event_type"] == "tool_call"
        and (e["payload"].get("tool_name") == "Bash")
        and isinstance((e["payload"].get("input") or {}).get("command"), str)
        and "git " in (e["payload"]["input"]["command"])
        for e in all_events
    ):
        cats.append("git")
    return cats


def _master_sessions(corpus: Path) -> list[Path]:
    """Master session files: `<corpus>/<project>/<session>.jsonl`; subagents live one level deeper
    under `<session>/subagents/` and are discovered by the loader, never iterated here."""
    return sorted(p for p in corpus.glob("*/*.jsonl") if "subagents" not in p.parts)


def main(argv: list[str] | None = None) -> None:
    from tests.graph_migration.harness import _to_events  # local: private helper, dev tool only

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if not args.corpus.is_dir():
        raise SystemExit(f"[baseline] corpus not found: {args.corpus}")
    args.out.mkdir(parents=True, exist_ok=True)

    masters = _master_sessions(args.corpus)
    rows: list[dict] = []
    cat_counts: dict[str, int] = {}
    ok = err = 0
    for path in masters:
        try:
            family = load_family(path)
            events_by_sid = {sid: _to_events(s) for sid, s in family.items()}
            snap = graph_snapshot_family(path)
        except Exception as exc:  # noqa: BLE001 -- one bad session must not stop the batch
            err += 1
            rows.append({"input": str(path), "status": "error", "message": str(exc)})
            print(f"[baseline] ERROR {path.name}: {exc}", flush=True)
            continue
        master = next((sid for sid, s in family.items() if s.parent_session_id is None), None)
        cats = _categories(events_by_sid)
        for c in cats:
            cat_counts[c] = cat_counts.get(c, 0) + 1
        (args.out / f"{master or path.stem}.json").write_text(
            json.dumps(snap, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        ok += 1
        rows.append({"input": str(path), "status": "ok", "master": master,
                     "members": sorted(snap), "categories": cats})
        print(f"[baseline] {path.name} -> {len(snap)} member(s) {cats}", flush=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": str(args.corpus),
        "total": len(masters),
        "ok": ok,
        "error": err,
        "category_counts": cat_counts,
        "not_derivable_from_jsonl": ["resume", "otel_trueup"],  # need other sources; use fixtures
        "families": rows,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[baseline] {ok} ok, {err} error(s) -> {args.out} | categories: {cat_counts}")


if __name__ == "__main__":
    main()
