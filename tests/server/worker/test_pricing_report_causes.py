"""Cause column rules of the pricing report (#68): a cause is assigned only when its mechanism
accounts for the move; everything else is `unexplained`. No database needed."""
import importlib.util
from datetime import date
from pathlib import Path

import pytest

from common.model_pricing import Pricer
from server.worker import cli_pricing_report as report
from server.worker.cli_pricing_report import Item, cause_of


@pytest.fixture(scope="module")
def pricer():
    return Pricer.load(None, None)


def _item(model, at, before, after, source="ledger", usage_model=None):
    return Item(source, model, usage_model or model, at, before, after)


def test_no_move_has_no_cause(pricer):
    assert cause_of(pricer, _item("claude-opus-5", date(2026, 10, 1), 100, 100)) is None


def test_2_requires_sonnet_5_on_or_after_the_change_and_the_matching_ratio(pricer):
    sonnet = "claude-sonnet-5"
    assert cause_of(pricer, _item(sonnet, date(2026, 9, 10), 1_050_000, 700_000)) == "#2"
    # right model and date, wrong ratio: not the price change
    assert cause_of(pricer, _item(sonnet, date(2026, 9, 10), 1_000_000, 900_000)) == "unexplained"
    # right ratio, before the change date
    assert cause_of(pricer, _item(sonnet, date(2026, 8, 15), 1_050_000, 700_000)) == "unexplained"
    # right ratio and date, other model
    assert cause_of(pricer, _item("claude-opus-5", date(2026, 9, 10), 1_050_000, 700_000)) == "unexplained"


def test_cache_bust_ratio_1_07_is_unexplained_never_a_price_cause(pricer):
    """The mislabel case: synthetic busts are priced per cache lifetime, the report reprices
    them combined at 1h -- a modelling difference (#67), not decision #7 or #2."""
    for model, day in (("claude-sonnet-5", date(2026, 1, 10)), ("claude-sonnet-5", date(2026, 9, 10)),
                       ("claude-opus-5", date(2026, 8, 5))):
        item = _item(model, day, 1_000_000, 1_070_000, source="fold:cache_busts")
        assert cause_of(pricer, item) == "unexplained", (model, day)


def test_7_only_when_unpriced_before_on_an_early_date_with_a_registry_id(pricer):
    early = date(2026, 1, 10)                       # before the catalog's first row (2026-09-25)
    assert cause_of(pricer, _item("claude-opus-5", early, None, 500)) == "#7"
    # same unpriced-before, but the date is covered by the catalog: nothing explains it
    assert cause_of(pricer, _item("claude-opus-5", date(2026, 10, 1), None, 500)) == "unexplained"
    # priced on both sides on an early date with a ratio of 1.0: no move, no cause
    assert cause_of(pricer, _item("claude-opus-5", early, 500, 500)) is None


def test_8_only_for_newly_matched_or_ambiguous_model_strings(pricer):
    dated = "claude-sonnet-4-5-20250929"
    assert cause_of(pricer, _item(dated, date(2026, 10, 1), None, 500)) == "#8"
    # the exact registry id was unpriced before: that is not a normalisation effect
    assert cause_of(pricer, _item("claude-sonnet-4-5", date(2026, 10, 1), None, 500)) == "unexplained"
    # priced before, unpriced now, model still resolves: unexplained
    assert cause_of(pricer, _item("claude-opus-5", date(2026, 10, 1), 500, None)) == "unexplained"


def test_8_newly_ambiguous(tmp_path):
    path = Path(__file__).resolve().parents[2] / "common" / "model_pricing" / "helpers.py"
    spec = importlib.util.spec_from_file_location("_pricing_test_helpers", path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    ambiguous = helpers.make_pricer(tmp_path, [
        helpers.row("claude-x-20250101", date(2026, 1, 1), per_million={"input": 1}),
        helpers.row("claude-x-20250202", date(2026, 1, 1), per_million={"input": 2})])
    assert cause_of(ambiguous, _item("claude-x", date(2026, 3, 1), 500, None)) == "#8"


def test_footer_documents_the_cache_bust_modelling_difference(pricer):
    out = report.render(report.summarize(pricer, []), pricer.fingerprint)
    assert "combined write at the 1h rate" in out and "modelling difference" in out
    assert "combined `cache_write`" in report.__doc__ and "unexplained" in report.__doc__
