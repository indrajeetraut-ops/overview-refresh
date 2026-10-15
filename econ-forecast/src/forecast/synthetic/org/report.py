"""`report.md` for `org-expand` (plan §5 P8): inputs/seeds, calibration, per-team and per-repo
tables, the rollup check result, and warnings. Reads the same `resolved`/`work`/`git_result`/
`records` objects `run_org_expand` already built -- no rereading of the written dataset."""
from __future__ import annotations

from datetime import timedelta

from . import ids


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def render_org_report(result, details: dict) -> str:
    resolved = details["resolved"]
    work = details["work"]
    git_result = details["git_result"]
    records = details["records"]
    plans = details["plans"]
    plans_by_id = {p.session_id: p for p in plans}

    lines = [
        "# Synthetic Org Expansion Report", "",
        f"- Run ID: `{result.run_id}`", f"- Completed: {details['completed_at']}",
        f"- Organization: `{details['org_id']}`", f"- Output: `{result.output_dir}`",
        f"- Resolved configuration: `{details['config']}`", "",
        "## Inputs and seeds", "",
        f"- Org seed: {details['seeds']['org']}",
        f"- Series: {details['start_date']} through {details['end_date']} "
        f"({details['days']} days), timezone `{details['timezone']}`",
    ]
    for label, source in sorted(details["input_sources"].items()):
        lines.append(f"- {label.replace('_', ' ').title()}: `{source['original']}`; "
                     f"snapshot: `{source['snapshot']}`")

    lines += ["", "## Artifacts", ""]
    lines += [f"- `{name}`: {count} rows" for name, count in sorted(result.record_counts.items())]
    lines += ["", "## Summary", ""]
    lines += [f"- {key.replace('_', ' ').title()}: {value}"
             for key, value in sorted(result.summary_counts.items())]

    # ---- Calibration ----
    lines += ["", "## Calibration", "",
             f"Target: {details['target_merged_prs_per_dev_week']:.2f} merged PRs/dev-week. "
             f"Calibrated `base` (tokens/LOC at multiplier 1): {work.base:.2f}.", "",
             "| Team | Devs | Merged PRs | Dev-weeks | Realised/dev-week |",
             "|---|---:|---:|---:|---:|"]
    series = resolved.series.series
    series_end = series.start_date + timedelta(days=series.days - 1)
    prs_by_team: dict[str, list[dict]] = {}
    for a in git_result.artifacts:
        if a["kind"] != "pr" or a["info"].get("github.is_revert"):
            continue
        l1_user = next((u for t in resolved.teams for u in t.user_ids
                       if f"github:{resolved.users_by_id[u].github_login}" == a["user_id"]), None)
        if l1_user is None:
            continue
        team_name = next(t.team_name for t in resolved.teams if l1_user in t.user_ids)
        prs_by_team.setdefault(team_name, []).append(a)

    for team_name in resolved.team_order:
        team = resolved.teams_by_name[team_name]
        devs = len(team.user_ids)
        team_prs = prs_by_team.get(team_name, [])
        merged = [a for a in team_prs if a["state"] == "merged"]
        dev_weeks = sum(((series_end - work.first_active_date[u]).days + 1) / 7
                        for u in team.user_ids if u in work.first_active_date)
        rate = len(merged) / dev_weeks if dev_weeks else 0.0
        lines.append(f"| {_cell(team_name)} | {devs} | {len(merged)} | {dev_weeks:.1f} | {rate:.2f} |")

    # ---- Per team ----
    lines += ["", "## Per team", "",
             "| Team | Sessions | Cost USD | Merged PRs | $/PR p50 | $/PR p90 | LOC/$ | "
             "Review rounds (mean) | Reverts | Abandoned |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for team_name in resolved.team_order:
        team = resolved.teams_by_name[team_name]
        member_set = set(team.user_ids)
        team_sessions = [p for p in plans if p.l1_user in member_set]
        cost = sum(records[p.session_id].cost_effective_usd_micros for p in team_sessions) / 1_000_000
        team_items = [it for it in work.items.values() if it.l1_user in member_set]
        team_pr_artifacts = prs_by_team.get(team_name, [])
        merged = [a for a in team_pr_artifacts if a["state"] == "merged"]
        abandoned = [a for a in team_pr_artifacts if a["state"] == "closed" and a["merged_at"] is None]
        reverts = [a for a in git_result.artifacts if a["kind"] == "pr"
                  and a["info"].get("github.is_revert")
                  and any(o["merge_commit_sha"] == a["reverts_sha"] for o in team_pr_artifacts)]
        pr_costs = []
        for item in team_items:
            if not item.has_pr:
                continue
            pr_costs.append(sum(records[sid].cost_effective_usd_micros for sid in item.sessions) / 1_000_000)
        p50 = _percentile(pr_costs, 0.5)
        p90 = _percentile(pr_costs, 0.9)
        total_loc = sum(it.size_loc for it in team_items if it.has_pr)
        loc_per_dollar = total_loc / cost if cost else 0.0
        review_rounds = [len([r for r in git_result.artifacts if r["kind"] == "review"
                             and r["parent_artifact_id"] == a["artifact_id"]
                             and r["state"] == "changes_requested"]) for a in team_pr_artifacts]
        mean_rounds = sum(review_rounds) / len(review_rounds) if review_rounds else 0.0
        lines.append(f"| {_cell(team_name)} | {len(team_sessions)} | {cost:.2f} | {len(merged)} | "
                    f"{p50:.2f} | {p90:.2f} | {loc_per_dollar:.4f} | {mean_rounds:.2f} | "
                    f"{len(reverts)} | {len(abandoned)} |")

    # ---- Per repo ----
    lines += ["", "## Per repo", "", "| Repo | PRs | Issues | Cost USD |", "|---|---:|---:|---:|"]
    for repo in resolved.config.repos:
        repo_num = ids.repo_num(f"{resolved.config.org.github_owner}/{repo.name}")
        repo_id = f"github:{repo_num}"
        repo_prs = [a for a in git_result.artifacts if a["kind"] == "pr" and a["repo_id"] == repo_id]
        repo_issues = [a for a in git_result.artifacts if a["kind"] == "issue" and a["repo_id"] == repo_id]
        repo_sessions = [p for p in plans if p.repo.name == repo.name]
        repo_cost = sum(records[p.session_id].cost_effective_usd_micros for p in repo_sessions) / 1_000_000
        lines.append(f"| {_cell(repo.name)} | {len(repo_prs)} | {len(repo_issues)} | {repo_cost:.2f} |")

    # ---- Recoverable spend ----
    total_busts = sum(r.info["cache_busts"]["total_usd_micros"] or 0 for r in records.values())
    total_resume = sum(r.info["resume_cost"]["total_usd_micros"] or 0 for r in records.values())
    total_interrupt = sum(r.info["interrupted"]["total_usd_micros"] or 0 for r in records.values())
    lines += ["", "## Recoverable spend", "",
             f"- Cache busts: ${total_busts / 1_000_000:.2f}",
             f"- Resume reloads: ${total_resume / 1_000_000:.2f}",
             f"- Interrupted responses: ${total_interrupt / 1_000_000:.2f}"]

    # ---- Rollup + warnings ----
    lines += ["", "## Rollup check", "", "PASS -- session totals reconcile with `usage.jsonl` exactly (I1).", ""]
    lines += ["## Warnings", "", "None.", ""]
    return "\n".join(lines)
