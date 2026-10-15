from common.msging.models import Envelope, MsgRecord


def _env(**kw):
    base = dict(producer_id="prodA", msg_id=1, service_name="ingest",
                created_at="2026-07-25T10:15:30Z")
    return Envelope(**{**base, **kw})


def test_unknown_keys_are_kept_not_rejected():
    """Must-ignore evolution: a newer producer's extra field must not break us."""
    env = Envelope.model_validate({
        "producer_id": "prodA", "msg_id": 1, "service_name": "ingest",
        "created_at": "2026-07-25T10:15:30Z", "future_field": "surprise",
    })
    assert env.model_dump()["future_field"] == "surprise"


def test_schema_version_defaults_to_1_0():
    assert _env().schema_version == "1.0"


def test_payload_and_info_default_empty():
    env = _env()
    assert env.payload == {} and env.info == {}


def test_msg_record_round_trips_the_whole_envelope():
    rec = MsgRecord(msg_id=1, envelope=_env(payload={"a": 1}, event_id="e1"),
                    created_at="2026-07-25T10:15:30Z")
    back = MsgRecord.model_validate_json(rec.model_dump_json())
    assert back.envelope.payload == {"a": 1}
    assert back.envelope.event_id == "e1"
