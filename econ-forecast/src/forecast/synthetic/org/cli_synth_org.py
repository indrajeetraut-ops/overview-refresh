"""One-shot chain: user-create -> roster-create -> series-create -> org-expand, with optional
end-date retiming so a checked-in example config always ends "now" (plan §5 P9).

`--end-date` is the only allowed wall-clock read in this whole package, and it happens exactly
once, here: `today` is resolved once and written into the copied `series.yaml`, so the saved
configs under `--output/configs/` replay deterministically on their own from then on.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import structlog
import yaml
from common.msging.log import configure_logging, get_logger
from forecast.synthetic.config import load_roster_config, load_series_config, load_users_config
from forecast.synthetic.errors import SyntheticError
from forecast.synthetic.runner import run_roster, run_series, run_users

from .config import load_org_config
from .expand import run_org_expand
from .pricing import pricing_files

_CONFIG_NAMES = ("users.yaml", "roster.yaml", "series.yaml", "org.yaml")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the full users -> roster -> series -> org-expand chain from one "
                    "config directory, optionally retiming the series to end on a given date.",
        epilog="""Example:
  synth_org.sh --config-dir econ-forecast/config/synthetic/org \\
    --output econ-forecast/artifacts/org-demo --end-date today
""", formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config-dir", required=True, type=Path,
                   help="directory containing users.yaml, roster.yaml, series.yaml, org.yaml")
    p.add_argument("--output", required=True, type=Path, help="new output directory")
    p.add_argument("--end-date", help="YYYY-MM-DD, or 'today'; retimes series.yaml so the "
                                      "series ends on this date")
    p.add_argument("--validate-only", action="store_true",
                   help="write the retimed/rewritten configs and validate each stage; "
                        "do not generate any dataset")
    return p


def _shift_date(value: str, delta: timedelta) -> str:
    return (date.fromisoformat(value) + delta).isoformat()


def _retime_series(raw: dict, end_date: date) -> dict:
    """`start_date = end - days + 1`; every event date shifts by the same delta (plan step 3)."""
    old_start = date.fromisoformat(raw["series"]["start_date"])
    days = raw["series"]["days"]
    new_start = end_date - timedelta(days=days - 1)
    delta = new_start - old_start
    raw["series"]["start_date"] = new_start.isoformat()
    if delta == timedelta(0):
        return raw
    events = raw.get("events") or {}
    for rule in events.get("spike_rules", []) or []:
        date_range = rule.get("date_range")
        if date_range:
            date_range["start_date"] = _shift_date(date_range["start_date"], delta)
            date_range["end_date"] = _shift_date(date_range["end_date"], delta)
    for zp in events.get("zero_periods", []) or []:
        zp["start_date"] = _shift_date(zp["start_date"], delta)
        if zp.get("end_date"):
            zp["end_date"] = _shift_date(zp["end_date"], delta)
    return raw


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # Absolute from here on: every cross-reference written into configs/*.yaml below is
    # re-resolved by Layer 1's own config loader relative to THAT FILE's directory
    # (configs_dir), not the caller's cwd -- a relative --output would otherwise double-resolve
    # into a path that never exists.
    args.output = args.output.resolve()
    args.config_dir = args.config_dir.resolve()
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger(__name__)

    for name in _CONFIG_NAMES:
        if not (args.config_dir / name).is_file():
            log.error("synthetic_org.run_failed", message=f"missing {name} in --config-dir")
            return 2

    if args.output.exists() or args.output.is_symlink():
        log.error("synthetic_org.run_failed", message="output path already exists")
        return 1

    try:
        # Step 1: create --output.
        configs_dir = args.output / "configs"
        configs_dir.mkdir(parents=True)

        # Step 2: copy the four YAMLs, and snapshot pricing into <output>/pricing/ (the bound
        # catalog version(s), history.yaml, channel/alias config) so the chain and its dataset
        # reproduce without the live catalog. org.yaml's `pricing_dir`, if set, is resolved
        # against the config-dir and used as the source; otherwise the live catalog is.
        raw_by_name = {name: _load_yaml(args.config_dir / name) for name in _CONFIG_NAMES}
        org_raw = raw_by_name["org.yaml"]
        source_dir = org_raw["org"].get("pricing_dir")
        if source_dir is not None:
            source_dir = Path(source_dir)
            if not source_dir.is_absolute():
                source_dir = (args.config_dir / source_dir).resolve()
        resolved_version, files = pricing_files(org_raw["org"].get("pricing_version"), source_dir)
        for rel, content in files:
            target = args.output / "pricing" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        org_raw["org"]["pricing_version"] = resolved_version
        org_raw["org"]["pricing_dir"] = str(args.output / "pricing")

        # Step 3: retime, resolving "today" exactly once.
        if args.end_date:
            end_date = date.today() if args.end_date == "today" else date.fromisoformat(args.end_date)
            raw_by_name["series.yaml"] = _retime_series(raw_by_name["series.yaml"], end_date)

        # Step 4: rewrite relative paths so the chain points at --output/{users,roster,series,org}.
        raw_by_name["roster.yaml"]["roster"]["users_file"] = str(args.output / "users" / "users.jsonl")
        raw_by_name["series.yaml"]["series"]["users_file"] = str(args.output / "users" / "users.jsonl")
        raw_by_name["series.yaml"]["series"]["roster_file"] = str(args.output / "roster" / "teams.jsonl")
        org_raw["org"]["series_dir"] = str(args.output / "series")
        # `curve.artifacts` (optional) is relative to the config-dir's series.yaml; the copy
        # under <output>/configs/ would resolve it against the wrong directory.
        curve = raw_by_name["series.yaml"].get("curve")
        if curve and not Path(curve["artifacts"]).is_absolute():
            curve["artifacts"] = str((args.config_dir / curve["artifacts"]).resolve())

        for name in _CONFIG_NAMES:
            _write_yaml(configs_dir / name, raw_by_name[name])

        if args.validate_only:
            users_cfg = load_users_config(configs_dir / "users.yaml")
            roster_cfg = load_roster_config(configs_dir / "roster.yaml")
            series_cfg = load_series_config(configs_dir / "series.yaml")
            org_cfg = load_org_config(configs_dir / "org.yaml")
            summary = {"status": "valid", "config_dir": str(args.config_dir),
                      "output_dir": str(args.output), "configs": [f"configs/{n}" for n in _CONFIG_NAMES],
                      "counts": {"requested_users": users_cfg.users.count,
                                "teams": len(roster_cfg.roster.teams),
                                "series_days": series_cfg.series.days,
                                "repos": len(org_cfg.repos)}}
            print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
            return 0

        # Step 5: run the chain in-process.
        run_id = str(uuid.uuid4())
        stage_log = log.bind(chain_run_id=run_id)
        users_cfg = load_users_config(configs_dir / "users.yaml")
        users_result = run_users(users_cfg, args.output / "users", log=stage_log)

        roster_cfg = load_roster_config(configs_dir / "roster.yaml")
        roster_result = run_roster(roster_cfg, args.output / "roster", log=stage_log)

        series_cfg = load_series_config(configs_dir / "series.yaml")
        series_result = run_series(series_cfg, args.output / "series", log=stage_log)

        org_cfg = load_org_config(configs_dir / "org.yaml")
        org_result = run_org_expand(org_cfg, args.output / "org", log=stage_log, run_id=run_id)

        summary = {
            "status": "complete", "chain_run_id": run_id, "output_dir": str(args.output),
            "users": {"run_id": users_result.run_id, "output_dir": str(users_result.output_dir),
                     "counts": users_result.summary_counts},
            "roster": {"run_id": roster_result.run_id, "output_dir": str(roster_result.output_dir),
                      "counts": roster_result.summary_counts},
            "series": {"run_id": series_result.run_id, "output_dir": str(series_result.output_dir),
                      "counts": series_result.summary_counts},
            "org": {"run_id": org_result.run_id, "output_dir": str(org_result.output_dir),
                   "counts": org_result.summary_counts},
        }
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        expected = isinstance(exc, SyntheticError)
        log.error("synthetic_org.run_failed", error_category=type(exc).__name__,
                  message=str(exc) if expected else "unexpected failure; see traceback")
        if not expected:
            import traceback
            traceback.print_exc(file=sys.stderr)
        return 1 if not expected else 2


if __name__ == "__main__":
    raise SystemExit(main())
