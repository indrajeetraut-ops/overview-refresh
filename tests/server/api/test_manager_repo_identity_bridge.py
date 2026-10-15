"""aggregate.resolve_git_repo_id(s) -- the bridge between session_info's repo_id scheme
(git_ident, `github:owner/name`) and git_artifacts' own scheme (git-source, `github:<repo_num>`).
See the function's own docstring for why this exists; this is the regression test for the bug
it fixes: A3's `merged_prs`/A6's PR data silently reading empty against a real dataset because
the two schemes never coincide, discovered while wiring F4."""
from datetime import datetime, timezone

from server.api.manager import aggregate as agg
from server.storage import session_scope
from server.storage.schema import GitRepo

from .conftest import requires_db

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _git_repo(repo_num, full_name):
    return GitRepo(repo_id=f"github:{repo_num}", repo_num=repo_num, full_name=full_name,
                   owner=full_name.split("/")[0], default_branch="main", private=False,
                   installation_id=1, org_id="default", backfill_state="done",
                   first_seen_at=NOW, updated_at=NOW)


@requires_db
def test_resolve_git_repo_id_bridges_the_two_schemes(engine, catalog):
    with session_scope() as s:
        s.add(_git_repo(123456, "acme/api"))
    with session_scope() as s:
        resolved = agg.resolve_git_repo_id(s, catalog, "github:acme/api")
    assert resolved == "github:123456"


@requires_db
def test_resolve_git_repo_id_is_case_insensitive(engine, catalog):
    with session_scope() as s:
        s.add(_git_repo(1, "Acme/API"))
    with session_scope() as s:
        assert agg.resolve_git_repo_id(s, catalog, "github:acme/api") == "github:1"


@requires_db
def test_resolve_git_repo_id_none_when_not_onboarded(engine, catalog):
    """A repo with sessions but no GitHub App connected -- absent, not a guess."""
    with session_scope() as s:
        assert agg.resolve_git_repo_id(s, catalog, "github:someone/unconnected") is None


@requires_db
def test_resolve_git_repo_id_none_for_a_local_repo(engine, catalog):
    with session_scope() as s:
        assert agg.resolve_git_repo_id(s, catalog, "local:my-repo") is None


@requires_db
def test_resolve_git_repo_ids_bulk(engine, catalog):
    with session_scope() as s:
        s.add(_git_repo(1, "acme/api"))
        s.add(_git_repo(2, "acme/web"))
    with session_scope() as s:
        resolved = agg.resolve_git_repo_ids(s, catalog, ["github:acme/api", "github:acme/web",
                                                         "github:acme/unconnected", "local:x"])
    assert resolved == {"github:acme/api": "github:1", "github:acme/web": "github:2"}


@requires_db
def test_resolve_git_repo_ids_empty_input(engine, catalog):
    with session_scope() as s:
        assert agg.resolve_git_repo_ids(s, catalog, []) == {}
