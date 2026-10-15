from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from server.api.precedence import ACTIVE_TIME_CHAIN, resolve, resolve_aggregate, resolve_hour_histogram

TOKENS_CHAIN = ["otel_measures", "otel_ledger", "jsonl_models"]
COST_CHAIN = ["otel_vendor", "estimated"]


def _row(otel=None, models=None, cost_effective_usd_micros=None, turns=None, started_at=None):
    return SimpleNamespace(otel=otel or {}, models=models or {},
                           cost_effective_usd_micros=cost_effective_usd_micros,
                           turns=turns or [], started_at=started_at)


# ------------------------------------------------------------------ tokens chain

def test_otel_measures_wins_when_present():
    row = _row(otel={"measures": {"tokens_input": "10", "tokens_output": "20"},
                     "tokens": {"tokens_input": "10", "tokens_output": "20",
                               "tokens_cache_read": "0", "tokens_cache_creation": "0"}})
    value, source = resolve(row, TOKENS_CHAIN)
    assert value == 30
    assert source == "otel_measures"


def test_otel_measures_availability_keys_on_the_sparse_measures_dict_not_zero_tokens():
    """`otel["tokens"]` is always present in the block with zero-defaults, even when the
    metrics fold never ran (build_cost_block always emits all four TOKEN_MEASURES keys) -- and
    `_fmt`'s "0" is a truthy string. Availability has to key on `otel["measures"]` (the sparse
    dict `_sum_by_measure` actually produced), or every session would wrongly resolve here."""
    row = _row(otel={"measures": {}, "tokens": {"tokens_input": "0", "tokens_output": "0",
                                                "tokens_cache_read": "0", "tokens_cache_creation": "0"}})
    assert resolve(row, TOKENS_CHAIN) is None


def test_otel_ledger_wins_when_measures_absent_but_requests_present():
    """The metrics-only-fleet-vs-ledger split (MODES §3.2's note): a session with request
    rows but no metrics fold output at all falls through to the ledger."""
    row = _row(otel={"measures": {}, "requests": [{"input_tokens": 5, "output_tokens": 7,
                                                    "cache_read_tokens": 0, "cache_creation_tokens": 0}]})
    value, source = resolve(row, TOKENS_CHAIN)
    assert value == 12
    assert source == "otel_ledger"


def test_jsonl_models_is_the_jsonl_only_fallback():
    row = _row(models={"claude-opus-5": {"input": 3, "output": 4, "cache_read": 1, "cache_write": 2}})
    value, source = resolve(row, TOKENS_CHAIN)
    assert value == 10
    assert source == "jsonl_models"


def test_resolve_returns_none_when_nothing_available():
    assert resolve(_row(), TOKENS_CHAIN) is None


# -------------------------------------------------------------------- cost chain

def test_otel_vendor_wins_when_every_request_is_vendor_priced():
    row = _row(otel={"cost_usd_micros": 1000, "cost_effective_usd_micros": 1000,
                     "cost_basis": {"vendor": 3, "estimated": 0, "unknown": 0}},
              cost_effective_usd_micros=1000)
    value, source = resolve(row, COST_CHAIN)
    assert value == 1000
    assert source == "otel_vendor"


def test_estimated_wins_and_uses_the_blended_total_not_the_estimate_alone():
    """A mixed session (some vendor, some estimated) must report the blended
    cost_effective_usd_micros, not cost_est_usd_micros alone -- that would omit the
    vendor-priced requests."""
    row = _row(otel={"cost_usd_micros": 700, "cost_est_usd_micros": 300,
                     "cost_effective_usd_micros": 1000,
                     "cost_basis": {"vendor": 2, "estimated": 1, "unknown": 0}},
              cost_effective_usd_micros=1000)
    value, source = resolve(row, COST_CHAIN)
    assert value == 1000          # the blended total, not 300 (estimate-only)
    assert source == "estimated"


def test_estimated_wins_for_a_jsonl_only_session_with_no_otel_block_at_all():
    row = _row(cost_effective_usd_micros=500)
    value, source = resolve(row, COST_CHAIN)
    assert value == 500
    assert source == "estimated"


# ------------------------------------------------------------------ active time chain

def test_otel_measures_active_time_wins_when_present():
    row = _row(otel={"measures": {"active_time_s": "1"}, "active_time_s": "148.502"})
    value, source = resolve(row, ACTIVE_TIME_CHAIN)
    assert value == 148.502
    assert source == "otel_measures_active_time"


def test_active_time_availability_also_keys_on_measures_not_the_zero_default_key():
    row = _row(otel={"measures": {}, "active_time_s": "0"}, turns=[{"duration_ms": 5000}])
    value, source = resolve(row, ACTIVE_TIME_CHAIN)
    assert value == 5.0
    assert source == "turn_duration"


def test_turn_duration_fallback_sums_all_turns():
    row = _row(turns=[{"duration_ms": 1000}, {"duration_ms": 2500}, {"duration_ms": None}])
    value, source = resolve(row, ACTIVE_TIME_CHAIN)
    assert value == 3.5
    assert source == "turn_duration"


def test_active_time_none_for_an_otel_only_session_with_no_turns():
    assert resolve(_row(), ACTIVE_TIME_CHAIN) is None


# ---------------------------------------------------------------- aggregate resolution

def test_aggregate_sums_per_session_and_reports_every_contributing_source():
    both_mode = _row(otel={"measures": {"tokens_input": "100"},
                           "tokens": {"tokens_input": "100", "tokens_output": "0",
                                     "tokens_cache_read": "0", "tokens_cache_creation": "0"}})
    jsonl_only = _row(models={"claude-opus-5": {"input": 25, "output": 0,
                                                "cache_read": 0, "cache_write": 0}})
    total, sources = resolve_aggregate([both_mode, jsonl_only], TOKENS_CHAIN)
    assert total == 125
    assert sources == ["jsonl_models", "otel_measures"]     # sorted, not insertion order


def test_aggregate_skips_sessions_with_nothing_resolvable():
    resolvable = _row(models={"m": {"input": 1, "output": 0, "cache_read": 0, "cache_write": 0}})
    empty = _row()
    total, sources = resolve_aggregate([resolvable, empty], TOKENS_CHAIN)
    assert total == 1
    assert sources == ["jsonl_models"]


# ------------------------------------------------------------------ hour histogram (TZ track)

def test_hour_histogram_buckets_by_utc_hour_when_tz_is_utc():
    row = _row(turns=[{"started": "2026-08-01T23:30:00Z"}])
    histogram, sources = resolve_hour_histogram([row], ZoneInfo("UTC"))
    assert histogram[23] == 1
    assert sources == ["turn_started"]


def test_hour_histogram_shifts_to_the_given_local_hour():
    """23:30 UTC is 16:30 the same day in America/Los_Angeles (UTC-7 in August, DST) -- the
    exact case a UTC-only histogram gets wrong for any non-UTC user."""
    row = _row(turns=[{"started": "2026-08-01T23:30:00Z"}])
    histogram, sources = resolve_hour_histogram([row], ZoneInfo("America/Los_Angeles"))
    assert histogram[16] == 1
    assert histogram[23] == 0
    assert sources == ["turn_started"]


def test_hour_histogram_session_started_fallback_also_respects_tz():
    row = _row(started_at=datetime(2026, 8, 1, 23, 30, tzinfo=timezone.utc))
    histogram, sources = resolve_hour_histogram([row], ZoneInfo("America/Los_Angeles"))
    assert histogram[16] == 1
    assert sources == ["session_started"]
