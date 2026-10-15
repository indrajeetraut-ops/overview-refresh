"""In-process metrics for msging (design §10.5).

Accumulated in a module-level store, read back as a point-in-time snapshot
via `metrics()`. Metric names are the lists in design §10.5 verbatim; label
sets mirror the `Code` enum in codes.py so logs, metrics, and return codes
share one vocabulary.

Exposure is explicitly out of scope here -- no HTTP endpoint, no
`prometheus_client` dependency, no file snapshot, no CLI. Whoever ships the
metrics decides how to expose them; this module only accumulates and hands
back a snapshot.

The client half is threaded (design §6.4): `send()` runs on the producer's
thread while the SSE reader and cleaner loop each run on their own thread.
All mutation and the snapshot read go through `_lock` to keep the store
consistent under concurrent access from those threads.
"""
import copy
import json
import threading

_lock = threading.Lock()

# name -> {label_key: value}
# value is int/float for counter/add/gauge, or {"sum": float, "count": int}
# for observe.
_store: dict[str, dict[str, object]] = {}


def _label_key(labels: dict) -> str:
    """Sorted, space-free JSON so identical label sets always collide on the
    same key regardless of call-site kwarg order."""
    return json.dumps(labels, sort_keys=True, separators=(",", ":"))


def counter(name: str, **labels) -> None:
    """Increment a counter series by 1."""
    add(name, 1, **labels)


def add(name: str, n: int, **labels) -> None:
    """Increment a counter series by `n`."""
    key = _label_key(labels)
    with _lock:
        series = _store.setdefault(name, {})
        series[key] = series.get(key, 0) + n


def gauge(name: str, value: float, **labels) -> None:
    """Replace a gauge series' current value."""
    key = _label_key(labels)
    with _lock:
        series = _store.setdefault(name, {})
        series[key] = value


def observe(name: str, ms: float, **labels) -> None:
    """Record an observation into a sum+count series (e.g. latency)."""
    key = _label_key(labels)
    with _lock:
        series = _store.setdefault(name, {})
        entry = series.get(key)
        if entry is None:
            entry = {"sum": 0.0, "count": 0}
            series[key] = entry
        entry["sum"] += ms
        entry["count"] += 1


def metrics() -> dict:
    """Point-in-time snapshot of every series.

    Returns a deep copy, not a live reference: a caller iterating this
    snapshot must not be able to observe a worker thread's concurrent
    `add`/`observe` call mutating a nested series dict mid-iteration.
    """
    with _lock:
        return copy.deepcopy(_store)


def reset_metrics() -> None:
    """Test seam. Clears every series -- not meant for production use."""
    with _lock:
        _store.clear()
