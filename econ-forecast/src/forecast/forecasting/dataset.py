"""Verified streaming adapters for neutral and synthetic daily datasets."""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
import hashlib, json
from pathlib import Path
import math
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import yaml
from pydantic import ValidationError
from forecast.contracts import CoverageRecord, DailyUsageRecord
from .allocation import TOKEN_TYPES
from .errors import InputError

MAX_DATA_BYTES = 2 * 1024**3
MAX_ROWS = 5_000_000
MAX_LINE_BYTES = 1024**2
MAX_METADATA_BYTES = 16 * 1024**2


@dataclass(frozen=True)
class ScopeMeta:
    org_id: str
    scope_type: str
    scope_id: str | None
    name: str
    keys: frozenset[tuple[str | None, str]]


@dataclass
class PreparedDataset:
    start: date
    end: date
    timezone: str
    models: dict
    scopes: dict
    usage: dict
    coverage: dict
    startup: list
    hashes: dict
    source_state: dict
    user_usage: dict = field(default_factory=dict)

    def verify_unchanged(self):
        for path, expected in self.source_state.items():
            try:
                actual = (path.stat().st_size, path.stat().st_mtime_ns)
            except OSError as exc:
                raise InputError("dataset input changed during ingestion") from exc
            if actual != expected:
                raise InputError("dataset input changed during ingestion")


def _sha(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                digest.update(block)
    except OSError as exc:
        raise InputError(f"cannot read dataset file: {path.name}") from exc
    return digest.hexdigest()


def _json(path):
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise InputError(f"metadata file exceeds 16 MiB: {path.name}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except InputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot parse {path.name}") from exc
    if not isinstance(value, dict):
        raise InputError(f"{path.name} must contain an object")
    return value


def _safe(root, relative):
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise InputError("unsafe dataset-relative path")
    path = root / candidate
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise InputError(f"missing dataset file: {relative}") from exc
    if root not in resolved.parents or path.is_symlink() or not resolved.is_file():
        raise InputError(f"unsafe dataset file: {relative}")
    return resolved


def _manifest_file(root, manifest, name, rows):
    files = manifest.get("files")
    metadata = files.get(name) if isinstance(files, dict) else None
    if not isinstance(metadata, dict) or not isinstance(metadata.get("sha256"), str):
        raise InputError(f"manifest omits {name}")
    if rows and (type(metadata.get("rows")) is not int or metadata["rows"] < 0):
        raise InputError(f"manifest row count invalid for {name}")
    path = _safe(root, name)
    if _sha(path) != metadata["sha256"]:
        raise InputError(f"hash mismatch for {name}")
    return path


def _neutral(path):
    meta = _json(path)
    organizations = meta.get("organizations")
    if (
        meta.get("schema_version") != 1
        or not isinstance(organizations, list)
        or not organizations
    ):
        raise InputError("invalid neutral metadata root")
    models = {}
    scopes = {}
    startup = []
    ranges = set()
    zones = set()
    for org in organizations:
        if not isinstance(org, dict):
            raise InputError("invalid organization metadata")
        try:
            oid = org["org_id"]
            start = date.fromisoformat(org["start_date"])
            end = date.fromisoformat(org["end_date"])
            zone = org["timezone"]
            ZoneInfo(zone)
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise InputError(
                "invalid organization identity, dates, or timezone"
            ) from exc
        if not isinstance(oid, str) or not oid or end < start or oid in models:
            raise InputError("invalid or duplicate organization")
        ranges.add((start, end))
        zones.add(zone)
        catalog = {}
        for item in org.get("models", []):
            if not isinstance(item, dict):
                raise InputError("invalid model catalog")
            model = item.get("model")
            agents = item.get("agent_types")
            if (
                not isinstance(model, str)
                or not model
                or not isinstance(agents, list)
                or not agents
                or model in catalog
                or len(agents) != len(set(agents))
                or any(not isinstance(a, str) or not a for a in agents)
            ):
                raise InputError("invalid or duplicate model/agent")
            catalog[model] = frozenset(agents)
        if not catalog:
            raise InputError("empty model catalog")
        models[oid] = catalog
        org_keys = None
        team_users = defaultdict(set)
        for item in org.get("scopes", []):
            if not isinstance(item, dict):
                raise InputError("invalid scope metadata")
            typ = item.get("type")
            raw_id = item.get("id")
            sid = oid if typ == "organization" else raw_id
            raw_keys = item.get("coverage_keys")
            if (
                typ not in ("organization", "team", "project")
                or not isinstance(raw_keys, list)
                or not raw_keys
            ):
                raise InputError("invalid scope metadata")
            keys = []
            for value in raw_keys:
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("agent_type"), str)
                    or not value["agent_type"]
                ):
                    raise InputError("invalid coverage key")
                user = value.get("user_id")
                if user is not None and (not isinstance(user, str) or not user):
                    raise InputError("invalid coverage user")
                keys.append((user, value["agent_type"]))
            frozen = frozenset(keys)
            if len(frozen) != len(keys):
                raise InputError("duplicate coverage key")
            if typ == "organization":
                if raw_id != oid or org_keys is not None:
                    raise InputError(
                        "organization scope must appear once with id=org_id"
                    )
                org_keys = frozen
            elif not isinstance(sid, str) or not sid:
                raise InputError("Team/Project scope needs an ID")
            key = (oid, typ, sid)
            if key in scopes:
                raise InputError("duplicate scope identity")
            name = item.get("name")
            if not isinstance(name, str) or not name:
                raise InputError("invalid scope name")
            scopes[key] = ScopeMeta(oid, typ, sid, name, frozen)
            if typ == "team":
                for user, _ in frozen:
                    if user is not None:
                        team_users[user].add(sid)
        if org_keys is None or any(
            s.org_id == oid and not s.keys <= org_keys for s in scopes.values()
        ):
            raise InputError("scope population is not an Organization subset")
        if any(len(ids) > 1 for ids in team_users.values()):
            raise InputError("user belongs to multiple Teams")
        evidence = org.get("startup_evidence", [])
        if not isinstance(evidence, list) or any(
            not isinstance(x, dict) for x in evidence
        ):
            raise InputError("invalid startup evidence")
        startup.extend([{**x, "org_id": oid} for x in evidence])
    if len(ranges) != 1 or len(zones) != 1:
        raise InputError("organizations need one date range/timezone")
    start, end = next(iter(ranges))
    return start, end, next(iter(zones)), models, scopes, startup


def _snapshot(root, source, label):
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("snapshot"), str)
        or not isinstance(source.get("sha256"), str)
    ):
        raise InputError(f"synthetic {label} snapshot metadata missing")
    path = _safe(root, source["snapshot"])
    if _sha(path) != source["sha256"]:
        raise InputError(f"synthetic {label} snapshot hash mismatch")
    source_manifest = _json(_safe(root, f"inputs/{label}/manifest.json"))
    meta = source_manifest.get("files", {}).get(path.name)
    if not isinstance(meta, dict) or meta.get("sha256") != source["sha256"]:
        raise InputError(f"synthetic {label} source manifest mismatch")
    return path


def _synthetic(root, manifest):
    config_path = _safe(root, "config.yaml")
    if _sha(config_path) != manifest.get("config_sha256"):
        raise InputError("synthetic config hash mismatch")
    try:
        cfg = yaml.safe_load(config_path.read_text())
        oid = manifest["org_id"]
        start = date.fromisoformat(manifest["start_date"])
        end = date.fromisoformat(manifest["end_date"])
        zone = manifest["timezone"]
        ZoneInfo(zone)
        agents = cfg["agents"]
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        KeyError,
        TypeError,
        ValueError,
        ZoneInfoNotFoundError,
    ) as exc:
        raise InputError("invalid synthetic metadata") from exc
    sources = manifest.get("input_sources")
    if not isinstance(sources, dict):
        raise InputError("synthetic input_sources missing")
    _snapshot(root, sources.get("users"), "users")
    roster_path = _snapshot(root, sources.get("roster"), "roster")
    catalog = {}
    for agent, options in agents.items():
        for model in options.get("models", {}):
            if model in catalog:
                raise InputError("model appears under multiple agents")
            catalog[model] = frozenset([agent])
    teams = []
    try:
        with roster_path.open("rb") as stream:
            for line in stream:
                teams.append(json.loads(line))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("invalid roster snapshot") from exc
    all_keys = frozenset((u, a) for t in teams for u in t["user_ids"] for a in agents)
    scopes = {
        (oid, "organization", oid): ScopeMeta(oid, "organization", oid, oid, all_keys)
    }
    for team in teams:
        sid = str(team["team_id"])
        keys = frozenset((u, a) for u in team["user_ids"] for a in agents)
        scopes[(oid, "team", sid)] = ScopeMeta(
            oid, "team", sid, team["team_name"], keys
        )
    for item in cfg["projects"]:
        project_name = item["name"]
        pid = str(item["project_id"]) if "project_id" in item else str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"forecast:project:{oid}:{project_name.casefold()}"))
        if (oid, "project", pid) in scopes:
            raise InputError("duplicate saved Project identity")
        users = {
            u
            for t in teams
            if cfg["team_profiles"][t["team_name"]]["project_mix"].get(
                pid if "project_id" in item else project_name, 0) > 0
            for u in t["user_ids"]
        }
        keys = frozenset((u, a) for u in users for a in agents)
        scopes[(oid, "project", pid)] = ScopeMeta(
            oid, "project", pid, project_name, keys
        )
    return start, end, zone, {oid: catalog}, scopes, []


def _stream(paths, manifest, parsed):
    start, end, _, models, scopes, _ = parsed
    org_keys = {o: scopes[(o, "organization", o)].keys for o in models}
    usage = defaultdict(float)
    user_usage = defaultdict(lambda: defaultdict(float))
    user_cells = 0
    coverage = {}
    combined = 0
    for kind, record_type in (
        ("usage", DailyUsageRecord),
        ("coverage", CoverageRecord),
    ):
        name = f"{kind}.jsonl"
        expected = manifest["files"][name]["rows"]
        seen = set()
        count = 0
        try:
            with paths[name].open("rb") as stream:
                for number, line in enumerate(stream, 1):
                    count += 1
                    combined += 1
                    if combined > MAX_ROWS:
                        raise InputError(
                            "combined usage and coverage exceed five million rows"
                        )
                    if not line.strip() or len(line) > MAX_LINE_BYTES:
                        raise InputError(f"invalid {kind} line {number}")
                    try:
                        row = record_type.model_validate_json(line)
                    except ValidationError as exc:
                        raise InputError(f"invalid {kind} line {number}") from exc
                    if row.org_id not in models or not start <= row.date <= end:
                        raise InputError(
                            f"{kind} outside declared organization/date range"
                        )
                    if kind == "coverage":
                        key = (row.date, row.org_id, row.user_id, row.agent_type)
                        if (row.user_id, row.agent_type) not in org_keys[
                            row.org_id
                        ] or key in seen:
                            raise InputError("invalid or duplicate coverage identity")
                        seen.add(key)
                        coverage[key] = row.status
                        continue
                    key = (
                        row.date,
                        row.org_id,
                        row.team_id,
                        row.user_id,
                        row.project_id,
                        row.agent_type,
                        row.model,
                    )
                    if key in seen:
                        raise InputError("duplicate usage identity")
                    seen.add(key)
                    if (
                        row.model not in models[row.org_id]
                        or row.agent_type not in models[row.org_id][row.model]
                    ):
                        raise InputError("unknown usage model/agent")
                    if row.billing_mode != "api" or None in (
                        row.tokens.input,
                        row.tokens.cache_write_5m,
                        row.tokens.cache_write_1h,
                        row.tokens.cache_read,
                        row.tokens.output,
                    ):
                        raise InputError("unsupported billing mode or null tokens")
                    identities = [(row.org_id, "organization", row.org_id)]
                    if row.team_id is not None:
                        identities.append((row.org_id, "team", row.team_id))
                    if row.project_id is not None:
                        identities.append((row.org_id, "project", row.project_id))
                    try:
                        row_scopes = [scopes[x] for x in identities]
                    except KeyError as exc:
                        raise InputError("unknown Team/Project attribution") from exc
                    if any(
                        (row.user_id, row.agent_type) not in s.keys for s in row_scopes
                    ):
                        raise InputError("usage outside scope population")
                    for scope in row_scopes:
                        row_total = 0.0
                        for cat in TOKEN_TYPES:
                            try:
                                amount = float(getattr(row.tokens, cat))
                            except (OverflowError, ValueError) as exc:
                                raise InputError(
                                    "token quantity cannot be represented as float64"
                                ) from exc
                            if not math.isfinite(amount):
                                raise InputError(
                                    "token quantity cannot be represented as finite float64"
                                )
                            usage[
                                (
                                    scope.org_id,
                                    scope.scope_type,
                                    scope.scope_id,
                                    row.model,
                                    row.date,
                                    row.agent_type,
                                    cat,
                                )
                            ] += amount
                            row_total += amount
                            if len(usage) + user_cells + len(coverage) > MAX_ROWS:
                                raise InputError("aggregate cells exceed five million")
                        contributor_key = (row.model, row.date, row.user_id)
                        scoped_usage = user_usage[
                            (scope.org_id, scope.scope_type, scope.scope_id)
                        ]
                        if contributor_key not in scoped_usage:
                            user_cells += 1
                        scoped_usage[contributor_key] += row_total
                        if len(usage) + user_cells + len(coverage) > MAX_ROWS:
                            raise InputError("aggregate cells exceed five million")
        except InputError:
            raise
        except OSError as exc:
            raise InputError(f"cannot read {kind} records") from exc
        if count != expected:
            raise InputError(f"row count mismatch for {kind}")
    return dict(usage), {key: dict(value) for key, value in user_usage.items()}, coverage


def load_dataset(root: Path) -> PreparedDataset:
    root = Path(root).resolve()
    manifest = _json(root / "manifest.json")
    version = manifest.get("schema_version")
    if type(version) is int and version == 1:
        raise InputError("dataset uses schema 1; regenerate with this version")
    if (
        type(version) is not int
        or version != 2
        or manifest.get("status") != "complete"
        or manifest.get("operation") not in ("series", "daily")
    ):
        raise InputError("unsupported or incomplete dataset manifest")
    paths = {
        name: _manifest_file(root, manifest, name, True)
        for name in ("usage.jsonl", "coverage.jsonl")
    }
    if sum(x.stat().st_size for x in paths.values()) > MAX_DATA_BYTES:
        raise InputError("usage and coverage exceed 2 GiB")
    if manifest["operation"] == "daily":
        paths["metadata.json"] = _manifest_file(root, manifest, "metadata.json", False)
        parsed = _neutral(paths["metadata.json"])
    else:
        parsed = _synthetic(root, manifest)
        paths["config.yaml"] = _safe(root, "config.yaml")
        for label in ("users", "roster"):
            source = manifest["input_sources"][label]
            paths[f"{label}.snapshot"] = _safe(root, source["snapshot"])
            paths[f"{label}.manifest"] = _safe(root, f"inputs/{label}/manifest.json")
    usage, user_usage, coverage = _stream(paths, manifest, parsed)
    state = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in paths.values()}
    hashes = {n: _sha(p) for n, p in paths.items()}
    for name in ("usage.jsonl", "coverage.jsonl"):
        if hashes[name] != manifest["files"][name]["sha256"]:
            raise InputError(f"{name} changed while it was parsed")
    start, end, timezone_name, models, scopes, startup = parsed
    result = PreparedDataset(
        start=start,
        end=end,
        timezone=timezone_name,
        models=models,
        scopes=scopes,
        usage=usage,
        user_usage=user_usage,
        coverage=coverage,
        startup=startup,
        hashes=hashes,
        source_state=state,
    )
    result.verify_unchanged()
    return result
