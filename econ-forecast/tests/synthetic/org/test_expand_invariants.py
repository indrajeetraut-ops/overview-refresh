"""P8: `org-expand` end to end -- I1, I3, I4, I5 over the written dataset (plan §5 P8)."""
import json
from pathlib import Path

import pytest

from forecast.synthetic.org.expand import run_org_expand


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture(scope="module")
def expanded(tmp_path_factory):
    """A small org (2 teams x 4 users, 2 repos, 14 days) run through `run_org_expand` end to
    end -- close enough to the plan's "2 teams x 3 users, 2 repos, 21 days" sizing to exercise
    every invariant. Built inline (module-scoped) rather than via the function-scoped
    `build_series_chain`/`small_series` fixtures, which `tmp_path` makes function-scoped."""
    import yaml

    from forecast.synthetic.config import RosterConfig, UsersConfig, load_series_config
    from forecast.synthetic.org.config import load_org_config
    from forecast.synthetic.runner import run_roster, run_series, run_users

    tmp = tmp_path_factory.mktemp("expand_small")
    users_cfg = UsersConfig(schema_version=1, users={
        "count": 8, "org_domain": "expand-small.test", "seed": 4242, "locale": "en_US", "identity_style": "consistent"})
    users_out = tmp / "users"
    run_users(users_cfg, users_out)
    roster_cfg = RosterConfig(schema_version=1, roster={
        "users_file": users_out / "users.jsonl", "seed": 4242,
        "teams": [{"team_name": "platform", "count": 4}, {"team_name": "payments", "count": 4}]})
    roster_out = tmp / "roster"
    run_roster(roster_cfg, roster_out)
    series_raw = {
        "schema_version": 3,
        "series": {"users_file": str(users_out / "users.jsonl"),
                  "roster_file": str(roster_out / "teams.jsonl"), "start_date": "2026-01-05",
                  "days": 14, "timezone": "UTC", "profile_seed": 4243, "usage_seed": 4244},
        "defaults": {"daily_variation_pct": 30, "output_ratio": 0.03, "cache_hit_ratio": 0.8,
                     "cache_hit_variation_pp": 5, "cache_write_ratio": 0.997, "cache_write_1h_share": 0.97, "mix_variation_pct": 10,
                     "daily_zero_probability": 0.1,
                     "weekend_reduction_probability": 0.9, "weekend_reduction_factor": 0.25},
        "profiles": {"light_profile": {"daily_input_tokens": 200_000},
                    "mid_profile": {"daily_input_tokens": 900_000},
                    "heavy_profile": {"daily_input_tokens": 3_000_000}},
        "team_profiles": {
            "platform": {"profile_mix": {"light_profile": 0.5, "mid_profile": 0.5},
                        "project_mix": {"platform_infra": 0.8, "payments_api": 0.2}},
            "payments": {"profile_mix": {"mid_profile": 0.5, "heavy_profile": 0.5},
                        "project_mix": {"payments_api": 1.0}},
        },
        "projects": [{"name": "platform_infra"}, {"name": "payments_api"}],
        "agents": {"claude_code": {"billing_mode": "api", "usage_share": 1.0,
                                   "models": {"claude-sonnet-5": 0.7, "claude-opus-5": 0.3}}},
        "events": {"spike_rules": [], "zero_periods": []},
    }
    series_yaml_path = tmp / "series.yaml"
    series_yaml_path.write_text(yaml.safe_dump(series_raw))
    series_out = tmp / "series"
    run_series(load_series_config(series_yaml_path), series_out)

    org_raw = {
        "schema_version": 1,
        "org": {"series_dir": str(series_out), "seed": 11, "github_owner": "acme",
               "admin": {"github_login": "vp-eng", "display_name": "Jordan Avery",
                        "email": "jordan.avery@org-conf.test"},
               "agent_versions": ["2.1.260"]},
        "calibration": {"target_merged_prs_per_dev_week": 3.0},
        "repos": [
            {"project": "platform_infra", "name": "platform-infra",
             "modules": [{"path": "src/", "weight": 1.0, "extensions": {".py": 1.0}}]},
            {"project": "payments_api", "name": "payments-api",
             "modules": [{"path": "services/ledger/", "weight": 1.0, "extensions": {".py": 1.0}}]},
        ],
        "teams": {"platform": {}, "payments": {}},
    }
    org_path = tmp / "org.yaml"
    org_path.write_text(yaml.safe_dump(org_raw))
    cfg = load_org_config(org_path)
    out = tmp / "org-expand-out"
    result = run_org_expand(cfg, out)
    return out


@pytest.fixture(scope="module")
def dataset(expanded):
    return {
        "sessions": _read_jsonl(expanded / "sessions.jsonl"),
        "session_info": _read_jsonl(expanded / "session_info.jsonl"),
        "expected_rollup": _read_jsonl(expanded / "expected_rollup.jsonl"),
        "git_artifacts": _read_jsonl(expanded / "git_artifacts.jsonl"),
        "git_artifact_events": _read_jsonl(expanded / "git_artifact_events.jsonl"),
        "pr_issue_links": _read_jsonl(expanded / "pr_issue_links.jsonl"),
        "session_git_links": _read_jsonl(expanded / "session_git_links.jsonl"),
        "directory_people": _read_jsonl(expanded / "directory_people.jsonl"),
        "directory_teams": _read_jsonl(expanded / "directory_teams.jsonl"),
        "user_map": _read_jsonl(expanded / "user_map.jsonl"),
        "repositories": _read_jsonl(expanded / "repositories.jsonl"),
        "git_repos": _read_jsonl(expanded / "git_repos.jsonl"),
    }


# `session_info.models` keeps one combined `cache_write` (5m + 1h); the per-type split is
# checked in-process against the ledgers (`test_i1_per_type_in_process`).
_DB_TYPES = ("input", "cache_write", "cache_read", "output")


def test_expected_rollup_rows_carry_five_token_fields(dataset):
    row = dataset["expected_rollup"][0]
    assert {"input", "cache_write_5m", "cache_write_1h", "cache_read", "output"} <= set(row)
    assert "input_combined" not in row


def test_org_manifest_is_schema_2(expanded):
    manifest = json.loads((expanded / "manifest.json").read_text())
    assert manifest["schema_version"] == 2


def test_i1_per_type_in_process(records_and_ledgers):
    """`_assert_rollup` over the real ledgers passes, and fails when any single type of any
    single request is nudged by one token."""
    import copy
    import dataclasses
    from forecast.synthetic.errors import InputDataError
    from forecast.synthetic.org.expand import _assert_rollup, _expected_rollup
    from forecast.synthetic.org.requests import Ledger

    resolved, plans, _work, _git, records, ledgers = records_and_ledgers
    from forecast.artifact_io import read_records
    from forecast.contracts import DailyUsageRecord
    from pathlib import Path
    usage = read_records(Path(resolved.config.org.series_dir) / "usage.jsonl", DailyUsageRecord,
                         schema_version=2)
    expected = _expected_rollup(resolved, usage)
    _assert_rollup(resolved, plans, records, ledgers, expected)

    sid, ledger = next((s, l) for s, l in ledgers.items() if l.requests)
    for kind in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output"):
        broken = dict(ledgers)
        first = copy.copy(ledger.requests[0])
        setattr(first, kind, getattr(first, kind) + 1)
        broken[sid] = Ledger(requests=[first, *ledger.requests[1:]], turn_count=ledger.turn_count,
                             compactions=ledger.compactions)
        with pytest.raises(InputDataError, match="rollup mismatch"):
            _assert_rollup(resolved, plans, records, broken, expected)


@pytest.mark.parametrize("moved_from,moved_to", [
    ("input", "cache_write_5m"), ("cache_write_5m", "cache_write_1h"),
    ("cache_write_1h", "cache_read"), ("cache_read", "output"), ("output", "input")])
def test_i1_per_type_catches_offsetting_perturbations(records_and_ledgers, moved_from, moved_to):
    """Moving one token between two types of one request keeps every combined total (and so
    the old `input + cache_write` check) but must still fail the per-type check."""
    import copy
    from pathlib import Path
    from forecast.artifact_io import read_records
    from forecast.contracts import DailyUsageRecord
    from forecast.synthetic.errors import InputDataError
    from forecast.synthetic.org.expand import _assert_rollup, _expected_rollup
    from forecast.synthetic.org.requests import Ledger

    resolved, plans, _work, _git, records, ledgers = records_and_ledgers
    usage = read_records(Path(resolved.config.org.series_dir) / "usage.jsonl", DailyUsageRecord,
                         schema_version=2)
    expected = _expected_rollup(resolved, usage)
    sid, ledger = next((s, l) for s, l in ledgers.items()
                       if l.requests and getattr(l.requests[0], moved_from) > 0)
    first = copy.copy(ledger.requests[0])
    setattr(first, moved_from, getattr(first, moved_from) - 1)
    setattr(first, moved_to, getattr(first, moved_to) + 1)
    broken = {**ledgers, sid: Ledger(requests=[first, *ledger.requests[1:]],
                                     turn_count=ledger.turn_count, compactions=ledger.compactions)}
    with pytest.raises(InputDataError, match="rollup mismatch"):
        _assert_rollup(resolved, plans, records, broken, expected)


def test_i1_rollup_exactness(dataset):
    actual: dict[tuple, dict[str, int]] = {}
    sessions_by_id = {s["session_id"]: s for s in dataset["sessions"]}
    for info in dataset["session_info"]:
        session = sessions_by_id[info["session_id"]]
        for model, cats in info["models"].items():
            key = (session["started_at"][:10], session["user_id"], info["repo_id"],
                  session["agent"], model)
            bucket = actual.setdefault(key, dict.fromkeys(_DB_TYPES, 0))
            for kind in _DB_TYPES:
                bucket[kind] += cats[kind]

    expected: dict[tuple, dict[str, int]] = {}
    for row in dataset["expected_rollup"]:
        key = (row["date"], row["user_id"], row["repo_id"], row["agent"], row["model"])
        bucket = expected.setdefault(key, dict.fromkeys(_DB_TYPES, 0))
        for kind in ("input", "cache_read", "output"):
            bucket[kind] += row[kind]
        bucket["cache_write"] += row["cache_write_5m"] + row["cache_write_1h"]

    assert actual == expected


def test_i3_referential_integrity(dataset):
    session_ids = {s["session_id"] for s in dataset["sessions"]}
    artifact_ids = {a["artifact_id"] for a in dataset["git_artifacts"]}
    repo_ids_git = {r["repo_id"] for r in dataset["git_repos"]}
    repo_ids_session = {r["repo_id"] for r in dataset["repositories"]}
    user_ids = {u["user_id"] for u in dataset["user_map"]}
    admin_login = next(p for p in dataset["directory_people"] if not p["teams"])
    admin_id = "github:" + next(k["external_id"] for k in admin_login["external_keys"]
                                if k["provider"] == "github")
    all_user_ids = user_ids | {admin_id}

    for session in dataset["sessions"]:
        assert session["user_id"] in all_user_ids
        if session["parent_session_id"]:
            assert session["parent_session_id"] in session_ids
        for child in session["subagents"]:
            assert child in session_ids

    for link in dataset["session_git_links"]:
        assert link["session_id"] in session_ids
        assert link["artifact_id"] in artifact_ids

    for a in dataset["git_artifacts"]:
        if a["kind"] == "pr" and a["author_login"]:
            assert a["user_id"] in all_user_ids
        if a["parent_artifact_id"]:
            assert a["parent_artifact_id"] in artifact_ids

    for link in dataset["pr_issue_links"]:
        assert link["pr_artifact_id"] in artifact_ids
        assert link["issue_artifact_id"] in artifact_ids

    for event in dataset["git_artifact_events"]:
        assert event["artifact_id"] in artifact_ids


def test_i4_temporal_order(dataset):
    sessions_by_id = {s["session_id"]: s for s in dataset["sessions"]}
    # Root sessions never overlap per user-day.
    from collections import defaultdict
    by_user_day = defaultdict(list)
    for s in dataset["sessions"]:
        if s["parent_session_id"] is None:
            by_user_day[(s["user_id"], s["started_at"][:10])].append((s["started_at"], s["ended_at"]))
    for key, intervals in by_user_day.items():
        intervals.sort()
        for (s1, e1), (s2, e2) in zip(intervals, intervals[1:]):
            assert e1 <= s2, key

    # Events monotonic per artifact.
    by_artifact = defaultdict(list)
    for e in dataset["git_artifact_events"]:
        by_artifact[e["artifact_id"]].append(e["occurred_at"])
    for artifact_id, times in by_artifact.items():
        assert times == sorted(times), artifact_id

    # Commit <= review <= merge/close per PR.
    reviews_by_pr = defaultdict(list)
    commits_by_pr = defaultdict(list)
    for a in dataset["git_artifacts"]:
        if a["kind"] == "review":
            reviews_by_pr[a["parent_artifact_id"]].append(a["created_at"])
        elif a["kind"] == "commit" and a["parent_artifact_id"]:
            commits_by_pr[a["parent_artifact_id"]].append(a["created_at"])
    for a in dataset["git_artifacts"]:
        if a["kind"] != "pr":
            continue
        reviews = sorted(reviews_by_pr.get(a["artifact_id"], []))
        commits = sorted(commits_by_pr.get(a["artifact_id"], []))
        if commits and reviews:
            assert commits[0] <= reviews[0]
        end = a.get("merged_at") or a.get("closed_at")
        for rt in reviews:
            assert a["created_at"] <= rt
            if end:
                assert rt <= end


def test_i5_cost_consistency(dataset):
    for info in dataset["session_info"]:
        cc = info["info"]["channel_cost"]
        carry = info["info"]["carry"]["carry_usd_micros"]
        total = cc["input"]["usd_micros"] + cc["output"]["usd_micros"] + cc["recache"]["usd_micros"] + carry
        assert abs(total - info["cost_effective_usd_micros"]) <= 4


def test_no_allowances_file_is_emitted(expanded):
    """Seat allowances derive from team budgets now; the dataset no longer ships (or lists in its
    manifest) a token allowances.jsonl."""
    assert not (expanded / "allowances.jsonl").exists()
    manifest = json.loads((expanded / "manifest.json").read_text())
    assert "allowances.jsonl" not in manifest["files"]
