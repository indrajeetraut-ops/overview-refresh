import io
import json

import structlog

from common.msging.log import configure_logging, get_logger, redact_key
from common.msging.metrics import add, counter, gauge, metrics, observe, reset_metrics


def setup_function():
    reset_metrics()


def test_redact_key_never_returns_the_secret():
    key = "tk_prod_" + "s" * 40
    out = redact_key(key)
    assert out not in (key,) and len(out) == 12 and key.startswith(out)


def test_redact_key_exactly_12_chars_is_not_the_key():
    key = "exactly12chr"
    assert len(key) == 12
    out = redact_key(key)
    assert out != key


def test_redact_key_shorter_than_12_is_not_the_key():
    key = "short"
    out = redact_key(key)
    assert out != key


def test_redact_key_empty_string_is_not_the_key():
    out = redact_key("")
    assert out != ""


def test_redact_key_none_is_not_the_key():
    out = redact_key(None)
    assert out is not None
    assert out != None  # noqa: E711 -- explicit value comparison, not identity


def test_configure_logging_json_renders_parseable_json_with_module():
    stream = io.StringIO()
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=stream))
    log = get_logger("test_mod")
    log.info("hello", foo="bar")

    line = stream.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["module"] == "test_mod"
    assert parsed["foo"] == "bar"
    assert parsed["event"] == "hello"


def test_counter_increments_and_labels_separate_series():
    counter("msgs_sent_total", err_code="ok")
    counter("msgs_sent_total", err_code="ok")
    counter("msgs_sent_total", err_code="slot_full")
    snap = metrics()
    assert snap["msgs_sent_total"]['{"err_code":"ok"}'] == 2
    assert snap["msgs_sent_total"]['{"err_code":"slot_full"}'] == 1


def test_gauge_replaces_rather_than_accumulates():
    gauge("pending_count", 5)
    gauge("pending_count", 3)
    assert metrics()["pending_count"]["{}"] == 3


def test_observe_records_sum_and_count():
    observe("send_latency_ms", 10.0)
    observe("send_latency_ms", 30.0)
    assert metrics()["send_latency_ms"]["{}"] == {"sum": 40.0, "count": 2}


def test_add_increments_by_n():
    add("epoch_swept_msgs_total", 7)
    assert metrics()["epoch_swept_msgs_total"]["{}"] == 7
