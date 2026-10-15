"""Rebuild the OTLP test fixtures from a recorded local dump.

Not a test and not a module CLI -- a developer utility, run by hand when the fixtures
need refreshing against a newer client version:

    python tests/fixtures/otel/make_fixtures.py ~/.claude/otel-local/data

The recorded dump holds **real identifiers** (email, account ids, org id, OS user).
Every one of them is rewritten to a synthetic value here before anything is written
into the repo -- see `IDENTITY_MAP`. The rewrite is exhaustive by construction: the
walker replaces on *value*, anywhere in the payload, so a new attribute carrying the
same real value is caught too. `verify_clean()` re-scans the output and fails loudly
if any source value survived.

Outputs (see README.md for what each one is for):
  metrics_session.jsonl        -- one session's metric exports, all 12 series
  logs_session.jsonl           -- the same session's log records, full event zoo
  metrics_counter_reset.jsonl  -- synthesized two-epoch series (counter reset)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT_DIR = Path(__file__).parent

#: The session the fixtures are built around: 12 series, a genuine two-epoch
#: series (session.count across a resume), a background `auxiliary` query_source,
#: and 8 api_request events -- everything the fold tests need in one session.
SESSION_ID = "8f87e0a5-872f-4617-9d27-d9d0bc8db54d"

#: real value -> synthetic value. Applied to every string in the payload.
IDENTITY_MAP = {
    "anil.khuteta@gmail.com": "dev1@example.test",
    "c313c2ff-9490-498c-88fe-08bb1222c2c3": "11111111-1111-4111-8111-111111111111",
    "user_01R6AGsC6uozA7UuJaKwVR8E": "user_00000000000000000001",
    "bdf2b0ea-1ab2-4741-a47e-6fe092d79fe7": "22222222-2222-4222-8222-222222222222",
    "aff16b7f1bb09737e337dd2e1f7084c4b0c6312dd37c48e3d714e5ce70680b50":
        "0000000000000000000000000000000000000000000000000000000000000001",
    "anil": "devuser",
}

#: Attributes that carry free-form content -- prompts, model replies, tool inputs
#: (which embed absolute paths and file contents). None of them is mapped to a
#: canonical column and none survives the v1 event filter, so the fixtures keep the
#: attribute *shape* and throw the value away. Substring identifiers hide in here;
#: replacing the whole value is the only reliable scrub, and a content-free fixture
#: is what the §13 logging rule would ask for anyway.
CONTENT_ATTRS = {"prompt", "response", "tool_input", "tool_parameters"}
CONTENT_PLACEHOLDER = "<redacted-for-fixture>"

#: Batches are subsampled to keep the fixture small. Every series' first and last
#: datapoint per epoch is retained (that is what the last-value fold reads); the
#: middle is thinned so the "many intervals of the same series" property survives
#: without carrying 85 exports of 12 series into git.
KEEP_EVERY = 8


def scrub(node):
    """Rewrite every mapped identifier and blank every content attribute."""
    if isinstance(node, dict):
        if node.get("key") in CONTENT_ATTRS:
            return {"key": node["key"], "value": {"stringValue": CONTENT_PLACEHOLDER}}
        return {k: scrub(v) for k, v in node.items()}
    if isinstance(node, list):
        return [scrub(v) for v in node]
    if isinstance(node, str):
        return IDENTITY_MAP.get(node, node)
    return node


def _attrs(obj) -> dict:
    return {a["key"]: next(iter(a["value"].values())) for a in obj.get("attributes", [])}


def _body(metric) -> tuple[str, dict] | tuple[None, None]:
    for kind in ("sum", "gauge", "histogram", "exponentialHistogram"):
        if kind in metric:
            return kind, metric[kind]
    return None, None


def filter_metrics(batch: dict, session_id: str) -> dict | None:
    """A copy of `batch` holding only datapoints belonging to `session_id`."""
    out_rm = []
    for rm in batch.get("resourceMetrics", []):
        out_sm = []
        for sm in rm.get("scopeMetrics", []):
            out_m = []
            for metric in sm.get("metrics", []):
                kind, body = _body(metric)
                if kind is None:
                    continue
                points = [dp for dp in body.get("dataPoints", [])
                          if _attrs(dp).get("session.id") == session_id]
                if not points:
                    continue
                out_m.append({**metric, kind: {**body, "dataPoints": points}})
            if out_m:
                out_sm.append({**sm, "metrics": out_m})
        if out_sm:
            out_rm.append({**rm, "scopeMetrics": out_sm})
    return {"resourceMetrics": out_rm} if out_rm else None


def filter_logs(batch: dict, session_id: str) -> dict | None:
    out_rl = []
    for rl in batch.get("resourceLogs", []):
        out_sl = []
        for sl in rl.get("scopeLogs", []):
            records = [lr for lr in sl.get("logRecords", [])
                       if _attrs(lr).get("session.id") == session_id]
            if records:
                out_sl.append({**sl, "logRecords": records})
        if out_sl:
            out_rl.append({**rl, "scopeLogs": out_sl})
    return {"resourceLogs": out_rl} if out_rl else None


def subsample(batches: list[dict]) -> list[dict]:
    """Thin the middle, keep the ends -- the ends carry the last values."""
    if len(batches) <= 6:
        return batches
    head, tail = batches[:2], batches[-2:]
    middle = batches[2:-2][::KEEP_EVERY]
    return head + middle + tail


#: (start offset, time offset, value) per export of the synthesized reset fixture.
#: Two epochs, two exports each, chosen so the four candidate readings are all
#: distinct numbers: last-value-per-epoch summed = 340 (correct), naive sum over all
#: points = 480, overall last value = 90, overall max = 250. A fold that gets any of
#: the wrong three cannot accidentally pass.
_RESET_EXPORTS = [
    (0, 60, 100),
    (0, 120, 250),
    (300, 360, 40),
    (300, 420, 90),
]
NS = 1_000_000_000


def build_counter_reset(metric_batches: list[dict]) -> list[dict]:
    """A single `claude_code.token.usage` series exported across a counter reset.

    Synthesized rather than sampled: the recorded dump has no reset with enough
    points on either side of it to tell a correct fold from an incorrect one.
    Everything except the timestamps and the value is a real payload.
    """
    template = None
    for batch in reversed(metric_batches):
        for rm in batch.get("resourceMetrics", []):
            for sm in rm.get("scopeMetrics", []):
                for metric in sm.get("metrics", []):
                    if metric["name"] != "claude_code.token.usage":
                        continue
                    kind, body = _body(metric)
                    for dp in body["dataPoints"]:
                        if _attrs(dp).get("type") == "output":
                            template = (rm, sm, metric, kind, body, dp)
                            break
    if template is None:
        raise SystemExit("no claude_code.token.usage output datapoint to build from")
    rm, sm, metric, kind, body, dp = template
    base = int(dp["timeUnixNano"])

    out = []
    for start_off, time_off, value in _RESET_EXPORTS:
        point = {**{k: v for k, v in dp.items() if k not in ("asInt", "asDouble")},
                 "startTimeUnixNano": str(base + start_off * NS),
                 "timeUnixNano": str(base + time_off * NS),
                 "asInt": str(value)}
        out.append({"resourceMetrics": [{
            "resource": rm["resource"],
            "scopeMetrics": [{
                "scope": sm["scope"],
                "metrics": [{**{k: v for k, v in metric.items() if k != kind},
                             kind: {**body, "dataPoints": [point]}}],
            }],
        }]})
    return out


def build_jsonl_session(log_batches: list[dict]) -> list[dict]:
    """A Claude Code JSONL session matching the OTel log fixture, request for request.

    Synthetic, and generated rather than hand-written on purpose: the end-to-end test joins
    the two signal sources on `requestId`, so the two fixtures have to agree about session id,
    request ids and token counts. Deriving this one from the other makes that agreement a
    property of the build instead of something a future edit can quietly break.

    Only the fields the normalizer reads are populated; content is absent by construction,
    which is what the fixtures want anyway.

    Each request gets its own turn: a synthetic `user` record carrying a fresh `promptId`
    precedes its `assistant` record. The causal-graph builder opens a turn only on a
    `promptId` seen on a `user_message`/`tool_result` record (`builder._prompt_id`) --
    without one, every assistant record lands in `startup` and never becomes a graph node,
    which silently empties `session_info.models`/`tools`/etc. for the whole session.
    """
    records: list[dict] = [{"type": "mode", "mode": "normal", "sessionId": SESSION_ID}]
    previous_uuid = None
    for batch in log_batches:
        for rl in batch.get("resourceLogs", []):
            for sl in rl.get("scopeLogs", []):
                for lr in sl.get("logRecords", []):
                    attrs = _attrs(lr)
                    if attrs.get("event.name") != "api_request":
                        continue
                    user_uuid = f"user-{attrs['request_id']}"
                    records.append({
                        "type": "user",
                        "uuid": user_uuid,
                        "parentUuid": previous_uuid,
                        "isSidechain": False,
                        "promptId": f"prompt-{attrs['request_id']}",
                        "sessionId": SESSION_ID,
                        "session_id": SESSION_ID,
                        "timestamp": attrs["event.timestamp"],
                        "userType": "external",
                        "entrypoint": "cli",
                        "cwd": "/work/repo",
                        "version": "2.1.219",
                        "message": {"role": "user", "content": CONTENT_PLACEHOLDER},
                    })
                    previous_uuid = user_uuid
                    uuid = f"msg-{attrs['request_id']}"
                    records.append({
                        "type": "assistant",
                        "uuid": uuid,
                        "parentUuid": previous_uuid,
                        "isSidechain": False,
                        "requestId": attrs["request_id"],
                        "sessionId": SESSION_ID,
                        "session_id": SESSION_ID,
                        "timestamp": attrs["event.timestamp"],
                        "userType": "external",
                        "entrypoint": "cli",
                        "cwd": "/work/repo",
                        "version": "2.1.219",
                        "message": {
                            "id": f"msg_{attrs['request_id']}",
                            "type": "message",
                            "role": "assistant",
                            "model": attrs.get("model"),
                            "content": [{"type": "text", "text": CONTENT_PLACEHOLDER}],
                            "stop_reason": "end_turn",
                            "usage": {
                                "input_tokens": int(attrs.get("input_tokens", 0)),
                                "output_tokens": int(attrs.get("output_tokens", 0)),
                                "cache_read_input_tokens": int(attrs.get("cache_read_tokens", 0)),
                                "cache_creation_input_tokens":
                                    int(attrs.get("cache_creation_tokens", 0)),
                            },
                        },
                    })
                    previous_uuid = uuid
    return records


def verify_clean(paths: list[Path]) -> None:
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for real in IDENTITY_MAP:
            if real in text:
                raise SystemExit(f"ABORT: {path} still contains a source identifier")


def main(argv: list[str]) -> int:
    src = Path(argv[1]).expanduser() if len(argv) > 1 else Path.home() / ".claude/otel-local/data"
    metrics_src = src / "claude-code-metrics.jsonl"
    logs_src = src / "claude-code-logs.jsonl"

    metric_batches = []
    for line in metrics_src.read_text(encoding="utf-8").splitlines():
        kept = filter_metrics(json.loads(line), SESSION_ID)
        if kept:
            metric_batches.append(scrub(kept))
    log_batches = []
    for line in logs_src.read_text(encoding="utf-8").splitlines():
        kept = filter_logs(json.loads(line), SESSION_ID)
        if kept:
            log_batches.append(scrub(kept))

    metric_batches = subsample(metric_batches)
    written = []
    for name, batches in (
        ("metrics_session.jsonl", metric_batches),
        ("logs_session.jsonl", log_batches),
        ("metrics_counter_reset.jsonl", build_counter_reset(metric_batches)),
        ("jsonl_session.jsonl", build_jsonl_session(log_batches)),
    ):
        path = OUT_DIR / name
        path.write_text("".join(json.dumps(b, separators=(",", ":")) + "\n" for b in batches),
                        encoding="utf-8")
        written.append(path)
        print(f"{path.name}: {len(batches)} batches, {path.stat().st_size} bytes")

    verify_clean(written)
    print("anonymization verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
