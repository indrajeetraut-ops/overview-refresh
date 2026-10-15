"""Normalize a real coding-agent session file and dump the result for manual review.

A standalone way to exercise the normalizer against real files -- no engine, S3, or msging
involved.

Usage:
    python -m collector.cli_normalize <path/to/session.jsonl> --out output.json
        [--vendor claude|codex] [--config config/normalizer/claude_code.yaml]
        [--source batch|live_tail] [-u ~/.claude.json]

Vendor dispatch goes through the two registries rather than naming a normalizer directly:
`local_loader.SOURCE_READERS` (file layout) and `normalizer.registry` (record mapping). Adding
a vendor is an entry in each, with no change here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from collector import normalizer as normalizer_pkg
from collector.normalizer.config import NormalizerConfig, load_config
from collector.normalizer.identity import AccountIdentity, load_account_identity
from collector.normalizer.local_loader import SOURCE_READERS, resolve_vendor
from collector.normalizer.models import EventIn, NormalizedSession

# Repo-relative, like every other service's default. Resolved from this file rather than the
# cwd so `python -m collector.cli_normalize` works from anywhere in the tree.
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config/normalizer/claude_code.yaml"
from common.events import SCHEMA_VERSION

log = structlog.get_logger(module="collector.cli_normalize")


def _serialize_event(event: EventIn) -> dict:
    return {
        "event_type": event.event_type,
        "record_id": event.record_id,
        "parent_id": event.parent_id,
        "event_ts": event.event_ts,
        "payload": event.payload,
    }


def _serialize_session(session: NormalizedSession) -> dict:
    return {
        "vendor": session.vendor,
        "session_id": session.session_id,
        "source": session.source,
        "agent_version": session.agent_version,
        "parent_session_id": session.parent_session_id,
        "events": [_serialize_event(e) for e in session.events],
    }


def config_digest(config: NormalizerConfig) -> str:
    """Fingerprints the resolved config. Two dumps with the same digest were produced under
    the same caps, taxonomy, and redaction settings -- which is the question you actually ask
    when comparing two normalization runs, and one the config *file* path cannot answer since
    defaults fill in everything it omits."""
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_meta_info(
    *,
    vendor: str,
    source_path: Path,
    config: NormalizerConfig,
    sessions: list[NormalizedSession],
    user: AccountIdentity | None = None,
) -> dict:
    """Provenance for one dump: what produced it, from what, under which settings, and -- when
    asked for -- whose account ran it.

    `vendor` is already per-session on each NormalizedSession; it is repeated here so a reader
    can route the file without descending into it. The rest has nowhere else to live.

    `user` is `null` unless `--user-config` was passed. That distinction is load-bearing: null
    means identity was never requested, whereas an object with null members means it was
    requested and the config had no organization (a personal account)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "vendor": vendor,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "collector_version": config.collector_version,
        "config_digest": config_digest(config),
        "source_path": str(source_path),
        "user": user,
        "counts": {
            "sessions": len(sessions),
            "events": sum(len(s.events) for s in sessions),
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_path", type=Path, help="Path to a <session>.jsonl file")
    parser.add_argument("--out", type=Path, required=True, help="Where to write the normalized JSON dump")
    parser.add_argument("--vendor", default="claude", help="claude | codex (default: claude)")
    parser.add_argument("--source", default="batch", choices=["batch", "live_tail"])
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Normalizer YAML config (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-u",
        "--user-config",
        type=Path,
        default=None,
        help="Claude Code config (e.g. ~/.claude.json) to read account identity from. "
        "Opt-in: without it meta_info.user is null and no identity is written.",
    )
    args = parser.parse_args(argv)

    # A bad --vendor and an unimplemented one are operator errors, not defects: report them as
    # a message and a non-zero exit, not a traceback. The batch wrapper captures stderr per
    # file, so a traceback there buries the one line that says what to do.
    try:
        vendor = resolve_vendor(args.vendor)
    except ValueError as exc:
        parser.error(str(exc))

    # The file is what runs. It used to default to None, so every real run silently used the
    # built-in defaults and the shipped YAML was documentation pretending to be configuration.
    # Every other service (uploader, ingest, worker, storage) already defaults to its own
    # config/<svc>.yaml; this was the outlier. A missing file is an error, not a fallback --
    # falling back is how a run ends up configured by something nobody looked at.
    if not args.config.is_file():
        parser.error(f"config not found: {args.config}")
    config: NormalizerConfig = load_config(args.config)

    user: AccountIdentity | None = None
    if args.user_config:
        try:
            user = load_account_identity(args.user_config)
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(f"[normalizer] {exc}") from None

    try:
        src = SOURCE_READERS[vendor](args.session_path, vendor=vendor, source=args.source)
    except NotImplementedError as exc:
        raise SystemExit(f"[normalizer] {exc}") from None
    # Through the registry, not a named normalizer -- the registry is the vendor seam and was
    # bypassed here until now. It holds instances built with default config, so rebuild the
    # registered class with this run's config rather than mutating the module-level singleton.
    normalizer = type(normalizer_pkg.registry[vendor])(config)
    sessions = list(normalizer.normalize(src))

    dump = {
        "meta_info": build_meta_info(
            vendor=vendor, source_path=args.session_path, config=config, sessions=sessions, user=user
        ),
        "sessions": [_serialize_session(s) for s in sessions],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")

    event_counts: dict[str, int] = {}
    for session in sessions:
        for event in session.events:
            event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1

    log.info(
        "normalize_complete",
        session_path=str(args.session_path),
        record_count=len(src.records),
        sessions=len(sessions),
        subagents=len(sessions) - 1,
        event_counts=event_counts,
        out=str(args.out),
    )


if __name__ == "__main__":
    main()
