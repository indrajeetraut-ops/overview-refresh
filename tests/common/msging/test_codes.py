import pytest

from common.msging.codes import IS_REJECTION, IS_STORED, Action, Code


def test_stored_codes_mean_durably_held_not_shipped():
    assert IS_STORED == {Code.STORED_TIMEOUT, Code.STORED_UNREACHABLE,
                         Code.STORED_THROTTLED, Code.SERVER_BUSY}


def test_only_two_codes_reject_outright():
    """slot_full and too_large are the only backpressure a caller must handle."""
    assert IS_REJECTION == {Code.SLOT_FULL, Code.TOO_LARGE}


def test_codes_are_plain_strings_on_the_wire():
    assert Code.OK == "ok"
    assert Code.SERVER_BUSY.value == "server_busy"


def test_action_default_is_retain():
    assert Action.RETAIN == "retain"


@pytest.mark.parametrize("code", list(Code))
def test_every_code_is_lowercase_snake(code):
    assert code.value == code.value.lower()
    assert " " not in code.value
