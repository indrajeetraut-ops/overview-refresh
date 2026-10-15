"""Acceptance harness for the cache-write contract (design §10 A1, A2, A6 input).

Re-runs Layer 2's `simulate` in process over a generated org (per-request 5m/1h splits are
not persisted) and, for the last `--days` days of the series, prices the same five-type
tokens three ways, per model:

- cost kernel: tokens x the cost run's `rate_sheet.json` rates, per type (A1 numerator);
- request level: `OrgPricer.request_micros` over the 5m/1h split (A1 denominator);
- dashboard: `session_info.models` as the dashboard prices it, one combined `cache_write`
  at the 1h rate (A2).

A1 is the cost-kernel / request-level ratio (1.00 where the two price tables agree);
A2 is the dashboard / request-level ratio (the 5m-at-1h residual, about 1.007 overall).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import structlog
from common.msging.log import configure_logging, get_logger
from common.model_pricing import RateSheet
from forecast.artifact_io import read_records
from forecast.contracts import DailyUsageRecord
from forecast.synthetic.errors import SyntheticError

from forecast.synthetic.org.config import load_org_config, read_series_snapshot, validate_org_inputs
from forecast.synthetic.org.pricing import OrgPricer, load_pricer
from forecast.synthetic.org.requests import simulate
from forecast.synthetic.org.sessions import plan_sessions

TOKEN_TYPES = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
_PER_TOKEN_KINDS = TOKEN_TYPES


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Print per-model cache-write effect (A1) and dashboard residual (A2) for a "
                    "generated org (the output directory of synth_org.sh).")
    p.add_argument("--org-run", required=True, type=Path,
                   help="synth_org.sh output directory (holds configs/, series/, org/)")
    p.add_argument("--rate-sheet", required=True, type=Path,
                   help="a cost run's rate_sheet.json (the cost kernel's rates)")
    p.add_argument("--days", type=int, default=28, help="trailing window in days (default 28)")
    p.add_argument("--origin", help="YYYY-MM-DD, last day of the window (default: series end)")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return p


def _rate(sheet: RateSheet, model: str, day: date, kind: str) -> Decimal | None:
    entry = next((m for m in sheet.models if m.usage_model_id == model), None)
    if entry is None or entry.status != "matched":
        return None
    segment = next((s for s in entry.segments if s.from_ <= day <= s.to), entry.segments[0])
    value = segment.rates.get(kind)
    return Decimal(value) if value is not None else None


def measure(org_run: Path, rate_sheet_path: Path, days: int = 28, origin: date | None = None) -> dict:
    configs = Path(org_run) / "configs"
    cfg = load_org_config(configs / "org.yaml")
    snapshot = read_series_snapshot(cfg.org.series_dir)
    pricing = load_pricer(cfg.org.pricing_version, cfg.org.pricing_org, cfg.org.pricing_dir)
    resolved = validate_org_inputs(cfg, snapshot["manifest"], snapshot["users"], snapshot["teams"],
                                   snapshot["series_cfg"], pricing)
    pricer = OrgPricer(pricing)
    sheet = RateSheet.model_validate_json(Path(rate_sheet_path).read_text(encoding="utf-8"))
    usage = read_records(Path(cfg.org.series_dir) / "usage.jsonl", DailyUsageRecord, schema_version=2)
    plans = plan_sessions(resolved, usage)

    series = resolved.series.series
    end = origin or (series.start_date + timedelta(days=series.days - 1))
    start = end - timedelta(days=days - 1)

    tokens: dict[tuple[str, date], dict[str, int]] = defaultdict(lambda: dict.fromkeys(TOKEN_TYPES, 0))
    request_level: dict[str, int] = defaultdict(int)
    dashboard: dict[str, int] = defaultdict(int)
    for plan in plans:
        if not start <= plan.date <= end:
            continue
        team = resolved.team_effective[next(t.team_name for t in resolved.teams
                                            if plan.l1_user in t.user_ids)]
        at = plan.started_at.date()
        for req in simulate(resolved, plan, team.sessions, team.habits).requests:
            bucket = tokens[(req.model, plan.date)]
            for kind in TOKEN_TYPES:
                bucket[kind] += getattr(req, kind)
            plain = {"input": req.input, "output": req.output, "cache_read": req.cache_read}
            detail = [{"ttl_s": ttl, "tokens": n} for ttl, n in
                      ((300, req.cache_write_5m), (3600, req.cache_write_1h)) if n]
            request_level[req.model] += pricer.request_micros(req.model, plain, at, detail)
            dashboard[req.model] += pricer.request_micros(
                req.model, {**plain, "cache_write": req.cache_write}, at)

    kernel: dict[str, Decimal | None] = {}
    for (model, day), amounts in tokens.items():
        total = Decimal(0)
        for kind, count in amounts.items():
            if not count:
                continue
            rate = _rate(sheet, model, day, kind)
            if rate is None:
                total = None
                break
            total += count * rate
        if kernel.get(model, Decimal(0)) is None or total is None:
            kernel[model] = None
        else:
            kernel[model] = kernel.get(model, Decimal(0)) + total

    per_model = {}
    for model in sorted(request_level):
        request_usd = Decimal(request_level[model]) / Decimal(1_000_000)
        kernel_usd = kernel.get(model)
        per_model[model] = {
            "request_level_usd": float(request_usd),
            "cost_kernel_usd": None if kernel_usd is None else float(kernel_usd),
            "a1_cost_over_request": None if kernel_usd is None else float(kernel_usd / request_usd),
            "dashboard_usd": dashboard[model] / 1_000_000,
            "a2_dashboard_over_request": dashboard[model] / request_level[model],
        }
    total_request = sum(request_level.values())
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "rate_sheet_price_version": sheet.price_version,
        "models": per_model,
        "overall": {
            "request_level_usd": total_request / 1_000_000,
            "a2_dashboard_over_request": sum(dashboard.values()) / total_request,
            "a2_residual_pct": (sum(dashboard.values()) / total_request - 1) * 100,
        },
    }


def _table(result: dict) -> str:
    lines = [f"Window {result['window']['start']} .. {result['window']['end']} "
             f"({result['window']['days']} days); rate sheet {result['rate_sheet_price_version']}", "",
             "| Model | Request-level USD | Cost-kernel USD | A1 ratio | Dashboard USD | A2 ratio |",
             "|---|---:|---:|---:|---:|---:|"]
    for model, row in result["models"].items():
        a1 = "unpriced" if row["a1_cost_over_request"] is None else f"{row['a1_cost_over_request']:.4f}"
        kernel = "-" if row["cost_kernel_usd"] is None else f"{row['cost_kernel_usd']:.2f}"
        lines.append(f"| {model} | {row['request_level_usd']:.2f} | {kernel} | {a1} | "
                     f"{row['dashboard_usd']:.2f} | {row['a2_dashboard_over_request']:.4f} |")
    overall = result["overall"]
    lines += ["", f"A2 overall: dashboard / request-level = {overall['a2_dashboard_over_request']:.4f} "
                  f"({overall['a2_residual_pct']:.2f}% residual)"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger(__name__)
    try:
        result = measure(args.org_run, args.rate_sheet, args.days,
                         date.fromisoformat(args.origin) if args.origin else None)
    except SyntheticError as exc:
        log.error("cache_write_acceptance.failed", message=str(exc))
        return 1
    log.info("cache_write_acceptance.completed", models=len(result["models"]))
    print(json.dumps(result, indent=2) if args.json else _table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
