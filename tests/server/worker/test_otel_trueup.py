"""The true-up acceptance battery (otel-intake design §4.4).

Four properties, each a way the design can be wrong rather than a way the code can be:

1. **The fold is not a sum.** Cumulative datapoints are running totals; summing them inflated
   the 2026-08-02 reconciliation 150×. Every fold assertion here also asserts the naive number
   is *not* what came out.
2. **Counter resets survive.** Totals are summed across reset epochs, last-value within one.
3. **Arrival order does not matter.** OTel-first, JSONL-first and interleaved must converge on
   the identical cost block, with `otel_only` migrating to `matched` when the JSONL lands.
4. **Prune never outruns the fold.** A point above the watermark is evidence the fold has not
   consumed yet, and deleting it would lower a session's totals with nothing left to notice
   with.
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from server.ingest.otlp.mapping import MappingRegistry
from server.ingest.otlp.receiver import OtlpReceiver
from server.storage import session_scope
from server.storage.dbm import operations
from server.storage.engine import dispose_engine, get_engine
from server.storage.queries import QueryCatalog
from server.storage.schema import OtelMetricPoint, OtelWatermark
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow
from server.storage.schema import User as UserRow
from server.worker.fold_api import SessionInfo as SessionInfoState
from server.worker.otel_trueup import (
    BASIS_ESTIMATED,
    BASIS_UNKNOWN,
    BASIS_VENDOR,
    GAP_PRE_OTEL,
    GAP_UNOBSERVED,
    SRC_BOTH,
    SRC_JSONL_ONLY,
    SRC_OTEL_ONLY,
    OtelTrueUp,
)
from server.worker.persistence import persist_session_info
from tests.server.storage.conftest import cfg, requires_db  # noqa: F401 -- pytest fixture

pytestmark = requires_db

FIXTURES = Path("tests/fixtures/otel")
SESSION_ID = "8f87e0a5-872f-4617-9d27-d9d0bc8db54d"
ORG = "org-acme"

#: Hand-computed from the fixture (tests/fixtures/otel/README.md): last value per epoch,
#: summed across epochs. The right-hand column is what a naive sum over all datapoints
#: produces -- the trap this whole design exists to avoid.
FOLD_GOLDEN = {
    "tokens_input": (Decimal("517"), Decimal("2597")),
    "tokens_output": (Decimal("7446"), Decimal("42116")),
    "tokens_cache_read": (Decimal("545250"), Decimal("3060049")),
    "tokens_cache_creation": (Decimal("59053"), Decimal("398296")),
    # Exact decimals, not floats: the wire says 0.9870844999999999 and that is what is stored
    # and summed. A float pipeline would land somewhere near here and nowhere exactly.
    "cost_usd": (Decimal("1.05188999999999989"), Decimal("6.57886949999999945")),
    "active_time_s": (Decimal("148.502"), Decimal("742.510")),
}


@pytest.fixture
def db(cfg):  # noqa: F811
    get_engine(cfg)
    operations.init(cfg, force=True)
    yield cfg
    dispose_engine()


@pytest.fixture
def registry() -> MappingRegistry:
    return MappingRegistry.load("config/otel")


@pytest.fixture
def catalog() -> QueryCatalog:
    return QueryCatalog.load("config/queries")


@pytest.fixture
def trueup(catalog, registry) -> OtelTrueUp:
    # No lag: these tests write and fold in the same instant, and a lag would push the
    # watermark behind rows the tick just consumed.
    return OtelTrueUp(catalog, registry, watermark_lag_s=0.0)


def _batches(name: str) -> list[dict]:
    return [json.loads(line) for line in
            (FIXTURES / name).read_text(encoding="utf-8").splitlines()]


def _load_otel(registry, name: str, signal: str = "metrics",
               now: datetime | None = None) -> None:
    receiver = OtlpReceiver(registry)
    with session_scope() as s:
        for batch in _batches(name):
            if signal == "metrics":
                receiver.ingest_metrics(batch, ORG, s, now=now)
            else:
                receiver.ingest_logs(batch, ORG, s, now=now)


def _load_jsonl(request_ids: list[str], session_id: str = SESSION_ID,
                event_ts: datetime | None = None, model: str = "claude-opus-5") -> None:
    """A JSONL-path session carrying `assistant_message` events for those request ids.

    Two events per request id, deliberately: one API request can stream several assistant
    messages, and the join has to dedup them. Without that dedup the JSONL side inflates
    exactly the way naive datapoint summing inflates the metrics side.

    `event_ts` and `model` are parameters rather than something a test edits afterwards.
    Mutating `signal_blob` in place and re-assigning it does not persist: the old and new
    values compare equal (they share the mutated inner objects), so SQLAlchemy concludes
    there is no net change and skips the UPDATE.
    """
    now = datetime.now(timezone.utc)
    stamp = (event_ts or now).isoformat()
    events = []
    for request_id in request_ids:
        for i in range(2):
            events.append({
                "event_type": "assistant_message",
                "record_id": f"{request_id}-{i}",
                "parent_id": None,
                "event_ts": stamp,
                "payload": {
                    "provider_request_id": request_id,
                    "model": model,
                    "input_tokens": 2, "output_tokens": 265,
                    "cache_read_tokens": 22042, "cache_write_tokens": 48630,
                },
            })
    with session_scope() as s:
        row = s.get(SessionRow, session_id)
        if row is None:
            row = SessionRow(session_id=session_id, producer_id="p", agent="claude_code",
                             started_at=now, last_event_at=now, signal_blob={},
                             info_blob={}, subagents=[])
            s.add(row)
        row.signal_blob = {"events": events}


def _block(session_id: str = SESSION_ID) -> dict:
    with session_scope() as s:
        return s.get(SessionInfoRow, session_id).otel


def _datasource(session_id: str = SESSION_ID) -> str | None:
    """`datasource` is PM.1's promoted `session_info` column, not a key inside the `otel` block
    -- it has to be sortable/filterable outside JSONB, which is the whole reason it's a column."""
    with session_scope() as s:
        return s.get(SessionInfoRow, session_id).datasource


def _otel_request_ids() -> list[str]:
    from server.storage.schema import OtelApiRequest

    with session_scope() as s:
        return sorted(r.request_id for r in s.scalars(select(OtelApiRequest)).all())


# ------------------------------------------------------------------- the fold

def test_last_value_fold_matches_the_golden_and_not_the_naive_sum(db, registry, trueup):
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()

    measures = _block()["measures"]
    for name, (folded, naive) in FOLD_GOLDEN.items():
        assert Decimal(measures[name]) == folded, name
        # the 150x trap, asserted directly: the fold must not be a sum
        assert Decimal(measures[name]) != naive, name


def test_counter_reset_totals_are_summed_across_epochs(db, registry, trueup):
    """The fixture is built so all four candidate readings differ: correct 340, naive sum 480,
    overall last value 90, overall max 250."""
    _load_otel(registry, "metrics_counter_reset.jsonl")
    trueup.tick()

    output = Decimal(_block()["measures"]["tokens_output"])
    assert output == Decimal("340")
    assert output not in (Decimal("480"), Decimal("90"), Decimal("250"))


#: The `query_source`-narrowed sub-views (PM.3) alongside the unconditional totals in
#: FOLD_GOLDEN -- a datapoint contributes to both (`_sum_by_measure` via `measures_of`), so
#: this is an addition to the measured set, not a replacement of it.
BACKGROUND_MEASURES = {"tokens_input_bg", "tokens_output_bg", "tokens_cache_read_bg",
                       "tokens_cache_creation_bg", "cost_usd_bg"}


def test_metrics_without_a_canonical_measure_do_not_fold(db, registry, trueup):
    """`session.count` is stored and simply does not appear -- normal, not a gap (§4.6)."""
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()
    assert set(_block()["measures"]) == set(FOLD_GOLDEN) | BACKGROUND_MEASURES


# ------------------------------------------------------------- the three-way join

def test_matched_requests_adopt_otel_cost(db, registry, trueup):
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(_otel_request_ids())
    trueup.tick()

    block = _block()
    assert block["sources"] == {SRC_BOTH: 8, SRC_OTEL_ONLY: 0, SRC_JSONL_ONLY: 0}
    assert all(r["sources"] == SRC_BOTH for r in block["requests"])
    assert all(r["cost_usd_micros"] is not None for r in block["requests"])
    assert block["cost_complete"] is True
    assert block["cost_usd_micros"] == sum(r["cost_usd_micros"] for r in block["requests"])


def test_otel_only_covers_the_background_calls_jsonl_never_sees(db, registry, trueup):
    """The ~5.7% of session spend the JSONL path is structurally blind to (§3)."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl([])                       # a session with no assistant_message events yet
    trueup.tick()

    block = _block()
    assert block["sources"][SRC_OTEL_ONLY] == 8
    assert block["sources"][SRC_BOTH] == 0
    assert {r["query_source"] for r in block["requests"]} >= {"prompt_suggestion", "sdk"}


def test_otel_only_stub_gets_raw_account_uuid_without_a_link(db, registry, trueup):
    """No `users` row links the OTel account identity to a JSONL `user_id` -- the stub falls
    back to the raw `user.account_uuid` (honest, never guessed), which is what made otel_only
    sessions invisible to a dashboard scoped to a different, JSONL-side `user_id`."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    trueup.tick()

    with session_scope() as s:
        assert s.get(SessionRow, SESSION_ID).user_id == "11111111-1111-4111-8111-111111111111"


def test_otel_only_stub_resolves_to_the_linked_jsonl_user_id(db, registry, trueup):
    """Once `utils/upload_claude_sessions.sh` has declared the link (fold_handler.py's
    `upsert_user_account_uuid`, seeded here directly), a fresh otel_only stub resolves to the
    same `user_id` the JSONL path uses -- the fix for sessions permanently invisible to their
    own developer's dashboard."""
    with session_scope() as s:
        s.add(UserRow(user_id="anil", account_uuid="11111111-1111-4111-8111-111111111111",
                      info={}, created_at=datetime.now(timezone.utc)))

    _load_otel(registry, "logs_session.jsonl", signal="logs")
    trueup.tick()

    with session_scope() as s:
        assert s.get(SessionRow, SESSION_ID).user_id == "anil"


def test_jsonl_only_requests_are_priced_from_tokens(db, registry, trueup):
    """OTel fires only on completion, so an interrupted request is permanently JSONL-only
    (§7). Its tokens are real, so the estimator can still price it -- the vendor figure stays
    null and the estimate lands in its own field, where nothing can confuse the two."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(_otel_request_ids() + ["req_interrupted_by_user"])
    trueup.tick()

    block = _block()
    assert block["sources"] == {SRC_BOTH: 8, SRC_OTEL_ONLY: 0, SRC_JSONL_ONLY: 1}
    estimated = next(r for r in block["requests"] if r["sources"] == SRC_JSONL_ONLY)
    assert estimated["cost_usd_micros"] is None            # the vendor never reported one
    assert estimated["cost_est_usd_micros"] > 0            # but we can price the tokens
    assert estimated["cost_basis"] == BASIS_ESTIMATED
    assert estimated["input_tokens"] == 2                  # input-side fields are real

    # The three session totals stay separable: vendor-only, estimate-only, and what to show.
    assert block["cost_usd_micros"] < block["cost_effective_usd_micros"]
    assert block["cost_effective_usd_micros"] == (
        block["cost_usd_micros"] + estimated["cost_est_usd_micros"])
    assert block["cost_complete"] is True                  # every request now has a cost


def test_a_model_with_no_price_stays_unknown_and_incomplete(db, registry, trueup):
    """`cost_complete: false` still means something after the estimator lands: it now marks
    the one case we genuinely cannot answer -- no vendor cost and no price for that model."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(["req_on_an_unpriced_model"], model="some-model-with-no-price")
    trueup.tick()

    block = _block()
    unpriced = next(r for r in block["requests"] if r["sources"] == SRC_JSONL_ONLY)
    assert unpriced["cost_basis"] == BASIS_UNKNOWN
    assert unpriced["cost_est_usd_micros"] is None         # never a partial or a guess
    assert block["cost_basis"][BASIS_UNKNOWN] == 1
    assert block["cost_complete"] is False


def test_the_jsonl_side_is_deduped_by_request_id(db, registry, trueup):
    """One request can stream several assistant_message events. Counting them twice is the
    JSONL-side version of the 150x bug."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(_otel_request_ids())      # writes two events per request id
    trueup.tick()
    assert len(_block()["requests"]) == 8


# ------------------------------------------------------- late-arrival convergence

@pytest.mark.parametrize("order", ["otel_first", "jsonl_first", "interleaved"])
def test_arrival_order_converges_on_the_same_cost_block(db, registry, trueup, order):
    """§4.4's named late-arrival test. Arrival order between the two sources is unconstrained
    -- JSONL can lag minutes or days in batch mode -- so the only acceptable outcome is that
    it does not matter."""
    request_ids_holder: list[str] = []

    if order == "otel_first":
        _load_otel(registry, "logs_session.jsonl", signal="logs")
        _load_otel(registry, "metrics_session.jsonl")
        trueup.tick()
        request_ids_holder = _otel_request_ids()
        _load_jsonl(request_ids_holder)
        trueup.tick()
    elif order == "jsonl_first":
        _load_jsonl([])
        trueup.tick()
        _load_otel(registry, "logs_session.jsonl", signal="logs")
        _load_otel(registry, "metrics_session.jsonl")
        trueup.tick()
        _load_jsonl(_otel_request_ids())
        trueup.tick()
    else:
        _load_otel(registry, "metrics_session.jsonl")
        trueup.tick()
        _load_otel(registry, "logs_session.jsonl", signal="logs")
        trueup.tick()
        _load_jsonl(_otel_request_ids())
        trueup.tick()

    block = _block()
    assert block["sources"] == {SRC_BOTH: 8, SRC_OTEL_ONLY: 0, SRC_JSONL_ONLY: 0}
    measures = {k: Decimal(v) for k, v in block["measures"].items()}
    assert {k: v for k, v in measures.items() if k not in BACKGROUND_MEASURES} == \
        {k: v[0] for k, v in FOLD_GOLDEN.items()}
    assert block["cost_complete"] is True


def test_otel_only_migrates_to_matched_when_the_jsonl_arrives(db, registry, trueup):
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    trueup.tick()
    assert _block()["sources"][SRC_OTEL_ONLY] == 8

    _load_jsonl(_otel_request_ids())
    trueup.tick()
    block = _block()
    assert block["sources"][SRC_OTEL_ONLY] == 0
    assert block["sources"][SRC_BOTH] == 8


# ---------------------------------------------------------------- idempotency

def test_running_the_tick_twice_changes_nothing(db, registry, trueup):
    _load_otel(registry, "metrics_session.jsonl")
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(_otel_request_ids())
    trueup.tick()
    first = _block()

    for _ in range(3):
        trueup.tick()
    second = _block()
    # computed_at is the one field that legitimately moves; everything else must not.
    assert {k: v for k, v in second.items() if k != "computed_at"} == \
           {k: v for k, v in first.items() if k != "computed_at"}


def test_a_tick_with_nothing_new_does_no_work(db, registry, trueup):
    _load_otel(registry, "metrics_session.jsonl")
    stats = trueup.tick()
    assert stats.sessions == 1
    assert trueup.tick().sessions == 0          # watermark advanced past everything


def test_the_watermark_advances_only_when_work_was_found(db, registry, trueup):
    assert trueup.tick().sessions == 0
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(OtelWatermark)) == 0

    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()
    with session_scope() as s:
        marks = s.scalars(select(OtelWatermark)).all()
        assert [m.vendor for m in marks] == ["claude_code"]


# -------------------------------------------------------------------- prune

def test_prune_never_deletes_above_the_watermark(db, registry, trueup):
    """A point the fold has not consumed is the only evidence its session's totals exist."""
    old = datetime.now(timezone.utc) - timedelta(days=90)
    _load_otel(registry, "metrics_session.jsonl", now=old)
    with session_scope() as s:
        before = s.scalar(select(func.count()).select_from(OtelMetricPoint))

    # Nothing folded yet -> no watermark -> nothing may be pruned, however old it is.
    assert trueup.prune(retention_days=30) == 0
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(OtelMetricPoint)) == before

    trueup.tick()
    assert trueup.prune(retention_days=30) == before
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(OtelMetricPoint)) == 0


def test_prune_respects_the_retention_window(db, registry, trueup):
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()
    with session_scope() as s:
        before = s.scalar(select(func.count()).select_from(OtelMetricPoint))
    assert trueup.prune(retention_days=30) == 0      # folded, but not old enough
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(OtelMetricPoint)) == before


def test_the_cost_block_survives_pruning_its_source_points(db, registry, trueup):
    """§4.3: after true-up the points serve only debugging and recompute. The cost block is
    the durable answer."""
    old = datetime.now(timezone.utc) - timedelta(days=90)
    _load_otel(registry, "metrics_session.jsonl", now=old)
    trueup.tick()
    folded = _block()["measures"]
    trueup.prune(retention_days=30)
    assert _block()["measures"] == folded


# --------------------------------------------------------------- otel-only tier

def test_a_session_with_no_jsonl_row_still_gets_a_cost_block(db, registry, trueup):
    """The intended zero-install tier: spend-true, fidelity-blank (§7), not an error.

    PM.2 makes this session findable at all: a stub `sessions` row with the identity fields
    every dashboard query filters on, and PM.1's promoted `datasource`/`cost_effective_usd_micros`
    columns for the sessions list.
    """
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()
    with session_scope() as s:
        row = s.get(SessionRow, SESSION_ID)
        assert row is not None                              # PM.2: no longer invisible
        assert row.status == "otel_only"
        assert row.agent == "claude_code"
        assert row.producer_id == f"otel:{ORG}"
        assert row.org_id == ORG
        # Bounds fall back to otel_metric_points' time_unix_nano: this fixture has no
        # otel_api_requests rows at all (metrics only, no logs loaded).
        assert row.started_at <= row.last_event_at
        assert row.started_at.year > 2000                   # a real event time, not epoch/now
        info_row = s.get(SessionInfoRow, SESSION_ID)
        assert info_row is not None
        assert info_row.datasource == SRC_OTEL_ONLY
        cost_effective = info_row.cost_effective_usd_micros
    block = _block()
    assert block["measures"]["cost_usd"] == str(FOLD_GOLDEN["cost_usd"][0])
    assert cost_effective == block["cost_effective_usd_micros"]


def test_the_second_tick_touches_zero_sessions_after_a_stub_is_created(db, registry, trueup):
    """The PM.2 loop hazard: `touched_sessions`' third and fourth arms are watermark-independent
    (they always re-scan `session_info`/`sessions`), so a stub whose `last_event_at` is `now()`
    would re-fold forever. It must come from OTel event timestamps, which are in the past by
    construction."""
    _load_otel(registry, "metrics_session.jsonl")
    assert trueup.tick().sessions == 1
    with session_scope() as s:
        assert s.get(SessionRow, SESSION_ID) is not None
    assert trueup.tick().sessions == 0


def test_datasource_is_both_when_two_pipes_are_deployed_but_no_request_matched(db, registry, trueup):
    """Mode describes which pipes are deployed, not whether a particular join succeeded -- a
    session whose JSONL and OTel both exist but whose request ids don't overlap is still a
    two-source deployment (MODES §3.2's fourth row)."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(["a-request-id-otel-never-saw"])
    trueup.tick()

    block = _block()
    assert block["sources"][SRC_BOTH] == 0
    assert block["sources"][SRC_OTEL_ONLY] == 8
    assert block["sources"][SRC_JSONL_ONLY] == 1
    assert _datasource() == SRC_BOTH


def test_a_jsonl_only_deployment_is_labelled_jsonl_only(db, registry, trueup):
    """No OTel evidence at all -- an org that runs the collector and declines telemetry."""
    _load_jsonl(["req_a", "req_b"], session_id="jsonl-only-session")
    trueup.tick()
    assert _datasource("jsonl-only-session") == SRC_JSONL_ONLY


def test_a_synthetic_session_is_never_touched(db, registry, trueup):
    """A session generated by econ-forecast's org-expand and loaded directly by
    synthetic_load.py carries `info_blob.synthetic = true` but no real per-request event
    stream (`signal_blob.events` is always `[]` for these -- that layer was never simulated).
    Without this exclusion, `touched_sessions`' JSONL-only arm would pick every such session
    up on its very next tick, find zero events to price, and overwrite the loader's own
    correct `cost_effective_usd_micros` with a $0 estimate. The fix must leave the row
    completely untouched, not just leave its cost alone -- `otel`/`datasource` stay exactly
    as the loader left them too."""
    session_id = "synthetic-session"
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(SessionRow(session_id=session_id, producer_id="synthetic", agent="claude_code",
                         started_at=now, last_event_at=now, signal_blob={"events": []},
                         info_blob={"synthetic": True, "generator_version": "1.0.0"},
                         subagents=[]))
        s.add(SessionInfoRow(session_id=session_id, created_at=now, updated_at=now, models={},
                             tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                             turn_count=0, long_turn_count=0, info={}, otel=None,
                             datasource="jsonl_only", tokens_total=12345,
                             cost_effective_usd_micros=987654))

    assert trueup.tick().sessions == 0

    with session_scope() as s:
        info_row = s.get(SessionInfoRow, session_id)
        assert info_row.otel is None
        assert info_row.datasource == "jsonl_only"
        assert info_row.cost_effective_usd_micros == 987654


def test_a_matched_session_is_labelled_both(db, registry, trueup):
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(_otel_request_ids())
    trueup.tick()
    assert _datasource() == SRC_BOTH


def test_the_otel_only_tier_expands_to_both_when_jsonl_arrives_through_real_ingest(
        db, registry, catalog, trueup):
    """The promise the zero-install tier is sold on (MODES §8): nothing is thrown away by
    starting at the bottom of the ladder. Uses the actual `ensure_session` query the ingest
    fold handler runs, not the test suite's `_load_jsonl` shortcut, because that is the exact
    seam PM.2 found broken: `ON CONFLICT ... DO NOTHING` left a stub's `producer_id`/`status`
    stuck forever once real JSONL identity showed up for the same session_id."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()

    with session_scope() as s:
        stub = s.get(SessionRow, SESSION_ID)
        assert stub.status == "otel_only"
        assert stub.producer_id == f"otel:{ORG}"
    first_block = _block()
    assert _datasource() == SRC_OTEL_ONLY

    with session_scope() as s:
        s.execute(catalog.get("ingest", "ensure_session"), {
            "session_id": SESSION_ID, "producer_id": "real-collector-producer",
            "org_id": ORG, "user_id": "real-user-id", "agent": "claude_code",
            "agent_version": "2.1.219", "parent_session_id": None,
        })
    with session_scope() as s:
        real = s.get(SessionRow, SESSION_ID)
        assert real.status == "live"                        # no longer otel_only
        assert real.producer_id == "real-collector-producer"  # no longer otel:<org>
        assert real.org_id == ORG
        assert real.user_id == "real-user-id"
        assert real.agent_version == "2.1.219"

    _load_jsonl(_otel_request_ids())    # the session's real JSONL, now that identity is real
    trueup.tick()

    block = _block()
    assert _datasource() == SRC_BOTH
    assert block["sources"] == {SRC_BOTH: 8, SRC_OTEL_ONLY: 0, SRC_JSONL_ONLY: 0}
    # Nothing from the OTel side was discarded by the transition.
    assert block["cost_usd_micros"] == first_block["cost_usd_micros"]
    with session_scope() as s:
        assert s.get(SessionRow, SESSION_ID).status != "otel_only"


def test_a_jsonl_fold_after_a_true_up_tick_leaves_the_promoted_columns_alone(db, registry, trueup):
    """Ground rule 3, extended to the promoted columns: `persist_session_info` must never write
    `cost_effective_usd_micros`/`datasource`/`jsonl_lag_s` -- those belong to the true-up alone,
    on pain of the JSONL fold silently reverting them to defaults on every re-upload."""
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()
    written = _block()
    with session_scope() as s:
        info_row = s.get(SessionInfoRow, SESSION_ID)
        assert info_row.datasource == SRC_OTEL_ONLY
        assert info_row.cost_effective_usd_micros == written["cost_effective_usd_micros"]

    with session_scope() as s:
        persist_session_info(s, SESSION_ID, SessionInfoState(session_id=SESSION_ID, models={"m": {}}))

    with session_scope() as s:
        info_row = s.get(SessionInfoRow, SESSION_ID)
        assert info_row.models == {"m": {}}                          # the JSONL fold's write landed
        assert info_row.datasource == SRC_OTEL_ONLY                  # true-up's columns untouched
        assert info_row.cost_effective_usd_micros == written["cost_effective_usd_micros"]
        assert info_row.otel["vendor"] == "claude_code"               # and the block itself


def test_the_worker_writes_its_own_column_and_leaves_the_jsonl_folds_alone(db, registry, trueup):
    """Two writers, two columns. `session_info.info` belongs to the JSONL fold, which replaces
    it wholesale on every arrival -- a cost block living inside it would be deleted by the next
    upload of the session it describes."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(SessionInfoRow(session_id=SESSION_ID, created_at=now, updated_at=now,
                             models={}, tools={}, files={}, skills=[], subagents={},
                             pr_list=[], turns=[], info={"jsonl_fold": {"kept": True}},
                             otel={}))
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()
    with session_scope() as s:
        row = s.get(SessionInfoRow, SESSION_ID)
        assert row.info == {"jsonl_fold": {"kept": True}}   # the JSONL fold's map, untouched
        assert row.otel["vendor"] == "claude_code"


# ------------------------------- pricing, cost basis and calibration (P0 wiring)

def _pricing_helpers():
    """`tests/common/model_pricing/helpers.py` by path: `tests/common` is shadowed by
    `src/common` on the import path, so it cannot be imported by package name."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "common" / "model_pricing" / "helpers.py"
    spec = importlib.util.spec_from_file_location("_pricing_test_helpers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.make_pricer, mod.row


def test_matched_requests_keep_the_vendor_cost_and_carry_an_estimate_alongside(db, registry, trueup):
    """The estimate is computed even where the vendor already told us -- that redundancy is
    what the calibration check is made of, and it costs a dict lookup."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(_otel_request_ids())
    trueup.tick()

    for record in _block()["requests"]:
        assert record["cost_basis"] == BASIS_VENDOR       # vendor wins wherever it exists
        assert record["cost_usd_micros"] is not None
        assert record["cost_est_usd_micros"] is not None  # and the estimate rides alongside
        # BLOCK_VERSION 5 regression: `cost_effective_usd_micros` must be the vendor figure
        # alone here, never vendor + the redundant estimate -- that sum is exactly the
        # double-count bug that inflated the overview widget and insight 26.
        assert record["cost_effective_usd_micros"] == record["cost_usd_micros"]


def test_calibration_reproduces_the_vendor_cost(db, registry, trueup):
    """The price table is derived from vendor-reported costs, so on these very requests it
    must reproduce them. This is the test that would catch a stale rate."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    trueup.tick()

    calibration = _block()["calibration"]["claude-opus-5"]
    assert calibration["n"] == 8
    # Sub-micro on every request: the table is not an approximation of the price list, it is
    # the price list, re-derived.
    assert calibration["max_abs_micros"] <= 1
    assert Decimal(calibration["max_rel"]) < Decimal("0.0001")


def test_drift_is_reported_when_the_table_is_wrong(db, registry, catalog, tmp_path):
    """A deliberately wrong price must show up as drift rather than quietly producing wrong
    estimates for every unmatched request."""
    from datetime import date as _date

    from common.model_pricing import Pricer
    make_pricer, row = _pricing_helpers()

    wrong = make_pricer(tmp_path, [row("claude-opus-5", _date(2026, 1, 1), per_million={
        "input": 50, "output": 250, "cache_read": 5, "cache_write_5m": 62.5,
        "cache_write_1h": 100})])                                          # 10x too high
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    OtelTrueUp(catalog, registry, watermark_lag_s=0.0, pricing=wrong).tick()

    calibration = _block()["calibration"]["claude-opus-5"]
    assert Decimal(calibration["max_rel"]) > Decimal("0.005")   # over DRIFT_ALARM


def test_requests_predating_telemetry_are_labelled_pre_otel(db, registry, trueup):
    """The gap this whole change exists for: enabling telemetry mid-history must not look
    like hundreds of interrupted requests."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    old = datetime(2026, 7, 1, tzinfo=timezone.utc)     # long before the fixture's OTel data
    _load_jsonl(["req_from_before_telemetry"], event_ts=old)
    trueup.tick()

    block = _block()
    assert block["otel_gap"][GAP_PRE_OTEL] == 1
    assert GAP_UNOBSERVED not in block["otel_gap"]
    assert block["otel_since"] is not None               # when coverage actually began


def test_requests_after_coverage_began_are_unobserved_not_pre_otel(db, registry, trueup):
    """OTel was watching and still did not see it -- for an interactive request that means
    the user interrupted it. A different fact from 'telemetry was off'."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(["req_interrupted_by_user"])            # _load_jsonl stamps events with now()
    trueup.tick()

    block = _block()
    assert block["otel_gap"][GAP_UNOBSERVED] == 1
    assert GAP_PRE_OTEL not in block["otel_gap"]


def test_a_jsonl_only_session_gets_a_priced_cost_block(db, registry, trueup):
    """The deployment tier that has no OTel at all. Before the estimator this session was
    never folded and had no cost; it is a choice an org makes, not a degraded mode."""
    _load_jsonl(["req_a", "req_b"], session_id="jsonl-only-session")
    trueup.tick()

    block = _block("jsonl-only-session")
    assert block["sources"] == {SRC_BOTH: 0, SRC_OTEL_ONLY: 0, SRC_JSONL_ONLY: 2}
    assert block["cost_basis"][BASIS_ESTIMATED] == 2
    assert block["cost_usd_micros"] == 0                 # no vendor figure exists
    assert block["cost_effective_usd_micros"] > 0        # but there is still a number to show
    assert block["cost_complete"] is True
    assert block["measures"] == {}                       # no metrics: JSONL carries none


def test_a_session_on_an_older_block_shape_is_refolded(db, registry, trueup):
    """Every other arm decides what to revisit by reading keys out of the block, so a block
    predating those keys matches none of them. Without this arm an idle session keeps an
    obsolete block forever -- which is exactly what happened to 20 real sessions."""
    from server.worker.otel_trueup import BLOCK_VERSION

    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()
    assert _block()["block_version"] == BLOCK_VERSION

    # Rewrite it as a v1-shaped block: no `sources`, no version, and idle (no new events).
    with session_scope() as s:
        row = s.get(SessionInfoRow, SESSION_ID)
        row.otel = {"vendor": "claude_code", "computed_at": row.otel["computed_at"],
                    "provenance": {"otel": 0, "otel_only": 0, "jsonl_est": 0},
                    "measures": {}, "requests": []}

    trueup.tick()
    refolded = _block()
    assert refolded["block_version"] == BLOCK_VERSION
    assert "sources" in refolded
    assert refolded["measures"]                       # and the real numbers came back
    assert trueup.tick().sessions == 0                # self-clearing: once only


def test_editing_the_price_table_refolds_everything_priced_by_the_old_one(db, registry, catalog, tmp_path):
    """The prices are an input to the fold. Adding a model has to re-price history, or its
    requests keep `cost_basis: unknown` forever -- which is exactly the state adding it was
    meant to fix, and it fails silently."""
    from datetime import date as _date

    make_pricer, row = _pricing_helpers()

    # a thinner catalog: one unrelated model, so claude-opus-5 requests are unpriced
    empty = make_pricer(tmp_path, [row("some-other-model", _date(2026, 1, 1),
                                       per_million={"input": 1})])
    thin = OtelTrueUp(catalog, registry, watermark_lag_s=0.0, pricing=empty)
    _load_jsonl(["req_a"], session_id="priced-later")
    thin.tick()
    assert _block("priced-later")["cost_basis"][BASIS_UNKNOWN] == 1

    # Now the model gets a price. Nothing else about the session has changed: no new OTel, no
    # new JSONL, same block version.
    full = OtelTrueUp(catalog, registry, watermark_lag_s=0.0)
    assert full.pricing_fingerprint != thin.pricing_fingerprint
    assert full.tick().sessions >= 1

    block = _block("priced-later")
    assert block["cost_basis"][BASIS_ESTIMATED] == 1
    assert block["cost_basis"][BASIS_UNKNOWN] == 0
    assert block["pricing_fingerprint"] == full.pricing_fingerprint
    assert full.tick().sessions == 0            # self-clearing: once only


# --------------------------------------------------------- by_query_source (PM.3)

def test_by_query_source_splits_the_ledger_when_matched(db, registry, trueup):
    """`both` mode: the ledger split is exact (§4)."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(_otel_request_ids())
    trueup.tick()

    by_source = _block()["by_query_source"]
    assert by_source["sdk"]["requests"] == 7
    assert by_source["prompt_suggestion"]["requests"] == 1
    assert set(by_source) == {"sdk", "prompt_suggestion"}
    # BLOCK_VERSION 5 regression: every request in these two buckets is vendor-priced (SRC_BOTH,
    # matched), so `cost_effective_usd_micros` must equal `cost_usd_micros` exactly -- summing
    # in `cost_est_usd_micros` too would double the real spend.
    for bucket in by_source.values():
        assert bucket["cost_effective_usd_micros"] == bucket["cost_usd_micros"]
        assert bucket["cost_effective_usd_micros"] < bucket["cost_usd_micros"] + bucket["cost_est_usd_micros"]


def test_by_query_source_is_present_in_otel_only_mode(db, registry, trueup):
    """The ledger split doesn't need JSONL at all -- it's read straight off `otel_api_requests`,
    which is why `otel_only` still gets it (unlike the JSONL-derived widgets in MODES §2)."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    trueup.tick()

    by_source = _block()["by_query_source"]
    assert by_source["sdk"]["requests"] == 7
    assert by_source["prompt_suggestion"]["requests"] == 1


def test_by_query_source_is_all_interactive_in_jsonl_only_mode(db, registry, trueup):
    """BLOCK_VERSION 4: every JSONL-only record's `query_source` is `None` (`otel_trueup.py`'s
    `_join_requests`) -- not ambiguous with true interactive, a fact: a background call cannot
    reach a JSONL transcript at all (it's a side-channel API call, never a conversation turn).
    So the rollup is present, and it's `{"interactive": ...}` covering every record -- v3 kept
    this key absent out of caution that no longer applies."""
    _load_jsonl(["req_a", "req_b"], session_id="jsonl-only-session")
    trueup.tick()
    by_source = _block("jsonl-only-session")["by_query_source"]
    assert set(by_source) == {"interactive"}
    assert by_source["interactive"]["requests"] == 2


def test_by_query_source_absent_when_there_are_no_records_at_all(db, registry, trueup):
    """The "absent, not empty" rule still holds -- just keyed on whether there's anything to
    summarize (`bool(records)`), not on OTel evidence specifically. A metrics-only fleet with
    no request ledger is the remaining real case (below)."""
    _load_jsonl([], session_id=SESSION_ID)              # creates the sessions row, no requests
    trueup.tick()
    assert "by_query_source" not in _block(SESSION_ID)


# -------------------------------------------------------------- active_time_s (PM.4)

def test_active_time_s_is_promoted_to_the_blocks_top_level(db, registry, trueup):
    """Already folded into `measures`; PM.4 is only that it's surfaced where a reader looks --
    the only exact active-time figure available, and the only one that exists at all in
    `otel_only` (DASH §3.1's Σ-turn-durations fallback needs JSONL)."""
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()
    assert Decimal(_block()["active_time_s"]) == FOLD_GOLDEN["active_time_s"][0]


# ------------------------------------------------------------ jsonl_lag_s (PM.5)

def test_jsonl_lag_is_reported_then_clears_once_jsonl_catches_up(db, registry, trueup):
    """MODES §7: OTel ahead of a session's last delivered JSONL event is a state to report, not
    a bug to hide. Manufactured by pinning `sessions.last_event_at` to a date well before the
    fixture's real OTel event timestamps -- the batch-upload-lag scenario the field exists for."""
    _load_jsonl([], session_id=SESSION_ID)             # creates the sessions row, no requests
    with session_scope() as s:
        s.get(SessionRow, SESSION_ID).last_event_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

    _load_otel(registry, "logs_session.jsonl", signal="logs")
    trueup.tick()

    with session_scope() as s:
        lag = s.get(SessionInfoRow, SESSION_ID).jsonl_lag_s
    assert lag is not None and lag > 0

    _load_jsonl(_otel_request_ids())                   # JSONL catches up: all 8 now matched
    trueup.tick()

    with session_scope() as s:
        assert s.get(SessionInfoRow, SESSION_ID).jsonl_lag_s is None


def test_jsonl_lag_is_none_without_otel_only_requests(db, registry, trueup):
    """No `sources.otel_only` means nothing is lagging -- the guard PM.5 states explicitly."""
    _load_otel(registry, "logs_session.jsonl", signal="logs")
    _load_jsonl(_otel_request_ids())
    trueup.tick()
    with session_scope() as s:
        assert s.get(SessionInfoRow, SESSION_ID).jsonl_lag_s is None


# ------------------------------------------------- background split, metrics-only (PM.3)

def test_background_split_measures_are_the_only_path_in_a_metrics_only_fleet(db, registry, trueup):
    """`OTEL_LOGS_EXPORTER=none`: no logs, no request ledger, so `by_query_source` cannot exist
    for this session -- the `*_bg` measures (config/otel/claude_code.yaml) are the only way to
    see background spend at all. Values from the fixture README's `auxiliary` column."""
    _load_otel(registry, "metrics_session.jsonl")
    trueup.tick()

    measures = _block()["measures"]
    assert Decimal(measures["tokens_input_bg"]) == Decimal("504")
    assert Decimal(measures["tokens_output_bg"]) == Decimal("13")
    assert Decimal(measures["tokens_cache_read_bg"]) == Decimal("78841")
    assert Decimal(measures["tokens_cache_creation_bg"]) == Decimal("2254")
    # Sanity: the background slice is strictly smaller than the combined (main + auxiliary)
    # total already asserted by FOLD_GOLDEN elsewhere.
    assert Decimal(measures["tokens_input_bg"]) < FOLD_GOLDEN["tokens_input"][0]
