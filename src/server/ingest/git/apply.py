"""Apply a `MapResult` to the DB (git-source.md §4.2, §5.7): the one write path shared by the
webhook-triggered `git_map` worker handler and the backfill/gap-fill readers (implementation
plan A5). This is what "one mapper, three readers" actually buys -- webhook, GraphQL and REST
all produce a `MapResult` through `map_github.map_delivery`/the backfill translators, and every
one of them lands through this exact function, so they cannot drift in how a result becomes
rows (§8.2).

Framework-free like `otlp/receiver.py`: takes a live `Session` and writes, no HTTP, no queue.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session as DbSession

from server.storage.queries import QueryCatalog

from .map_github import MapResult

_JSONB_FIELDS = ("stats", "files", "labels", "commit_shas", "info")


def resolve_user_id(s: DbSession, catalog: QueryCatalog, author_login: str | None, is_bot: bool) -> str | None:
    """`author_login -> users.github_user -> user_id` (git-source.md §6.3), auto-creating on
    first sighting. This is the ONLY identity signal this module uses -- deliberately not email:
    a commit author email is frequently a GitHub-generated `id+login@users.noreply.github.com`
    privacy address, which "defeats" email matching outright (§6.3's own phrasing) rather than
    merely weakening it, and the noreply string *does* embed the login after the `+` -- parsing
    it back out would be exactly the "guess" this module refuses to make, since nothing here
    verifies GitHub actually generated it rather than a user typing that shape by hand. Login is
    the one namespace the mapper is already certain of (webhooks and both backfill readers all
    supply it straight from the platform), so it is the only thing trusted enough to auto-create
    an identity from.

    Bots are never resolved to a user: `is_bot` authors are excluded from ROI denominators
    downstream (§6.3), and creating a `users` row for `dependabot[bot]` would misrepresent
    coverage as if a developer existed there.

    A login that already resolves to a DECLARED user (a collector-supplied login at
    registration -- `utils/upload_claude_sessions.sh`'s `github_user` param, fold_handler.py's
    `upsert_user_github_login` -- or an operator import) is found and reused, never overwritten:
    this module only ever fills a gap, it does not correct an existing mapping.

    Normalized lower-case before lookup/store: GitHub logins are case-insensitive, but the
    case actually reported for the same account can differ between a webhook payload, a
    REST/GraphQL response, and whatever case a person typed declaring their own handle at
    upload time -- comparing raw would silently defeat the declared-user lookup above."""
    if not author_login or is_bot:
        return None
    github_login = author_login.strip().lower()
    row = s.execute(catalog.get("git", "get_user_by_github_login"),
                    {"github_login": github_login}).first()
    if row:
        return row[0]
    user_id = f"github:{github_login}"
    s.execute(catalog.get("git", "create_user_for_github_login"),
             {"user_id": user_id, "github_login": github_login})
    return user_id


def _upsert_artifact(s: DbSession, catalog: QueryCatalog, artifact: dict) -> None:
    params = dict(artifact)
    if params.get("user_id") is None:
        params["user_id"] = resolve_user_id(s, catalog, params.get("author_login"), bool(params.get("is_bot")))
    for key in _JSONB_FIELDS:
        if params.get(key) is not None:
            params[key] = json.dumps(params[key])
    s.execute(catalog.get("git", "upsert_artifact"), params)


def _apply_issue_links(s: DbSession, catalog: QueryCatalog, result: MapResult) -> None:
    """Persist a PR's resolved issue links to `pr_issue_links` (ISSUE §3.2, H3). The resolver
    (`linkage.resolve_links`, via `map_github.pr_issue_links`) always returns one rung's worth
    of links at one confidence; clear this PR's links at or below that confidence, then insert
    the winning set -- so a webhook `synchronize` that can only reach the branch/title/body
    rungs never downgrades a stronger `graphql` link a backfill established. An empty
    `issue_links` (a PR that resolved to nothing) touches neither query, leaving any
    stronger-sourced links intact."""
    if not result.issue_links:
        return
    winning_confidence = max(lk["confidence"] for lk in result.issue_links)
    pr_ids = {lk["pr_artifact_id"] for lk in result.issue_links}
    for pr_artifact_id in pr_ids:
        s.execute(catalog.get("git", "delete_pr_issue_links_up_to"),
                  {"pr_artifact_id": pr_artifact_id, "confidence": winning_confidence})
    for lk in result.issue_links:
        s.execute(catalog.get("git", "upsert_pr_issue_link"), lk)


def _attach_issue_to_links(s: DbSession, catalog: QueryCatalog, artifact: dict) -> None:
    """An `issue` artifact just landed -- stamp its id onto any `pr_issue_links` row that named
    it (by `tracker`/`project_key`/`number`) but was recorded before the issue was pulled
    (`issue_artifact_id IS NULL`). Lets a link created from a PR's `Closes #N` compose into a
    real feature the moment the issue arrives, in either ingest order (H5)."""
    s.execute(catalog.get("git", "attach_issue_artifact_to_links"), {
        "tracker": "github",
        "project_key": artifact["repo_id"].removeprefix("github:"),
        "number": artifact["number"],
        "issue_artifact_id": artifact["artifact_id"],
    })


def _correlate_merge_transition(s: DbSession, catalog: QueryCatalog, result: MapResult) -> list[str]:
    """The two-sided lookup git-source.md §5.7 requires: the merge's before/after/method can
    arrive from either direction (the base-branch push or the merged `pull_request`), and
    neither delivery/record is guaranteed to land second. Returns the artifact_ids of any PR
    actually updated, so the caller can re-ring correlation for it (a PR's `commit_shas` may
    have gone from empty to populated right here, which is exactly what the pr-work-
    classification correlator, implementation plan B2, needs to find its sessions)."""
    touched: list[str] = []
    if result.push_after:
        # This result IS the correlating push: try to fill an already-landed merged PR.
        row = s.execute(catalog.get("git", "correlate_push_into_pr"), {
            "merge_commit_sha": result.push_after,
            "before_sha": result.push_before, "after_sha": result.push_after,
            "integration_method": result.push_integration_method,
            "commit_shas": json.dumps(result.push_commit_shas or []),
        }).first()
        if row:
            touched.append(row[0])
    if result.merge_commit_sha:
        # This result IS the merged PR: see if the correlating push already landed as a
        # `commit` artifact (the mapper stashes it in `info.github.push_meta`, §5.7).
        info_row = s.execute(catalog.get("git", "get_commit_info_by_sha"),
                             {"sha": result.merge_commit_sha}).first()
        meta = (info_row[0] or {}).get("github.push_meta") if info_row else None
        if meta:
            row = s.execute(catalog.get("git", "apply_push_meta_to_pr"), {
                "artifact_id": result.artifacts[0]["artifact_id"],
                "before_sha": meta.get("before_sha"), "after_sha": meta.get("after_sha"),
                "integration_method": meta.get("integration_method"),
                "commit_shas": json.dumps(meta.get("commit_shas") or []),
            }).first()
            if row:
                touched.append(row[0])
    return touched


def apply_map_result(s: DbSession, catalog: QueryCatalog, result: MapResult) -> None:
    """Upsert every artifact/repo/installation a `MapResult` carries, then correlate. Assumes
    `result.mapped` is already true -- the caller (a delivery handler, a backfill page) decides
    what an unmapped result means for *its own* bookkeeping (a delivery's `status`, a cursor's
    advance); this function only ever writes artifacts.

    Rings `git_correlate` (B2) for every `commit` artifact and every merged `pr` artifact this
    call touches, and `pr_classify` (B3-B5) for every `pr` artifact reaching a TERMINAL state
    (merged or closed -- broader, since a closed-unmerged PR is a recall candidate that still
    needs classifying). git-source's own job ends at "the artifact exists"; whether a session
    should be (re-)linked to it, or how a PR should be classified, is pr-work-classification's
    question, asked the same way `git_map` itself is asked (a coalesced queue_jobs ring, never
    a direct call into a module this one has no business importing)."""
    pr_ids_in_batch = {a["artifact_id"] for a in result.artifacts if a["kind"] == "pr"}
    to_ring: set[str] = set()
    to_ring_classify: set[str] = set()
    for artifact in result.artifacts:
        _upsert_artifact(s, catalog, artifact)
        if artifact["kind"] == "issue":
            _attach_issue_to_links(s, catalog, artifact)
        if artifact["kind"] == "commit" or (artifact["kind"] == "pr" and artifact.get("merged_at")):
            to_ring.add(artifact["artifact_id"])
        if artifact["kind"] == "pr" and artifact.get("state") in ("merged", "closed"):
            to_ring_classify.add(artifact["artifact_id"])
        parent = artifact.get("parent_artifact_id")
        if artifact["kind"] == "review" and parent and parent not in pr_ids_in_batch:
            # A webhook-mapped review, upserted alone (no accompanying PR in this same
            # MapResult): recompute review_count from the actual rows now that this one has
            # landed. Skipped when a PR ships in the same batch (backfill) -- that PR's own
            # stats already carry the API's authoritative totalCount, which a recompute over a
            # possibly-paginated review list here would wrongly undercount against (git.yaml's
            # `recompute_review_count` docstring).
            s.execute(catalog.get("git", "recompute_review_count"), {"pr_artifact_id": parent})
    _apply_issue_links(s, catalog, result)
    if result.pr_state_event is not None:
        # Append the observed PR transition (Track churn/reopen). ON CONFLICT (source_key) DO
        # NOTHING makes replay a no-op -- two deliveries for the same (PR, action, updated_at)
        # are the same transition, so no `delivery_id` needs threading here to dedup.
        s.execute(catalog.get("git", "insert_artifact_event"), result.pr_state_event)
    for bump in result.bump_counters:
        s.execute(catalog.get("git", "bump_comment_count"), bump)
    for repo in result.repos:
        s.execute(catalog.get("git", "upsert_repo"),
                  {**repo, "info": json.dumps(repo.get("info") or {})})
    if result.installation is not None:
        s.execute(catalog.get("git", "upsert_installation"),
                  {**result.installation, "info": json.dumps(result.installation.get("info") or {})})
    to_ring.update(_correlate_merge_transition(s, catalog, result))

    for artifact_id in to_ring:
        s.execute(catalog.get("git", "ring_git_correlate"), {"artifact_id": artifact_id})
    for artifact_id in to_ring_classify:
        s.execute(catalog.get("git", "ring_pr_classify"), {"artifact_id": artifact_id})
    _ring_commit_stats(s, catalog, result)


def _ring_commit_stats(s: DbSession, catalog: QueryCatalog, result: MapResult) -> None:
    """A push's commit artifacts land with empty `stats` (the webhook payload has no line
    counts, `map_github.py`); ring the git_commit_stats worker to fill them via the compare API
    (Track churn/reopen, Landing 2). Only for a push with a real `before` -- a branch-creation
    push (all-zero `before`) has no compare base, and post-open churn is measured from pushes to
    an already-open PR's branch, which always have a real base."""
    before, after = result.push_before, result.push_after
    if not after or not before or set(before) == {"0"}:
        return
    repo_id = next((a["repo_id"] for a in result.artifacts if a["kind"] == "commit"), None)
    if repo_id is None:
        return
    s.execute(catalog.get("git", "ring_git_commit_stats"),
              {"dedup_key": f"{repo_id}:{before}..{after}", "repo_id": repo_id,
               "before": before, "after": after})
