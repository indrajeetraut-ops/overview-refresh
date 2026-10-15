"""Independent generation operations with streaming persistence and stage reports."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import yaml
from common.msging.log import get_logger
from forecast.artifact_io import DatasetWriter, read_records, sha256
from forecast.curve.artifacts import load_curve_artifact, resolve_curve_ref
from forecast.curve.errors import CurveError
from forecast.curve.model import evaluate_curve
from .config import RosterConfig, SeriesConfig, UsersConfig, validate_series_inputs
from .errors import ArtifactError, InputDataError
from .models import GenerationResult, PersonalSpikeEvent, SpikeEvent, TeamRecord, UserRecord
from .profiles import assign_profiles
from .reporting import render_report
from .roster import create_roster
from .series import CurveLevels, generate_days
from .users import create_users

GENERATOR_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _operation(operation, run_id, log):
    state = {"operation": operation, "run_id": run_id or str(uuid.uuid4()),
             "stage": "validation", "created_at": _now(), "started": perf_counter()}
    state["log"] = (log or get_logger(__name__)).bind(
        module=__name__, operation=operation, run_id=state["run_id"])
    try:
        yield state
    except Exception as exc:
        exc.stage = state["stage"]
        exc.run_id = state["run_id"]
        if not hasattr(exc, "cleanup_outcome"):
            exc.cleanup_outcome = "not-created"
        raise


def _stage_log(state, module):
    return state["log"].bind(module=module)


def _force_clear_output(path: Path, *, protected: tuple[Path, ...] = (), log=None) -> None:
    """Remove an existing output path before a forced recreate.

    Refuses (raising `ArtifactError`, the same type the non-force path raises) unless
    `path` resolves to a plain directory that is not `/`, the caller's home directory,
    the current working directory, and is neither equal to nor an ancestor of any path
    in `protected` (this operation's own input files/directories). Deletion is confined
    to exactly the resolved directory; nothing outside it is ever touched.
    """
    if path.is_symlink():
        raise ArtifactError(f"output path is a symlink; refusing --force: {path}")
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise ArtifactError(f"output path exists and is not a directory; refusing --force: {resolved}")
    guarded = {Path("/"), Path.home().resolve(), Path.cwd().resolve()}
    if resolved in guarded:
        raise ArtifactError(f"refusing to --force a protected system path: {resolved}")
    for raw in protected:
        try:
            guard = Path(raw).resolve()
        except OSError:
            continue
        if resolved == guard or resolved in guard.parents:
            raise ArtifactError(
                f"refusing to --force an output path that is, or contains, a required input: {resolved}")
    if log:
        log.info("synthetic.force_recreate", output_dir=str(resolved))
    shutil.rmtree(resolved)


def _check_output(path: Path, *, force: bool = False, protected: tuple[Path, ...] = (), log=None) -> Path:
    path = Path(path).absolute()
    # lexists also rejects a dangling symlink; it must never be reused as output.
    if path.exists() or path.is_symlink():
        if not force:
            raise ArtifactError("output path already exists; choose a new directory")
        _force_clear_output(path, protected=protected, log=log)
    return path


def _snapshot(writer, source, label, record_type, expected, schema_version: int = 1):
    relative = f"inputs/{label}/{source.name}"
    writer.snapshot(source, relative)
    writer.snapshot(source.parent / "manifest.json", f"inputs/{label}/manifest.json")
    if read_records(writer.output_dir / relative, record_type, schema_version) != expected:
        raise InputDataError("input changed between validation and snapshot")
    return {"original": str(source), "sha256": sha256(writer.output_dir / relative), "snapshot": relative}


def _snapshot_curve(writer, artifact_dir: Path, name: str, curve_id: str) -> dict:
    """Snapshot a bound curve artifact's config+manifest; verify no TOCTOU tampering.

    Sibling to `_snapshot` rather than a reuse of it: `_snapshot`'s post-copy check calls
    `read_records` (JSONL row validation), which cannot validate curve's single-document
    `config.yaml`. This verifies the copy the same way `load_curve_artifact` already did:
    the copied bytes must still hash to the artifact's own recorded `config_sha256`.
    """
    relative = f"inputs/curves/{name}/{curve_id}"
    writer.snapshot(artifact_dir / "config.yaml", f"{relative}/config.yaml")
    writer.snapshot(artifact_dir / "manifest.json", f"{relative}/manifest.json")
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    copied_sha256 = sha256(writer.output_dir / f"{relative}/config.yaml")
    if copied_sha256 != manifest.get("config_sha256"):
        raise InputDataError(f"curve input changed between validation and snapshot: {name}:{curve_id}")
    return {"name": name, "id": curve_id, "config_sha256": copied_sha256, "snapshot": relative}


def _resolve_curve_levels(writer, config: SeriesConfig) -> tuple[CurveLevels, dict]:
    """Precompute per-offset curve levels for bound org/team/agent targets and snapshot them.

    Returns `(CurveLevels, manifest_section)`; `manifest_section` is `None` when `curve` is
    unset. Errors from the curve module are re-raised as `InputDataError` (the boundary
    `validate_series_inputs` already established) so the CLI's exit-code dispatch is
    unaffected by which module actually detected the problem.
    """
    if config.curve is None:
        return CurveLevels(), None
    curve = config.curve
    days = config.series.days
    bound = {"org": None, "teams": {}, "agents": {}}

    def _load(ref: str):
        try:
            artifact_dir = resolve_curve_ref(curve.artifacts, ref)
            artifact = load_curve_artifact(artifact_dir)
        except CurveError as exc:
            raise InputDataError(f"curve binding {ref!r} is invalid: {exc}") from exc
        levels = evaluate_curve(artifact.anchors, days)
        snapshot = _snapshot_curve(writer, artifact_dir, artifact.name, artifact.curve_id)
        return levels, snapshot

    org_levels = None
    if curve.org is not None:
        org_levels, snapshot = _load(curve.org)
        bound["org"] = snapshot
    team_levels = {}
    for team_name, ref in curve.teams.items():
        team_levels[team_name], snapshot = _load(ref)
        bound["teams"][team_name] = snapshot
    agent_levels = {}
    for agent_name, ref in curve.agents.items():
        agent_levels[agent_name], snapshot = _load(ref)
        bound["agents"][agent_name] = snapshot
    levels = CurveLevels(org_levels=org_levels, team_levels=team_levels, agent_levels=agent_levels)
    # A present-but-empty `curve:` block (no org/team/agent bound) is the same no-op as an
    # absent one; keep the manifest free of a vacuous `curve` section in that case too.
    if bound["org"] is None and not bound["teams"] and not bound["agents"]:
        return levels, None
    return levels, bound


def _finish(writer, state, config, org_id, summary, details, sources):
    """Report and manifest share counters; publish the completion marker last."""
    state["stage"] = "report"
    config_bytes = yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True).encode()
    writer.write_bytes("config.yaml", config_bytes)
    result = GenerationResult(
        operation=state["operation"], output_dir=writer.output_dir.resolve(), run_id=state["run_id"],
        files=[*writer.files, "config.yaml", "report.md", "manifest.json"],
        record_counts={key: value["rows"] for key, value in writer.files.items()},
        summary_counts=dict(summary), report_path=(writer.output_dir / "report.md").resolve())
    details.update(completed_at=_now(), created_at=state["created_at"], org_id=org_id,
                   config="config.yaml", input_sources=sources)
    writer.write_bytes("report.md", render_report(result, details).encode())
    state["stage"] = "manifest"
    manifest = {
        "schema_version": 2 if state["operation"] == "series" else 1,
        "generator_version": GENERATOR_VERSION,
        "operation": state["operation"], "status": "complete", "run_id": state["run_id"],
        "created_at": state["created_at"], "org_id": org_id,
        "versions": {"python": platform.python_version(), "faker": version("Faker"),
                     "pydantic": version("pydantic"), "pyyaml": version("PyYAML")},
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(), "files": writer.files,
        "report": {"path": "report.md", "sha256": sha256(writer.output_dir / "report.md")},
        "input_sources": sources, "summary": dict(summary),
    }
    for field in ("start_date", "end_date", "timezone", "curve"):
        if field in details:
            manifest[field] = str(details[field]) if field != "curve" else details[field]
    writer.manifest(manifest)
    return result


def _completed(state, result):
    state["log"].info("synthetic.run_completed", output_dir=str(result.output_dir),
                      counts=result.summary_counts, duration_ms=round((perf_counter() - state["started"]) * 1000, 3))
    return result


def run_users(config: UsersConfig, output_dir: Path, *, log=None, run_id=None, force=False) -> GenerationResult:
    with _operation("users", run_id, log) as state:
        out = _check_output(output_dir, force=force, log=state["log"])
        state["stage"] = "users"
        rows = create_users(config, log=_stage_log(state, "forecast.synthetic.users"))
        summary = {"users": len(rows)}
        state["stage"] = "artifacts"
        with DatasetWriter(out, "users", log=_stage_log(state, "forecast.artifact_io")) as writer:
            writer.write_records("users.jsonl", rows)
            details = {"locale": config.users.locale, "seeds": {"identity": config.users.seed},
                       "requested_users": config.users.count}
            result = _finish(writer, state, config, config.users.org_domain, summary, details, {})
        return _completed(state, result)


def run_roster(config: RosterConfig, output_dir: Path, *, log=None, run_id=None, force=False) -> GenerationResult:
    with _operation("roster", run_id, log) as state:
        out = _check_output(output_dir, force=force, protected=(config.roster.users_file,), log=state["log"])
        users = read_records(config.roster.users_file, UserRecord)
        teams = create_roster(config, users, log=_stage_log(state, "forecast.synthetic.roster"))
        assigned = sum(len(team.user_ids) for team in teams)
        summary = {"users": len(users), "teams": len(teams), "rostered_users": assigned,
                   "unassigned_users": len(users) - assigned}
        state["stage"] = "artifacts"
        with DatasetWriter(out, "roster", log=_stage_log(state, "forecast.artifact_io")) as writer:
            writer.write_records("teams.jsonl", teams)
            sources = {"users": _snapshot(writer, config.roster.users_file, "users", UserRecord, users)}
            saved = config.model_copy(deep=True)
            saved.roster.users_file = Path(sources["users"]["snapshot"])
            details = {"seeds": {"roster": config.roster.seed}, "teams": [
                {"name": team.team_name, "id": team.team_id,
                 "source": "generated", "count": len(team.user_ids)}
                for team in teams]}
            result = _finish(writer, state, saved, teams[0].org_id, summary, details, sources)
        return _completed(state, result)


def run_series(config: SeriesConfig, output_dir: Path, *, log=None, run_id=None, force=False) -> GenerationResult:
    with _operation("series", run_id, log) as state:
        protected = [config.series.users_file, config.series.roster_file]
        if config.curve is not None:
            protected.append(config.curve.artifacts)
        out = _check_output(output_dir, force=force, protected=tuple(protected), log=state["log"])
        users = read_records(config.series.users_file, UserRecord)
        teams = read_records(config.series.roster_file, TeamRecord)
        validate_series_inputs(config, users, teams)
        assignments = assign_profiles(config, teams, log=_stage_log(state, "forecast.synthetic.profiles"))
        rostered = sum(len(team.user_ids) for team in teams)
        summary = Counter({"users": len(users), "teams": len(teams), "rostered_users": rostered,
                           "unassigned_users": len(users) - rostered, "dates": config.series.days,
                           "active_user_days": 0, "inactive_user_days": 0, "zero_volume_user_days": 0})
        totals = Counter({"input": 0, "cache_write_5m": 0, "cache_write_1h": 0,
                          "cache_read": 0, "output": 0})
        event_counts = Counter()
        spike_days = set()
        spike_rules = defaultdict(Counter)
        # Personal spikes are reported only when some profile opts in, so a config without
        # them keeps its report and manifest summary byte-identical.
        personal_enabled = [name for name, profile in sorted(config.profiles.items())
                            if profile.has_personal_spikes]
        personal_spikes = {name: Counter() for name in personal_enabled}
        breakdowns = {key: defaultdict(Counter) for key in ("team", "project", "agent_model")}
        # Include zero-consumption dimensions so all-zero reports remain informative.
        for team in teams:
            breakdowns["team"][team.team_id].update(totals)
        for project in config.projects:
            breakdowns["project"][project.name].update(totals)
        project_names = {str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"forecast:project:{teams[0].org_id}:{p.name.casefold()}")): p.name
            for p in config.projects}
        for agent, options in config.agents.items():
            for model in options.models:
                breakdowns["agent_model"][f"{agent}/{model}"].update(totals)
        state["stage"] = "artifacts"
        with DatasetWriter(out, "series", log=_stage_log(state, "forecast.artifact_io")) as writer:
            sources = {
                "users": _snapshot(writer, config.series.users_file, "users", UserRecord, users),
                "roster": _snapshot(writer, config.series.roster_file, "roster", TeamRecord, teams)}
            writer.write_records("user_profiles.jsonl", assignments)
            curve_levels, curve_manifest = _resolve_curve_levels(writer, config)
            state["stage"] = "series"
            with (writer.open_records("usage.jsonl") as usage,
                  writer.open_records("coverage.jsonl") as coverage,
                  writer.open_records("generation_events.jsonl") as events):
                for batch in generate_days(config, users, teams, assignments,
                                           log=_stage_log(state, "forecast.synthetic.series"),
                                           curve_levels=curve_levels):
                    usage.extend(batch.usage)
                    coverage.extend(batch.coverage)
                    events.extend(batch.events)
                    summary.update(active_user_days=batch.active_users, inactive_user_days=batch.inactive_users,
                                   zero_volume_user_days=batch.zero_volume_users)
                    for event in batch.events:
                        event_counts[event.kind] += 1
                        if isinstance(event, PersonalSpikeEvent):
                            personal_spikes[event.profile].update(
                                events=1,
                                baseline_processed_input=event.baseline_processed_input,
                                incremental_processed_input=event.incremental_processed_input,
                            )
                        if isinstance(event, SpikeEvent):
                            spike_days.add(event.date)
                            spike_rules[event.rule_id].update(
                                applied_days=1,
                                baseline_processed_input=event.baseline_processed_input,
                                incremental_processed_input=event.incremental_processed_input,
                            )
                    for row in batch.usage:
                        values = row.tokens.model_dump()
                        totals.update(values)
                        for key, label in (("team", row.team_id), ("project", project_names[row.project_id]),
                                           ("agent_model", f"{row.agent_type}/{row.model}")):
                            breakdowns[key][label].update(values)
            summary.update(usage_rows=usage.rows, coverage_rows=coverage.rows, event_rows=events.rows,
                           forced_zero_events=event_counts["forced_zero"],
                           spike_events=event_counts["spike"], spike_days=len(spike_days),
                           spike_incremental_processed_input=sum(
                               counts["incremental_processed_input"] for counts in spike_rules.values()),
                           input_tokens=totals["input"],
                           cache_write_5m_tokens=totals["cache_write_5m"],
                           cache_write_1h_tokens=totals["cache_write_1h"],
                           cache_read_tokens=totals["cache_read"], output_tokens=totals["output"])
            if personal_enabled:
                summary.update(
                    personal_spike_events=event_counts["personal_spike"],
                    personal_spike_incremental_processed_input=sum(
                        counts["incremental_processed_input"] for counts in personal_spikes.values()))
            expected_coverage = rostered * config.series.days * len(config.agents)
            if coverage.rows != expected_coverage:
                raise InputDataError("generated coverage count does not match roster/date/agent population")
            saved = config.model_copy(deep=True)
            saved.series.users_file = Path(sources["users"]["snapshot"])
            saved.series.roster_file = Path(sources["roster"]["snapshot"])
            warning = "All user-days were inactive or zero-volume." if not usage.rows else None
            if warning:
                state["log"].warning("synthetic.empty_usage", usage_rows=0, coverage_rows=coverage.rows)
            details = {
                "start_date": config.series.start_date,
                "end_date": config.series.start_date + timedelta(days=config.series.days - 1),
                "days": config.series.days, "timezone": config.series.timezone,
                "seeds": {"profile": config.series.profile_seed, "usage": config.series.usage_seed},
                "profiles": {team.team_name: dict(Counter(a.profile for a in assignments if a.team_id == team.team_id)) for team in teams},
                "totals": dict(totals), "breakdowns": breakdowns,
                "spike_rules": {rule: dict(counts) for rule, counts in sorted(spike_rules.items())},
                "warnings": warning,
                "expected_coverage": expected_coverage, "actual_coverage": coverage.rows}
            if personal_enabled:
                details["personal_spikes"] = {
                    name: {"events": counts["events"],
                           "baseline_processed_input": counts["baseline_processed_input"],
                           "incremental_processed_input": counts["incremental_processed_input"]}
                    for name, counts in personal_spikes.items()}
            if curve_manifest is not None:
                details["curve"] = curve_manifest
            result = _finish(writer, state, saved, teams[0].org_id, summary, details, sources)
        return _completed(state, result)
