import pytest

from common import git_ident


# git-source.md §G6: SSH/HTTPS/`.git`/trailing-slash/case/bare-`owner/name` all collapse to
# one id -- the exact regression this table exists to prevent.
@pytest.mark.parametrize("value", [
    "git@github.com:talium-ai/code-econ.git",
    "git@github.com:talium-ai/code-econ",
    "https://github.com/talium-ai/code-econ.git",
    "https://github.com/talium-ai/code-econ",
    "https://github.com/talium-ai/code-econ/",
    "https://GitHub.com/Talium-AI/Code-Econ.git",
    "talium-ai/code-econ",
    "Talium-AI/Code-Econ",
])
def test_all_forms_collapse_to_one_canonical_id(value):
    assert git_ident.normalize_remote(value) == "github:talium-ai/code-econ"


def test_non_github_host_gets_a_vendor_neutral_id():
    assert git_ident.normalize_remote("git@gitlab.com:acme/widgets.git") == "git:gitlab.com/acme/widgets"
    assert git_ident.normalize_remote("https://gitlab.com/acme/widgets") == "git:gitlab.com/acme/widgets"


@pytest.mark.parametrize("value", [None, "", "not-a-remote-or-owner-repo", "just-one-token", "   "])
def test_unparseable_input_returns_none(value):
    assert git_ident.normalize_remote(value) is None


def test_strip_worktree_suffix_recovers_the_real_root():
    assert (git_ident.strip_worktree_suffix(
        "/Users/dev/talium/code-econ/.claude/worktrees/flamboyant-easley-cf2002")
        == "/Users/dev/talium/code-econ")


def test_strip_worktree_suffix_is_a_no_op_without_a_worktree_path():
    assert git_ident.strip_worktree_suffix("/Users/dev/talium/code-econ") == "/Users/dev/talium/code-econ"


def test_path_repo_key_uses_the_stripped_root_basename():
    assert git_ident.path_repo_key(
        "/Users/dev/talium/code-econ/.claude/worktrees/flamboyant-easley-cf2002") == "code-econ"
    assert git_ident.path_repo_key("/Users/dev/talium/code-econ") == "code-econ"
    assert git_ident.path_repo_key("/Users/dev/talium/code-econ/") == "code-econ"


def test_path_repo_key_none_without_a_cwd():
    assert git_ident.path_repo_key(None) is None
    assert git_ident.path_repo_key("") is None


def test_describe_github_id():
    assert git_ident.describe("github:talium-ai/code-econ") == ("git_remote", "talium-ai/code-econ")


def test_describe_vendor_neutral_git_id_drops_the_host_from_the_label():
    assert git_ident.describe("git:gitlab.com/acme/widgets") == ("git_remote", "acme/widgets")


def test_describe_local_path_id():
    assert git_ident.describe("local:code-econ") == ("local_path", "code-econ")


def test_sibling_project_root_finds_the_divergence_point():
    assert git_ident.sibling_project_root(
        "/Users/dev/talium/code-econ", "/Users/dev/talium/other-repo/src/foo.py"
    ) == "/Users/dev/talium/other-repo"


def test_sibling_project_root_none_when_shared_depth_too_shallow():
    # Only the empty root segment is shared -- /tmp and /var are not "the same workspace."
    assert git_ident.sibling_project_root("/tmp/code-econ", "/var/other-repo/foo.py") is None


def test_sibling_project_root_none_for_a_bare_file_at_the_divergence_level():
    # No directory below the divergence point to serve as a project boundary.
    assert git_ident.sibling_project_root(
        "/Users/dev/talium/code-econ", "/Users/dev/talium/README.md"
    ) is None


def test_sibling_project_root_handles_trailing_slashes():
    assert git_ident.sibling_project_root(
        "/Users/dev/talium/code-econ/", "/Users/dev/talium/other-repo/foo.py"
    ) == "/Users/dev/talium/other-repo"
