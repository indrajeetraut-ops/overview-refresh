"""CLI dispatch for standalone shape-curve authoring."""
from __future__ import annotations

import argparse
import json
import sys
import traceback
import uuid
import webbrowser
from pathlib import Path

import structlog
from common.msging.log import configure_logging, get_logger

from .config import load_curve_config
from .errors import ArtifactError, CurveError
from .runner import config_hash, run_curve


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Author a reusable, duration-normalized shape curve and save it as an artifact.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""A curve is anchors [pos, "value"] on an abstract integer axis, PCHIP-
interpolated into a dimensionless multiplier over normalized time. This tool authors and
saves one curve artifact; it does not consume roster/series data and draws no calendar
semantics into the shape.

Output layout: <output>/<name>/<config-hash>/ (config.yaml, curve.jsonl, preview.html,
preview.png, report.md, manifest.json; manifest written last). The config hash keys the
directory, so an identical config always resolves to the same directory: re-running an
unchanged config is a friendly no-op (prints the existing path, exits 0, writes nothing)
unless --force is given, which always clears and rebuilds exactly that <name>/<hash>/
leaf (never the --output artifacts root, which may hold other curves) -- including when
it already holds a complete, matching artifact.

Examples:
  curve.sh --config curve.yaml --output artifacts/curves --open
  curve.sh --config curve.yaml --validate-only
  curve.sh --config curve.yaml --output artifacts/curves --preview-days 90
  curve.sh --config curve.yaml --output artifacts/curves --force

One JSON summary always goes to stdout (on failure too, with "status":"error" and a
"message"); JSON diagnostic logs go to stderr.
""")
    argument_parser.add_argument("--config", required=True, type=Path,
                                 help="shape-curve YAML configuration")
    argument_parser.add_argument("--output", type=Path,
                                 help="artifacts root; each run writes <name>/<config-hash>/ "
                                      "under it (required unless --validate-only)")
    argument_parser.add_argument("--validate-only", action="store_true",
                                 help="validate the configuration; write nothing")
    argument_parser.add_argument("--open", action="store_true",
                                 help="open preview.html in the default browser after a run")
    argument_parser.add_argument("--preview-days", type=_positive_int,
                                 help="sample the shape over N days for the preview x-axis; "
                                      "overrides config preview.days")
    argument_parser.add_argument("--force", action="store_true",
                                 help="always clear and rebuild the computed <name>/<config-hash>/ "
                                      "leaf, even a complete/matching one (default: reuse a complete "
                                      "match as a no-op; refuse an incomplete/orphaned one)")
    return argument_parser


def main(argv: list[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if not args.validate_only and args.output is None:
        argument_parser.error("--output is required unless --validate-only")
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    run_id = None if args.validate_only else str(uuid.uuid4())
    log = get_logger(__name__).bind(operation="curve-create", run_id=run_id)
    stage = "validation"
    config = None
    digest = None
    try:
        config = load_curve_config(args.config)
        if args.preview_days is not None:
            # Fold the CLI override into the hashed config so it is not silently ignored
            # on an existing artifact: different --preview-days values now hash to
            # different directories, and the persisted config.yaml stays self-consistent
            # with the reported config_hash.
            config = config.model_copy(
                update={"preview": config.preview.model_copy(update={"days": args.preview_days})})
        digest = config_hash(config)
        log.info("curve.config_validated", schema_version=config.schema_version,
                 config_path=str(args.config.resolve()), name=config.name, config_hash=digest)
        if args.validate_only:
            summary = {"status": "valid", "run_id": None, "name": config.name,
                       "config_hash": digest, "output_dir": None, "files": [],
                       "report_path": None}
            print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
            return 0

        stage = "generation"
        result = run_curve(config, args.output, log=log, run_id=run_id, force=args.force)
        if result is None:
            existing = (Path(args.output) / config.name / digest).resolve()
            summary = {"status": "unchanged", "run_id": None, "name": config.name,
                       "config_hash": digest, "output_dir": str(existing), "files": [],
                       "report_path": str(existing / "report.md")}
            preview = existing / "preview.html"
        else:
            summary = {"status": "complete", "run_id": result.run_id, "name": config.name,
                       "config_hash": digest, "output_dir": str(result.output_dir),
                       "files": result.files, "report_path": str(result.report_path),
                       "summary": result.summary}
            preview = result.output_dir / "preview.html"
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        if args.open:
            webbrowser.open(preview.as_uri())
        return 0
    except Exception as exc:
        expected = isinstance(exc, CurveError)
        category = type(exc).__name__ if expected else "UnexpectedError"
        message = str(exc) if expected else "unexpected failure; see stderr traceback"
        log.error("curve.run_failed", error_category=category,
                  stage=getattr(exc, "stage", stage), message=message,
                  cleanup_outcome=getattr(exc, "cleanup_outcome",
                                          "not-created" if stage == "validation" else "unknown"))
        if not expected:
            traceback.print_exc(file=sys.stderr)
        # Emit a summary on stdout for failures too, mirroring the success summary shape,
        # so callers get one parseable outcome object on stdout regardless of result.
        summary = {"status": "error", "error_category": category, "message": message,
                   "name": getattr(config, "name", None), "config_hash": digest,
                   "output_dir": None, "files": [], "report_path": None, "run_id": run_id}
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 1 if not expected or isinstance(exc, ArtifactError) else 2


if __name__ == "__main__":
    raise SystemExit(main())
