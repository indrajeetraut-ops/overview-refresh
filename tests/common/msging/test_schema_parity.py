"""JSON is the readable source of truth (D50); pydantic mirrors it. This test is
what makes drift impossible — edit the JSON, this fails and names the field."""
import json
import pathlib

import pytest

from common.msging.models import Envelope, MsgRecord, NotifyEv

SCHEMA = pathlib.Path(__file__).resolve().parents[3] / "src" / "common" / "msging" / "schema"


@pytest.mark.parametrize(
    "filename,model",
    [("envelope.json", Envelope), ("notify_ev.json", NotifyEv), ("msg_record.json", MsgRecord)],
)
def test_model_fields_match_schema_json(filename, model):
    keys = set(json.loads((SCHEMA / filename).read_text(encoding="utf-8")))
    assert set(model.model_fields) == keys
