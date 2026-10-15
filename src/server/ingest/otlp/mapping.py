"""Vendor mapping configs: the only place a vendor dialect exists (design §4.6, D-OT6).

`config/otel/{vendor}.yaml` declares how one agent's OTLP payload maps onto canonical
concepts -- which payload is that vendor's, where session/user identity lives, which log
events are kept and how their attributes become columns, and which vendor metric names carry
which canonical measure. The receiver and the true-up fold consume this and nothing else, so
adding an agent is adding a file (design rule 4).

Canonical *columns* are code, not config: they are the columns of `OtelApiRequest`, and their
types are what `_COERCE` below enforces. A mapping file says which attribute fills a column;
it never invents one. Everything a mapping does not name lands in `attrs` as residue --
must-ignore on read, promotable to a column later by migration (§4.6, canonical-vs-residue).

TODO(otel-intake): load via the shared config loader in src/common when it lands
(arch §12 "one shared loader"); the same TODO is open in ingest/config.py and
collector/normalizer/config.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, Field, model_validator

from ..errors import IngestError

#: Stamped on payloads no mapping claims. Metrics still store under it (prunable, harmless);
#: log events are dropped with an alarm (§4.1 step 4).
UNKNOWN_VENDOR = "unknown"


class MappingConfigError(IngestError):
    """A mapping file is missing, unparseable, or declares something the loader cannot honor.

    Raised at startup, never per request: a bad mapping is an operator error, and a receiver
    that boots with half its vendor knowledge would silently downgrade real traffic to
    `unknown`.
    """


class DetectRules(BaseModel):
    """Payload match rules. **Any one hit claims the payload** -- they are alternatives, not
    a conjunction: the signals disagree about scope names, and a fleet can rename
    `service.name` but not its SDK's instrumentation scope, so requiring all of them would
    make detection fail on exactly the deployments that need it most."""

    model_config = {"extra": "forbid"}

    scope_name: str | None = None
    scope_name_prefix: str | None = None
    service_name_prefix: str | None = None
    #: any one of these resource attribute keys being present is a hit
    resource_attr_present: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one_rule(self) -> "DetectRules":
        if not any((self.scope_name, self.scope_name_prefix, self.service_name_prefix,
                    self.resource_attr_present)):
            raise ValueError("detect: at least one match rule is required")
        return self

    def matches(self, resource_attrs: dict[str, Any], scope_name: str | None) -> bool:
        if self.scope_name and scope_name == self.scope_name:
            return True
        if self.scope_name_prefix and scope_name and scope_name.startswith(self.scope_name_prefix):
            return True
        if self.service_name_prefix:
            service = resource_attrs.get("service.name")
            if isinstance(service, str) and service.startswith(self.service_name_prefix):
                return True
        return any(key in resource_attrs for key in self.resource_attr_present)


class Identity(BaseModel):
    model_config = {"extra": "forbid"}

    session_attr: str | None = None
    #: first present wins -- ordered most-stable identifier first
    user_attrs: list[str] = Field(default_factory=list)


class FieldSource(BaseModel):
    """One candidate attribute for a canonical column, optionally scaled.

    `scale` exists for exactly one documented case: clients older than claude_code v2.1.219
    emit `cost_usd` as a float where newer ones emit `cost_usd_micros` (§8). Keeping it as a
    multiplier rather than a named transform is what keeps the DSL small and honest (D-OT6) --
    if a vendor ever needs real computation, that is a code adapter and a decision to reopen.
    """

    model_config = {"extra": "forbid"}

    attr: str
    scale: float | None = None


class MeasureSelector(BaseModel):
    """A canonical measure: one vendor metric name, optionally narrowed by datapoint labels."""

    model_config = {"extra": "forbid"}

    metric: str
    where: dict[str, str] = Field(default_factory=dict)

    def selects(self, metric_name: str, attrs: dict[str, Any]) -> bool:
        if metric_name != self.metric:
            return False
        return all(str(attrs.get(k)) == v for k, v in self.where.items())


class Events(BaseModel):
    model_config = {"extra": "forbid"}

    #: `event.name` values kept; everything else is counted and dropped (§4.1 step 6)
    accept: list[str] = Field(default_factory=list)
    #: which accepted event maps to otel_api_requests
    request_event: str | None = None
    #: canonical column -> candidate sources, first present wins
    fields: dict[str, list[FieldSource]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: Any) -> Any:
        """`name: "attr"` is sugar for `name: [{attr: "attr"}]`. The plain form is what
        almost every field uses; spelling it out everywhere would bury the two that differ."""
        if not isinstance(data, dict):
            return data
        fields = data.get("fields")
        if not isinstance(fields, dict):
            return data
        out: dict[str, Any] = {}
        for column, source in fields.items():
            if isinstance(source, str):
                out[column] = [{"attr": source}]
            elif isinstance(source, dict):
                out[column] = [source]
            else:
                out[column] = source
        return {**data, "fields": out}

    @model_validator(mode="after")
    def _request_event_is_accepted(self) -> "Events":
        if self.request_event and self.request_event not in self.accept:
            raise ValueError(
                f"events.request_event {self.request_event!r} is not in events.accept -- "
                "the filter would drop the very event the field map exists for"
            )
        if self.fields and not self.request_event:
            raise ValueError("events.fields declared without a request_event to apply them to")
        return self


#: Canonical column -> coercion. The keys are the writable columns of `OtelApiRequest`; a
#: mapping naming anything else is rejected at load, which is how a typo in a YAML file
#: becomes a boot failure instead of a column that silently never fills.
_COERCE = {
    "request_id": str,
    "model": str,
    "input_tokens": int,
    "output_tokens": int,
    "cache_read_tokens": int,
    "cache_creation_tokens": int,
    "cost_usd_micros": int,
    "duration_ms": int,
    "query_source": str,
    "prompt_id": str,
    "event_ts": "timestamp",
}


def _to_timestamp(value: Any) -> datetime | None:
    """An OTLP attribute timestamp -> aware UTC datetime.

    Accepts the ISO-8601 string vendors put in `event.timestamp` (`Z` suffix included, which
    `fromisoformat` rejects before 3.11 and accepts after -- normalized here either way) and
    integer epoch nanoseconds, which is what a vendor using the record's own time field would
    give us.
    """
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1e9, tz=timezone.utc)
    return None


def _coerce(column: str, value: Any) -> Any:
    """Apply a canonical column's type. Returns None when the value cannot be honored --
    never a partial or invented one, and never an exception: one unusable attribute must not
    cost the whole record, let alone the export body it arrived in (§4.1)."""
    kind = _COERCE[column]
    if value is None:
        return None
    if kind == "timestamp":
        return _to_timestamp(value)
    try:
        if kind is int:
            # OTLP/JSON carries int64 as a string, and a scaled float lands here too.
            return int(round(float(value)))
        return kind(value)
    except (TypeError, ValueError):
        return None


class VendorMapping(BaseModel):
    """One `config/otel/{vendor}.yaml`, validated."""

    model_config = {"extra": "forbid"}

    vendor: str
    detect: DetectRules
    identity: Identity = Field(default_factory=Identity)
    events: Events = Field(default_factory=Events)
    measures: dict[str, MeasureSelector] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fields_are_canonical_columns(self) -> "VendorMapping":
        unknown = set(self.events.fields) - set(_COERCE)
        if unknown:
            raise ValueError(
                f"events.fields names non-canonical columns {sorted(unknown)}; "
                f"canonical columns are {sorted(_COERCE)} -- unmapped concepts ride `attrs`"
            )
        if self.vendor == UNKNOWN_VENDOR:
            raise ValueError(f"vendor {UNKNOWN_VENDOR!r} is reserved for unclaimed payloads")
        return self

    # ---------------------------------------------------------------- identity

    def session_id_of(self, attrs: dict[str, Any]) -> str | None:
        if not self.identity.session_attr:
            return None
        value = attrs.get(self.identity.session_attr)
        return str(value) if value is not None else None

    def user_key_of(self, attrs: dict[str, Any]) -> str | None:
        for key in self.identity.user_attrs:
            value = attrs.get(key)
            if value is not None:
                return str(value)
        return None

    # ------------------------------------------------------------------ events

    def accepts_event(self, event_name: str | None) -> bool:
        return event_name in self.events.accept

    def apply_event_fields(self, attrs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """A request event's attributes -> (canonical column dict, residue).

        Residue is everything the mapping did not consume, kept verbatim: the vendor-specific
        columns of tomorrow, and the reconciliation evidence of today (the payload's own
        `organization.id` lands here, while the row's `org_id` comes from the token -- §4.5).
        """
        columns: dict[str, Any] = {}
        consumed: set[str] = set()
        for column, sources in self.events.fields.items():
            for source in sources:
                if source.attr not in attrs:
                    continue
                raw = attrs[source.attr]
                if source.scale is not None:
                    try:
                        raw = float(raw) * source.scale
                    except (TypeError, ValueError):
                        continue
                value = _coerce(column, raw)
                if value is None:
                    continue
                columns[column] = value
                consumed.add(source.attr)
                break  # first source that yields a usable value wins

        if self.identity.session_attr in attrs:
            columns["session_id"] = self.session_id_of(attrs)
            consumed.add(self.identity.session_attr)
        user_key = self.user_key_of(attrs)
        if user_key is not None:
            columns["user_key"] = user_key
            # Only the winning attribute is consumed. The runners-up stay in residue on
            # purpose -- they are the evidence that a user_key rotated (§4.5 reconciliation).
            consumed.add(next(k for k in self.identity.user_attrs if k in attrs))

        residue = {k: v for k, v in attrs.items() if k not in consumed}
        return columns, residue

    # ---------------------------------------------------------------- measures

    def measure_of(self, metric_name: str, attrs: dict[str, Any]) -> str | None:
        """The canonical measure a datapoint contributes to, or None if it carries no
        canonical meaning. None is normal, not an error: `session.count` and
        `lines_of_code.count` are stored and simply do not fold into the cost block."""
        for measure, selector in self.measures.items():
            if selector.selects(metric_name, attrs):
                return measure
        return None

    def measures_of(self, metric_name: str, attrs: dict[str, Any]) -> list[str]:
        """Every canonical measure a datapoint contributes to -- plural, unlike `measure_of`.

        A datapoint can be both an unconditional total (`tokens_input`, `where: {type:
        "input"}`) *and* a narrower sub-view of the same total (`tokens_input_bg`, `where:
        {type: "input", query_source: "auxiliary"}`, dashboard-datasource-modes plan PM.3) --
        those are two different, overlapping questions about the same value, not a partition.
        `measure_of` keeps its existing first-match-wins, single-label contract (its own tests
        assert exactly that); this is `_sum_by_measure`'s method alone.
        """
        return [measure for measure, selector in self.measures.items()
               if selector.selects(metric_name, attrs)]


class MappingRegistry:
    """Every `config/otel/*.yaml`, loaded once at startup and evaluated in filename order.

    Filename order is the tie-break and it is load-bearing: `claude_code.yaml` sorts before
    `gen_ai.yaml`, so a Claude Code payload that also carries GenAI semconv attributes is
    claimed by its own richer mapping rather than the generic one.
    """

    def __init__(self, mappings: list[VendorMapping]) -> None:
        self._mappings = mappings
        self._by_vendor = {m.vendor: m for m in mappings}

    @classmethod
    def load(cls, configs_dir: str | Path) -> "MappingRegistry":
        directory = Path(configs_dir)
        if not directory.is_dir():
            raise MappingConfigError(f"{directory}: OTel mapping directory not found")
        mappings: list[VendorMapping] = []
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise MappingConfigError(f"{path}: invalid YAML: {exc}") from exc
            try:
                mapping = VendorMapping.model_validate(data)
            except Exception as exc:
                raise MappingConfigError(f"{path}: {exc}") from exc
            if mapping.vendor in {m.vendor for m in mappings}:
                raise MappingConfigError(f"{path}: duplicate vendor {mapping.vendor!r}")
            mappings.append(mapping)
        if not mappings:
            raise MappingConfigError(f"{directory}: no vendor mappings found")
        return cls(mappings)

    def detect(self, resource_attrs: dict[str, Any], scope_name: str | None) -> str:
        """The vendor claiming this payload, or `unknown` (§4.1 step 4)."""
        for mapping in self._mappings:
            if mapping.detect.matches(resource_attrs, scope_name):
                return mapping.vendor
        return UNKNOWN_VENDOR

    def get(self, vendor: str) -> VendorMapping | None:
        return self._by_vendor.get(vendor)

    def vendors(self) -> Iterable[str]:
        return tuple(self._by_vendor)
