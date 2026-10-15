"""End-to-end replay (git-source.md §11, implementation plan A8's acceptance bar): a fixture
repo timeline through the REAL webhook route + the real `git_map` worker handler (not each
piece tested in isolation, as elsewhere) -- installation discovery, a PR opened/reviewed/merged,
and its correlating push -- landing as `git_artifacts`/`git_repos`/`git_installations`/`users`
rows, plus one backfill pass over the same PR. **Run twice** (fresh delivery ids, as a real
GitHub redelivery would send — same payload, new id) and the resulting rows must be
byte-identical, proving the whole pipeline is idempotent end to end, not just each stage alone.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from server.ingest.git.backfill import RestBackfillReader
from server.ingest.git.client import GitHubClient
from server.ingest.git.config import GithubConfig
from server.ingest.git.routes import make_router
from server.ingest.git.verify import compute_signature
from server.storage.dbm import operations
from server.storage.engine import dispose_engine, get_engine, session_scope
from server.worker.git_map import git_map
from tests.server.storage.conftest import cfg, requires_db  # noqa: F401

FIXTURES = Path("tests/fixtures/github")
SECRET = "e2e-webhook-secret-do-not-use-in-prod"
REPO_NUM = 41213765
INSTALLATION_ID = 42

TIMELINE = [
    "installation_created",
    "pull_request_opened",
    "pull_request_review_submitted",
    "pull_request_closed_merged",
    "push_squash_merge",
]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _run_timeline(client, catalog, run: int) -> None:
    for name in TIMELINE:
        fx = _load(name)
        body = json.dumps(fx["payload"]).encode()
        delivery_id = f"{name}-run{run}"
        signature = compute_signature(SECRET, body)
        resp = client.post(
            "/talium/git/v1/webhook/github", content=body,
            headers={"X-GitHub-Event": fx["event"], "X-GitHub-Delivery": delivery_id,
                    "X-Hub-Signature-256": signature, "Content-Type": "application/json"},
        )
        assert resp.status_code == 202, (name, resp.text)
        # Simulate the worker's poll loop consuming the job this delivery rang.
        with session_scope() as s:
            git_map(s, catalog, {"delivery_id": delivery_id})


def _snapshot() -> dict:
    with session_scope() as s:
        installation = dict(s.execute(text(
            "SELECT installation_id, org_id, account_login, account_type, state "
            "FROM git_installations WHERE installation_id = :id"
        ), {"id": INSTALLATION_ID}).mappings().one())
        repo = dict(s.execute(text(
            "SELECT repo_id, repo_num, full_name, installation_id FROM git_repos WHERE repo_id = :id"
        ), {"id": f"github:{REPO_NUM}"}).mappings().one())
        artifacts = [
            dict(r) for r in s.execute(text(
                "SELECT artifact_id, kind, state, title, sha, merge_commit_sha, before_sha, "
                "after_sha, integration_method, user_id, stats, commit_shas "
                "FROM git_artifacts WHERE repo_id = :repo_id ORDER BY artifact_id"
            ), {"repo_id": f"github:{REPO_NUM}"}).mappings().all()
        ]
        user = dict(s.execute(text(
            "SELECT user_id, github_user FROM users WHERE github_user = 'akhuteta'"
        )).mappings().one())
    return {"installation": installation, "repo": repo, "artifacts": artifacts, "user": user}


@requires_db
def test_fixture_repo_timeline_replays_to_identical_state_twice(cfg, catalog):  # noqa: F811
    get_engine(cfg)
    operations.init(cfg, force=True)
    config = GithubConfig(webhook_secret=SECRET)
    app = FastAPI()
    app.include_router(make_router(config, catalog))

    with TestClient(app) as client:
        _run_timeline(client, catalog, run=1)
        snapshot_1 = _snapshot()

        _run_timeline(client, catalog, run=2)   # fresh delivery ids, identical payloads
        snapshot_2 = _snapshot()

    assert snapshot_1 == snapshot_2

    # The full merge-transition correlation actually resolved (not just "didn't crash").
    pr = next(a for a in snapshot_1["artifacts"] if a["kind"] == "pr")
    assert pr["state"] == "merged"
    assert pr["before_sha"] is not None
    assert pr["after_sha"] == pr["merge_commit_sha"]
    assert pr["integration_method"] == "squash"
    assert pr["stats"]["review_count"] == 1   # the review webhook's bump survived the merge upsert
    assert pr["user_id"] == "github:akhuteta"

    # No duplicate rows from replaying under new delivery ids -- artifact_id is the upsert key,
    # not delivery_id, so run 2 must land on the SAME rows, not new ones.
    assert len(snapshot_1["artifacts"]) == 3   # pr, review, commit -- exactly, not 6

    with session_scope() as s:
        delivery_count = s.execute(text("SELECT count(*) FROM git_deliveries")).scalar()
    assert delivery_count == len(TIMELINE) * 2   # every delivery landed once each; none duplicated

    dispose_engine()


@requires_db
def test_backfill_pass_over_the_same_pr_integrates_cleanly(cfg, catalog, tmp_path):  # noqa: F811
    """The "+ backfill responses" half of A8's replay bar: a history read over a PR the webhook
    timeline already fully mapped must not corrupt it -- consistent with `test_backfill_db.py`'s
    narrower version of this same property."""
    get_engine(cfg)
    operations.init(cfg, force=True)
    config = GithubConfig(webhook_secret=SECRET)
    app = FastAPI()
    app.include_router(make_router(config, catalog))

    with TestClient(app) as client:
        _run_timeline(client, catalog, run=1)
    before = _snapshot()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(pem)
    gh_client = GitHubClient(GithubConfig(client_id="Iv1.abc", private_key_path=str(key_path)))

    pr_json = _load("pull_request_closed_merged")["payload"]["pull_request"]
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": "tok"})
        )
        mock.get("https://api.github.com/repos/o/n/pulls/1184").mock(return_value=httpx.Response(200, json={
            **pr_json, "commits": 1,
        }))
        mock.get("https://api.github.com/repos/o/n/pulls/1184/files").mock(return_value=httpx.Response(200, json=[]))
        mock.get("https://api.github.com/repos/o/n/pulls/1184/commits").mock(return_value=httpx.Response(200, json=[
            {"sha": pr_json["merge_commit_sha"], "commit": {"message": "m"}},
        ]))
        mock.get("https://api.github.com/repos/o/n/pulls/1184/reviews").mock(return_value=httpx.Response(200, json=[
            {"id": 9001, "user": {"login": "reviewer1"}, "state": "approved",
             "submitted_at": "2026-07-25T10:00:00Z", "commit_id": pr_json["head"]["sha"]},
        ]))
        result = RestBackfillReader(gh_client).read_pr(
            INSTALLATION_ID, "o", "n", REPO_NUM, "default", 1184,
        )
    from server.ingest.git.apply import apply_map_result
    with session_scope() as s:
        apply_map_result(s, catalog, result)

    after = _snapshot()
    pr_before = next(a for a in before["artifacts"] if a["kind"] == "pr")
    pr_after = next(a for a in after["artifacts"] if a["kind"] == "pr")
    assert pr_before["state"] == pr_after["state"] == "merged"
    assert pr_before["merge_commit_sha"] == pr_after["merge_commit_sha"]
    assert pr_before["before_sha"] == pr_after["before_sha"]   # backfill has none; COALESCE preserves it
    assert len(after["artifacts"]) == len(before["artifacts"]) == 3   # no duplicates from re-reading

    dispose_engine()
