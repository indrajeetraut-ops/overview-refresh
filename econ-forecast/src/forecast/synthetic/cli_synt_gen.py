"""CLI dispatch for independent synthetic user, roster, and series creation."""
from __future__ import annotations

import argparse
import json
import sys
import traceback
import uuid
from pathlib import Path

import structlog
from common.msging.log import configure_logging, get_logger
from forecast.artifact_io import read_records
from .config import load_roster_config, load_series_config, load_users_config, validate_series_inputs
from .errors import ArtifactError, SyntheticError
from .models import TeamRecord, UserRecord
from .roster import create_roster
from .runner import run_roster, run_series, run_users

OPERATIONS = {"user-create": "users", "roster-create": "roster", "series-create": "series",
              "org-expand": "org"}


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Create reusable users, fixed Team membership, daily synthetic token "
                    "consumption, or an org-expand dataset.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Operations:
  user-create    Read user count/domain/seed; write users.jsonl and a report.
  roster-create  Read saved users and Team names/sizes; write teams.jsonl and a report.
  series-create  Read saved users/roster and daily settings; write usage, coverage,
                 profile/event records, snapshots, and a report.
  org-expand     Read a completed series dataset and org.yaml; write sessions, session_info,
                 GitHub artifacts, directory records, and a report (forecast.synthetic.org).

Token settings belong in YAML: input is uncached input; cache writes (5m, 1h) and
cache_read are separate; output is additional. Dates/durations are calendar days. Relative YAML input paths
resolve against the config file; --output resolves against your current directory.

Examples:
  synt_gen.sh --op user-create --config users.yaml --output artifacts/users
  synt_gen.sh --op roster-create --config roster.yaml --output artifacts/roster
  synt_gen.sh --op series-create --config series.yaml --output artifacts/series
  synt_gen.sh --op series-create --config series.yaml --validate-only

Existing output directories are refused unless --force is given, which removes exactly
the resolved output directory before writing (never a parent, never a symlink target).
JSON summaries go to stdout; JSON logs and errors go to stderr. Each completed operation
includes report.md.
""")
    argument_parser.add_argument("--op", required=True, choices=OPERATIONS, help="independent operation to execute")
    argument_parser.add_argument("--config", required=True, type=Path, help="YAML configuration for the operation")
    argument_parser.add_argument("--output", type=Path, help="new dataset directory (required unless validating only)")
    argument_parser.add_argument("--validate-only", action="store_true", help="validate settings and saved inputs; write nothing")
    argument_parser.add_argument("--force", action="store_true",
                                 help="remove an existing/non-empty --output directory before writing "
                                      "(default: refuse an existing output path)")
    return argument_parser


def _validate(operation, config, log) -> dict[str, int]:
    if operation == "users":
        return {"users": config.users.count}
    if operation == "roster":
        users = read_records(config.roster.users_file, UserRecord)
        # Includes duplicate membership/identity and final supplied/generated ID checks.
        teams = create_roster(config, users, log=log.bind(module="forecast.synthetic.roster"))
        assigned = sum(len(team.user_ids) for team in teams)
        return {"users": len(users), "teams": len(teams), "rostered_users": assigned,
                "unassigned_users": len(users) - assigned}
    if operation == "org":
        from .org.config import read_series_snapshot, validate_org_inputs
        from .org.pricing import load_pricer

        snapshot = read_series_snapshot(config.org.series_dir)
        pricing = load_pricer(config.org.pricing_version, config.org.pricing_org, config.org.pricing_dir)
        resolved = validate_org_inputs(config, snapshot["manifest"], snapshot["users"],
                                       snapshot["teams"], snapshot["series_cfg"], pricing)
        return {"users": len(resolved.users), "teams": len(resolved.teams),
                "rostered_users": sum(len(t.user_ids) for t in resolved.teams),
                "repos": len(config.repos)}
    users = read_records(config.series.users_file, UserRecord)
    teams = read_records(config.series.roster_file, TeamRecord)
    validate_series_inputs(config, users, teams)
    rostered = sum(len(team.user_ids) for team in teams)
    return {"users": len(users), "teams": len(teams), "rostered_users": rostered,
            "unassigned_users": len(users) - rostered, "dates": config.series.days}


def main(argv: list[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if not args.validate_only and args.output is None:
        argument_parser.error("--output is required unless --validate-only")
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    operation = OPERATIONS[args.op]
    run_id = None if args.validate_only else str(uuid.uuid4())
    log = get_logger(__name__).bind(operation=operation, run_id=run_id)
    stage = "validation"
    try:
        from .org.config import load_org_config
        loaders = {"users": load_users_config, "roster": load_roster_config,
                  "series": load_series_config, "org": load_org_config}
        config = loaders[operation](args.config)
        # org-expand has no --force: it still refuses an existing output up front. The three
        # Layer 1 operations check (and, with --force, clear) their output inside the runner.
        if operation == "org" and args.output and (args.output.exists() or args.output.is_symlink()):
            raise ArtifactError("output path already exists; choose a new directory")
        counts = _validate(operation, config, log)
        log.info("synthetic.config_validated", schema_version=config.schema_version,
                 config_path=str(args.config.resolve()))
        if args.validate_only:
            summary = {"status": "valid", "operation": operation, "run_id": None,
                       "output_dir": str(args.output.resolve()) if args.output else None,
                       "files": [], "report_path": None, "counts": counts}
        else:
            stage = "generation"
            if operation == "org":
                from .org.expand import run_org_expand
                result = run_org_expand(config, args.output, log=log, run_id=run_id)
            else:
                result = {"users": run_users, "roster": run_roster, "series": run_series}[operation](
                    config, args.output, log=log, run_id=run_id, force=args.force)
            summary = {"status": "complete", "operation": result.operation, "run_id": result.run_id,
                       "output_dir": str(result.output_dir), "files": result.files,
                       "report_path": str(result.report_path), "counts": result.summary_counts}
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        expected = isinstance(exc, SyntheticError)
        log.error("synthetic.run_failed", error_category=type(exc).__name__,
                  stage=getattr(exc, "stage", stage), message=str(exc) if expected else "unexpected failure; see traceback",
                  cleanup_outcome=getattr(exc, "cleanup_outcome", "not-created" if stage == "validation" else "unknown"))
        if not expected:
            traceback.print_exc(file=sys.stderr)
        return 1 if not expected or isinstance(exc, ArtifactError) else 2


if __name__ == "__main__":
    raise SystemExit(main())
