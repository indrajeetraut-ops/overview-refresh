"""The OTel true-up worker (otel-intake design §4.4, arch §8.4).

Turns raw OTel staging rows into session cost truth. A poller, not a doorbell: the OTel
stream is constant, so a timer is the honest trigger (§4.4) — there is nothing to be woken by
that would not already be there on the next tick.

```
each tick, per vendor -- ONE transaction:
  watermark = read (FOR UPDATE)
  touched   = distinct session_id with otel rows newer than it
  for each session:
      metrics fold   branch on temporality; group by canonical measure
      request join   three-way against the JSONL side on request_id
      write          the cost block into session_info.info["otel"]
  advance watermark
```

§4.4 sketches "one transaction per session" *and* "the watermark advances in the same
transaction as the write" — which cannot both hold once a tick touches more than one session.
Resolved in favour of the crash-safety requirement (arch §8.3): **one transaction per vendor
per tick**, work and watermark together. A crash aborts both and the next tick re-finds the
work; the per-session split would have needed the watermark to advance outside the writes it
describes, which is the one arrangement §8.3 rules out. The tick is small enough to afford it
— a few hundred sessions of indexed aggregates (§6.2) — and its idempotency makes a retried
tick free.

**The one thing that must not be got wrong.** Cumulative datapoints are running totals, not
increments. Summing them inflated the 2026-08-02 reconciliation by 150×. The fold reads the
*last value per reset epoch* and sums *across* epochs — `_fold_series` is where that lives,
and the acceptance battery asserts the correct number differs from the naive one.

**Idempotent by construction.** Recomputing a session from unpruned rows yields identical
state: the fold reads, it never accumulates. That is also what makes late arrival safe —
`otel_only` is a transient label for an interactive request whose JSONL has not landed yet,
and every tick that sees new JSONL rows re-runs the join and migrates it to `matched`.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.orm import Session as DbSession

from common.model_pricing import Pricer, price_to_micros
from server.ingest.otlp.mapping import MappingRegistry, VendorMapping
from server.storage import session_scalars
from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow
from server.worker.graph_fold import load_pricing_survivable

log = structlog.get_logger(module="worker.otel_trueup")

#: Where the cost block lands: `session_info.otel`, this worker's own column. Not a key in
#: `session_info.info` -- the JSONL fold replaces that map wholesale on every arrival of the
#: session, so a key there would be deleted by the next upload. §4.3's rule still holds
#: either way: the dashboard reads spend from the cost block, never from the otel_* tables.
COST_BLOCK_COLUMN = "otel"

#: **Which pipes saw a request.** Answers "what do we have", not "can we trust the number" --
#: those are different questions and one field cannot answer both.
SRC_BOTH = "both"               # matched on request_id
SRC_OTEL_ONLY = "otel_only"     # background call, or JSONL not arrived yet (transient)
SRC_JSONL_ONLY = "jsonl_only"   # interrupted, pre-telemetry, or a JSONL-only deployment

#: **Where the dollar figure came from.** This is what a dashboard must surface: a number
#: derived from tokens is a different kind of claim from one the vendor reported.
BASIS_VENDOR = "vendor"         # the vendor's own cost_usd, adopted as truth
BASIS_ESTIMATED = "estimated"   # priced from tokens via the model_pricing catalog
BASIS_UNKNOWN = "unknown"       # no vendor cost and no price for that model/date

#: **Why OTel is missing**, where we can tell. `pre_otel` is computable -- the request predates
#: the first thing OTel ever saw for this org -- and separating it matters: an operator who
#: enables telemetry mid-history would otherwise see hundreds of gaps indistinguishable from
#: real ones. Everything else stays `unobserved`: OTel was running and still did not see it,
#: which for an interactive request means the user interrupted it.
GAP_PRE_OTEL = "pre_otel"
GAP_UNOBSERVED = "unobserved"

#: Shape of the stored cost block. Bump it whenever the block's keys change, and the
#: `touched_sessions` query re-folds every session still on an older one.
#:
#: Without this a shape change strands data: the arms that decide what to re-fold read keys
#: from the block itself, so a session holding an *old-shaped* block matches none of them and
#: is never revisited. It went unnoticed until 20 real sessions sat on the previous shape --
#: idle, so no new events, and invisible to every other arm.
#:
#:   1  provenance: {otel, otel_only, jsonl_est}
#:   2  sources + cost_basis + otel_gap, estimated costs, calibration
#:   3  by_query_source, active_time_s promoted to the block; datasource/cost_effective_usd_micros/
#:      jsonl_lag_s promoted to session_info columns (written alongside this block, not in it);
#:      otel_only sessions get a stub `sessions` row
#:   4  by_query_source no longer requires OTel evidence: a jsonl_only session's records are
#:      unconditionally `query_source: None` (JSONL has no such attribute), and that is not
#:      ambiguous the way v3 treated it -- a background call structurally cannot reach a JSONL
#:      transcript at all, so "every record here is interactive" is a fact, not a guess. Gate
#:      is `bool(records)` now, not `has_otel_evidence`; a jsonl_only session's spend now
#:      belongs in the same "interactive" total an OTel-confirmed one does.
#:   5  each ledger record gets `cost_effective_usd_micros` (vendor, else estimate -- the same
#:      precedence `build_cost_block`'s block-level `effective` already used); `by_query_source`
#:      buckets sum it as a new field. Fixes a double-count: `cost_est_usd_micros` was computed
#:      unconditionally, even on a request that already had a real vendor cost, so every
#:      consumer that displayed `cost_usd_micros + cost_est_usd_micros` (the overview widget,
#:      insight 26) was showing vendor-plus-estimate for the same spend, not vendor-or-estimate.
BLOCK_VERSION = 5

#: Drift above this fraction between an estimate and the vendor's own cost, on a request where
#: both exist, means the price table no longer describes reality. 0.5% is far above the
#: rounding the vendor's own micros introduce (measured max residual: under one micro on 61
#: requests) and far below anything a real rate change would produce.
DRIFT_ALARM = Decimal("0.005")

#: Canonical measures the cost block reports, in a fixed order so the stored block is stable
#: across ticks (and therefore diffable, and therefore idempotency is observable).
TOKEN_MEASURES = ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation")

#: `_join_requests` record field -> canonical measure name, for `_by_query_source`'s per-group
#: token totals -- the same four quantities `TOKEN_MEASURES` names, read from the ledger instead
#: of the metrics fold.
_RECORD_TOKEN_FIELDS = {
    "input_tokens": "tokens_input",
    "output_tokens": "tokens_output",
    "cache_read_tokens": "tokens_cache_read",
    "cache_creation_tokens": "tokens_cache_creation",
}

#: The stable key for "OTel reported no `query_source`", i.e. an interactive request. A JSON
#: `null` key does not round-trip predictably through JSONB, and the ledger already treats
#: `None` this way (`_join_requests`'s JSONL-only branch sets it explicitly) -- this is that
#: same sentinel, named.
QUERY_SOURCE_INTERACTIVE = "interactive"


@dataclass
class TrueUpStats:
    """Per-tick counters for the log line. Identifiers and counts only (arch §13)."""

    sessions: int = 0
    sources: Counter = field(default_factory=Counter)     # both | otel_only | jsonl_only
    cost_basis: Counter = field(default_factory=Counter)  # vendor | estimated | unknown
    otel_gap: Counter = field(default_factory=Counter)    # pre_otel | unobserved
    #: worst estimate-vs-vendor relative error seen this tick, per model
    drift: dict = field(default_factory=dict)
    pruned_points: int = 0


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _fmt(value: Decimal) -> str:
    """A decimal as plain text — no trailing zeros, no exponent.

    Trailing zeros would make the stored block depend on the wire encoding the client used
    (see `receiver._normalized`), and `str(Decimal)` reaches for scientific notation on
    normalized round numbers, which would put `5.4525E+5` in a cost block.
    """
    return format(value.normalize(), "f")


def _sum_by_measure(rows: list, mapping: VendorMapping) -> dict[str, Decimal]:
    """Datapoint rows -> `{canonical measure: total}`.

    **The temporality branch is in which rows arrive here, not in this function.** Cumulative
    series come in as one row per (series, reset epoch) carrying that epoch's *last* value —
    `series_last_values` does that pick — so summing them is summing across epochs, which is
    correct. Delta series come in as every datapoint, because there each value is an
    increment. Summing the wrong row set is the 150× inflation in one direction and a total
    loss of all but the final interval in the other, so the caller picks the query and an
    unrecognised temporality reaches neither.

    Gauges collapse to a single epoch through the same last-value query and read correctly as
    "the last value"; histograms arrive with their own sum already in `value` (§8's recorded
    v1 limit — bucket detail is dropped).

    Grouping is by *canonical measure(s)*, which only the mapping config can name. A datapoint
    with no measure (`session.count`, `lines_of_code.count`) is stored and simply does not
    fold — the normal case, not a gap. A datapoint can also match **more than one** measure --
    `measures_of`, not `measure_of` -- because a `query_source`-narrowed sub-view
    (`tokens_input_bg`, PM.3) and its unconditional total (`tokens_input`) are two different
    questions about the same value, not a partition: without this, whichever selector happened
    to be declared first in the YAML would silently swallow every datapoint of the other.
    """
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        for measure in mapping.measures_of(row.metric_name, row.attrs or {}):
            totals[measure] += _dec(row.value)
    return dict(totals)


def _estimate(record: dict, at: Any, pricing: Pricer | None,
              cache_write_detail: list | None) -> int | None:
    """Price one request's tokens, in micros, or None if the model has no price.

    Runs on **every** request, including ones the vendor already priced — that redundancy is
    what makes the calibration check below possible, and it costs a dictionary lookup.
    """
    if pricing is None or not at:
        return None
    price = pricing.price_tokens(
        record.get("model"),
        {"input": record.get("input_tokens"), "output": record.get("output_tokens"),
         "cache_read": record.get("cache_read_tokens"),
         "cache_write": record.get("cache_creation_tokens")},
        at, cache_write_detail=cache_write_detail,
    )
    return price_to_micros(price)


def _join_requests(otel_rows: list, jsonl_rows: list, pricing: Pricer | None = None,
                   otel_since: datetime | None = None) -> tuple[list[dict], dict]:
    """The three-way join on `request_id` (§4.4), with each side's cost basis recorded.

    Both sources are already deduped — OTel by its `(vendor, request_id)` primary key, JSONL
    by the named query's `DISTINCT ON`. Neither dedup is optional: without them the same
    request appears several times and the totals inflate on the JSONL side exactly the way
    naive summing inflates them on the metrics side.

    **Two labels per request, not one.** `sources` says which pipes saw it; `cost_basis` says
    where the dollar figure came from. A single label conflated them, and the cost was three
    unrelated situations wearing one name: an interrupted request, a session that predates
    telemetry, and an org that deploys JSONL only. They need different answers from a
    dashboard, so they need different fields here.
    """
    by_id = {r.request_id: r for r in otel_rows}
    jsonl_by_id = {r.request_id: r for r in jsonl_rows}
    sources = {SRC_BOTH: 0, SRC_OTEL_ONLY: 0, SRC_JSONL_ONLY: 0}
    bases = {BASIS_VENDOR: 0, BASIS_ESTIMATED: 0, BASIS_UNKNOWN: 0}
    gaps: Counter = Counter()
    records: list[dict] = []

    for request_id, otel in by_id.items():
        jsonl = jsonl_by_id.get(request_id)
        # The TTL split is JSONL's alone: OTel's api_request reports one combined
        # cache_creation number, so a matched request prices more accurately than either
        # source could on its own.
        detail = getattr(jsonl, "cache_write_detail", None) if jsonl else None
        record = {
            "request_id": request_id,
            "sources": SRC_BOTH if jsonl else SRC_OTEL_ONLY,
            "model": otel.model,
            "input_tokens": otel.input_tokens,
            "output_tokens": otel.output_tokens,
            "cache_read_tokens": otel.cache_read_tokens,
            "cache_creation_tokens": otel.cache_creation_tokens,
            "cost_usd_micros": otel.cost_usd_micros,
            "query_source": otel.query_source,
        }
        record["cost_est_usd_micros"] = _estimate(record, otel.event_ts, pricing, detail)
        record["cost_basis"] = (BASIS_VENDOR if otel.cost_usd_micros is not None
                                else BASIS_ESTIMATED if record["cost_est_usd_micros"] is not None
                                else BASIS_UNKNOWN)
        # Vendor, else estimate -- never both (build_cost_block's own `effective` does the same
        # precedence at the block level; stored per-record here so every downstream consumer
        # summing across records -- by_query_source, insight 26/27 -- has one correct number to
        # read instead of re-deriving it, which is exactly how the double-count bug happened:
        # `cost_est_usd_micros` is computed unconditionally, even when a real vendor cost
        # already exists, so summing it alongside `cost_usd_micros` counts the same request's
        # spend twice.
        record["cost_effective_usd_micros"] = (
            record["cost_usd_micros"] if record["cost_usd_micros"] is not None
            else (record["cost_est_usd_micros"] or 0))
        sources[record["sources"]] += 1
        bases[record["cost_basis"]] += 1
        records.append(record)

    for request_id, jsonl in jsonl_by_id.items():
        if request_id in by_id:
            continue
        # OTel has no row for this request. Three causes, and one of them is computable: if
        # the request predates the first thing OTel ever saw for this org, telemetry simply
        # was not on yet. Otherwise OTel was watching and still missed it, which for an
        # interactive request means the user interrupted it (§7) -- OTel fires on completion.
        event_ts = _parse_ts(jsonl.event_ts)
        gap = (GAP_PRE_OTEL if otel_since and event_ts and event_ts < otel_since
               else GAP_UNOBSERVED)
        record = {
            "request_id": request_id,
            "sources": SRC_JSONL_ONLY,
            "otel_gap": gap,
            "model": jsonl.model,
            "input_tokens": jsonl.input_tokens,
            "output_tokens": jsonl.output_tokens,
            "cache_read_tokens": jsonl.cache_read_tokens,
            "cache_creation_tokens": jsonl.cache_creation_tokens,
            "cost_usd_micros": None,             # the vendor never reported one; never will
            "query_source": None,
        }
        record["cost_est_usd_micros"] = _estimate(
            record, event_ts, pricing, jsonl.cache_write_detail)
        record["cost_basis"] = (BASIS_ESTIMATED if record["cost_est_usd_micros"] is not None
                                else BASIS_UNKNOWN)
        record["cost_effective_usd_micros"] = record["cost_est_usd_micros"] or 0
        sources[SRC_JSONL_ONLY] += 1
        bases[record["cost_basis"]] += 1
        gaps[gap] += 1
        records.append(record)

    records.sort(key=lambda r: r["request_id"])   # stable block -> diffable -> idempotent
    return records, {"sources": sources, "cost_basis": bases, "otel_gap": dict(gaps)}


#: **Which pipes a deployment runs**, not whether a particular request joined (MODES §3.2).
#: `sources[SRC_BOTH] > 0` wins outright: even one matched request proves both pipes are live.
#: Otherwise it comes down to which kinds of evidence exist at all -- `has_otel` cannot key on
#: the request ledger alone, because a metrics-only fleet (`OTEL_LOGS_EXPORTER=none`) produces
#: `measures` and zero request rows, and a derivation keyed on requests would leave it
#: unclassified forever.
def _derive_datasource(measures: dict, otel_rows: list, jsonl_rows: list,
                       sources_counts: dict) -> str | None:
    has_otel = bool(measures) or bool(otel_rows)
    has_jsonl = bool(jsonl_rows)
    if sources_counts[SRC_BOTH] > 0:
        return SRC_BOTH
    if has_otel and has_jsonl:
        # Two pipes deployed, but this fold's requests happened not to overlap. Mode describes
        # which pipes are deployed, not whether a particular join succeeded -- conflating the
        # two would flip a healthy two-source org's mode on a single interrupted request.
        return SRC_BOTH
    if has_otel:
        return SRC_OTEL_ONLY
    if has_jsonl:
        return SRC_JSONL_ONLY
    return None


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def calibrate(records: list[dict]) -> dict:
    """Compare the estimate against the vendor's own cost, wherever both exist.

    This is the check that makes an estimate defensible. A request the vendor priced *and*
    that we can price from tokens is a free test of the price table, and there are usually
    plenty: on a hybrid deployment every matched request is one. Drift means the table is
    stale or the vendor changed rates — and it surfaces on the next tick rather than in a
    quarterly audit.

    Reported per model, because a rate change affects one model at a time.
    """
    per_model: dict[str, dict] = {}
    for record in records:
        vendor_cost, estimate = record.get("cost_usd_micros"), record.get("cost_est_usd_micros")
        if vendor_cost is None or estimate is None or vendor_cost == 0:
            continue
        model = record.get("model") or "unknown"
        bucket = per_model.setdefault(model, {"n": 0, "max_abs_micros": 0,
                                              "max_rel": Decimal(0)})
        delta = abs(estimate - vendor_cost)
        bucket["n"] += 1
        bucket["max_abs_micros"] = max(bucket["max_abs_micros"], delta)
        bucket["max_rel"] = max(bucket["max_rel"], Decimal(delta) / Decimal(abs(vendor_cost)))

    return {model: {"n": b["n"], "max_abs_micros": b["max_abs_micros"],
                    "max_rel": _fmt(b["max_rel"].quantize(Decimal("0.000001")))}
            for model, b in sorted(per_model.items())}


def _by_query_source(records: list[dict]) -> dict:
    """Session cost split by `query_source` (PM.3, DASH §3.2/§6 insight 26): "what are
    background calls costing me?" -- a question `build_cost_block`'s totals cannot answer
    because they collapse `query_source` away.

    Exact wherever it runs, because it reads the per-request ledger `_join_requests` already
    built -- unlike the metrics-side split (`config/otel/claude_code.yaml`'s `*_bg` measures),
    which exists only because a metrics-only fleet has no ledger to read at all.

    Key ordering sorted and values formatted through `_fmt`, same as the rest of the block --
    the block must stay diffable, which is what makes idempotency observable (the reason
    `TOKEN_MEASURES` is a fixed tuple).
    """
    groups: dict[str, dict] = {}
    for record in records:
        key = record.get("query_source") or QUERY_SOURCE_INTERACTIVE
        bucket = groups.setdefault(key, {"requests": 0, "tokens": defaultdict(Decimal),
                                         "cost_usd_micros": 0, "cost_est_usd_micros": 0,
                                         "cost_effective_usd_micros": 0})
        bucket["requests"] += 1
        for field, measure in _RECORD_TOKEN_FIELDS.items():
            bucket["tokens"][measure] += _dec(record.get(field))
        if record.get("cost_usd_micros") is not None:
            bucket["cost_usd_micros"] += record["cost_usd_micros"]
        if record.get("cost_est_usd_micros") is not None:
            bucket["cost_est_usd_micros"] += record["cost_est_usd_micros"]
        # Vendor, else estimate, per record -- `cost_usd_micros` and `cost_est_usd_micros` above
        # are kept separate for transparency (same three-field shape as the block-level total),
        # but a consumer wanting "what did this cost" must read *this* field, never the sum of
        # the other two: `cost_est_usd_micros` is populated even on a request that already has
        # a real vendor cost, so summing both double-counts every vendor-priced request.
        bucket["cost_effective_usd_micros"] += record.get("cost_effective_usd_micros") or 0

    return {
        source: {
            "requests": bucket["requests"],
            "tokens": {m: _fmt(bucket["tokens"].get(m, Decimal(0))) for m in TOKEN_MEASURES},
            "cost_usd_micros": bucket["cost_usd_micros"],
            "cost_est_usd_micros": bucket["cost_est_usd_micros"],
            "cost_effective_usd_micros": bucket["cost_effective_usd_micros"],
        }
        for source, bucket in sorted(groups.items())
    }


def build_cost_block(measures: dict[str, Decimal], records: list[dict], counts: dict,
                     vendor: str, computed_at: datetime,
                     otel_since: datetime | None = None,
                     pricing_fingerprint: str | None = None) -> dict:
    """The `session_info.otel` block.

    Metrics and requests are reported side by side rather than reconciled into one number:
    they are two independent views of the same session (a counter fold and a request ledger),
    and where they disagree that disagreement is the signal the usage reconciler wants — not
    something to average away here.
    """
    vendor_total = sum(r["cost_usd_micros"] for r in records
                       if r["cost_usd_micros"] is not None)
    est_total = sum(r["cost_est_usd_micros"] for r in records
                    if r.get("cost_est_usd_micros") is not None)
    # What a dashboard should show: the vendor's figure where it exists, the estimate where it
    # does not. Kept separate from both components so nobody has to guess which they are
    # looking at, and so an estimate can never silently overwrite ground truth.
    effective = sum(
        r["cost_usd_micros"] if r["cost_usd_micros"] is not None
        else (r.get("cost_est_usd_micros") or 0)
        for r in records
    )
    unknown = counts["cost_basis"][BASIS_UNKNOWN]

    block = {
        "block_version": BLOCK_VERSION,
        # Which price table produced the estimates here. A different one means this block is
        # stale and the tick re-folds it.
        "pricing_fingerprint": pricing_fingerprint,
        "vendor": vendor,
        "computed_at": computed_at.isoformat(),
        # from the metrics fold: whole-session totals, present even with no request events
        "measures": {k: _fmt(v) for k, v in sorted(measures.items())},
        # from the request ledger: per-request truth, its source and its cost basis
        "requests": records,
        "sources": dict(sorted(counts["sources"].items())),
        "cost_basis": dict(sorted(counts["cost_basis"].items())),
        "otel_gap": dict(sorted(counts["otel_gap"].items())),
        # When telemetry first covered this org. A request older than this was never
        # observable, however healthy the pipeline is.
        "otel_since": otel_since.isoformat() if otel_since else None,
        "cost_usd_micros": vendor_total,             # vendor-reported only
        "cost_est_usd_micros": est_total,            # priced from tokens only
        "cost_effective_usd_micros": effective,      # what to show: vendor, else estimate
        #: False while any request has no cost at all -- the honest flag for "this total is a
        #: floor". With the estimator in place this is now true for a JSONL-only session too,
        #: and false only where a model has no price on that date.
        "cost_complete": unknown == 0,
        "calibration": calibrate(records),
        "tokens": {
            m: _fmt(measures.get(m, Decimal(0))) for m in TOKEN_MEASURES
        },
        # Already folded into `measures` via config/otel/*.yaml; promoted to the top level
        # because it is the only exact active-time figure available, and the only one that
        # exists at all in `otel_only` (PM.4) -- DASH §3.1's Σ-turn-durations fallback needs
        # JSONL.
        "active_time_s": _fmt(measures.get("active_time_s", Decimal(0))),
    }
    # `by_query_source` is *absent*, not `{}`, when there is nothing to summarize -- but unlike
    # v3, presence no longer requires OTel evidence (BLOCK_VERSION 4): a jsonl_only session's
    # records are unconditionally `query_source: None`, and that is a fact (background calls
    # cannot reach a JSONL transcript at all), not an ambiguous default standing in for "can't
    # tell." Its spend belongs in the same `interactive` bucket an OTel-confirmed one does.
    if records:
        block["by_query_source"] = _by_query_source(records)
    return block


class OtelTrueUp:
    """One tick of the true-up fold. Holds the mapping registry; everything else per call."""

    def __init__(self, catalog: QueryCatalog, registry: MappingRegistry,
                 watermark_lag_s: float = 5.0,
                 pricing: Pricer | None = None,
                 pricing_org: str | None = None,
                 pricing_version: str | None = None) -> None:
        self.catalog = catalog
        self.registry = registry
        #: How far behind the newest row the watermark is allowed to advance. A write that
        #: commits after this tick read the maximum would otherwise fall in the gap and never
        #: be folded. The overlap costs nothing because the fold is idempotent.
        self.watermark_lag_s = watermark_lag_s
        #: Loaded once at construction, like the mapping registry: a price config that fails
        #: to parse must stop the worker starting, not silently unprice every request.
        #: An absent catalog is survivable, though -- the fold still runs and records
        #: `cost_basis: unknown`, which is the truth when no table exists.
        self.pricing = (pricing if pricing is not None
                        else load_pricing_survivable(pricing_org, pricing_version))
        #: Stamped into every block, and the trigger that re-folds sessions priced by an
        #: older table (see the `touched_sessions` arm).
        self.pricing_fingerprint = self.pricing.fingerprint if self.pricing else None

    # ------------------------------------------------------------------ tick

    def tick(self, now: datetime | None = None) -> TrueUpStats:
        now = now or datetime.now(timezone.utc)
        stats = TrueUpStats()
        for vendor in self.registry.vendors():
            stats = self._tick_vendor(vendor, now, stats)
        return stats

    def _tick_vendor(self, vendor: str, now: datetime, stats: TrueUpStats) -> TrueUpStats:
        mapping = self.registry.get(vendor)
        if mapping is None:
            return stats

        with session_scope() as s:
            watermark = self._read_watermark(s, vendor)
            params = {"vendor": vendor, "watermark": watermark}
            session_ids = s.execute(
                self.catalog.get("otel", "touched_sessions"),
                {**params, "block_version": BLOCK_VERSION,
                 "pricing_fingerprint": self.pricing_fingerprint}).scalars().all()
            high = s.execute(
                self.catalog.get("otel", "max_received_at"), params).scalar_one()

            for session_id in session_ids:
                counts, drift = self._fold_session(s, vendor, mapping, session_id, now)
                stats.sessions += 1
                stats.sources.update(counts["sources"])
                stats.cost_basis.update(counts["cost_basis"])
                stats.otel_gap.update(counts["otel_gap"])
                for model, worst in drift.items():
                    stats.drift[model] = max(stats.drift.get(model, Decimal(0)), worst)

            if session_ids:
                # Same transaction as the writes above (arch §8.3): a crash takes both or
                # neither, and the next tick re-finds work it did not finish.
                self._advance_watermark(s, vendor, high)

        log.info("otel_trueup_tick", vendor=vendor, sessions=len(session_ids),
                 sources=dict(stats.sources), cost_basis=dict(stats.cost_basis),
                 otel_gap=dict(stats.otel_gap))
        for model, worst in stats.drift.items():
            if worst > DRIFT_ALARM:
                # The price table no longer reproduces costs the vendor itself reported.
                # Either a rate changed or the entry was wrong; both need a human, and both
                # would otherwise be invisible until someone questioned an invoice.
                log.warning("pricing_drift", vendor=vendor, model=model,
                            max_rel=_fmt(worst.quantize(Decimal("0.000001"))),
                            threshold=_fmt(DRIFT_ALARM))
        return stats

    # ------------------------------------------------------------- one session

    def _fold_session(self, s: DbSession, vendor: str, mapping: VendorMapping,
                      session_id: str, now: datetime) -> tuple[dict, dict]:
        last_values = s.execute(self.catalog.get("otel", "series_last_values"),
                                {"vendor": vendor, "session_id": session_id}).all()
        # Last value per epoch, summed across epochs. Gauges qualify on their *kind*, not
        # their temporality: OTLP gauges carry no `aggregationTemporality` at all, so they
        # arrive "unspecified" and a temporality-only test would drop every one of them.
        measures = _sum_by_measure(
            [r for r in last_values
             if r.temporality == "cumulative" or r.metric_kind == "gauge"], mapping)
        # Delta: every datapoint is an increment, so every datapoint is summed.
        delta_rows = s.execute(self.catalog.get("otel", "series_all_values"),
                               {"vendor": vendor, "session_id": session_id}).all()
        for measure, total in _sum_by_measure(delta_rows, mapping).items():
            measures[measure] = measures.get(measure, Decimal(0)) + total

        unfolded = sorted({(r.metric_kind, r.temporality) for r in last_values
                           if r.metric_kind != "gauge"
                           and r.temporality not in ("cumulative", "delta")})
        if unfolded:
            # Not folded either way, and said out loud. An unspecified temporality means the
            # client did not say whether its values are totals or increments; guessing is the
            # 150× inflation in one direction and a near-total loss in the other.
            log.warning("otel_trueup_unfolded_series", vendor=vendor,
                        session_id=session_id, shapes=[list(x) for x in unfolded])

        otel_requests = s.execute(self.catalog.get("otel", "requests_for_session"),
                                  {"vendor": vendor, "session_id": session_id}).all()
        jsonl_requests = s.execute(self.catalog.get("otel", "jsonl_requests_for_session"),
                                   {"vendor": vendor, "session_id": session_id}).all()

        org_id = s.execute(self.catalog.get("otel", "org_for_session"),
                           {"vendor": vendor, "session_id": session_id}).scalar()
        otel_since = self._otel_coverage_start(s, vendor, org_id)
        # Stub creation must happen before the write below (PM.2, ground rule 2/4): the zero-
        # install tier is otherwise spend-true but invisible, since every dashboard query
        # filters on `sessions.user_id`/`started_at`/`agent`, none of which `session_info`
        # carries. Never touches a row the JSONL path already owns.
        bounds = s.execute(self.catalog.get("otel", "otel_session_bounds"),
                           {"vendor": vendor, "session_id": session_id}).one()
        self._ensure_session_row(s, vendor, session_id, org_id,
                                 bounds.earliest_event_ts, bounds.latest_event_ts,
                                 bounds.user_key)

        records, counts = _join_requests(otel_requests, jsonl_requests,
                                         self.pricing, otel_since)
        datasource = _derive_datasource(measures, otel_requests, jsonl_requests, counts["sources"])
        jsonl_lag_s = self._jsonl_lag(s, session_id, counts["sources"], bounds.latest_event_ts)

        block = build_cost_block(measures, records, counts, vendor, now, otel_since,
                                 self.pricing_fingerprint)
        self._write_cost_block(s, session_id, block, datasource, jsonl_lag_s)
        drift = {model: Decimal(c["max_rel"]) for model, c in block["calibration"].items()}
        return counts, drift

    def _otel_coverage_start(self, s: DbSession, vendor: str,
                             org_id: str | None) -> datetime | None:
        """The first request OTel ever saw for this session's org, or None if it saw none.

        Per org rather than per session: telemetry is switched on fleet-wide, so the boundary
        that matters is the org's, and a session that started before it was never observable
        however healthy the pipeline was.
        """
        if org_id is None:
            return None
        return s.execute(self.catalog.get("otel", "otel_coverage_start"),
                         {"vendor": vendor, "org_id": org_id}).scalar()

    def _ensure_session_row(self, s: DbSession, vendor: str, session_id: str, org_id: str | None,
                            earliest_event_ts: datetime | None,
                            latest_event_ts: datetime | None, user_key: str | None) -> None:
        """The zero-install tier's stub `sessions` row (MODES §8, PM.2).

        Creates only when absent -- **never updates an existing row**. The JSONL path owns that
        row wherever it exists, and a true-up that edited it would fight ingest on every tick.
        `started_at`/`last_event_at` come from OTel event timestamps, never `now()` -- they are
        in the past by construction, which is what keeps `touched_sessions`' fourth arm
        (`sess.last_event_at > computed_at`) from re-firing on this session forever (ground
        rule 4: the exact loop hazard that stranded 20 real sessions before this bump).

        `user_key` is the raw OTel account identity (`user.account_uuid`) -- a different
        namespace than the JSONL-side `user_id`, which is why an otel_only session used to be
        permanently invisible to a dashboard scoped to the JSONL `user_id`: every list/rollup
        query filters `sessions.user_id`, and the two never matched. Resolved through
        `users.account_uuid` (linked at JSONL upload time, `ingest.yaml`'s
        `upsert_user_account_uuid`) when a link exists; falls back to the raw `user_key`
        otherwise -- honest, never guessed, and this fold never rewrites an existing row so the
        fix only reaches sessions folded fresh after a link is recorded.
        """
        if s.get(SessionRow, session_id) is not None:
            return
        if org_id is None or earliest_event_ts is None or latest_event_ts is None:
            # No org (defensive -- every otel_series/otel_api_requests row is written with a
            # NOT NULL org_id from the token) or no OTel evidence yet to bound the row with.
            return
        resolved_user_id = user_key
        if user_key is not None:
            linked = s.execute(self.catalog.get("otel", "resolve_user_by_account_uuid"),
                               {"account_uuid": user_key}).scalar()
            if linked is not None:
                resolved_user_id = linked
        s.add(SessionRow(
            session_id=session_id,
            producer_id=f"otel:{org_id}",   # no producer; name the absence, not a guess
            org_id=org_id,
            user_id=resolved_user_id,       # null-safe; MODES §8's operational identity req.
            agent=vendor,
            status="otel_only",
            started_at=earliest_event_ts,
            last_event_at=latest_event_ts,
            signal_blob={}, info_blob={}, subagents=[],
        ))

    @staticmethod
    def _jsonl_lag(s: DbSession, session_id: str, sources_counts: dict,
                   latest_otel_event_ts: datetime | None) -> int | None:
        """`jsonl_lag_s` (MODES §7, PM.5): OTel has seen requests JSONL has not delivered.

        Written only when `sources.otel_only > 0` -- otherwise there is nothing lagging.
        Negative values (JSONL ahead of OTel, the normal steady state: JSONL is live,
        OTel is 1-2 min behind) clamp to `None` rather than reporting "negative lag", which
        would put a "still arriving" caption on every healthy session.
        """
        if sources_counts.get(SRC_OTEL_ONLY, 0) <= 0 or latest_otel_event_ts is None:
            return None
        current = s.get(SessionRow, session_id)
        if current is None or current.last_event_at is None:
            return None
        lag = (latest_otel_event_ts - current.last_event_at).total_seconds()
        return int(lag) if lag > 0 else None

    @staticmethod
    def _write_cost_block(s: DbSession, session_id: str, block: dict,
                          datasource: str | None, jsonl_lag_s: int | None) -> None:
        """Write the block to `session_info.otel`, creating the row if the JSONL path has not
        made one yet.

        A session that exists only in OTel is the intended zero-install tier (§7), not an
        error: spend-true, fidelity-blank. Writing the row here is what makes that tier
        visible at all.

        `cost_effective_usd_micros`/`datasource`/`jsonl_lag_s` are written in this same
        statement (PM.1) -- the true-up's own promoted columns, never touched by
        `persist_session_info` (ground rule 3).
        """
        row = s.get(SessionInfoRow, session_id)
        now = datetime.now(timezone.utc)
        if row is None:
            row = SessionInfoRow(session_id=session_id, created_at=now, updated_at=now,
                                 models={}, tools={}, files={}, skills=[], subagents={},
                                 pr_list=[], turns=[], info={}, otel={})
            s.add(row)
        row.otel = block
        row.cost_effective_usd_micros = block["cost_effective_usd_micros"]
        # api-latency plan Phase E: the true-up's own promoted column, same statement as
        # cost_effective_usd_micros -- never touched by persist_session_info (ground rule 3).
        row.otel_vendor_cost_usd_micros = session_scalars.otel_vendor_cost(block)
        row.datasource = datasource
        row.jsonl_lag_s = jsonl_lag_s
        row.updated_at = now

    # -------------------------------------------------------------- watermark

    def _read_watermark(self, s: DbSession, vendor: str) -> datetime:
        found = s.execute(self.catalog.get("otel", "get_watermark"),
                          {"vendor": vendor}).scalar()
        # Epoch, not "now": a first tick must fold everything already stored, not skip it.
        return found or datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _advance_watermark(self, s: DbSession, vendor: str, high: datetime) -> None:
        s.execute(self.catalog.get("otel", "set_watermark"), {
            "vendor": vendor,
            "watermark": (high - timedelta(seconds=self.watermark_lag_s)).isoformat(),
        })

    # ----------------------------------------------------------------- prune

    def prune(self, retention_days: int, now: datetime | None = None) -> int:
        """Delete folded staging points past the retention window (§4.3).

        Bounded by the watermark as well as the window. That bound is the load-bearing one:
        deleting a point the fold has not consumed would lower that session's totals silently,
        and the raw trajectory is the only evidence there was to notice with.
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=retention_days)
        deleted = 0
        with session_scope() as s:
            for vendor in self.registry.vendors():
                watermark = self._read_watermark(s, vendor)
                result = s.execute(self.catalog.get("otel", "prune_points"), {
                    "vendor": vendor,
                    "cutoff": cutoff.isoformat(),
                    "watermark": watermark.isoformat(),
                })
                deleted += result.rowcount or 0
        if deleted:
            log.info("otel_points_pruned", deleted=deleted, retention_days=retention_days)
        return deleted
