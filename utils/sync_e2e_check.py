"""End-to-end check of the local sync stack (docker/docker-compose.sync.yml): did what
talium-sync uploaded arrive intact, parse under the collector's contract, and fold with its repo?

Three layers, each reported separately so a failure points at the hop that broke:

  1. MinIO  -- every manifest.json in the bucket, under any base prefix, validated with the
               collector's own `common.manifest.Manifest`; org_id vs the stack's org; every
               listed object present at <manifest prefix><key> with the listed size.
  2. Bytes  -- each object sha256-compared with the local file it came from (<source>/<rel>),
               skipping files that have changed locally since the upload.
  3. Folded -- the manifest's session ids looked up in Postgres: present, org, and
               session_info.repo_id / repo_id_provenance ("remote" == git_remote arrived).

Exit status is non-zero on a hard failure (unparseable manifest, wrong org, missing or
mismatched object). Repo coverage and fold lag are reported, not failed: a session whose
directory no longer exists legitimately has no repo, and the collector sweeps on an interval.

Usage (via utils/sync_e2e_check.sh, which sets PYTHONPATH and the stack's org):
    utils/sync_e2e_check.sh [--source DIR] [--no-bytes] [--dsn DSN] [--s3-endpoint URL]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from common.manifest import Manifest

BUCKET = "talium-staging"
STACK_DSN = "postgresql://postgres@localhost:5433/econ"
_MANIFEST_KEY = re.compile(r"^(?P<prefix>(?:.*/)?uploads/[^/]+/)manifest\.json$")
_CHUNK = 1 << 20


def find_manifest_keys(keys: list[str]) -> list[tuple[str, str]]:
    """(prefix, key) for every per-endpoint/per-upload manifest, under any base prefix. The
    prefix is everything before "manifest.json" -- the same derivation the collector uses."""
    out = []
    for key in keys:
        m = _MANIFEST_KEY.match(key)
        if m:
            out.append((m.group("prefix"), key))
    return out


@dataclass
class ManifestSummary:
    scope_id: str
    generation: str  # "go" (endpoint_id) | "python" (upload_id)
    org_id: str
    user_id: str
    uploader_version: str
    file_count: int
    listed: int
    vendors: Counter = field(default_factory=Counter)
    claude_files: int = 0
    with_repo_url: int = 0
    with_repo: int = 0
    repos: Counter = field(default_factory=Counter)
    session_ids: set[str] = field(default_factory=set)


def summarize_manifest(manifest: Manifest) -> ManifestSummary:
    s = ManifestSummary(
        scope_id=manifest.scope_id,
        generation="go" if manifest.endpoint_id else "python",
        org_id=manifest.declared.org_id,
        user_id=manifest.declared.user_id,
        uploader_version=manifest.uploader_version,
        file_count=manifest.file_count,
        listed=len(manifest.files),
    )
    for f in manifest.files:
        vendor = f.vendor or "claude_code"
        s.vendors[vendor] += 1
        if vendor != "claude_code":
            continue
        s.claude_files += 1
        if f.repo_url:
            s.with_repo_url += 1
        if f.repo:
            s.with_repo += 1
        if f.repo_url or f.repo:
            s.repos[f.repo_url or f.repo] += 1
        if f.session_id:
            s.session_ids.add(f.session_id)
    return s


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else f"{n}/0"


def _sha256_stream(chunks) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()


def _list_keys(s3, bucket: str) -> list[str]:
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        keys.extend(o["Key"] for o in page.get("Contents", []))
    return keys


def check_minio(s3, keys: list[str], expected_org: str, source: Path | None,
                check_bytes: bool) -> tuple[list[ManifestSummary], list[str]]:
    failures: list[str] = []
    summaries: list[ManifestSummary] = []
    key_set = set(keys)
    refs = find_manifest_keys(keys)
    print(f"== MinIO: {len(keys)} objects in s3://{BUCKET}, {len(refs)} manifest(s)")
    if not refs:
        failures.append("no manifest.json found -- has talium-sync run against this stack?")
    for prefix, key in refs:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        try:
            manifest = Manifest.model_validate_json(body)
        except Exception as exc:  # the collector would fail on exactly this
            failures.append(f"{key}: does not parse under common.manifest.Manifest: {exc}")
            continue
        s = summarize_manifest(manifest)
        summaries.append(s)
        print(f"\n-- {key}")
        print(f"   generation={s.generation} id={s.scope_id} org={s.org_id} user={s.user_id} "
              f"uploader_version={s.uploader_version!r}")
        print(f"   files: file_count={s.file_count} listed={s.listed} vendors={dict(s.vendors)}")
        print(f"   repo_url: {_pct(s.with_repo_url, s.claude_files)}   "
              f"repo: {_pct(s.with_repo, s.claude_files)}   distinct repos: {len(s.repos)}")
        for repo, n in s.repos.most_common(8):
            print(f"      {n:4d}  {repo}")
        if s.org_id != expected_org:
            failures.append(f"{key}: org_id {s.org_id!r} != stack org {expected_org!r}")
        if s.file_count != s.listed:
            failures.append(f"{key}: file_count {s.file_count} != {s.listed} listed")
        if s.claude_files and not s.with_repo_url and not s.with_repo:
            print("   WARN: no file carries a repo -- is talium-sync older than the "
                  "real-cwd fix (../upload 0d43896..dae4f26)?")

        missing = identical = mismatched = changed = absent_local = 0
        for f in manifest.files:
            full = prefix + f.key
            if full not in key_set:
                missing += 1
                failures.append(f"missing object {full}")
                continue
            if not (check_bytes and source):
                continue
            local = source / f.rel
            if not local.is_file():
                absent_local += 1
                continue
            if local.stat().st_size != f.bytes:
                changed += 1  # grew/rewrote locally after the upload; the next pass sends it
                continue
            with local.open("rb") as fh:
                local_sha = _sha256_stream(iter(lambda: fh.read(_CHUNK), b""))
            obj = s3.get_object(Bucket=BUCKET, Key=full)["Body"]
            remote_sha = _sha256_stream(iter(lambda: obj.read(_CHUNK), b""))
            if local_sha == remote_sha:
                identical += 1
            else:
                mismatched += 1
                failures.append(f"byte mismatch {full} vs {local}")
        print(f"   objects: missing={missing}")
        if check_bytes and source:
            print(f"   bytes vs {source}: identical={identical} mismatched={mismatched} "
                  f"changed_since_upload={changed} not_on_this_machine={absent_local}")
    return summaries, failures


def check_folded(dsn: str, summaries: list[ManifestSummary], expected_org: str) -> list[str]:
    import psycopg

    failures: list[str] = []
    ids = sorted(set().union(*(s.session_ids for s in summaries if s.generation == "go")))
    print(f"\n== Postgres: {len(ids)} session id(s) named by Go-generation manifests")
    if not ids:
        return failures
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """SELECT s.session_id, s.org_id, s.status, si.datasource,
                      si.repo_id, si.repo_id_provenance
               FROM sessions s LEFT JOIN session_info si USING (session_id)
               WHERE s.session_id = ANY(%s)""",
            (ids,),
        ).fetchall()
    found = {r[0]: r for r in rows}
    orgs = Counter(r[1] for r in rows)
    prov = Counter(r[5] or "none" for r in rows)
    ds = Counter(r[3] or "none" for r in rows)
    print(f"   in sessions table: {_pct(len(found), len(ids))}  (the rest: not yet swept, or "
          f"collected earlier through the jsonl path and checkpoint-skipped)")
    print(f"   org_id: {dict(orgs)}")
    print(f"   datasource: {dict(ds)}")
    print(f"   repo_id_provenance: {dict(prov)}  (remote == git_remote arrived from the manifest)")
    for repo, n in Counter(r[4] for r in rows if r[5] == "remote").most_common(8):
        print(f"      {n:4d}  {repo}")
    wrong_org = [sid for sid, r in found.items() if r[1] != expected_org]
    if wrong_org:
        failures.append(f"{len(wrong_org)} session(s) folded under an org other than "
                        f"{expected_org!r}, e.g. {wrong_org[0]}")
    return failures


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default=os.environ.get("CLAUDE_PROJECTS_DIR",
                                                      str(Path.home() / ".claude" / "projects")),
                   help="local dir the uploads came from, for the byte comparison")
    p.add_argument("--no-bytes", action="store_true", help="skip the sha256 byte comparison")
    p.add_argument("--s3-endpoint", default=f"http://localhost:{os.environ.get('STACK_MINIO_PORT', '9000')}")
    p.add_argument("--dsn", default=os.environ.get("TALIUM_PROBE_DSN", STACK_DSN))
    p.add_argument("--org", default=os.environ.get("TALIUM_ORG_ID", "local-dev"))
    args = p.parse_args(argv)

    import boto3

    s3 = boto3.client("s3", endpoint_url=args.s3_endpoint, region_name="us-east-1",
                      aws_access_key_id="minioadmin", aws_secret_access_key="minioadmin")
    keys = _list_keys(s3, BUCKET)
    summaries, failures = check_minio(s3, keys, args.org, Path(args.source).expanduser(),
                                      not args.no_bytes)
    failures += check_folded(args.dsn, summaries, args.org)

    print()
    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures[:25]:
            print(f"  - {f}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
