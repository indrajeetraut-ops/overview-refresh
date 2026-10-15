"""Regenerates the published normalized-event contract, or derives one from real output.

Two modes:

    python -m common.events.cli_publish_payload
        Regenerates the committed artifacts next to this file, from the normalizer's own
        fixture corpus. This is the canonical, deterministic form -- the same corpus the drift
        guard uses, so the committed files and the guard can never disagree.

    python -m common.events.cli_publish_payload --from-dump <normalized.json> [--out-dir DIR]
        Derives a shape from a real normalized dump instead. Use this to check whether real
        data carries fields the published contract does not -- a new Claude Code version, a
        vendor extension -- without touching the committed artifacts.

The fixture import is deliberate and only reachable in the first mode: the corpus is the one
deterministic input that exercises every event type, and it lives with the tests because that
is what it is for. `--from-dump` needs no fixtures at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common.events import SCHEMA_VERSION
from common.events.descriptions import missing_descriptions
from common.events.schema import derive_shape, worked_example

SCHEMA_PATH = Path(__file__).resolve().parent / "normalized_schema.json"
PAYLOAD_PATH = Path(__file__).resolve().parent / "payload.json"


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fixture_sessions():
    """The canonical corpus: recorded real sessions plus the synthetic builders that cover the
    types the recordings lack. Imported lazily so `--from-dump` works without the test tree."""
    tests_root = Path(__file__).resolve().parents[3] / "tests"
    if str(tests_root) not in sys.path:
        sys.path.insert(0, str(tests_root))
    from fixtures.normalizer import corpus  # noqa: PLC0415 -- lazy by design

    return corpus.all_sessions()


class _DumpSession:
    """Adapts a serialized dump back to the attribute access derive_shape expects."""

    def __init__(self, session: dict) -> None:
        self.events = [_DumpEvent(e) for e in session.get("events", [])]


class _DumpEvent:
    __slots__ = ("event_type", "record_id", "parent_id", "event_ts", "payload")

    def __init__(self, event: dict) -> None:
        self.event_type = event.get("event_type")
        self.record_id = event.get("record_id")
        self.parent_id = event.get("parent_id")
        self.event_ts = event.get("event_ts")
        self.payload = event.get("payload", {})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-dump", type=Path, default=None, help="Derive from a cli_normalize dump instead")
    parser.add_argument("--out-dir", type=Path, default=None, help="Where to write (default: beside this module)")
    args = parser.parse_args(argv)

    if args.from_dump:
        dump = json.loads(args.from_dump.read_text(encoding="utf-8"))
        raw_sessions = dump["sessions"] if isinstance(dump, dict) else dump
        sessions = [_DumpSession(s) for s in raw_sessions]
    else:
        sessions = list(_fixture_sessions())

    schema = derive_shape(sessions, schema_version=SCHEMA_VERSION)
    example = worked_example(sessions, schema_version=SCHEMA_VERSION)

    # An undocumented field in a published contract is worse than no contract: it looks
    # authoritative and says nothing. Refuse to write rather than ship one.
    gaps = missing_descriptions({k: v["fields"] for k, v in schema["payload_by_type"].items()})
    if gaps:
        print(f"refusing to publish: {len(gaps)} field(s) have no description in "
              f"common/events/descriptions.py", file=sys.stderr)
        for gap in gaps:
            print(f"  {gap}", file=sys.stderr)
        raise SystemExit(1)

    out_dir = args.out_dir or SCHEMA_PATH.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    schema_path, payload_path = out_dir / SCHEMA_PATH.name, out_dir / PAYLOAD_PATH.name
    _write(schema_path, schema)
    _write(payload_path, example)

    types = sorted(schema["payload_by_type"])
    print(f"schema_version {SCHEMA_VERSION} · {len(types)} event types: {', '.join(types)}")
    print(f"wrote {schema_path}")
    print(f"wrote {payload_path}")


if __name__ == "__main__":
    main()
