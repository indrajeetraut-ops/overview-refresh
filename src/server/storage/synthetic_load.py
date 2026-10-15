"""`load_dataset(dataset_dir, dsn, *, replace, dry_run, log) -> LoadResult` (plan §5 P10): the
mainline-owned loader for an `org-expand` dataset into a dedicated `*_synth` database.

Reads the dataset's JSONL files as plain dicts, never by importing
`forecast.synthetic.org.*` types -- the dependency direction stays one-way (the forecast
package already depends on mainline via its PYTHONPATH; mainline does not depend back on it).
The record field names were deliberately designed (plan §2) to already match the ORM column
names, so almost every table upserts generically through `_upsert_table`.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session as DbSession

from server.api.manager.budgets import put_budget
from server.directory.config import load_config as load_directory_config
from server.directory.models import DirectoryRecord, TeamRecord
from server.directory.sync import sync as directory_sync
from server.storage import session_scalars
from server.worker.pr_class.config import load_config as load_pr_class_config
from server.worker.pr_class.pr_classify import pr_classify

from .dbm import operations as dbm_operations
from .config import StorageConfig
from .engine import dispose_engine, get_engine, session_scope
from .forecast_load import ForecastLoadError, ParsedExport, check_against_dataset, parse_export, replace_run
from .queries import QueryCatalog
from .schema import (
    GitArtifact, GitArtifactEvent, GitInstallation, GitRepo, PrIssueLink, Repository,
    Session as SessionModel, SessionGitLink, SessionInfo,
)

log = structlog.get_logger(module="storage.synthetic_load")

_BATCH_SIZE = 1000


class SyntheticLoadError(Exception):
    """Guard, manifest, or config failures (exit 2)."""


class RollupMismatchError(Exception):
    """The post-load I1 rollup check disagreed with `expected_rollup.jsonl` (exit 1)."""


@dataclass
class LoadResult:
    row_counts: dict[str, int]
    admin_viewer_id: str | None
    sample_lead_viewer_id: str | None
    sample_dev_viewer_id: str | None
    rollup_ok: bool
    duration_ms: float
    dry_run: bool = False
    forecast_run_id: str | None = None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _normalize_segment_turns(session_info_row: dict) -> None:
    """Mutates `row["info"]["segments"]` in place so every segment's `turns` is a count, not the
    list of turn indices the generator used to write (api-latency plan D8/Phase 0). Every SQL
    and Python reader already treats `turns` as "how many", so this lets an already-generated
    dataset load sensibly without needing to be regenerated."""
    for seg in (session_info_row.get("info") or {}).get("segments") or []:
        if isinstance(seg.get("turns"), list):
            seg["turns"] = len(seg["turns"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _guard_dsn(dsn: str) -> None:
    """D13: refuse any database whose name does not end with `_synth`, before ever connecting."""
    try:
        url = make_url(dsn)
    except Exception as exc:
        raise SyntheticLoadError(f"cannot parse dsn: {exc}") from exc
    db = url.database
    if not db or not db.endswith("_synth"):
        raise SyntheticLoadError(
            f"refusing to load into database {db!r}: the name must end with '_synth' (D13); "
            "there is no override")


def _expected_rollup_tuples(rows) -> Counter:
    """`expected_rollup.jsonl` rows -> multiset of per-type tuples. The database has one
    `cache_write`, so the file's 5m and 1h writes are summed."""
    return Counter(
        (r["date"], r["user_id"], r["repo_id"], r["agent"], r["model"],
         r["input"], r["cache_write_5m"] + r["cache_write_1h"], r["cache_read"], r["output"])
        for r in rows)


def _actual_rollup_tuples(rows) -> Counter:
    """`daily_rollup` result rows -> the same tuple shape as `_expected_rollup_tuples`."""
    return Counter(
        (row.date.isoformat(), row.user_id, row.repo_id, row.agent, row.model,
         int(row.input), int(row.cache_write), int(row.cache_read), int(row.output))
        for row in rows)


def _verify_manifest(dataset_dir: Path) -> dict:
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SyntheticLoadError(f"manifest.json not found in {dataset_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SyntheticLoadError(f"cannot read manifest.json: {exc}") from exc
    if manifest.get("schema_version") == 1:
        raise SyntheticLoadError("dataset uses schema 1; regenerate with this version")
    if manifest.get("schema_version") != 2:
        raise SyntheticLoadError(
            f"manifest.schema_version must be 2, got {manifest.get('schema_version')!r}")
    if manifest.get("operation") != "org":
        raise SyntheticLoadError(
            f"manifest.operation must be 'org', got {manifest.get('operation')!r}")
    if manifest.get("status") != "complete":
        raise SyntheticLoadError(
            f"manifest.status must be 'complete', got {manifest.get('status')!r}")
    files = manifest.get("files") or {}
    for name, meta in files.items():
        path = dataset_dir / name
        if not path.is_file():
            raise SyntheticLoadError(f"manifest lists {name!r} but it is missing from {dataset_dir}")
        actual = _sha256(path)
        expected = meta.get("sha256") if isinstance(meta, dict) else None
        if actual != expected:
            raise SyntheticLoadError(f"sha256 mismatch for {name!r}: manifest tampered or corrupt")
    return manifest


def _check_org_id(manifest: dict, directory_config_path: str) -> None:
    directory_cfg = load_directory_config(directory_config_path)
    dataset_org_id = manifest.get("directory_org_id")
    if dataset_org_id != directory_cfg.default_org_id:
        raise SyntheticLoadError(
            f"dataset directory_org_id {dataset_org_id!r} != "
            f"{directory_config_path}'s default_org_id {directory_cfg.default_org_id!r}")


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _batched(rows: list[dict], size: int = _BATCH_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def _upsert_table(s: DbSession, model, pk_cols: list[str], rows: list[dict], *,
                  dt_fields: tuple[str, ...] = (), decimal_fields: tuple[str, ...] = ()) -> int:
    if not rows:
        return 0
    columns = {c.name for c in model.__table__.columns}
    total = 0
    for batch in _batched(rows):
        values = []
        for row in batch:
            row = {k: v for k, v in row.items() if k in columns}
            for f in dt_fields:
                if f in row:
                    row[f] = _parse_dt(row[f])
            for f in decimal_fields:
                if row.get(f) is not None:
                    row[f] = Decimal(str(row[f]))
            values.append(row)
        non_pk = [c for c in values[0] if c not in pk_cols]
        stmt = pg_insert(model.__table__).values(values)
        if non_pk:
            stmt = stmt.on_conflict_do_update(
                index_elements=pk_cols, set_={c: getattr(stmt.excluded, c) for c in non_pk})
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
        s.execute(stmt)
        total += len(batch)
    return total


def _directory_people(dataset_dir: Path) -> list[dict]:
    return _read_jsonl(dataset_dir / "directory_people.jsonl")


def _viewer_ids(dataset_dir: Path) -> tuple[str | None, str | None, str | None]:
    people = _directory_people(dataset_dir)
    admin_id = lead_id = dev_id = None
    for person in sorted(people, key=lambda p: next(
            (k["external_id"] for k in p["external_keys"] if k["provider"] == "github"), "")):
        login = next((k["external_id"] for k in person["external_keys"] if k["provider"] == "github"), None)
        if not login:
            continue
        user_id = f"github:{login}"
        roles = person.get("roles") or []
        if admin_id is None and any(r["role"] == "admin" for r in roles):
            admin_id = user_id
        for team in person.get("teams") or []:
            if team.get("role_in_team") == "lead" and lead_id is None:
                lead_id = user_id
            elif team.get("role_in_team") == "member" and dev_id is None:
                dev_id = user_id
    return admin_id, lead_id, dev_id


def _load_forecast_export(path: Path, manifest: dict, dataset_dir: Path) -> ParsedExport:
    """Parse an export and check it belongs to this dataset (its org scope is the manifest's
    `directory_org_id`, every team scope a directory team, every repo scope a repository), all
    before any database work. Failures are `SyntheticLoadError`s (exit 2)."""
    try:
        parsed = parse_export(path)
        team_ids = {r["team_id"] for r in _read_jsonl(dataset_dir / "directory_teams.jsonl")}
        repo_ids = {r["repo_id"] for r in _read_jsonl(dataset_dir / "repositories.jsonl")}
        check_against_dataset(parsed, org_id=manifest.get("directory_org_id"),
                              team_ids=team_ids, repo_ids=repo_ids)
    except ForecastLoadError as exc:
        raise SyntheticLoadError(f"forecast export: {exc}") from exc
    return parsed


def load_dataset(dataset_dir: Path, dsn: str, *, replace: bool, dry_run: bool,
                 log: Any = None, directory_config_path: str = "config/directory.yaml",
                 pr_class_config_path: str = "config/pr_class.yaml",
                 queries_dir: str = "config/queries",
                 forecast_export: Path | None = None) -> LoadResult:
    log = log or structlog.get_logger(module="storage.synthetic_load")
    started = time.monotonic()
    dataset_dir = Path(dataset_dir)

    # Step 1: guard (D13) -- before anything else, including reading the dataset.
    _guard_dsn(dsn)
    log.info("synthetic_load.guard", database=make_url(dsn).database)

    # Step 2: manifest.
    manifest = _verify_manifest(dataset_dir)
    log.info("synthetic_load.manifest", files=len(manifest.get("files") or {}))

    # Step 3: org-id check.
    _check_org_id(manifest, directory_config_path)
    log.info("synthetic_load.org_id", directory_org_id=manifest.get("directory_org_id"))

    # Step 3b: the forecast export, if any -- parsed and checked against this dataset now, so a
    # bad or foreign export fails before any database work (and in a dry run too).
    parsed_forecast = None
    if forecast_export is not None:
        parsed_forecast = _load_forecast_export(Path(forecast_export), manifest, dataset_dir)
        log.info("synthetic_load.forecast_export", run_id=parsed_forecast.run_id,
                 scopes=parsed_forecast.scope_count, presets=parsed_forecast.preset_count,
                 scope_series_rows=len(parsed_forecast.series))

    file_names = [
        "directory_teams.jsonl", "directory_people.jsonl", "user_map.jsonl", "user_prefs.jsonl",
        "git_installations.jsonl", "git_repos.jsonl", "repositories.jsonl", "git_artifacts.jsonl",
        "git_artifact_events.jsonl", "pr_issue_links.jsonl", "sessions.jsonl", "session_info.jsonl",
        "session_git_links.jsonl", "pr_rework.jsonl", "budgets.jsonl", "expected_rollup.jsonl",
    ]

    # Step 4: dry-run -- planned row counts, never touching the database.
    if dry_run:
        row_counts = {name: len(_read_jsonl(dataset_dir / name)) for name in file_names}
        if parsed_forecast is not None:
            # Table-name keys, unlike the file-name keys above: the export is not a dataset file.
            row_counts["forecast_runs"] = 1
            row_counts["forecast_scope_series"] = len(parsed_forecast.series)
        log.info("synthetic_load.dry_run", row_counts=row_counts)
        return LoadResult(row_counts=row_counts, admin_viewer_id=None, sample_lead_viewer_id=None,
                          sample_dev_viewer_id=None, rollup_ok=False,
                          duration_ms=(time.monotonic() - started) * 1000, dry_run=True,
                          forecast_run_id=parsed_forecast.run_id if parsed_forecast else None)

    cfg = StorageConfig(dsn=dsn, queries_dir=queries_dir)
    catalog = QueryCatalog.load(queries_dir)

    # Step 5: migrate.
    dbm_operations.migrate(cfg)
    log.info("synthetic_load.migrate")

    engine = get_engine(cfg)
    try:
        with session_scope() as s:
            # Step 6: --replace.
            if replace:
                s.execute(catalog.get("synthetic", "truncate_all"))
                log.info("synthetic_load.replace")

            # Step 7: directory.
            directory_teams = _read_jsonl(dataset_dir / "directory_teams.jsonl")
            directory_people = _read_jsonl(dataset_dir / "directory_people.jsonl")
            user_prefs = _read_jsonl(dataset_dir / "user_prefs.jsonl")
            user_map = _read_jsonl(dataset_dir / "user_map.jsonl")

            directory_sync(
                s, catalog, [DirectoryRecord.from_dict(r) for r in directory_people],
                teams=[TeamRecord.from_dict(t) for t in directory_teams])
            set_user_prefs = catalog.get("synthetic", "set_user_prefs")
            for row in user_prefs:
                s.execute(set_user_prefs, {"github_user": row["github_user"],
                                          "timezone": row["timezone"],
                                          "primary_team_id": row["primary_team_id"],
                                          "user_id": row["user_id"]})
            existing = {r[0] for r in s.execute(text("SELECT user_id FROM users")).all()}
            missing = [row["user_id"] for row in user_map if row["user_id"] not in existing]
            if missing:
                raise RuntimeError(
                    f"D5 broke: {len(missing)} user_map ids not found in users after sync "
                    f"(first: {missing[:5]})")
            log.info("synthetic_load.directory", teams=len(directory_teams),
                    people=len(directory_people))

            # Step 8: remaining tables, in order.
            git_installations = _read_jsonl(dataset_dir / "git_installations.jsonl")
            _upsert_table(s, GitInstallation, ["installation_id"], git_installations,
                         dt_fields=("installed_at", "updated_at"))

            git_repos = _read_jsonl(dataset_dir / "git_repos.jsonl")
            _upsert_table(s, GitRepo, ["repo_id"], git_repos,
                         dt_fields=("backfill_cursor", "first_seen_at", "updated_at"))

            repositories = _read_jsonl(dataset_dir / "repositories.jsonl")
            _upsert_table(s, Repository, ["repo_id"], repositories,
                         dt_fields=("first_seen_at", "last_seen_at"))

            git_artifacts = _read_jsonl(dataset_dir / "git_artifacts.jsonl")
            _upsert_table(s, GitArtifact, ["artifact_id"], git_artifacts,
                         dt_fields=("created_at", "updated_at", "closed_at", "merged_at"))

            pr_issue_links = _read_jsonl(dataset_dir / "pr_issue_links.jsonl")
            _upsert_table(s, PrIssueLink, ["pr_artifact_id", "tracker", "project_key", "number"],
                         pr_issue_links, dt_fields=("created_at",), decimal_fields=("confidence",))

            sessions_rows = _read_jsonl(dataset_dir / "sessions.jsonl")
            _upsert_table(s, SessionModel, ["session_id"], sessions_rows,
                         dt_fields=("started_at", "last_event_at", "ended_at"))

            session_info_rows = _read_jsonl(dataset_dir / "session_info.jsonl")
            started_by_session = {r["session_id"]: r["started_at"] for r in sessions_rows}
            for row in session_info_rows:
                # session_info carries no timing of its own (plan §5 P10 step 8's "set
                # created_at/updated_at ... to the record's own started_at").
                row["created_at"] = started_by_session.get(row["session_id"])
                row["updated_at"] = started_by_session.get(row["session_id"])
                _normalize_segment_turns(row)
                # api-latency plan Phase E: the same promoted scalars the real writers derive,
                # so a synthetic dataset's rows carry them too. _upsert_table keeps only keys
                # that are table columns, so these flow straight through.
                row.update(session_scalars.fold_scalars(row.get("info"), row.get("files")))
                row["otel_vendor_cost_usd_micros"] = session_scalars.otel_vendor_cost(row.get("otel"))
            _upsert_table(s, SessionInfo, ["session_id"], session_info_rows,
                         dt_fields=("created_at", "updated_at"), decimal_fields=("cost_usd",))

            session_git_links = _read_jsonl(dataset_dir / "session_git_links.jsonl")
            _upsert_table(s, SessionGitLink, ["session_id", "artifact_id"], session_git_links,
                         dt_fields=("created_at",), decimal_fields=("confidence",))

            events_rows = _read_jsonl(dataset_dir / "git_artifact_events.jsonl")
            events_inserted = 0
            for batch in _batched(events_rows):
                values = []
                for row in batch:
                    row = dict(row)
                    row.pop("event_id", None)
                    row["occurred_at"] = _parse_dt(row["occurred_at"])
                    row["recorded_at"] = _parse_dt(row["recorded_at"])
                    values.append(row)
                stmt = pg_insert(GitArtifactEvent.__table__).values(values).on_conflict_do_nothing(
                    index_elements=["source_key"])
                s.execute(stmt)
                events_inserted += len(batch)
            log.info("synthetic_load.tables", installations=len(git_installations),
                    repos=len(git_repos), repositories=len(repositories),
                    artifacts=len(git_artifacts), links=len(pr_issue_links),
                    sessions=len(sessions_rows), session_info=len(session_info_rows),
                    session_git_links=len(session_git_links), events=events_inserted)

            # Step 9: classification.
            pr_class_cfg = load_pr_class_config(pr_class_config_path)
            pr_rows = [a for a in git_artifacts if a["kind"] == "pr"]
            for row in pr_rows:
                pr_classify(s, catalog, pr_class_cfg, {"artifact_id": row["artifact_id"]})
            set_rework_ratio = catalog.get("synthetic", "set_rework_ratio")
            pr_rework_rows = _read_jsonl(dataset_dir / "pr_rework.jsonl")
            for row in pr_rework_rows:
                s.execute(set_rework_ratio, {"rework_ratio": row["rework_ratio"],
                                            "artifact_id": row["artifact_id"]})
            log.info("synthetic_load.classification", prs=len(pr_rows),
                    rework_rows=len(pr_rework_rows))

            # Step 10: budgets.
            budgets_rows = _read_jsonl(dataset_dir / "budgets.jsonl")
            for row in budgets_rows:
                put_budget(s, catalog, row["scope_type"], row["scope_id"], row["period"],
                          Decimal(str(row["amount_usd"])))
            log.info("synthetic_load.budgets", budgets=len(budgets_rows))
            # Token allowances were removed (cost-based seats plan D3): seats derive from team
            # budgets. Datasets generated before that still ship allowances.jsonl (normally
            # empty) and must keep loading -- the file is read only to say it was ignored.
            ignored_allowances = _read_jsonl(dataset_dir / "allowances.jsonl")
            if ignored_allowances:
                log.warning("synthetic_load.allowances_ignored", rows=len(ignored_allowances))

            # Step 10b: the forecast run (same transaction: a failed rollup check below rolls it
            # back with everything else). Without an export, existing forecast rows are left
            # alone unless --replace truncated them.
            if parsed_forecast is not None:
                replace_run(s, catalog, parsed_forecast, log=log)

            # Step 11: post-load rollup check.
            series_config_path = dataset_dir / "series" / "config.yaml"
            series_raw = yaml.safe_load(series_config_path.read_text(encoding="utf-8"))
            tz = series_raw["series"]["timezone"]
            expected_rollup_rows = _read_jsonl(dataset_dir / "expected_rollup.jsonl")
            expected_tuples = _expected_rollup_tuples(expected_rollup_rows)
            actual_rows = s.execute(catalog.get("synthetic", "daily_rollup"), {"tz": tz}).all()
            actual_tuples = _actual_rollup_tuples(actual_rows)
            if actual_tuples != expected_tuples:
                diffs = []
                all_keys = set(expected_tuples) | set(actual_tuples)
                for key in sorted(all_keys):
                    if expected_tuples.get(key) != actual_tuples.get(key):
                        diffs.append({"key": key, "expected": expected_tuples.get(key, 0),
                                     "actual": actual_tuples.get(key, 0)})
                    if len(diffs) >= 20:
                        break
                log.error("synthetic_load.rollup_mismatch", diff_count=len(diffs), diffs=diffs)
                raise RollupMismatchError(
                    f"post-load rollup check failed: {len(diffs)}+ differing keys (first 20 shown)")
            log.info("synthetic_load.rollup_ok", rows=len(expected_tuples))

            row_counts_result = {r[0]: r[1] for r in s.execute(catalog.get("synthetic", "row_counts")).all()}
            admin_id, lead_id, dev_id = _viewer_ids(dataset_dir)

        return LoadResult(row_counts=row_counts_result, admin_viewer_id=admin_id,
                          sample_lead_viewer_id=lead_id, sample_dev_viewer_id=dev_id,
                          rollup_ok=True, duration_ms=(time.monotonic() - started) * 1000,
                          forecast_run_id=parsed_forecast.run_id if parsed_forecast else None)
    finally:
        dispose_engine()
