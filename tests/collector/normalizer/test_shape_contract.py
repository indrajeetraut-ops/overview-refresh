"""Drift guard for the published normalized-event contract.

The normalizer is the fidelity choke point. If a refactor silently renames `input_tokens`,
drops `derived_from_event_id`, or flips `exit_status` from int to str, every value-asserting
test still passes while every downstream consumer quietly breaks. This freezes the *structure*
instead: per `event_type`, the exact payload key-paths and the value types each may take,
unioned over a fixed corpus.

The artifacts it guards are the ones the causal-graph worker codes against:

    src/common/events/normalized_schema.json   the contract
    src/common/events/payload.json             a worked example

They are **generated, not written** -- a hand-written schema is wrong the moment someone adds a
field and forgets the doc. This test failing *is* the staleness alarm: the artifacts can only
be wrong if nobody regenerated them.

Regenerate after an intended change, then eyeball the git diff:

    python -m common.events.cli_publish_payload

Derivation lives in `src/common/events/schema.py`, shared with that CLI, so the guard and the
generator can never measure different things. The corpus is `fixtures.normalizer.corpus`, for
the same reason.

`payload.raw` and the vendor-opaque `input` / `server_tool_use` dicts are recorded as a single
type, not descended into -- their internal shape is the vendor's, arbitrary by design (§9), so
pinning it would make this fire on their changes rather than ours.
"""
from __future__ import annotations

import json

import pytest

from common.events import SCHEMA_VERSION
from common.events.cli_publish_payload import PAYLOAD_PATH, SCHEMA_PATH
from common.events.descriptions import missing_descriptions
from common.events.schema import derive_shape, diff_shape, worked_example
from fixtures.normalizer import corpus

REGEN_HINT = "Regenerate with:  python -m common.events.cli_publish_payload"


@pytest.fixture(scope="module")
def sessions():
    return list(corpus.all_sessions())


def _load(path):
    if not path.is_file():
        pytest.fail(f"missing published artifact {path}\n{REGEN_HINT}")
    return json.loads(path.read_text(encoding="utf-8"))


# -- the contract ---------------------------------------------------------------------------


def test_published_schema_matches_real_output(sessions):
    actual = derive_shape(sessions, schema_version=SCHEMA_VERSION)
    problems = diff_shape(_load(SCHEMA_PATH), actual)
    assert not problems, (
        "published schema no longer matches what the normalizer emits:\n  "
        + "\n  ".join(problems)
        + f"\n\nIf the change is intentional: {REGEN_HINT}\n"
        "Then review the diff, and bump SCHEMA_VERSION in src/common/events/__init__.py if a "
        "field was removed, retyped, or redefined -- additive changes do not need a bump."
    )


def test_published_example_matches_real_output(sessions):
    actual = worked_example(sessions, schema_version=SCHEMA_VERSION)
    assert _load(PAYLOAD_PATH) == actual, f"payload.json is stale.\n{REGEN_HINT}"


def test_artifacts_agree_on_schema_version():
    """The two files are published together and must never disagree about which contract they
    describe -- a consumer reads the version from whichever it happens to open."""
    assert _load(SCHEMA_PATH)["schema_version"] == SCHEMA_VERSION
    assert _load(PAYLOAD_PATH)["schema_version"] == SCHEMA_VERSION


# -- the corpus still covers everything -----------------------------------------------------


def test_corpus_covers_every_event_type(sessions):
    """Guard the guard: if the corpus stops producing a type, the contract silently narrows and
    this test is the only thing that notices."""
    expected = {
        "session_start", "user_message", "assistant_message", "tool_call", "tool_result",
        "file_change", "sub_agent_spawn", "api_error", "compaction", "attachment", "unknown",
    }
    covered = set(derive_shape(sessions, schema_version=SCHEMA_VERSION)["payload_by_type"])
    assert expected <= covered, f"corpus stopped covering: {expected - covered}"


def test_example_carries_one_event_per_type(sessions):
    example = worked_example(sessions, schema_version=SCHEMA_VERSION)
    schema = derive_shape(sessions, schema_version=SCHEMA_VERSION)
    assert set(example["events_by_type"]) == set(schema["payload_by_type"])


def test_example_elides_raw(sessions):
    """raw is the full vendor record -- routinely kilobytes, and it would bury the fields the
    example exists to show."""
    example = worked_example(sessions, schema_version=SCHEMA_VERSION)
    for etype, event in example["events_by_type"].items():
        raw = event["payload"].get("raw")
        if raw is not None:
            assert isinstance(raw, str) and raw.startswith("<full vendor record"), etype


# -- derivation behaviour -------------------------------------------------------------------


def test_types_are_unioned_not_intersected():
    """A field that is null in one session and str in another must be recorded as both --
    that is what a consumer has to handle."""

    class _E:
        def __init__(self, payload):
            self.event_type, self.record_id, self.parent_id, self.event_ts = "x", None, None, None
            self.payload = payload

    class _S:
        def __init__(self, events):
            self.events = events

    shape = derive_shape([_S([_E({"f": None}), _E({"f": "v"})])], schema_version="t")
    assert shape["payload_by_type"]["x"]["fields"]["f"]["types"] == ["null", "str"]


def test_opaque_keys_are_not_descended_into():
    class _E:
        def __init__(self, payload):
            self.event_type, self.record_id, self.parent_id, self.event_ts = "x", None, None, None
            self.payload = payload

    class _S:
        def __init__(self, events):
            self.events = events

    shape = derive_shape([_S([_E({"raw": {"vendor_field": 1}, "shown": {"inner": 1}})])], schema_version="t")
    paths = shape["payload_by_type"]["x"]["fields"]
    assert paths["raw"]["types"] == ["dict"] and "raw.vendor_field" not in paths
    assert "shown.inner" in paths


# -- descriptions are part of the contract --------------------------------------------------


def test_every_published_field_has_a_description(sessions):
    """Types are derived and cannot lie; meaning cannot be derived at all. A published field
    with no prose looks authoritative and says nothing, which is worse than not publishing it.

    This is the guard that makes documenting a new field non-optional -- the publisher refuses
    to write one out, and this fails if the committed artifact ever contains one."""
    shape = derive_shape(sessions, schema_version=SCHEMA_VERSION)
    gaps = missing_descriptions({k: v["fields"] for k, v in shape["payload_by_type"].items()})
    assert not gaps, (
        f"{len(gaps)} published field(s) have no description. Add them to "
        "src/common/events/descriptions.py:\n  " + "\n  ".join(gaps)
    )


def test_committed_schema_carries_the_descriptions():
    """Not just derivable -- actually present in the file a consumer opens."""
    schema = _load(SCHEMA_PATH)
    assistant = schema["payload_by_type"]["assistant_message"]
    assert assistant["description"]
    field = assistant["fields"]["estimated_visible_tokens"]
    assert field["types"] == ["int"]
    assert "estimate" in field["description"].lower()
    for name, spec in schema["outer"].items():
        assert spec["description"], f"outer field {name} has no description"


def test_publisher_refuses_an_undocumented_field(monkeypatch, tmp_path):
    """The gate itself. Without this, the rule is a convention rather than a guarantee."""
    from common.events import cli_publish_payload as cli

    monkeypatch.setattr(cli, "missing_descriptions", lambda _: ["assistant_message.made_up"])
    with pytest.raises(SystemExit) as exc:
        cli.main(["--out-dir", str(tmp_path)])
    assert exc.value.code == 1
    assert not (tmp_path / SCHEMA_PATH.name).exists(), "refused publish must not write"


def test_description_drift_is_reported_separately_from_type_drift(sessions):
    """A stale description is real drift, but it is a different fix from a changed type and
    must not be reported as one."""
    actual = derive_shape(sessions, schema_version=SCHEMA_VERSION)
    stale = json.loads(json.dumps(actual))
    stale["payload_by_type"]["assistant_message"]["fields"]["model"]["description"] = "old text"
    problems = diff_shape(stale, actual)
    assert problems == ["assistant_message.model: description changed -- regenerate"]
