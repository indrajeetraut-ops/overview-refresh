"""The OTLP receive pipeline (otel-intake design §4.1 steps 4–7).

Framework-free by the same rule the JSONL fold follows: `router.py` owns HTTP, this owns the
walk from a decoded payload to rows. Vendor-blind — every dialect decision is a lookup in the
mapping config (§4.6), and the only vendor literal in this file is `UNKNOWN_VENDOR`.

**Interpretation-free on the metrics side (D-OT3b).** The receiver stores datapoints exactly
as they arrived, epochs and all, and never folds. Moving reset/ordering logic into this hot
path would mean a bug here corrupts totals unrecoverably; leaving it in the worker means the
raw trajectory is always there to recompute from.

**Idempotent.** Every insert is insert-if-absent on the dedup key, and one export body is one
transaction. An OTel SDK retry is normal, not exceptional (§2): replaying a body writes zero
rows and returns the same 200.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as DbSession

from server.storage.schema import OtelApiRequest, OtelMetricPoint, OtelSeries

from .mapping import UNKNOWN_VENDOR, MappingRegistry, VendorMapping
from .payload import flatten_attrs

#: OTLP `AggregationTemporality` enum -> the value stored on the series. The fold branches on
#: this string, so an unrecognised temporality must not silently become "cumulative".
_TEMPORALITY = {
    0: "unspecified", 1: "delta", 2: "cumulative",
    "AGGREGATION_TEMPORALITY_UNSPECIFIED": "unspecified",
    "AGGREGATION_TEMPORALITY_DELTA": "delta",
    "AGGREGATION_TEMPORALITY_CUMULATIVE": "cumulative",
}

#: The metric-body keys OTLP defines, in the order we probe them. `exponentialHistogram` is
#: recognised so its datapoints are stored rather than dropped; v1 reads only its `sum`.
_METRIC_KINDS = ("sum", "gauge", "histogram", "exponentialHistogram")


@dataclass
class IngestResult:
    """What one export body did. Every field is a count or an identifier — no payload content
    reaches a log line through this object (§13)."""

    vendor: str = UNKNOWN_VENDOR
    series_seen: int = 0
    points_written: int = 0
    events_kept: int = 0
    #: event.name -> how many were counted and dropped by the accept filter (§4.1 step 6)
    events_dropped: Counter = field(default_factory=Counter)
    #: records the server could not map at all -> OTLP `partialSuccess`
    rejected: int = 0
    reject_reasons: Counter = field(default_factory=Counter)

    @property
    def events_dropped_total(self) -> int:
        return sum(self.events_dropped.values())


def series_id(vendor: str, metric_name: str, resource_attrs: dict[str, Any],
              point_attrs: dict[str, Any]) -> str:
    """The stable identity of one metric stream: sha256 over vendor + metric name + both
    attribute sets, canonically ordered (§4.3).

    Canonical means sorted keys and a fixed separator set, so the same series exported by the
    same client hashes identically forever — that is what makes `(series_id, time_unix_nano)`
    a dedup key rather than a hint. The two attribute sets are hashed as separate lists rather
    than merged, so a key moving from resource to datapoint scope is a different series and
    not a silent collision.

    `vendor` is inside the hash because ids are vendor namespaces (D-OT6): two agents using
    the same metric name are two series, and no migration can retrofit that later.
    """
    canonical = json.dumps(
        [vendor, metric_name, sorted(resource_attrs.items()), sorted(point_attrs.items())],
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _point_value(point: dict[str, Any], kind: str) -> Decimal | None:
    """The number a datapoint contributes, as an exact decimal.

    `Decimal`, never `float`: the column is NUMERIC because these values are summed into
    money, and a binary fraction accumulating across ~90 exports per session is a rounding
    error nobody can later account for. `Decimal(str(x))` round-trips the value's own decimal
    text — which for an OTLP/JSON body is literally what arrived on the wire, since the JSON
    decoder is told to parse floats as Decimal.

    Histograms give up their own `sum` and their buckets are dropped — a recorded v1 limit
    (§8), sufficient because the fold consumes totals only. A sum or gauge with neither
    `asInt` nor `asDouble` is zero, not missing: both encodings omit default values.
    """
    if kind in ("histogram", "exponentialHistogram"):
        raw = point.get("sum")
        return _normalized(raw) if raw is not None else None
    if "asInt" in point:
        return _normalized(point["asInt"])
    if "asDouble" in point:
        return _normalized(point["asDouble"])
    return Decimal(0)


def _normalized(raw: Any) -> Decimal:
    """Exact decimal, trailing zeros stripped.

    Stripping matters because the two wire encodings disagree about scale for the same
    number: Claude Code writes whole token counts into `asDouble`, so `2` in an OTLP/JSON
    body round-trips through protobuf as `2.0`. NUMERIC keeps that scale, and it would
    propagate into the folded totals' text — making a fleet's stored numbers depend on which
    `OTEL_EXPORTER_OTLP_PROTOCOL` it happened to set.
    """
    return Decimal(str(raw)).normalize()


def _metric_body(metric: dict[str, Any]) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    for kind in _METRIC_KINDS:
        if kind in metric:
            return kind, metric[kind] or {}
    return None, None


class OtlpReceiver:
    """Holds the mapping registry; everything else arrives per call.

    The DB session is passed in rather than opened here so the caller owns the transaction
    boundary — one body, one transaction (§4.1 step 7).
    """

    def __init__(self, registry: MappingRegistry) -> None:
        self.registry = registry

    # ------------------------------------------------------------- metrics

    def ingest_metrics(self, payload: dict[str, Any], org_id: str,
                       s: DbSession, now: datetime | None = None) -> IngestResult:
        now = now or datetime.now(timezone.utc)
        result = IngestResult()
        series_rows: dict[str, dict] = {}
        point_rows: list[dict] = []

        for resource_block in payload.get("resourceMetrics") or []:
            resource_attrs = flatten_attrs(
                (resource_block.get("resource") or {}).get("attributes"))
            for scope_block in resource_block.get("scopeMetrics") or []:
                scope_name = (scope_block.get("scope") or {}).get("name")
                vendor = self.registry.detect(resource_attrs, scope_name)
                mapping = self.registry.get(vendor)
                result.vendor = vendor

                for metric in scope_block.get("metrics") or []:
                    kind, body = _metric_body(metric)
                    if kind is None:
                        # A metric shape OTLP does not define, or an empty one. Counted, not
                        # guessed at.
                        result.rejected += 1
                        result.reject_reasons["metric_without_datapoints"] += 1
                        continue
                    metric_name = metric.get("name") or ""
                    temporality = _TEMPORALITY.get(
                        body.get("aggregationTemporality"), "unspecified")

                    for point in body.get("dataPoints") or []:
                        row = self._metric_point(
                            metric_name, kind, temporality, resource_attrs, point,
                            vendor, mapping, org_id, now, result)
                        if row is None:
                            continue
                        series, datapoint = row
                        series_rows.setdefault(series["series_id"], series)
                        point_rows.append(datapoint)

        result.series_seen = len(series_rows)
        if series_rows:
            self._insert_if_absent(s, OtelSeries, list(series_rows.values()))
        if point_rows:
            result.points_written = self._insert_if_absent(s, OtelMetricPoint, point_rows)
        return result

    def _metric_point(self, metric_name: str, kind: str, temporality: str,
                      resource_attrs: dict[str, Any], point: dict[str, Any],
                      vendor: str, mapping: VendorMapping | None, org_id: str,
                      now: datetime, result: IngestResult):
        time_unix_nano = point.get("timeUnixNano")
        if time_unix_nano is None:
            # Half of the dedup key. Without it the row is undedupable, and storing it would
            # inflate totals on the next retry -- the exact failure this table exists to avoid.
            result.rejected += 1
            result.reject_reasons["datapoint_without_timestamp"] += 1
            return None
        value = _point_value(point, kind)
        if value is None:
            result.rejected += 1
            result.reject_reasons["datapoint_without_value"] += 1
            return None

        point_attrs = flatten_attrs(point.get("attributes"))
        # Resource first so a datapoint-scoped key wins the collision -- the narrower scope is
        # the more specific claim.
        attrs = {**resource_attrs, **point_attrs}
        sid = series_id(vendor, metric_name, resource_attrs, point_attrs)

        series = {
            "series_id": sid,
            "vendor": vendor,
            "metric_name": metric_name,
            "metric_kind": kind,
            "temporality": temporality,
            "org_id": org_id,            # from the token, never the payload (§4.5)
            # An unclaimed payload has no mapping to read identity through, so its series are
            # org-scoped only. Harmless: they are prunable and never fold.
            "session_id": mapping.session_id_of(attrs) if mapping else None,
            "user_key": mapping.user_key_of(attrs) if mapping else None,
            "attrs": attrs,
            "first_seen_at": now,
        }
        datapoint = {
            "series_id": sid,
            "time_unix_nano": int(time_unix_nano),
            # Absent start time means "same epoch as always"; 0 is the honest encoding of
            # unknown here and keeps the fold's per-epoch grouping total.
            "start_unix_nano": int(point.get("startTimeUnixNano") or 0),
            "value": value,
            "received_at": now,
        }
        return series, datapoint

    # ---------------------------------------------------------------- logs

    def ingest_logs(self, payload: dict[str, Any], org_id: str,
                    s: DbSession, now: datetime | None = None) -> IngestResult:
        now = now or datetime.now(timezone.utc)
        result = IngestResult()
        request_rows: dict[tuple[str, str], dict] = {}

        for resource_block in payload.get("resourceLogs") or []:
            resource_attrs = flatten_attrs(
                (resource_block.get("resource") or {}).get("attributes"))
            for scope_block in resource_block.get("scopeLogs") or []:
                scope_name = (scope_block.get("scope") or {}).get("name")
                vendor = self.registry.detect(resource_attrs, scope_name)
                mapping = self.registry.get(vendor)
                result.vendor = vendor

                for record in scope_block.get("logRecords") or []:
                    attrs = {**resource_attrs, **flatten_attrs(record.get("attributes"))}
                    event_name = attrs.get("event.name")

                    if mapping is None:
                        # No mapping means no way to know what this record means. Dropping is
                        # the honest answer and the alarm is the router's (§4.1 step 4).
                        result.events_dropped[str(event_name)] += 1
                        continue
                    if not mapping.accepts_event(event_name):
                        # v1 keeps one event in ~37 (§6.2). Counted per name so the drop
                        # distribution is queryable without storing a single record.
                        result.events_dropped[str(event_name)] += 1
                        continue

                    row = self._request_row(attrs, vendor, mapping, org_id, now, result)
                    if row is None:
                        continue
                    result.events_kept += 1
                    # Last write wins within a body: a client that repeats a request_id in
                    # one batch means the later record, and the DB would reject the duplicate
                    # key outright.
                    request_rows[(row["vendor"], row["request_id"])] = row

        if request_rows:
            self._insert_if_absent(s, OtelApiRequest, list(request_rows.values()))
        return result

    def _request_row(self, attrs: dict[str, Any], vendor: str, mapping: VendorMapping,
                     org_id: str, now: datetime, result: IngestResult) -> dict | None:
        columns, residue = mapping.apply_event_fields(attrs)
        if not columns.get("request_id"):
            # The join key to JSONL is the entire reason this event is kept (D-OT2). Without
            # it the row is unjoinable and undedupable, so it is rejected, not stored.
            result.rejected += 1
            result.reject_reasons["request_event_without_request_id"] += 1
            return None
        return {
            "vendor": vendor,
            "org_id": org_id,
            "attrs": residue,
            "received_at": now,
            **columns,
        }

    # --------------------------------------------------------------- write

    @staticmethod
    def _insert_if_absent(s: DbSession, model, rows: list[dict]) -> int:
        """Insert-if-absent on the model's PK; returns how many rows actually landed.

        Counted via RETURNING: a conflicting row is not "affected", so `rowcount` cannot tell
        a fresh datapoint from a replayed one — and that count is the observable that says
        whether retries are behaving.
        """
        stmt = (pg_insert(model.__table__).on_conflict_do_nothing()
                .returning(*model.__table__.primary_key.columns))
        return len(s.execute(stmt, rows).all())
