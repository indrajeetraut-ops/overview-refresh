from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from server.api.manager import budgets as budgets_service
from server.api.single_user import service
from server.api.config import ApiConfig
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scalars, session_scope
from server.storage.schema import GitArtifact
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow

from .conftest import requires_db

pytestmark = requires_db

USER = "u1"
NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed(s, session_id, datasource, *, started_at=None, cost_effective_usd_micros=None,
          otel=None, models=None, tools=None, files=None, skills=None, subagents=None,
          turns=None, turn_count=0, info=None, status="live", parent_session_id=None,
          repo_id=None, repo_id_provenance=None):
    started_at = started_at or NOW
    info, files, otel = info or {}, files or {}, otel or {}
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="org-acme", user_id=USER,
                     agent="claude_code", status=status, started_at=started_at,
                     last_event_at=started_at, signal_blob={}, info_blob={}, subagents=[],
                     parent_session_id=parent_session_id))
    s.add(SessionInfoRow(
        session_id=session_id, created_at=NOW, updated_at=NOW,
        models=models or {}, tools=tools or {}, files=files, skills=skills or [],
        subagents=subagents or {}, pr_list=[], turns=turns or [], turn_count=turn_count,
        long_turn_count=1 if turn_count else 0, info=info, otel=otel,
        cost_effective_usd_micros=cost_effective_usd_micros, datasource=datasource,
        repo_id=repo_id, repo_id_provenance=repo_id_provenance,
        otel_vendor_cost_usd_micros=session_scalars.otel_vendor_cost(otel),
        **session_scalars.fold_scalars(info, files),
    ))


def _both_session_otel():
    return {
        "measures": {"tokens_input": "100", "active_time_s": "120.0"},
        "tokens": {"tokens_input": "100", "tokens_output": "50",
                  "tokens_cache_read": "0", "tokens_cache_creation": "0"},
        "active_time_s": "120.0",
        "requests": [{"model": "claude-opus-5", "input_tokens": 100, "output_tokens": 50,
                     "cache_read_tokens": 0, "cache_creation_tokens": 0,
                     "cost_usd_micros": 5_000_000, "cost_est_usd_micros": None}],
        "cost_usd_micros": 5_000_000,
        "cost_basis": {"vendor": 1, "estimated": 0, "unknown": 0},
        "by_query_source": {"sdk": {"requests": 1, "cost_usd_micros": 5_000_000,
                                    "cost_est_usd_micros": 0, "cost_effective_usd_micros": 5_000_000}},
    }


def _otel_only_otel():
    return {
        "measures": {"tokens_input": "300", "active_time_s": "60.0"},
        "tokens": {"tokens_input": "300", "tokens_output": "0",
                  "tokens_cache_read": "0", "tokens_cache_creation": "0"},
        "active_time_s": "60.0",
        "requests": [{"model": "claude-haiku", "input_tokens": 300, "output_tokens": 0,
                     "cache_read_tokens": 0, "cache_creation_tokens": 0,
                     "cost_usd_micros": None, "cost_est_usd_micros": 2_000_000}],
        "cost_basis": {"vendor": 0, "estimated": 1, "unknown": 0},
        "by_query_source": {"prompt_suggestion": {"requests": 1, "cost_usd_micros": 0,
                                                  "cost_est_usd_micros": 2_000_000,
                                                  "cost_effective_usd_micros": 2_000_000}},
    }


def test_overview_mixed_mode_range(engine, catalog):
    with session_scope() as s:
        _seed(s, "both-1", "both", cost_effective_usd_micros=5_000_000, otel=_both_session_otel(),
             tools={"Bash": {"count": 5, "failed": 1}},
             files={"a.py": {"created": True}, "b.py": {"created": False}},
             skills=[{"skill_name": "foo", "count": 2, "tokens": 100}],
             subagents={"c1": {}}, turns=[{"duration_ms": 600_000}], turn_count=10,
             repo_id="local:myrepo", repo_id_provenance="path",
             info={"repo": {"repo_key": "myrepo"},
                  "mcp_servers": {"by_server": {"srv1": {"calls": 3, "bytes_read_total": 900}}}})
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=1_000_000,
             models={"claude-sonnet": {"input": 200, "output": 100, "cache_read": 10, "cache_write": 5}},
             tools={"Read": {"count": 3, "failed": 0}}, files={"c.py": {"created": True}},
             turn_count=5, repo_id="local:otherrepo", repo_id_provenance="path",
             info={"repo": {"repo_key": "otherrepo"}})
        _seed(s, "otel-1", "otel_only", cost_effective_usd_micros=2_000_000, otel=_otel_only_otel(),
             status="otel_only")

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["modes_present"] == ["both", "jsonl_only", "otel_only"]
    assert result["sessions_by_mode"] == {"both": 1, "jsonl_only": 1, "otel_only": 1}

    z1 = result["z1"]
    assert z1["sessions"] == 3
    # cost: both-session is fully vendor (otel_vendor), otel-session is fully estimated
    # (estimated), jsonl-session has no otel block at all (estimated, via cost_effective).
    assert z1["cost_usd_micros"]["value"] == 5_000_000 + 2_000_000 + 1_000_000
    assert set(z1["cost_usd_micros"]["source"].split(",")) == {"otel_vendor", "estimated"}
    # tokens: both + otel_only resolve via otel_measures (150 tokens each's measures dict);
    # jsonl_only resolves via jsonl_models (200+100+10+5 = 315).
    assert z1["tokens"]["value"] == 150 + 300 + 315
    assert "otel_measures" in z1["tokens"]["source"]
    assert "jsonl_models" in z1["tokens"]["source"]
    # active time: both (120.0, otel measures) + otel-only (60.0, otel measures); jsonl-1 has
    # no otel measures and no turns with duration -- wait it has none, so contributes nothing.
    assert z1["active_time_s"]["value"] == 180.0
    # Mixed range has jsonl-capable sessions -> real sums, not unavailable.
    assert z1["subagents_spawned"] == 1
    assert z1["turns"] == 15
    assert z1["long_turns"] == 2
    assert z1["files_touched"] == {"created": 2, "edited": 1, "total": 3}
    assert z1["repos"] == ["local:myrepo", "local:otherrepo"]

    z2 = result["z2"]
    assert z2["model_split"]["claude-opus-5"] == {"tokens": 150, "cost_usd_micros": 5_000_000}
    assert z2["model_split"]["claude-haiku"] == {"tokens": 300, "cost_usd_micros": 2_000_000}
    assert z2["model_split"]["claude-sonnet"] == {"tokens": 315, "cost_usd_micros": 0}
    # Both otel-observed sessions carried a non-interactive query_source (sdk, prompt_suggestion)
    # -- collapsed into one "sdk" bucket at the API layer, since which subsystem issued a
    # background call isn't something a user can act on.
    assert z2["by_query_source"]["sdk"]["requests"] == 2
    assert "prompt_suggestion" not in z2["by_query_source"]
    # 2 of the 3 sessions in range (both, otel_only) carry OTel telemetry; jsonl-1 doesn't.
    assert z2["by_query_source_coverage"] == {"sessions_with_otel": 2, "sessions_total": 3}
    assert z2["provenance_mix"] is None                 # cold-start, parked this pass

    z3 = result["z3"]
    assert {t["name"] for t in z3["top_tools"]} == {"Bash", "Read"}
    assert z3["top_skills"][0]["name"] == "foo"
    assert z3["top_mcp_servers"][0]["name"] == "srv1"
    assert {r["repo_key"] for r in z3["top_repos"]} == {"myrepo", "otherrepo"}

    assert result["z5_scorecard"] is None                # cold-start, parked this pass


def test_overview_repos_group_by_repo_id_not_the_fragile_repo_key(engine, catalog):
    """dashboard plan §2/§3 regression: two sessions whose `repo_key` display strings differ
    (a worktree path vs. the plain clone path) but whose evidence resolves to the *same*
    `repo_id` must collapse into one repo, not inflate the count the way pure `repo_key`
    grouping did."""
    with session_scope() as s:
        _seed(s, "s1", "jsonl_only", cost_effective_usd_micros=100_000,
             repo_id="github:talium-ai/code-econ", repo_id_provenance="remote",
             info={"repo": {"repo_key": "flamboyant-easley-cf2002"}})  # a worktree slug
        _seed(s, "s2", "jsonl_only", cost_effective_usd_micros=100_000,
             repo_id="github:talium-ai/code-econ", repo_id_provenance="remote",
             info={"repo": {"repo_key": "code-econ"}})  # the plain clone path

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["z1"]["repos"] == ["github:talium-ai/code-econ"]
    assert result["z3"]["top_repos"] == [{"repo_key": "talium-ai/code-econ", "sessions": 2}]


def test_overview_subagent_fanout_counts_one_session_but_sums_all_tokens(engine, catalog):
    """FEAS §4/R0: a parent that spawned 2 sub-agents must count as 1 session, not 3 -- but
    the children's tokens and repo still belong in the totals, since own-session fields are
    never rolled up into the parent anywhere else in the system."""
    with session_scope() as s:
        _seed(s, "parent-1", "jsonl_only", cost_effective_usd_micros=1_000_000,
             models={"claude-sonnet": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}},
             subagents={"child-1": {}, "child-2": {}}, turn_count=3,
             repo_id="local:myrepo", repo_id_provenance="path", info={"repo": {"repo_key": "myrepo"}})
        _seed(s, "child-1", "jsonl_only", cost_effective_usd_micros=500_000,
             models={"claude-sonnet": {"input": 50, "output": 0, "cache_read": 0, "cache_write": 0}},
             turn_count=1, repo_id="local:myrepo", repo_id_provenance="path",
             info={"repo": {"repo_key": "myrepo"}}, parent_session_id="parent-1")
        _seed(s, "child-2", "jsonl_only", cost_effective_usd_micros=500_000,
             models={"claude-sonnet": {"input": 50, "output": 0, "cache_read": 0, "cache_write": 0}},
             turn_count=1, repo_id="local:myrepo", repo_id_provenance="path",
             info={"repo": {"repo_key": "myrepo"}}, parent_session_id="parent-1")

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    z1 = result["z1"]
    assert z1["sessions"] == 1                    # roots only: the fan-out is not 3 sessions
    assert z1["active_sessions"] == 1
    assert z1["tokens"]["value"] == 100 + 50 + 50  # every row's tokens still counted
    assert z1["turns"] == 3 + 1 + 1
    assert z1["repos"] == ["local:myrepo"]

    assert result["z3"]["top_repos"] == [{"repo_key": "myrepo", "sessions": 1}]


def test_overview_otel_only_range_is_structurally_unavailable_not_zero(engine, catalog):
    """MODES: 'the strip renders its first four rows and drops the rest'. A range with only
    otel_only sessions must not report 0 subagents/turns/files/repos -- that would claim
    knowledge the deployment structurally cannot have."""
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", cost_effective_usd_micros=2_000_000,
             otel=_otel_only_otel(), status="otel_only")

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    z1 = result["z1"]
    assert z1["sessions"] == 1
    assert z1["cost_usd_micros"]["value"] == 2_000_000    # Mode: all -- still computed
    for field in ("subagents_spawned", "turns", "long_turns", "files_touched", "repos"):
        assert z1[field] == {"unavailable": {"block": "z1_structure", "requires": "jsonl"}}

    z3 = result["z3"]
    for field in ("top_tools", "top_skills", "top_mcp_servers", "top_repos"):
        assert z3[field] == {"unavailable": {"block": "z3_usage_patterns", "requires": "jsonl"}}


def test_overview_folded_jsonl_only_session_counts_as_interactive(engine, catalog):
    """A JSONL-only session the worker *has* folded (otel_trueup.py's 4th touched-sessions arm)
    carries a `by_query_source` block of its own -- unconditionally `{"interactive": ...}`,
    since JSONL has no `query_source` attribute at all and background calls never reach a
    JSONL transcript in the first place (they're a side-channel API call, not a conversation
    turn). Its spend belongs in the same `interactive` total as an OTel-confirmed one, not a
    separate "unknown" bucket -- this is definitionally correct, not a default."""
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=1_000_000,
             otel={"by_query_source": {"interactive": {
                 "requests": 3, "cost_usd_micros": 0, "cost_est_usd_micros": 1_000_000,
                 "cost_effective_usd_micros": 1_000_000}}})
        _seed(s, "otel-1", "otel_only", cost_effective_usd_micros=2_000_000,
             otel=_otel_only_otel())   # prompt_suggestion, confirmed via real OTel evidence

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    z2 = result["z2"]
    assert z2["by_query_source"]["interactive"]["requests"] == 3   # the jsonl-only session's
    assert z2["by_query_source"]["sdk"]["requests"] == 1           # only the otel-observed one
    # Only 1 of the 2 sessions has real OTel telemetry -- the `sdk` total above is a lower
    # bound, and this is the number that lets the UI say so.
    assert z2["by_query_source_coverage"] == {"sessions_with_otel": 1, "sessions_total": 2}


def test_overview_jsonl_only_range_has_no_by_query_source(engine, catalog):
    """PM.3's rule, exercised through the API: absent, not empty, when nothing in range has
    any OTel evidence at all."""
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=1_000_000,
             models={"claude-sonnet": {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0}})

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["z2"]["by_query_source"] == {
        "unavailable": {"block": "by_query_source", "requires": "otel"}}
    assert result["z2"]["by_query_source_coverage"] is None


def test_overview_pre_tick_session_is_jsonl_capable_not_unavailable(engine, catalog):
    """A session the JSONL fold has folded but the true-up has never ticked for has
    `datasource is None` (PM ground rule 2) -- its tools/turns/files are real, folded data,
    not a structural absence. `datasource in ("jsonl_only", "both")` would wrongly exclude it;
    the fix keys on `datasource != "otel_only"` instead."""
    with session_scope() as s:
        _seed(s, "pre-tick", None, cost_effective_usd_micros=None,
             tools={"Bash": {"count": 1, "failed": 0}}, files={"a.py": {"created": True}},
             turn_count=4, subagents={"c1": {}}, repo_id="local:myrepo", repo_id_provenance="path",
             info={"repo": {"repo_key": "myrepo"}})

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    z1 = result["z1"]
    assert z1["turns"] == 4
    assert z1["subagents_spawned"] == 1
    assert z1["files_touched"] == {"created": 1, "edited": 0, "total": 1}
    assert z1["repos"] == ["local:myrepo"]
    assert result["z3"]["top_tools"][0]["name"] == "Bash"


def test_overview_empty_range(engine, catalog):
    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["modes_present"] == []
    assert result["sessions_by_mode"] == {}
    assert result["z1"]["sessions"] == 0
    assert result["z1"]["tokens"] == {"value": 0, "source": None}
    assert result["z1"]["merged_prs"] == 0
    assert result["z1"]["cost_per_pr"] == {"usd_micros": None, "cost_basis": None, "cost_complete": None}
    assert result["recoverable"] == {"usd_micros": None, "cost_basis": None, "cost_complete": None}
    assert result["seat"] is None                         # no team on record -- no seat to read


def test_overview_merged_prs_and_cost_per_pr(engine, catalog):
    """U5-myusage: `z1.merged_prs`/`cost_per_pr` are this person's own merged PRs in range,
    reusing `manager_aggregate.merged_pr_count`/`blended_cost_per_pr_usd_micros` the same way
    `person_detail` (A5) already does."""
    with session_scope() as s:
        _seed(s, "s1", "jsonl_only", cost_effective_usd_micros=4_000_000)
        s.add(GitArtifact(artifact_id="github:1:pr:1", kind="pr", repo_id="github:1",
                          org_id="default", user_id=USER, is_bot=False, state="merged",
                          created_at=NOW, updated_at=NOW, merged_at=NOW))

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["z1"]["merged_prs"] == 1
    assert result["z1"]["cost_per_pr"]["usd_micros"] == 4_000_000


def test_overview_recoverable_spend(engine, catalog):
    with session_scope() as s:
        _seed(s, "s1", "jsonl_only", cost_effective_usd_micros=1_000_000,
             info={"channel_cost": {"output": {"usd_micros": 600_000}, "recache": {"usd_micros": 100_000}},
                  "carry": {"carry_usd_micros": 200_000},
                  "cache_busts": {"total_usd_micros": 50_000, "events": []},
                  "interrupted": {"total_usd_micros": 30_000, "events": []}})

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), USER,
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    # cache busts (50k) + interrupted (30k); carry and recache are never recoverable.
    assert result["recoverable"]["usd_micros"] == 80_000


def test_overview_seat_is_team_budget_per_member_window_scaled(engine, catalog):
    """`seat` (cost-based seats plan §3.5): the user's primary team's monthly budget / members,
    scaled to the viewed window -- not the whole team's monthly budget as "your limit"."""
    with session_scope() as s:
        sync(s, catalog, [
            DirectoryRecord(external_keys=(ExternalKey("csv", USER),), email="u1@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "u2"),), email="u2@x.com",
                            teams=(TeamMembership("platform"),)),
        ], teams=[TeamRecord("platform", "default", "Platform")])
        # seats that predate the window: sync stamps created_at with wall-clock time
        s.execute(text("UPDATE users SET created_at = :t"), {"t": NOW - timedelta(days=90)})
        budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal("600"))

    with session_scope() as s:
        # `sync` maps the external key to a resolved user_id -- `csv:u1`, not the bare `u1`
        # this file's other fixtures write directly to `sessions.user_id`.
        result = service.overview(s, catalog, ApiConfig(), "csv:u1",
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))

    seat = result["seat"]
    assert seat["team_id"] == "platform"
    assert seat["seat_monthly"]["usd_micros"] == 300_000_000     # $600 / 2 members
    assert seat["allowance"]["usd_micros"] == round(300_000_000 * 2 / 31)   # 2 days of August (31)
    assert seat["usage_pct"] == 0.0
    assert seat["status"] == "under"
    assert seat["in_grace"] is False


def test_overview_seat_none_when_team_has_no_budget(engine, catalog):
    with session_scope() as s:
        sync(s, catalog, [
            DirectoryRecord(external_keys=(ExternalKey("csv", USER),), email="u1@x.com",
                            teams=(TeamMembership("platform"),)),
        ], teams=[TeamRecord("platform", "default", "Platform")])

    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), "csv:u1",
                                  NOW - timedelta(days=1), NOW + timedelta(days=1))
    assert result["seat"] is None


def test_overview_seat_for_a_period_to_date_window_is_the_full_period_allowance(engine, catalog):
    """A window that starts on the period's first day is measured against the whole period's seat
    budget -- here 1 Aug to 2 Aug of a $600 team budget / 2 members = $300 (A2)."""
    with session_scope() as s:
        sync(s, catalog, [
            DirectoryRecord(external_keys=(ExternalKey("csv", USER),), email="u1@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "u2"),), email="u2@x.com",
                            teams=(TeamMembership("platform"),)),
        ], teams=[TeamRecord("platform", "default", "Platform")])
        s.execute(text("UPDATE users SET created_at = :t"), {"t": NOW - timedelta(days=90)})
        budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal("600"))
    with session_scope() as s:
        result = service.overview(s, catalog, ApiConfig(), "csv:u1", NOW, NOW + timedelta(days=1))
    assert result["seat"]["allowance"]["usd_micros"] == 300_000_000


def test_overview_seat_reads_the_budget_of_the_active_cycle(engine, catalog):
    from server.api.manager import budget_cycles
    with session_scope() as s:
        sync(s, catalog, [
            DirectoryRecord(external_keys=(ExternalKey("csv", USER),), email="u1@x.com",
                            teams=(TeamMembership("platform"),)),
        ], teams=[TeamRecord("platform", "default", "Platform")])
        s.execute(text("UPDATE users SET created_at = :t"), {"t": NOW - timedelta(days=90)})
        budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal("600"))
        budget_cycles.put_cycle(s, catalog, "default", budget_cycles.validate("quarter"))
    with session_scope() as s:
        assert service.overview(s, catalog, ApiConfig(), "csv:u1", NOW, NOW + timedelta(days=1))["seat"] is None
        budgets_service.put_budget(s, catalog, "team", "platform", "quarterly", Decimal("900"))
    with session_scope() as s:
        seat = service.overview(s, catalog, ApiConfig(), "csv:u1", NOW, NOW + timedelta(days=1))["seat"]
    # 1 Aug is mid-Q3 (92 days, from 1 Jul): a rolling day of the $900 quarter
    assert seat["seat_monthly"]["usd_micros"] == 900_000_000
    assert seat["allowance"]["usd_micros"] == round(900_000_000 * 1 / 92)
