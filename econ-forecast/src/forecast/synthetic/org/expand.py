"""`run_org_expand` orchestration (plan §5 P8): the `org-expand` operation end to end.

Follows `runner.run_series`'s structure using its own module-level helpers
(`_operation`, `_check_output`, `_snapshot`) directly, per the plan ("importing is permitted; do
not copy them"). **`_finish` is deliberately NOT reused**: it is hardwired to Layer 1's own
`render_report`, which branches on `result.operation in {"users", "roster", "series"}` and would
silently fall into the *series* report template for an `"org"` operation, reading `details` keys
(`start_date`, `profiles`, `breakdowns`, ...) this module never produces. This module writes its
own finishing routine, calling `org.report.render_org_report` instead, but keeps the same shape
(config.yaml -> report.md -> manifest.json, in that order) and reuses Layer 1's generic
`GenerationResult` type (its fields are operation-agnostic). Flagged here and in the progress log
as a considered, necessary deviation from the plan's literal "reuse _finish" instruction.
"""
from __future__ import annotations

import hashlib
import platform
from collections import Counter
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

import yaml
from forecast.artifact_io import DatasetWriter, read_records, sha256
from forecast.contracts import DailyUsageRecord
from forecast.synthetic.errors import InputDataError
from forecast.synthetic.models import GenerationResult, UserProfileRecord
from forecast.synthetic.models import TeamRecord as L1TeamRecord
from forecast.synthetic.models import UserRecord as L1UserRecord
from forecast.synthetic.runner import _check_output, _operation, _snapshot

from ..randomness import rng_for
from . import ids
from .config import OrgConfig, ResolvedOrg, read_series_snapshot, validate_org_inputs
from .content import build_all
from .directory import build_directory
from .git import compute_git
from .models import (
    BudgetRecord, ExpectedRollupRecord, GitArtifactEventRecord,
    GitArtifactRecord, PrIssueLinkRecord, PrReworkRecord, SessionGitLinkRecord, SessionRecord,
    synthetic_marker,
)
from .pricing import OrgPricer, load_pricer, pricing_files
from .report import render_org_report
from .repos import build_repos
from .sessions import plan_sessions
from .work import compute_work

GENERATOR_VERSION = "1.0.0"


def _stage_log(state, module):
    return state["log"].bind(module=module)


def _artifact_number_key(a: dict):
    return a["number"] if a["number"] is not None else a["created_at"]


def _build_sessions_jsonl(resolved: ResolvedOrg, plans, records) -> list[SessionRecord]:
    seed = resolved.config.org.seed
    org_id = resolved.config.org.directory_org_id
    owner = resolved.config.org.github_owner
    marker = synthetic_marker(seed)
    out = []
    for plan in plans:
        agent_r = rng_for(seed, "layout", plan.l1_user, plan.date.isoformat())
        agent_version = agent_r.choice(sorted(resolved.config.org.agent_versions))
        record = records[plan.session_id]
        out.append(SessionRecord(
            session_id=plan.session_id, producer_id=f"synthetic:{owner}", org_id=org_id,
            user_id=plan.user_id, agent=plan.agent, agent_version=agent_version,
            status="idle_closed", started_at=plan.started_at, last_event_at=plan.ended_at,
            ended_at=plan.ended_at, signal_blob={"events": []},
            info_blob={"synthetic": True, **marker}, parent_session_id=plan.parent_session_id,
            subagents=list(plan.children)))
    return out


def _expected_rollup(resolved: ResolvedOrg, usage: list[DailyUsageRecord]) -> list[ExpectedRollupRecord]:
    from .sessions import _project_name_by_id

    project_by_id = _project_name_by_id(resolved)
    out = []
    for row in usage:
        project = project_by_id[row.project_id]
        repo = resolved.project_to_repo[project]
        user = resolved.users_by_id[row.user_id]
        repo_id = f"github:{resolved.config.org.github_owner}/{repo.name}".lower()
        out.append(ExpectedRollupRecord(
            date=row.date, user_id=f"github:{user.github_login}", repo_id=repo_id,
            agent=row.agent_type, model=row.model, input=row.tokens.input or 0,
            cache_write_5m=row.tokens.cache_write_5m or 0,
            cache_write_1h=row.tokens.cache_write_1h or 0,
            cache_read=row.tokens.cache_read or 0, output=row.tokens.output or 0))
    return out


def run_org_expand(config: OrgConfig, output_dir: Path, *, log=None, run_id=None) -> GenerationResult:
    with _operation("org", run_id, log) as state:
        out = _check_output(output_dir)
        state["stage"] = "validate"
        snapshot = read_series_snapshot(config.org.series_dir)
        pricing = load_pricer(config.org.pricing_version, config.org.pricing_org, config.org.pricing_dir)
        resolved = validate_org_inputs(config, snapshot["manifest"], snapshot["users"],
                                       snapshot["teams"], snapshot["series_cfg"], pricing)
        pricer = OrgPricer(pricing)

        state["stage"] = "directory"
        wire, user_map, user_prefs = build_directory(resolved)
        installation, git_repos, repositories = build_repos(resolved)

        state["stage"] = "sessions"
        series_dir = Path(config.org.series_dir)
        usage = read_records(series_dir / "usage.jsonl", DailyUsageRecord, schema_version=2)
        plans = plan_sessions(resolved, usage)

        state["stage"] = "work"
        work = compute_work(resolved, plans, log=_stage_log(state, "forecast.synthetic.org.work"))

        state["stage"] = "git"
        git_result = compute_git(resolved, plans, work,
                                 log=_stage_log(state, "forecast.synthetic.org.git"))

        state["stage"] = "content"
        records, ledgers = build_all(resolved, plans, work, git_result, pricer,
                            log=_stage_log(state, "forecast.synthetic.org.content"))

        state["stage"] = "rollup"
        expected_rollup = _expected_rollup(resolved, usage)
        _assert_rollup(resolved, plans, records, ledgers, expected_rollup)

        state["stage"] = "artifacts"
        with DatasetWriter(out, "org", log=_stage_log(state, "forecast.artifact_io")) as writer:
            sources = {}
            sources["usage"] = _snapshot(writer, series_dir / "usage.jsonl", "series",
                                         DailyUsageRecord, usage, schema_version=2)
            user_profiles = read_records(series_dir / "user_profiles.jsonl", UserProfileRecord,
                                       schema_version=2)
            sources["user_profiles"] = _snapshot(writer, series_dir / "user_profiles.jsonl",
                                                 "series", UserProfileRecord, user_profiles,
                                                 schema_version=2)
            sources["series_users"] = _snapshot(writer, series_dir / "inputs" / "users" / "users.jsonl",
                                                "series/inputs/users", L1UserRecord, snapshot["users"])
            sources["series_roster"] = _snapshot(writer, series_dir / "inputs" / "roster" / "teams.jsonl",
                                                 "series/inputs/roster", L1TeamRecord, snapshot["teams"])

            def _direct_snapshot(source: Path, destination: str) -> dict:
                path = writer.snapshot(source, destination)
                return {"original": str(source), "sha256": sha256(path), "snapshot": destination}

            sources["series_config"] = _direct_snapshot(series_dir / "config.yaml", "series/config.yaml")
            sources["series_manifest"] = _direct_snapshot(series_dir / "manifest.json", "series/manifest.json")
            # Self-contained: the bound catalog version(s), history and channel config, so the
            # dataset loads and prices with the live catalog gone (#68 design §5).
            resolved_version, files = pricing_files(config.org.pricing_version, config.org.pricing_dir)
            sources["pricing"] = {
                "original": f"catalog version {resolved_version}"
                            + (f" ({config.org.pricing_dir})" if config.org.pricing_dir else ""),
                "snapshot": "pricing/", "version": resolved_version,
                "org": config.org.pricing_org, "files": {}}
            for rel, content in files:
                (writer.output_dir / "pricing" / rel).parent.mkdir(parents=True, exist_ok=True)
                writer.write_bytes(f"pricing/{rel}", content)
                sources["pricing"]["files"][rel] = {"snapshot": f"pricing/{rel}",
                                                    "sha256": hashlib.sha256(content).hexdigest()}

            writer.write_records("directory_teams.jsonl", wire.teams)
            writer.write_records("directory_people.jsonl", wire.people)
            writer.write_records("user_map.jsonl", user_map)
            writer.write_records("user_prefs.jsonl", user_prefs)

            writer.write_records("git_installations.jsonl", [installation])
            writer.write_records("git_repos.jsonl", git_repos)
            writer.write_records("repositories.jsonl", repositories)

            artifacts_sorted = sorted(
                git_result.artifacts,
                key=lambda a: (a["repo_id"], a["kind"], _artifact_number_key(a), a["artifact_id"]))
            writer.write_records("git_artifacts.jsonl",
                                 [GitArtifactRecord(**a) for a in artifacts_sorted])
            writer.write_records("git_artifact_events.jsonl",
                                 [GitArtifactEventRecord(**e) for e in git_result.events])
            writer.write_records("pr_issue_links.jsonl",
                                 [PrIssueLinkRecord(**link) for link in git_result.pr_issue_links])

            sessions_sorted = sorted(plans, key=lambda p: (p.started_at, p.session_id))
            writer.write_records("sessions.jsonl", _build_sessions_jsonl(resolved, sessions_sorted, records))
            writer.write_records("session_info.jsonl",
                                 [records[p.session_id] for p in sessions_sorted])
            writer.write_records("session_git_links.jsonl",
                                 [SessionGitLinkRecord(**link) for link in git_result.session_links])

            writer.write_records("pr_rework.jsonl",
                                 [PrReworkRecord(**r) for r in git_result.pr_rework])
            writer.write_records("budgets.jsonl", _build_budgets(resolved))
            writer.write_records("expected_rollup.jsonl", expected_rollup)

            summary = _summary_counts(resolved, plans, work, git_result)
            details = _report_details(resolved, plans, work, git_result, records, summary)
            state["stage"] = "report"
            config_bytes = yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True).encode()
            writer.write_bytes("config.yaml", config_bytes)
            result = GenerationResult(
                operation=state["operation"], output_dir=writer.output_dir.resolve(),
                run_id=state["run_id"], files=[*writer.files, "config.yaml", "report.md", "manifest.json"],
                record_counts={key: value["rows"] for key, value in writer.files.items()},
                summary_counts=dict(summary), report_path=(writer.output_dir / "report.md").resolve())
            details.update(completed_at=datetime.now(timezone.utc).isoformat(),
                           created_at=state["created_at"], org_id=resolved.org_id,
                           config="config.yaml", input_sources=sources)
            writer.write_bytes("report.md", render_org_report(result, details).encode())
            state["stage"] = "manifest"
            manifest = {
                "schema_version": 2, "generator_version": GENERATOR_VERSION,
                "operation": "org", "status": "complete", "run_id": state["run_id"],
                "created_at": state["created_at"], "org_id": resolved.org_id,
                "directory_org_id": resolved.config.org.directory_org_id,
                "versions": {"python": platform.python_version(),
                            "pydantic": version("pydantic"), "pyyaml": version("PyYAML")},
                "config_sha256": hashlib.sha256(config_bytes).hexdigest(), "files": writer.files,
                "report": {"path": "report.md", "sha256": sha256(writer.output_dir / "report.md")},
                "input_sources": sources,
                "summary": {**dict(summary),
                           **_manifest_extra_summary(resolved, work, git_result, records)},
            }
            writer.manifest(manifest)
        state["log"].info("synthetic.org.run_completed", output_dir=str(result.output_dir),
                          counts=result.summary_counts)
        return result


_ROLLUP_TYPES = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")


def _assert_rollup(resolved: ResolvedOrg, plans, records, ledgers,
                   expected: list[ExpectedRollupRecord]) -> None:
    """I1 per token type: the sum over every request's five types, grouped by (date, user,
    repo, agent, model), equals Layer 1's rows. Totals come from the ledgers because
    `SessionRecord.models` carries only the combined `cache_write`."""
    plans_by_id = {p.session_id: p for p in plans}
    actual: dict[tuple, dict[str, int]] = {}
    for sid, ledger in ledgers.items():
        plan = plans_by_id[sid]
        repo_id = records[sid].repo_id
        for req in ledger.requests:
            key = (plan.date, plan.user_id, repo_id, plan.agent, req.model)
            bucket = actual.setdefault(key, dict.fromkeys(_ROLLUP_TYPES, 0))
            for kind in _ROLLUP_TYPES:
                bucket[kind] += getattr(req, kind)

    expected_agg: dict[tuple, dict[str, int]] = {}
    for row in expected:
        key = (row.date, row.user_id, row.repo_id, row.agent, row.model)
        bucket = expected_agg.setdefault(key, dict.fromkeys(_ROLLUP_TYPES, 0))
        for kind in _ROLLUP_TYPES:
            bucket[kind] += getattr(row, kind)

    if actual != expected_agg:
        mismatches = []
        for key in set(actual) | set(expected_agg):
            a = actual.get(key)
            e = expected_agg.get(key)
            if a != e:
                mismatches.append((key, a, e))
        raise InputDataError(f"rollup mismatch: {len(mismatches)} keys differ; "
                             f"first: {mismatches[:3]}")


def _build_budgets(resolved: ResolvedOrg) -> list[BudgetRecord]:
    budgets = resolved.config.budgets
    if budgets is None:
        return []
    out = [BudgetRecord(scope_type="org", scope_id=resolved.config.org.directory_org_id,
                        period=budgets.period, amount_usd=budgets.org_usd)]
    for team_name, amount in budgets.teams.items():
        out.append(BudgetRecord(scope_type="team", scope_id=ids.team_slug(team_name),
                                period=budgets.period, amount_usd=amount))
    return out


def _summary_counts(resolved: ResolvedOrg, plans, work, git_result) -> Counter:
    """Pure INTEGER counts only, since this feeds `GenerationResult.summary_counts`
    (`dict[str, int]`, Layer 1's type). `calibrated_base`/`merged_prs_per_dev_week`/
    `total_cost_usd` are real-valued and go straight into the manifest's own `summary` dict
    instead (see `run_org_expand`), which is unconstrained JSON, not this typed Counter."""
    roots = sum(1 for p in plans if p.parent_session_id is None)
    children = len(plans) - roots
    reverts = [a for a in git_result.artifacts
              if a["kind"] == "pr" and a["info"].get("github.is_revert")]
    regular_prs = [a for a in git_result.artifacts
                  if a["kind"] == "pr" and not a["info"].get("github.is_revert")]
    merged = [a for a in regular_prs if a["state"] == "merged"]
    open_ = [a for a in regular_prs if a["state"] == "open"]
    closed_unmerged = [a for a in regular_prs if a["state"] == "closed"]
    issues = [a for a in git_result.artifacts if a["kind"] == "issue"]
    reviews = [a for a in git_result.artifacts if a["kind"] == "review"]
    commits = [a for a in git_result.artifacts if a["kind"] == "commit"]

    return Counter(
        teams=len(resolved.teams), users=len(resolved.users), repos=len(resolved.config.repos),
        sessions_root=roots, sessions_child=children, prs=len(regular_prs), prs_merged=len(merged),
        prs_open=len(open_), prs_closed_unmerged=len(closed_unmerged), reverts=len(reverts),
        issues=len(issues), reviews=len(reviews), commits=len(commits),
        links=len(git_result.pr_issue_links))


def _manifest_extra_summary(resolved: ResolvedOrg, work, git_result, records) -> dict:
    """The three real-valued manifest summary fields the plan lists alongside the integer
    counts (`calibrated_base`, `merged_prs_per_dev_week`, `total_cost_usd`) -- kept out of
    `_summary_counts`'s `Counter` since `GenerationResult.summary_counts` is `dict[str, int]`."""
    series = resolved.series.series
    series_end = series.start_date + timedelta(days=series.days - 1)
    dev_weeks = sum(((series_end - j).days + 1) / 7 for j in work.first_active_date.values())
    merged = sum(1 for a in git_result.artifacts if a["kind"] == "pr" and a["state"] == "merged"
                and not a["info"].get("github.is_revert"))
    total_cost_usd = sum(rec.cost_effective_usd_micros for rec in records.values()) / 1_000_000
    return {"calibrated_base": work.base,
           "merged_prs_per_dev_week": merged / dev_weeks if dev_weeks else 0.0,
           "total_cost_usd": total_cost_usd}


def _report_details(resolved: ResolvedOrg, plans, work, git_result, records, summary) -> dict:
    series = resolved.series.series
    return {
        "start_date": series.start_date,
        "end_date": series.start_date + timedelta(days=series.days - 1),
        "days": series.days, "timezone": series.timezone,
        "seeds": {"org": resolved.config.org.seed},
        "calibrated_base": work.base,
        "target_merged_prs_per_dev_week": resolved.config.calibration.target_merged_prs_per_dev_week,
        "resolved": resolved, "plans": plans, "work": work, "git_result": git_result,
        "records": records, "summary": summary,
    }
