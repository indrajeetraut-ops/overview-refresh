"""Before/after pricing report for the model_pricing migration (#68 design §4.2). Run:

    python -m server.worker.cli_pricing_report --dsn postgresql+psycopg://... [--org ORG]
                                               [--version VERSION] [--queries-dir config/queries]

Read-only. "Before" is **our own computed money**, not the vendor's: `otel_api_requests.
cost_usd_micros` is the vendor's cost, adopted as truth by the true-up, so comparing it with
`Pricer` would measure drift, not the migration. What depends on the price table is:

1. `ledger` -- the true-up's per-request estimate `session_info.otel.requests[].
   cost_est_usd_micros`, repriced from the same stored token counts (`input_tokens`,
   `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`) with the TTL split and the
   request date the true-up itself used (`otel_api_requests.event_ts`, else the JSONL event's
   `event_ts`). Requests the vendor priced are excluded from the ratios and shown only in the
   informational drift column (vendor cost vs `Pricer`).
2. `fold:*` -- what the fold persisted in `session_info.info`, repriced at the session's own
   `sessions.started_at`: `channel_cost` (sum of the three channels) and `carry` from the
   per-model token map in `session_info.models`; `resume_cost` (per-event `reload_tokens`) and
   `cache_busts` (per-event `rewrite_tokens`, which carries its own `model`).

**What the database does not persist** (so the report cannot attribute it): the fold's money
fields are session totals, with no per-model split, and `resume_cost` events carry no model.
Per-model attribution is therefore exact only for single-model sessions (and always for
`cache_busts` events and ledger requests); a multi-model session's fold totals are reported
under the model label `(multi-model)`, and its `resume_cost` is not repriceable and is skipped
(counted in the log). The ledger records carry no timestamp either; the date is re-read from
`otel_api_requests` / the session's JSONL events, and a request with no date anywhere is
skipped (counted).

Columns: stored sum ("before") and `Pricer` sum ("after") over the items priced on both sides,
their ratio (one row per source, model and calendar month, so a dated price change shows up as
its own row), the number of items unpriced before / after, and a cause for every item whose
number moved: `#2` (a price change the legacy table carried and the catalog does not),
`#7` (a date before the catalog's first row now prices at the first row), `#8` (a model string
newly matched / newly ambiguous), else `unexplained`.

**A cause is assigned only when its mechanism accounts for the move**, never as a fallback:
`#2` only for `claude-sonnet-5` items dated on or after 2026-09-01 whose repriced/stored ratio
matches catalog/legacy (2/3, within rounding); `#7` only when the item was unpriced before, its
date precedes the model's first catalog row, and the model string is already a registry id (a
date before the first row is the only reason the retired table could not price it); `#8` only
for a model string that is newly matched (a dated or aliased id now normalising to a registry
id) or newly ambiguous. Everything else whose number moved is `unexplained`.

**`fold:cache_busts` is a modelling difference, not a price-table effect.** The report
reprices a bust's rewritten tokens as one combined `cache_write` at the 1h rate (what the real
fold does); synthetic data (the org-expand generator) prices a bust per cache lifetime (5m/1h
split, #67). Ratios of roughly 1.06 to 1.20 on synthetic `cache_busts` rows are therefore
reported `unexplained` on purpose.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import create_engine, text

from common.model_pricing import Pricer, price_to_micros
from common.msging.log import configure_logging, get_logger
from server.storage.queries import QueryCatalog

#: Price moves the retired legacy price table carried that the catalog does not (§4 #2):
#: `(registry model_id, first affected date)`. Sonnet 5 was $3/$15 from 2026-09-01 in the
#: legacy table; the catalog (LiteLLM, the price authority) keeps $2/$10.
LEGACY_PRICE_CHANGES: dict[str, date] = {"claude-sonnet-5": date(2026, 9, 1)}

MULTI_MODEL = "(multi-model)"

SESSIONS_SQL = text("""
    SELECT i.session_id, i.otel, i.models, i.info, s.started_at
    FROM session_info i
    LEFT JOIN sessions s ON s.session_id = i.session_id
    ORDER BY i.session_id
""")


@dataclass
class Item:
    source: str
    model: str                     # row label: registry/usage model string, or MULTI_MODEL
    usage_model: str | None        # the observed string to resolve (None for multi-model)
    at: date | None
    before: int | None
    after: int | None
    vendor: int | None = None      # ledger only: the vendor's own cost, informational


def _as_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


# ------------------------------------------------------------------ repricing (pure)

def _micros(pricer: Pricer, model: str | None, tokens: dict, at: datetime | date) -> int | None:
    price = pricer.price_tokens(model, tokens, at)
    return price_to_micros(price)


def _channel_micros(pricer: Pricer, models: dict, kind: str, at: datetime) -> int | None:
    """`graph_fold._price_channel`: one channel summed across models, None if any contributing
    model is unpriced, rounded to micros once."""
    total = Decimal(0)
    for model, bucket in models.items():
        count = (bucket or {}).get(kind) or 0
        if not count:
            continue
        price = pricer.price_tokens(model, {kind: count}, at)
        if price is None:
            return None
        total += price
    return price_to_micros(total)


def _sum_or_none(values: list[int | None]) -> int | None:
    return None if any(v is None for v in values) else sum(values)


def ledger_items(pricer: Pricer, block: dict, otel_rows: list, jsonl_rows: list) -> tuple[list[Item], int]:
    """One `Item` per stored ledger record; `(items, skipped_without_date)`."""
    otel_by_id = {r.request_id: r for r in otel_rows}
    jsonl_by_id = {r.request_id: r for r in jsonl_rows}
    items: list[Item] = []
    skipped = 0
    for rec in block.get("requests") or []:
        rid = rec.get("request_id")
        otel, jsonl = otel_by_id.get(rid), jsonl_by_id.get(rid)
        if otel is not None:
            at = _as_dt(otel.event_ts)
            detail = getattr(jsonl, "cache_write_detail", None) if jsonl else None
        elif jsonl is not None:
            at = _as_dt(jsonl.event_ts)
            detail = jsonl.cache_write_detail
        else:
            at, detail = None, None
        if not at:
            skipped += 1
            continue
        price = pricer.price_tokens(
            rec.get("model"),
            {"input": rec.get("input_tokens"), "output": rec.get("output_tokens"),
             "cache_read": rec.get("cache_read_tokens"),
             "cache_write": rec.get("cache_creation_tokens")},
            at, cache_write_detail=detail)
        items.append(Item("ledger", rec.get("model") or "unknown", rec.get("model"), at.date(),
                          rec.get("cost_est_usd_micros"), price_to_micros(price),
                          vendor=rec.get("cost_usd_micros")))
    return items, skipped


def fold_items(pricer: Pricer, models: dict, info: dict, started_at: datetime) -> tuple[list[Item], int]:
    """Items for one session's persisted fold money; `(items, resume_cost_not_repriceable)`."""
    models = models or {}
    info = info or {}
    names = sorted(models)
    single = names[0] if len(names) == 1 else None
    label = single or MULTI_MODEL
    at = started_at
    day = at.date()
    items: list[Item] = []

    cc = info.get("channel_cost")
    if cc and models:
        before = _sum_or_none([(cc.get(ch) or {}).get("usd_micros") for ch in ("input", "output", "recache")])
        after = _sum_or_none([_channel_micros(pricer, models, kind, at)
                              for kind in ("input", "output", "cache_write")])
        items.append(Item("fold:channel_cost", label, single, day, before, after))

    carry = info.get("carry")
    if carry and models:
        items.append(Item("fold:carry", label, single, day, carry.get("carry_usd_micros"),
                          _channel_micros(pricer, models, "cache_read", at)))

    not_repriceable = 0
    resume = info.get("resume_cost") or {}
    events = resume.get("events") or []
    if events:
        if single is None:
            not_repriceable = 1                  # events carry no model; see module docstring
        else:
            after = _sum_or_none([_micros(pricer, single, {"cache_write": e.get("reload_tokens") or 0}, at)
                                  for e in events])
            items.append(Item("fold:resume_cost", single, single, day,
                              resume.get("total_usd_micros"), after))

    for ev in (info.get("cache_busts") or {}).get("events") or []:
        model = ev.get("model") or "unknown"
        items.append(Item("fold:cache_busts", model, model, day, ev.get("usd_micros"),
                          _micros(pricer, model, {"cache_write": ev.get("rewrite_tokens") or 0}, at)))
    return items, not_repriceable


#: catalog / retired price for Sonnet 5 from 2026-09-01: $2/$10 over $3/$15.
_SONNET_5_RATIO = Decimal(2) / Decimal(3)


def cause_of(pricer: Pricer, item: Item) -> str | None:
    """Why this item's number moved (design §4); None when it did not. A cause is returned
    only when its mechanism accounts for the move, else `"unexplained"` (module docstring)."""
    if item.before == item.after:
        return None
    match = pricer.resolve(item.usage_model)
    registry_id = match.model_id if match.status == "matched" else None
    if item.before is None:
        if registry_id and registry_id != item.usage_model:
            return "#8"                              # newly matched dated / aliased string
        effective = pricer.effective_from(item.usage_model, item.at) if registry_id and item.at else None
        if effective and item.at < effective:
            return "#7"                              # only an early date left it unpriced
        return "unexplained"
    if item.after is None:
        return "#8" if match.status == "ambiguous" else "unexplained"
    changed_from = LEGACY_PRICE_CHANGES.get(registry_id or "")
    if (changed_from and item.at and item.at >= changed_from
            and abs(Decimal(item.after) - Decimal(item.before) * _SONNET_5_RATIO)
            <= max(Decimal(1), Decimal(item.before) * Decimal("0.005"))):
        return "#2"
    # #7 never explains an item priced on both sides: the earliest-row rule only differs from
    # the retired table's own block when a price changed in between, which is #2's case.
    return "unexplained"


# ------------------------------------------------------------------ aggregation / render

def summarize(pricer: Pricer, items: list[Item]) -> list[dict]:
    groups: dict[tuple[str, str, str], list[Item]] = defaultdict(list)
    for it in items:
        groups[(it.source, it.model, it.at.strftime("%Y-%m") if it.at else "-")].append(it)
    rows = []
    for (source, model, period), group in sorted(groups.items()):
        # Vendor-priced ledger requests never move the ratios (their cost is the vendor's).
        own = [i for i in group if i.vendor is None]
        both = [i for i in own if i.before is not None and i.after is not None]
        before_sum = sum(i.before for i in both)
        after_sum = sum(i.after for i in both)
        causes = Counter(c for i in own if (c := cause_of(pricer, i)))
        vendor_rows = [i for i in group if i.vendor is not None and i.after is not None]
        rows.append({
            "source": source, "model": model, "period": period, "n": len(own),
            "before": before_sum, "after": after_sum,
            "ratio": (Decimal(after_sum) / Decimal(before_sum)) if before_sum else None,
            "unpriced_before": sum(1 for i in own if i.before is None),
            "unpriced_after": sum(1 for i in own if i.after is None),
            "causes": dict(sorted(causes.items())),
            "vendor_n": len(vendor_rows),
            "drift": (Decimal(sum(i.after for i in vendor_rows)) / Decimal(sum(i.vendor for i in vendor_rows))
                      if vendor_rows and sum(i.vendor for i in vendor_rows) else None),
        })
    return rows


def render(rows: list[dict], fingerprint: str) -> str:
    lines = [f"# Pricing before/after (catalog `{fingerprint}`)", "",
             "| source | model | month | items | before (micros) | after (micros) | ratio | "
             "unpriced before | unpriced after | cause | vendor drift (info) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ratio = "-" if r["ratio"] is None else f"{r['ratio']:.3f}"
        cause = ", ".join(f"{k} x{v}" for k, v in r["causes"].items()) or "-"
        drift = "-" if r["drift"] is None else f"{r['drift']:.3f} (n={r['vendor_n']})"
        lines.append(f"| {r['source']} | {r['model']} | {r['period']} | {r['n']} | {r['before']} | {r['after']} | "
                     f"{ratio} | {r['unpriced_before']} | {r['unpriced_after']} | {cause} | {drift} |")
    lines += ["", "Cause: `#2` Sonnet 5 price (catalog $2/$10 vs retired $3/$15, from 2026-09-01); `#7` date "
              "before the catalog's first row now priced; `#8` model string newly matched or ambiguous; "
              "`unexplained` otherwise.", "",
              "`fold:cache_busts` reprices a bust's rewritten tokens as one combined write at the 1h rate "
              "(the real fold's behaviour), while synthetic data prices per cache lifetime (5m/1h, #67): "
              "ratios of about 1.06 to 1.20 there are a modelling difference, not a price-table effect, "
              "and are reported `unexplained`."]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ DB read

def collect(conn, catalog: QueryCatalog, pricer: Pricer, log) -> list[Item]:
    items: list[Item] = []
    skipped_no_date = resume_skipped = sessions = 0
    for row in conn.execute(SESSIONS_SQL).all():
        sessions += 1
        block = row.otel or {}
        if block.get("requests"):
            vendor = block.get("vendor") or "claude_code"
            params = {"vendor": vendor, "session_id": row.session_id}
            otel_rows = conn.execute(catalog.get("otel", "requests_for_session"), params).all()
            jsonl_rows = conn.execute(catalog.get("otel", "jsonl_requests_for_session"), params).all()
            got, skipped = ledger_items(pricer, block, otel_rows, jsonl_rows)
            items += got
            skipped_no_date += skipped
        if row.info and row.started_at is not None:
            got, skipped = fold_items(pricer, row.models, row.info, _as_dt(row.started_at))
            items += got
            resume_skipped += skipped
    log.info("pricing_report_collected", sessions=sessions, items=len(items),
             ledger_requests_without_date=skipped_no_date,
             multi_model_resume_cost_skipped=resume_skipped)
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dsn", default=os.environ.get("TALIUM_DB_DSN"))
    parser.add_argument("--org", default=None, help="org id for channel selection (default: vendor channels)")
    parser.add_argument("--version", default=None, help="catalog version (default: current)")
    parser.add_argument("--queries-dir", default="config/queries")
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn (or TALIUM_DB_DSN) is required")

    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger("server.worker.cli_pricing_report")
    pricer = Pricer.load(args.org, args.version)
    catalog = QueryCatalog.load(args.queries_dir)
    engine = create_engine(args.dsn)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            items = collect(conn, catalog, pricer, log)
    finally:
        engine.dispose()
    print(render(summarize(pricer, items), pricer.fingerprint), end="")
    log.info("pricing_report_done", fingerprint=pricer.fingerprint, items=len(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
