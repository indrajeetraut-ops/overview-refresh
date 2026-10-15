from collections import Counter
from datetime import date
import uuid

from forecast.contracts import DailyUsageRecord, TokenCounts
from forecast.synthetic.config import SeriesConfig
from forecast.synthetic.models import TeamRecord, UserProfileRecord, UserRecord
from forecast.synthetic.profiles import assign_profiles
from forecast.synthetic.series import generate_days


def config(*, days=1, start="2026-07-01", usage_seed=7, profile_seed=8,
           cache=.8, cache_pp=0, write_ratio=0.997, write_1h_share=0.97, weekend_probability=0, weekend_factor=1,
           events=None, agents=None):
    return SeriesConfig.model_validate({
        "schema_version": 3,
        "series": {"users_file": "unused", "roster_file": "unused", "start_date": start,
                   "days": days, "timezone": "UTC", "profile_seed": profile_seed,
                   "usage_seed": usage_seed},
        "defaults": {"daily_variation_pct": 0, "output_ratio": .03,
                     "cache_hit_ratio": cache, "cache_hit_variation_pp": cache_pp, "cache_write_ratio": write_ratio, "cache_write_1h_share": write_1h_share,
                     "mix_variation_pct": 0, "daily_zero_probability": 0,
                     "weekend_reduction_probability": weekend_probability,
                     "weekend_reduction_factor": weekend_factor},
        "profiles": {"light": {"daily_input_tokens": 1_000_000},
                     "heavy": {"daily_input_tokens": 1_000_000}},
        "team_profiles": {"team": {"profile_mix": {"light": .8, "heavy": .2},
                                     "project_mix": {"Project": 1}}},
        "projects": [{"name": "Project"}],
        "agents": agents or {
            "claude": {"billing_mode": "api", "usage_share": .6,
                       "models": {"claude-opus": .25, "claude-sonnet": .75}},
            "codex": {"billing_mode": "api", "usage_share": .4,
                      "models": {"gpt-sol": .25, "gpt-terra": .75}},
        },
        "events": events or {"spike_rules": [], "zero_periods": []},
    })


def population(n=1):
    users=[UserRecord(user_id=f"u{i:02}", org_id="org", name=f"User {i}",
                      email=f"u{i}@org.test") for i in range(n)]
    team=TeamRecord(team_id="t", org_id="org", team_name="team",
                    user_ids=[u.user_id for u in users])
    return users, [team]


def batches(cfg, n=1):
    users, teams=population(n)
    assignments=assign_profiles(cfg, teams)
    return list(generate_days(cfg, users, teams, assignments)), assignments


def test_numerical_oracle_and_single_project_per_user_day():
    day,_=batches(config())
    actual={(r.agent_type,r.model):(r.tokens.input,r.tokens.cache_write_5m,
                                    r.tokens.cache_write_1h,r.tokens.cache_read,r.tokens.output)
            for r in day[0].usage}
    assert actual == {
        ("claude","claude-opus"):(90,897,29_013,120_000,4_500),
        ("claude","claude-sonnet"):(270,2_692,87_038,360_000,13_500),
        ("codex","gpt-sol"):(60,598,19_342,80_000,3_000),
        ("codex","gpt-terra"):(180,1_795,58_025,240_000,9_000),
    }
    assert {r.project_id for r in day[0].usage} == {str(uuid.uuid5(
        uuid.NAMESPACE_URL, "forecast:project:org:project"))}


def test_profile_largest_remainder_is_exact_and_seed_independent_of_usage():
    _,teams=population(15)
    first=assign_profiles(config(days=30, usage_seed=1),teams)
    extended=assign_profiles(config(days=60, usage_seed=999),teams)
    assert first == extended
    assert Counter(x.profile for x in first) == {"light":12,"heavy":3}


def test_duration_extension_preserves_earlier_days_and_mapping_order():
    short,_=batches(config(days=2),3)
    long,_=batches(config(days=4),3)
    assert short == long[:2]
    reordered=config(days=2)
    reordered.agents=dict(reversed(list(reordered.agents.items())))
    again,_=batches(reordered,3)
    assert short == again


def _processed(row):
    t=row.tokens
    return t.input+t.cache_write_5m+t.cache_write_1h+t.cache_read


def test_cache_boundaries_conserve_processed_input():
    zero,_=batches(config(cache=0))
    one,_=batches(config(cache=1))
    assert sum(r.tokens.cache_read for r in zero[0].usage)==0
    assert sum(_processed(r) for r in zero[0].usage)==1_000_000
    assert sum(_processed(r) for r in one[0].usage)==1_000_000
    assert sum(r.tokens.input+r.tokens.cache_write_5m+r.tokens.cache_write_1h
               for r in one[0].usage)==0
    assert sum(r.tokens.cache_read for r in one[0].usage)==1_000_000
    assert all(_processed(r)>0 for r in one[0].usage)


def test_cache_write_split_worked_example():
    # Design 2.2: T=1,000,000, hit .80, writes .997, 1h share .97, output .03.
    agents={"claude": {"billing_mode": "api", "usage_share": 1, "models": {"m": 1}}}
    day,_=batches(config(agents=agents))
    # The one-user population draws a profile; every profile is 1,000,000 tokens/day.
    (row,)=day[0].usage
    t=row.tokens
    assert (t.input,t.cache_write_5m,t.cache_write_1h,t.cache_read,t.output)==(
        600,5_982,193_418,800_000,30_000)
    assert _processed(row)==1_000_000


def test_cache_write_ratio_zero_reproduces_the_non_cached_input():
    day,_=batches(config(write_ratio=0))
    for r in day[0].usage:
        assert r.tokens.cache_write_5m==0 and r.tokens.cache_write_1h==0
        assert r.tokens.input==_processed(r)-r.tokens.cache_read


def test_cache_write_ratio_one_leaves_no_uncached_input_and_share_edges():
    day,_=batches(config(write_ratio=1,write_1h_share=1))
    for r in day[0].usage:
        assert r.tokens.input==0 and r.tokens.cache_write_5m==0
        assert r.tokens.cache_write_1h>0
    day,_=batches(config(write_ratio=1,write_1h_share=0))
    for r in day[0].usage:
        assert r.tokens.input==0 and r.tokens.cache_write_1h==0
        assert r.tokens.cache_write_5m>0


def test_write_ratios_change_only_the_split_not_the_seeded_totals():
    base,_=batches(config(write_ratio=0))
    split,_=batches(config(write_ratio=0.5,write_1h_share=0.25))
    for a,b in zip(base[0].usage,split[0].usage):
        assert (a.tokens.cache_read,a.tokens.output)==(b.tokens.cache_read,b.tokens.output)
        assert _processed(a)==_processed(b)


def test_weekend_reduction_preserves_activity_and_complete_coverage():
    result,_=batches(config(start="2026-07-04",days=2,
                            weekend_probability=1,weekend_factor=.25))
    assert all(day.usage and day.active_users==1 and day.inactive_users==0 for day in result)
    assert all(sum(_processed(row) for row in day.usage)==250_000
               for day in result)
    assert all(len(day.coverage)==2 and {x.status for x in day.coverage}=={"complete"}
               for day in result)


def test_weekend_reduction_is_drawn_per_user_before_project_aggregation():
    result,_=batches(config(start="2026-07-04",weekend_probability=.5,
                            weekend_factor=.25),20)
    per_user={}
    for row in result[0].usage:
        per_user[row.user_id]=per_user.get(row.user_id,0)+_processed(row)
    assert len(per_user)==20
    assert set(per_user.values())=={250_000,1_000_000}
    assert len({row.project_id for row in result[0].usage})==1


def test_zero_period_keeps_zero_when_a_spike_rule_is_selected():
    events={"spike_rules":[{"id":"boost","probability":1,
                             "multiplier_min":4,"multiplier_max":4,
                             "targets":{"teams":[],"agents":[],"models":[]}}],
            "zero_periods":[{"id":"break","target_type":"user","target":"u00",
                             "start_date":"2026-07-01","end_date":"2026-07-01"}]}
    result,_=batches(config(events=events))
    assert result[0].usage == []
    assert [(e.kind,e.event_id) for e in result[0].events] == [("forced_zero","break")]


def test_overlapping_rules_are_additive_from_the_immutable_baseline():
    events={"spike_rules":[
                {"id":"team","probability":1,"multiplier_min":2,"multiplier_max":2,
                 "targets":{"teams":["team"],"agents":[],"models":[]}},
                {"id":"model","probability":1,"multiplier_min":3,"multiplier_max":3,
                 "targets":{"teams":[],"agents":[],
                            "models":[{"agent":"codex","model":"gpt-sol"}]}}],
            "zero_periods":[]}
    result,_=batches(config(events=events))
    totals={(r.agent_type,r.model):_processed(r)
            for r in result[0].usage}
    assert sum(totals.values())==2_200_000
    assert totals[("codex","gpt-sol")]==400_000
    assert [(e.kind,e.rule_id,e.baseline_processed_input,e.incremental_processed_input)
            for e in result[0].events] == [
                ("spike","model",100_000,200_000),
                ("spike","team",1_000_000,1_000_000),
            ]


def test_spike_rules_never_apply_on_weekends():
    events={"spike_rules":[{"id":"full-range","probability":1,
                             "multiplier_min":2,"multiplier_max":2,
                             "targets":{"teams":[],"agents":[],"models":[]}}],
            "zero_periods":[]}
    result,_=batches(config(start="2026-07-03",days=3,events=events))
    assert [sum(_processed(r) for r in day.usage) for day in result] == [
        2_000_000,1_000_000,1_000_000]
    assert [len(day.events) for day in result] == [1,0,0]


def test_spike_range_is_inclusive_and_still_excludes_weekends():
    events={"spike_rules":[{"id":"release","probability":1,
                             "multiplier_min":2,"multiplier_max":2,
                             "date_range":{"start_date":"2026-07-02",
                                           "end_date":"2026-07-05"},
                             "targets":{"teams":[],"agents":[],"models":[]}}],
            "zero_periods":[]}
    result,_=batches(config(start="2026-07-01",days=5,events=events))
    assert [sum(_processed(r) for r in day.usage) for day in result] == [
        1_000_000,2_000_000,2_000_000,1_000_000,1_000_000]
    assert [event.date.isoformat() for day in result for event in day.events] == [
        "2026-07-02","2026-07-03"]


def test_team_and_technology_targets_intersect_while_agent_and_model_union():
    raw=config().model_dump(mode="json")
    raw["team_profiles"]["other"]={"profile_mix":{"light":1},
                                      "project_mix":{"Project":1}}
    raw["events"]={"spike_rules":[{
        "id":"targeted","probability":1,"multiplier_min":2,"multiplier_max":2,
        "targets":{"teams":["team"],"agents":["codex"],
                   "models":[{"agent":"claude","model":"claude-opus"}]}}],
        "zero_periods":[]}
    cfg=SeriesConfig.model_validate(raw)
    users=[UserRecord(user_id="u0",org_id="org",name="U0",email="u0@org.test"),
           UserRecord(user_id="u1",org_id="org",name="U1",email="u1@org.test")]
    teams=[TeamRecord(team_id="t0",org_id="org",team_name="team",user_ids=["u0"]),
           TeamRecord(team_id="t1",org_id="org",team_name="other",user_ids=["u1"])]
    result=list(generate_days(cfg,users,teams,assign_profiles(cfg,teams)))[0]
    totals={(row.team_id,row.agent_type,row.model):_processed(row)
            for row in result.usage}
    assert totals[("t0","claude","claude-opus")]==300_000
    assert totals[("t0","claude","claude-sonnet")]==450_000
    assert totals[("t0","codex","gpt-sol")]==200_000
    assert totals[("t0","codex","gpt-terra")]==600_000
    assert sum(value for key,value in totals.items() if key[0]=="t1")==1_000_000
    assert [(event.baseline_processed_input,event.incremental_processed_input)
            for event in result.events]==[(550_000,550_000)]


def test_spike_increment_uses_exact_baseline_proportions_with_integer_residue():
    raw=config(cache=0).model_dump(mode="json")
    raw["profiles"]={"light":{"daily_input_tokens":5},"heavy":{"daily_input_tokens":5}}
    raw["defaults"]["output_ratio"]=0
    raw["agents"]={"agent":{"billing_mode":"api","usage_share":1,
                              "models":{"a":.4,"b":.6}}}
    raw["events"]={"spike_rules":[{
        "id":"fractional","probability":1,"multiplier_min":1.5,"multiplier_max":1.5,
        "targets":{"teams":[],"agents":[],"models":[]}}],"zero_periods":[]}
    result,_=batches(SeriesConfig.model_validate(raw))
    assert {row.model:_processed(row) for row in result[0].usage}=={"a":3,"b":5}
    assert result[0].events[0].incremental_processed_input==3


def test_spike_rule_order_and_duration_extension_preserve_the_prefix():
    rules=[
        {"id":"b","probability":1,"multiplier_min":1.5,"multiplier_max":1.5,
         "targets":{"teams":[],"agents":["codex"],"models":[]}},
        {"id":"a","probability":1,"multiplier_min":2,"multiplier_max":2,
         "targets":{"teams":[],"agents":[],"models":[]}},
    ]
    short,_=batches(config(days=2,events={"spike_rules":rules,"zero_periods":[]}),2)
    reordered,_=batches(config(days=2,events={"spike_rules":list(reversed(rules)),
                                              "zero_periods":[]}),2)
    extended,_=batches(config(days=4,events={"spike_rules":rules,"zero_periods":[]}),2)
    assert short==reordered==extended[:2]


def test_shared_contract_nulls_additive_fields_and_invalid_counts():
    raw={"date":"2026-07-01","org_id":"o","team_id":None,"user_id":None,
         "project_id":None,"agent_type":"a","model":"m","billing_mode":"unknown",
         "tokens":{"input":None,"cache_write_5m":0,"cache_write_1h":0,"cache_read":0,
                   "output":None,"future":1},"future":"ok"}
    assert DailyUsageRecord.model_validate(raw).tokens.input is None
    for field in ("cache_write_5m","cache_write_1h"):
        changed={**raw,"tokens":{**raw["tokens"],field:-1}}
        try: DailyUsageRecord.model_validate(changed)
        except ValueError: pass
        else: raise AssertionError(f"negative {field} accepted")
    for bad in (-1,1.5,True):
        changed={**raw,"tokens":{"input":bad,"cache_write_5m":0,"cache_write_1h":0,
                                 "cache_read":0,"output":0}}
        try: DailyUsageRecord.model_validate(changed)
        except ValueError: pass
        else: raise AssertionError(f"invalid token count accepted: {bad!r}")
