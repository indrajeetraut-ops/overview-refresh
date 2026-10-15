"""Pure git-remote/repo-identity normalization (git-source.md §6.2/G6's reserved module).

One canonical form for "this repository," derived from whatever raw identity evidence is
available -- a git remote URL (SSH or HTTPS, with or without `.git`, any case) or a bare
`owner/name` pair (as a `pr-link` record's `prRepository` supplies it verbatim). No I/O: the
same function the normalizer's transcript-evidence extraction (claude_code.py) and any future
GitHub-App webhook mapper both call, so the two sides of a correlation always agree on one
identity scheme rather than each inventing a slightly different one (git-source.md §6.2).

Deliberately narrow: this module only turns already-extracted strings into a canonical id. It
never touches the network or the filesystem, and it never decides *which* evidence to trust --
that ranking is the fold's `repo_info()` (metrics.py), which is the one place with visibility
into a whole session's evidence at once.
"""
from __future__ import annotations

import re

# SSH: git@github.com:owner/repo(.git)?   HTTPS: https://github.com/owner/repo(.git)?(/)?
# The `[:/]` between host and owner/repo is what lets one pattern cover both wire forms.
_REMOTE_URL_RE = re.compile(
    r"^(?:git@|https?://)(?P<host>[\w.-]+)[:/](?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"
)
# Bare `owner/name`, as `pr-link`'s `prRepository` already supplies it -- no host, no scheme.
_BARE_OWNER_REPO_RE = re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)$")


def normalize_remote(value: str | None) -> str | None:
    """A git remote URL or a bare `owner/name` -> one canonical id, or `None` if `value`
    doesn't parse as either shape.

    `github.com` collapses to the `github:` scheme (the vast majority of real-world remotes,
    and the convention git-source.md §6.2 already committed to for the eventual GitHub App
    integration); any other host uses a vendor-neutral `git:<host>/<owner>/<name>` form so a
    non-GitHub remote still gets one stable id rather than being dropped.

    Case-folded throughout -- GitHub treats `Owner/Repo` and `owner/repo` as the same
    repository, and two differently-cased sightings of the same remote (a URL from one Bash
    call, a `prRepository` string from another) must resolve to one id, never two."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()

    match = _REMOTE_URL_RE.match(value)
    if match:
        host, owner, repo = match.group("host"), match.group("owner"), match.group("repo")
        owner_repo = f"{owner}/{repo}".lower()
        return f"github:{owner_repo}" if host.lower() == "github.com" else f"git:{host.lower()}/{owner_repo}"

    match = _BARE_OWNER_REPO_RE.match(value)
    if match:
        return f"github:{match.group('owner')}/{match.group('repo')}".lower()

    return None


_WORKTREE_SUFFIX_RE = re.compile(r"/\.claude/worktrees/[^/]+/?$")


def strip_worktree_suffix(cwd: str) -> str:
    """A Claude Code worktree path's basename is a generated slug
    (`flamboyant-easley-cf2002`), not a repo name. Stripping the `/.claude/worktrees/<slug>`
    suffix recovers the real repo root -- measured: collapses 17 distinct `cwd` values to 10
    canonical roots in a 44-session corpus, and removes every generated-slug "repo" from the
    dashboard on its own, with no evidence beyond the path string itself."""
    return _WORKTREE_SUFFIX_RE.sub("", cwd)


_MIN_SHARED_DEPTH = 3  # e.g. "", "Users", "anil" -- below this, a shared prefix is too generic
                        # (it would match "/tmp" against "/var") to trust as one workspace.


def sibling_project_root(primary_cwd: str, file_path: str) -> str | None:
    """A file path that falls *outside* `primary_cwd` -> the absolute root of the sibling
    project it likely belongs to, or `None` when the two paths share too little to trust
    (dashboard plan R1.3: sessions are multi-project -- measured 8/46 touching 2+ roots).

    No filesystem access -- the fold worker never sees the collector's disk, so this is pure
    path arithmetic: the sibling root is the first path segment that diverges from
    `primary_cwd`, on the reasoning that a second project touched in the same session is
    almost always a sibling directory under the same workspace parent
    (`.../workspace/org/repo-a` next to `.../workspace/org/repo-b`), not an unrelated
    filesystem location -- the shape every multi-project session in the corpus actually took.

    Two guards, either failing returns `None` rather than guess -- a dropped root is a missed
    reference count, a wrong one is a corrupted rollup:
      - fewer than `_MIN_SHARED_DEPTH` segments shared before the divergence (rules out two
        unrelated top-level paths, e.g. `/tmp/x` vs `/var/y`, sharing only the empty root)
      - no path segment left in `file_path` below the divergence point (rules out a bare file
        dropped at the workspace root -- `.../workspace/README.md` -- which has no directory
        of its own to serve as a project boundary)

    Callers are expected to have already established that `file_path` is not under
    `primary_cwd` -- this function does not check that itself."""
    primary_parts = primary_cwd.rstrip("/").split("/")
    file_parts = file_path.rstrip("/").split("/")
    common = 0
    for a, b in zip(primary_parts, file_parts):
        if a != b:
            break
        common += 1
    if common < _MIN_SHARED_DEPTH or common >= len(primary_parts) or common >= len(file_parts) - 1:
        return None
    return "/".join(file_parts[: common + 1])


def describe(repo_id: str) -> tuple[str, str]:
    """A `repo_id` -> `(kind, label)` for the `repositories` table: a `git_remote` row shows
    `owner/name` (the host segment is dropped from a `git:<host>/<owner>/<name>` id -- the
    label is for display, and the host is already implied by the id itself); a `local_path`
    row (the `path` provenance's `local:<repo_key>` id) shows just the repo_key. The inverse
    of `normalize_remote`/`path_repo_key`, kept here so both directions live in one module
    rather than every caller re-deriving the split."""
    if repo_id.startswith("local:"):
        return "local_path", repo_id[len("local:"):]
    if repo_id.startswith("github:"):
        return "git_remote", repo_id[len("github:"):]
    if repo_id.startswith("git:"):
        _host, _, owner_repo = repo_id[len("git:"):].partition("/")
        return "git_remote", owner_repo or repo_id
    return "local_path", repo_id


def path_repo_key(cwd: str | None) -> str | None:
    """The `path`-tier display label: the repo root's basename, after worktree-stripping.

    Never a stable identity -- two clones of the same repo on different machines, or two
    unrelated repos that happen to share a folder name, both collide onto the same key. This
    is the evidence ladder's floor, used only when nothing better (a remote URL, a
    `prRepository`) is available, and it is why `repo_id_provenance` exists: a consumer must
    be able to tell a `path`-tier guess from a `remote`-tier fact."""
    if not cwd:
        return None
    stripped = strip_worktree_suffix(cwd).rstrip("/")
    if not stripped:
        return None
    return stripped.rsplit("/", 1)[-1]
