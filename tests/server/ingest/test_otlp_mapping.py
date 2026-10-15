"""Vendor mapping configs + loader (otel-intake design §4.6, D-OT6).

The property under test is genericity: the shipped YAML must carry *all* the vendor knowledge,
so a payload that no file claims degrades to `unknown` rather than being mis-parsed, and a
field the mapping does not name survives in residue rather than being dropped.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server.ingest.otlp.mapping import (
    UNKNOWN_VENDOR,
    MappingConfigError,
    MappingRegistry,
    VendorMapping,
)
from server.ingest.otlp.payload import attr_value, flatten_attrs

CONFIGS_DIR = "config/otel"
FIXTURES = Path("tests/fixtures/otel")


@pytest.fixture(scope="module")
def registry() -> MappingRegistry:
    return MappingRegistry.load(CONFIGS_DIR)


def _log_records(name: str):
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        for rl in json.loads(line)["resourceLogs"]:
            resource = flatten_attrs(rl.get("resource", {}).get("attributes"))
            for sl in rl["scopeLogs"]:
                scope = sl.get("scope", {}).get("name")
                for record in sl["logRecords"]:
                    yield resource, scope, flatten_attrs(record.get("attributes"))


def _metric_points(name: str):
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        for rm in json.loads(line)["resourceMetrics"]:
            resource = flatten_attrs(rm.get("resource", {}).get("attributes"))
            for sm in rm["scopeMetrics"]:
                scope = sm.get("scope", {}).get("name")
                for metric in sm["metrics"]:
                    kind = next(k for k in ("sum", "gauge", "histogram") if k in metric)
                    for point in metric[kind]["dataPoints"]:
                        yield resource, scope, metric["name"], flatten_attrs(point.get("attributes"))


# ------------------------------------------------------------------ loading

def test_shipped_mappings_parse(registry):
    assert set(registry.vendors()) == {"claude_code", "gen_ai"}


def test_claude_code_mapping_declares_the_v1_contract(registry):
    mapping = registry.get("claude_code")
    # D-OT2: exactly one accepted event in v1 -- the one carrying request_id + cost.
    assert mapping.events.accept == ["api_request"]
    assert mapping.events.request_event == "api_request"
    assert set(mapping.measures) == {
        "tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation",
        "cost_usd", "active_time_s",
        # Background-call sub-views (dashboard-datasource-modes plan PM.3) -- the only path to
        # "what are background calls costing me" in a metrics-only fleet, where there is no
        # request ledger to split by query_source instead.
        "tokens_input_bg", "tokens_output_bg", "tokens_cache_read_bg",
        "tokens_cache_creation_bg", "cost_usd_bg",
    }


def test_gen_ai_ships_measures_but_no_events(registry):
    """§4.6: semconv defines no request-shaped event carrying a join key, so the event path
    is empty by design -- metrics alone carry gen_ai spend in v1."""
    mapping = registry.get("gen_ai")
    assert mapping.events.accept == []
    assert mapping.events.request_event is None
    assert mapping.measures["tokens_input"].metric == "gen_ai.client.token.usage"


def test_missing_directory_fails_at_load(tmp_path):
    with pytest.raises(MappingConfigError):
        MappingRegistry.load(tmp_path / "nope")


def test_empty_directory_fails_at_load(tmp_path):
    """A receiver that boots with no vendor knowledge would downgrade all real traffic to
    `unknown` -- loudly unusable is better than quietly useless."""
    with pytest.raises(MappingConfigError):
        MappingRegistry.load(tmp_path)


def test_field_naming_a_non_canonical_column_is_rejected():
    with pytest.raises(ValueError, match="non-canonical"):
        VendorMapping.model_validate({
            "vendor": "v", "detect": {"scope_name": "s"},
            "events": {"accept": ["e"], "request_event": "e", "fields": {"nonsense": "a"}},
        })


def test_request_event_outside_accept_is_rejected():
    """The filter runs before the field map; a request_event the filter drops is a mapping
    that silently produces no rows."""
    with pytest.raises(ValueError, match="not in events.accept"):
        VendorMapping.model_validate({
            "vendor": "v", "detect": {"scope_name": "s"},
            "events": {"accept": ["other"], "request_event": "e"},
        })


def test_detect_requires_a_rule():
    with pytest.raises(ValueError, match="at least one match rule"):
        VendorMapping.model_validate({"vendor": "v", "detect": {}})


# ---------------------------------------------------------------- detection

def test_detects_claude_code_on_both_signals(registry):
    """Metrics arrive under scope `com.anthropic.claude_code`, log events under
    `com.anthropic.claude_code.events` -- the prefix rule covers both."""
    resource, scope, _metric, _attrs = next(iter(_metric_points("metrics_session.jsonl")))
    assert scope == "com.anthropic.claude_code"
    assert registry.detect(resource, scope) == "claude_code"

    resource, scope, _attrs = next(iter(_log_records("logs_session.jsonl")))
    assert scope == "com.anthropic.claude_code.events"
    assert registry.detect(resource, scope) == "claude_code"


def test_detects_claude_code_from_service_name_alone(registry):
    """A gateway that strips scope names must not cost us the vendor."""
    assert registry.detect({"service.name": "claude-code-desktop"}, None) == "claude_code"


def test_unclaimed_payload_is_unknown(registry):
    assert registry.detect({"service.name": "some-other-agent"}, "org.example.thing") == UNKNOWN_VENDOR
    assert registry.detect({}, None) == UNKNOWN_VENDOR


def test_gen_ai_detected_by_semconv_attribute(registry):
    assert registry.detect({"gen_ai.provider.name": "openai"}, "x") == "gen_ai"


def test_claude_code_wins_over_gen_ai_on_a_payload_matching_both(registry):
    """Filename order is the tie-break: the richer, dump-validated mapping claims it."""
    both = {"service.name": "claude-code-desktop", "gen_ai.provider.name": "anthropic"}
    assert registry.detect(both, "com.anthropic.claude_code") == "claude_code"


# ---------------------------------------------------------------- field map

def _first_api_request() -> dict:
    for _resource, _scope, attrs in _log_records("logs_session.jsonl"):
        if attrs.get("event.name") == "api_request":
            return attrs
    raise AssertionError("fixture has no api_request record")


def test_api_request_field_map_golden(registry):
    """The exact column dict a fixture `api_request` produces (§4.6 fields)."""
    mapping = registry.get("claude_code")
    columns, _residue = mapping.apply_event_fields(_first_api_request())
    assert columns == {
        "request_id": "req_011CdcSmKkKXQVDvx8gHa7MW",
        "model": "claude-opus-5",
        "input_tokens": 2,
        "output_tokens": 265,
        "cache_read_tokens": 22042,
        "cache_creation_tokens": 48630,
        "cost_usd_micros": 503956,
        "duration_ms": 6106,
        "query_source": "sdk",
        "prompt_id": "b0d59518-2424-4d44-938a-5273974c9a84",
        "event_ts": datetime(2026, 8, 1, 17, 45, 15, 473000, tzinfo=timezone.utc),
        "session_id": "8f87e0a5-872f-4617-9d27-d9d0bc8db54d",
        "user_key": "11111111-1111-4111-8111-111111111111",
    }


def test_unmapped_attributes_land_in_residue(registry):
    """§4.6 canonical-vs-residue: a canonical column exists only for a concept ≥2 vendors
    share or the fold consumes; everything else rides `attrs`, never dropped."""
    mapping = registry.get("claude_code")
    attrs = _first_api_request()
    columns, residue = mapping.apply_event_fields(attrs)

    # claude_code specifics with no canonical home
    assert residue["speed"] == "normal"
    assert residue["effort"] == "high"
    assert residue["client_request_id"]
    # §4.5: the payload's own org claim is evidence, not authority -- it rides residue while
    # the row's org_id comes from the token.
    assert residue["organization.id"] == "22222222-2222-4222-8222-222222222222"
    # identity runners-up stay visible (the winning attr was user.account_uuid)
    assert residue["user.email"] == "dev1@example.test"
    assert "user.account_uuid" not in residue
    # Nothing is lost: every attribute is either consumed into a column or in residue, and
    # the consumed set is exactly what the mapping names plus the identity attrs it used.
    assert set(attrs) - set(residue) == {
        "request_id", "model", "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_creation_tokens", "cost_usd_micros", "duration_ms", "query_source",
        "prompt.id", "event.timestamp", "session.id", "user.account_uuid",
    }
    assert set(residue) & set(columns) == set()


def test_cost_falls_back_to_the_float_when_micros_is_absent(registry):
    """§8: clients older than v2.1.219 emit only `cost_usd`."""
    mapping = registry.get("claude_code")
    attrs = {k: v for k, v in _first_api_request().items() if k != "cost_usd_micros"}
    attrs["cost_usd"] = 0.503956
    columns, residue = mapping.apply_event_fields(attrs)
    assert columns["cost_usd_micros"] == 503956
    assert "cost_usd" not in residue          # consumed, not duplicated

    # and when both are present the exact integer wins over the float
    both = _first_api_request()
    both["cost_usd"] = 0.999999
    assert mapping.apply_event_fields(both)[0]["cost_usd_micros"] == 503956


def test_unusable_attribute_yields_no_column_rather_than_an_error(registry):
    mapping = registry.get("claude_code")
    attrs = {**_first_api_request(), "input_tokens": "not-a-number",
             "event.timestamp": "not-a-timestamp"}
    columns, _residue = mapping.apply_event_fields(attrs)
    assert "input_tokens" not in columns
    assert "event_ts" not in columns
    assert columns["output_tokens"] == 265     # the rest of the record is unaffected


def test_session_less_payload_maps_without_identity(registry):
    """A vendor (or a record) with no session attr still yields a row -- §8."""
    mapping = registry.get("gen_ai")
    columns, residue = mapping.apply_event_fields({"gen_ai.provider.name": "openai"})
    assert columns == {}
    assert residue == {"gen_ai.provider.name": "openai"}


# ------------------------------------------------------------------ filter

def test_accept_list_keeps_only_the_request_event(registry):
    mapping = registry.get("claude_code")
    kept = [a for _r, _s, a in _log_records("logs_session.jsonl")
            if mapping.accepts_event(a.get("event.name"))]
    dropped = [a for _r, _s, a in _log_records("logs_session.jsonl")
               if not mapping.accepts_event(a.get("event.name"))]
    assert len(kept) == 8                       # every api_request in the fixture
    assert len(dropped) == 29                   # ~99% of records, per §6.2
    assert {a["event.name"] for a in kept} == {"api_request"}


# ---------------------------------------------------------------- measures

def test_measures_cover_the_folded_metrics_and_ignore_the_rest(registry):
    """Datapoints with no canonical measure are stored and simply do not fold -- `None` here
    is the normal case for session.count / lines_of_code.count, not a mapping gap."""
    mapping = registry.get("claude_code")
    seen: dict[str, set[str]] = {}
    for _r, _s, metric_name, attrs in _metric_points("metrics_session.jsonl"):
        seen.setdefault(str(mapping.measure_of(metric_name, attrs)), set()).add(metric_name)

    assert seen["tokens_input"] == {"claude_code.token.usage"}
    assert seen["tokens_cache_creation"] == {"claude_code.token.usage"}
    assert seen["cost_usd"] == {"claude_code.cost.usage"}
    assert seen["active_time_s"] == {"claude_code.active_time.total"}
    assert seen["None"] == {"claude_code.session.count"}


def test_measure_selector_requires_every_label_to_match(registry):
    mapping = registry.get("claude_code")
    assert mapping.measure_of("claude_code.token.usage", {"type": "output"}) == "tokens_output"
    assert mapping.measure_of("claude_code.token.usage", {"type": "unheard-of"}) is None
    assert mapping.measure_of("claude_code.token.usage", {}) is None


def test_measures_of_returns_every_match_not_just_the_first(registry):
    """`measure_of` (single, first-match) is unchanged and still used by its own callers above;
    `measures_of` is `_sum_by_measure`'s multi-match method, needed because a background
    datapoint is both an unconditional total (`tokens_input_bg`'s plan PM.3) and belongs to the
    unconditional total (`tokens_input`) -- an overlapping pair, not a partition. Without this,
    the broader selector (declared first in the YAML) would claim every datapoint and the
    narrower `_bg` one would never see a match."""
    mapping = registry.get("claude_code")
    background_input = {"type": "input", "query_source": "auxiliary"}
    assert set(mapping.measures_of("claude_code.token.usage", background_input)) == \
        {"tokens_input", "tokens_input_bg"}

    interactive_input = {"type": "input", "query_source": "main"}
    assert mapping.measures_of("claude_code.token.usage", interactive_input) == ["tokens_input"]

    assert mapping.measures_of("claude_code.session.count", {}) == []


# ------------------------------------------------------- attribute flattening

def test_any_value_variants():
    assert attr_value({"stringValue": "s"}) == "s"
    assert attr_value({"intValue": "559"}) == "559"     # OTLP/JSON carries int64 as a string
    assert attr_value({"doubleValue": 0.5}) == 0.5
    assert attr_value({"boolValue": False}) is False
    assert attr_value({"arrayValue": {"values": [{"stringValue": "a"}]}}) == ["a"]
    assert attr_value({"kvlistValue": {"values": [{"key": "k", "value": {"intValue": "1"}}]}}) == {"k": "1"}
    assert attr_value({}) is None
    assert attr_value(None) is None


def test_flatten_skips_keyless_entries():
    assert flatten_attrs([{"value": {"stringValue": "orphan"}}, {"key": "k", "value": {"stringValue": "v"}}]) == {"k": "v"}
