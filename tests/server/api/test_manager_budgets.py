"""F1: budgets service (`server/api/manager/budgets.py`) and its config-import CLI. Token
allowances were removed (docs/plan/2026-09-26-cost-based-seat-utilization.md)."""
from datetime import date
from decimal import Decimal

import pytest

from server.api.errors import BadRequestError, NotFoundError
from server.api.manager import budget_cycles as bc
from server.api.manager import budgets as svc


# ----------------------------------------------------------------- period_bounds (E1-budget-period)

def test_period_bounds_monthly_returns_the_containing_calendar_month():
    start, end = svc.period_bounds("monthly", date(2026, 1, 15))
    assert (start, end) == (date(2026, 1, 1), date(2026, 2, 1))
    assert (end - start).days == 31  # the period's own length, not the caller's window


def test_period_bounds_monthly_rolls_the_year_over_at_december():
    start, end = svc.period_bounds("monthly", date(2026, 12, 20))
    assert (start, end) == (date(2026, 12, 1), date(2027, 1, 1))


def test_period_bounds_resolves_the_other_calendar_labels():
    assert svc.period_bounds("quarterly", date(2026, 11, 15)) == (date(2026, 10, 1), date(2027, 1, 1))
    assert svc.period_bounds("half-yearly", date(2026, 3, 1)) == (date(2026, 1, 1), date(2026, 7, 1))
    assert svc.period_bounds("yearly", date(2028, 2, 29)) == (date(2028, 1, 1), date(2029, 1, 1))


def test_period_bounds_takes_a_cycle_or_an_active_cycle_for_any_kind():
    sprint = bc.validate("sprint", anchor_date="2026-09-14", sprint_weeks=2, anchor_number=231)
    assert svc.period_bounds(sprint, date(2026, 10, 12)) == (date(2026, 10, 12), date(2026, 10, 26))
    assert svc.period_bounds(bc.ActiveCycle("sprint", sprint), date(2026, 10, 25)) == (
        date(2026, 10, 12), date(2026, 10, 26))
    assert svc.period_bounds(bc.ActiveCycle("monthly", bc.DEFAULT_CYCLE), date(2026, 1, 15)) == (
        svc.period_bounds("monthly", date(2026, 1, 15)))
    custom = bc.validate("custom", "one-time", anchor_date="2026-10-01", custom_end="2026-10-10")
    assert svc.period_bounds(custom, date(2026, 10, 5)) == (date(2026, 10, 1), date(2026, 10, 11))


def test_period_bounds_rejects_a_bare_sprint_or_custom_label():
    for label in ("sprint", "custom"):
        with pytest.raises(BadRequestError):
            svc.period_bounds(label, date(2026, 1, 15))


def test_period_bounds_rejects_an_unsupported_period_rather_than_guessing():
    with pytest.raises(BadRequestError):
        svc.period_bounds("weekly", date(2026, 1, 15))
from server.directory import DirectoryRecord, ExternalKey, TeamRecord, sync
from server.storage import session_scope
from server.storage.schema import SessionInfo
from server.storage.schema import Session as SessionRow

from .conftest import requires_db


def _seed_team(s, catalog, team_id="platform"):
    sync(s, catalog, [DirectoryRecord(external_keys=(ExternalKey("csv", "u1"),))],
         teams=[TeamRecord(team_id, "default", team_id.title())])


def _seed_repo_session(s, repo_id="github:acme/api"):
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    s.add(SessionRow(session_id="sx", producer_id="p", org_id="org", user_id="u1",
                     agent="claude_code", status="ended", started_at=now, last_event_at=now,
                     signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id="sx", created_at=now, updated_at=now, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info={}, otel={}, datasource="both", repo_id=repo_id))
    s.flush()


@requires_db
def test_get_budget_is_none_when_unset_not_zero(engine, catalog):
    with session_scope() as s:
        result = svc.get_budget(s, catalog, "org", "default", "monthly")
    assert result == {"scope_type": "org", "scope_id": "default", "period": "monthly",
                      "amount_usd": None}


@requires_db
def test_budget_round_trips(engine, catalog):
    with session_scope() as s:
        written = svc.put_budget(s, catalog, "org", "default", "monthly", Decimal("15000"))
    assert written["amount_usd"] == "15000.00"
    with session_scope() as s:
        read_back = svc.get_budget(s, catalog, "org", "default", "monthly")
    assert read_back["amount_usd"] == "15000.00"


@requires_db
def test_budget_on_unknown_team_scope_is_rejected(engine, catalog):
    with session_scope() as s, pytest.raises(NotFoundError):
        svc.put_budget(s, catalog, "team", "does-not-exist", "monthly", Decimal("1000"))


@requires_db
def test_budget_on_unknown_repo_scope_is_rejected(engine, catalog):
    with session_scope() as s, pytest.raises(NotFoundError):
        svc.put_budget(s, catalog, "repo", "does-not-exist", "monthly", Decimal("1000"))


@requires_db
def test_budget_on_a_real_team_scope_succeeds(engine, catalog):
    with session_scope() as s:
        _seed_team(s, catalog, "platform")
        svc.put_budget(s, catalog, "team", "platform", "monthly", Decimal("4000"))
    with session_scope() as s:
        assert svc.get_budget(s, catalog, "team", "platform", "monthly")["amount_usd"] == "4000.00"


@requires_db
def test_budget_on_a_real_repo_scope_succeeds(engine, catalog):
    with session_scope() as s:
        _seed_repo_session(s)
        svc.put_budget(s, catalog, "repo", "github:acme/api", "monthly", Decimal("500"))
    with session_scope() as s:
        result = svc.get_budget(s, catalog, "repo", "github:acme/api", "monthly")
    assert result["amount_usd"] == "500.00"


@requires_db
def test_unknown_scope_type_is_a_bad_request(engine, catalog):
    with session_scope() as s, pytest.raises(BadRequestError):
        svc.get_budget(s, catalog, "planet", "earth", "monthly")


def test_cli_refuses_a_file_that_still_has_an_allowances_section(tmp_path, capsys):
    """A stale config must fail loudly (exit 2), not be half-applied -- its token allowances
    no longer mean anything and silently dropping them would hide that. Refused before any
    database work, so this needs no DB."""
    from server.api.manager import cli_budgets

    source = tmp_path / "budgets.yaml"
    source.write_text(
        "budgets:\n  - {scope_type: team, scope_id: platform, amount_usd: 4000}\n"
        "allowances:\n  - {scope_type: team, scope_id: platform, limit_tokens: 2000000}\n")
    assert cli_budgets.main(["--source", str(source)]) == 2
    assert "allowances" in capsys.readouterr().err


@requires_db
def test_budgets_for_scopes_omits_scopes_with_no_row(engine, catalog):
    with session_scope() as s:
        _seed_team(s, catalog, "platform")
        _seed_team(s, catalog, "growth")
        svc.put_budget(s, catalog, "team", "platform", "monthly", Decimal("4000"))
    with session_scope() as s:
        result = svc.budgets_for_scopes(s, catalog, "team", ["platform", "growth"], "monthly")
    assert result == {"platform": Decimal("4000.00")}


@requires_db
@pytest.mark.parametrize("saved_kind, label", [(None, "monthly"), ("quarter", "quarterly")])
def test_cli_import_writes_under_the_active_cycle_label(engine, catalog, tmp_path, saved_kind, label):
    """The config import has no period of its own: with no saved cycle rows go under `monthly`,
    otherwise under the saved cycle's label (A1)."""
    from server.api.manager import budget_cycles as bc
    from server.api.manager import cli_budgets
    from server.storage.engine import dispose_engine
    from tests.server.api.conftest import DSN

    with session_scope() as s:
        _seed_team(s, catalog, "platform")
        if saved_kind:
            bc.put_cycle(s, catalog, "default", bc.validate(saved_kind))
    source = tmp_path / "budgets.yaml"
    source.write_text("budgets:\n  - {scope_type: team, scope_id: platform, amount_usd: 4000}\n")
    config = tmp_path / "storage.yaml"
    config.write_text(f"storage:\n  dsn: {DSN}\n", encoding="utf-8")
    dispose_engine()
    try:
        assert cli_budgets.main(["--source", str(source), "--storage-config", str(config),
                                 "--apply"]) == 0
    finally:
        dispose_engine()

    from server.storage.engine import get_engine
    from server.storage.config import StorageConfig
    get_engine(StorageConfig(dsn=DSN))
    with session_scope() as s:
        assert svc.budgets_for_scopes(s, catalog, "team", ["platform"], label) == {
            "platform": Decimal("4000.00")}
        other = "quarterly" if label == "monthly" else "monthly"
        assert svc.budgets_for_scopes(s, catalog, "team", ["platform"], other) == {}
