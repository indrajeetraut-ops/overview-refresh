"""interrupted_of pricing (efficiency note §2, bucket 3): the response the user interrupted and
discarded is recoverable in full, priced at its own model."""
from datetime import date

from common.model_pricing import Pricer
from server.worker.graph_fold import INTERRUPT_SENTINEL, interrupted_of

PRICING = Pricer.load(None, None)  # claude-opus-5: input $5/M, output $25/M
AT = date(2026, 1, 1)


def _events(model: str = "claude-opus-5") -> list[dict]:
    return [
        {"event_type": "assistant_message", "event_ts": "2026-01-01T00:00:00Z",
         "payload": {"model": model, "input_tokens": 1_000_000, "output_tokens": 40_000,
                     "cache_read_tokens": 0, "cache_write_tokens": 0}},
        {"event_type": "user_message", "event_ts": "2026-01-01T00:00:05Z",
         "payload": {"text": INTERRUPT_SENTINEL}},
    ]


def test_interrupted_response_is_priced_in_full():
    result = interrupted_of(_events(), PRICING, AT)
    assert result["count"] == 1
    assert result["events"][0]["usd_micros"] == 6_000_000  # $5 input + $1 output
    assert result["total_usd_micros"] == 6_000_000


def test_interrupted_is_unpriced_without_a_pricing_table():
    result = interrupted_of(_events())
    assert result["total_tokens"] == 1_040_000
    assert result["events"][0]["usd_micros"] is None
    assert result["total_usd_micros"] is None


def test_interrupted_total_is_none_when_any_response_is_unpriced():
    result = interrupted_of(_events(model="some-future-model-nobody-priced-yet"), PRICING, AT)
    assert result["events"][0]["usd_micros"] is None
    assert result["total_usd_micros"] is None


def test_interrupted_zero_usage_record_prices_as_zero_not_unpriced():
    events = _events(model="<synthetic>")
    events[0]["payload"].update(input_tokens=0, output_tokens=0)
    result = interrupted_of(events, PRICING, AT)
    assert result["events"][0]["usd_micros"] == 0
    assert result["total_usd_micros"] == 0
