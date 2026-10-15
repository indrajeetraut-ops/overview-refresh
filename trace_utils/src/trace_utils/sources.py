"""Source resolution: a dataset entry (+ selector) -> concrete readable file URIs.

Handles the three observed packagings (sharded parquet, single-parquet-plus-jsonl-trees,
one-jsonl-per-session) uniformly by listing the repo's files and partitioning by
extension. ``stream`` yields ``hf://`` URIs (no download); ``download`` materializes the
selected files to ``cache_dir`` and yields local paths.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download

from .config import Dataset, Source
from .logging import get_logger

log = get_logger(__name__)

_PARQUET_EXT = (".parquet",)
_JSONL_EXT = (".jsonl", ".ndjson")


@dataclass
class Selector:
    """Scopes a command to a subset. All fields optional; None = whole dataset."""

    session: str | None = None  # a session id (matched to a file, else filtered at query time)
    glob: str | None = None  # a repo-relative fnmatch pattern, e.g. "sessions/codex/*.jsonl"
    fmt: str | None = None  # force format ("parquet" | "jsonl"); default auto


@dataclass
class ResolvedSource:
    repo_id: str
    fmt: str  # "parquet" | "jsonl"
    uris: list[str]  # readable paths (hf:// or local), passed to DuckDB read_*
    files: list[str] = field(default_factory=list)  # repo-relative paths (for display)
    local: bool = False
    session_filter: str | None = None  # set when --session couldn't be resolved to a file


def parse_repo(url: str) -> tuple[str, str | None]:
    """Return (repo_id, subpath) from a web URL or hf:// form.

    Accepts:
      https://huggingface.co/datasets/org/name[/tree/main/<subpath>]
      hf://datasets/org/name[/<subpath>]
      org/name
    """
    u = url.strip().rstrip("/")
    for prefix in ("https://huggingface.co/datasets/", "http://huggingface.co/datasets/",
                   "hf://datasets/", "huggingface.co/datasets/"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    parts = u.split("/")
    if len(parts) < 2:
        raise ValueError(f"cannot parse dataset repo from {url!r}")
    repo_id = f"{parts[0]}/{parts[1]}"
    rest = parts[2:]
    # strip a "tree/<branch>" segment if present (web URL form)
    if rest[:1] == ["tree"]:
        rest = rest[2:]
    subpath = "/".join(rest) if rest else None
    return repo_id, subpath


def _classify(files: list[str]) -> str | None:
    if any(f.lower().endswith(_PARQUET_EXT) for f in files):
        return "parquet"
    if any(f.lower().endswith(_JSONL_EXT) for f in files):
        return "jsonl"
    return None


def list_files(dataset: Dataset, token: str | None = None) -> list[str]:
    """All data-bearing repo-relative files under the dataset (+ its subpath)."""
    repo_id, url_subpath = parse_repo(dataset.url)
    subpath = dataset.subpath or url_subpath
    api = HfApi(token=token)
    all_files = api.list_repo_files(repo_id, repo_type="dataset")
    data = [f for f in all_files if f.lower().endswith(_PARQUET_EXT + _JSONL_EXT)]
    if subpath:
        pref = subpath.rstrip("/") + "/"
        data = [f for f in data if f == subpath or f.startswith(pref)]
    elif any("/" in f for f in data):
        # Trace data lives in subdirs (data/, sessions/…); a lone root-level file is
        # almost always a manifest/README, not trace data. Prefer the subdir files.
        data = [f for f in data if "/" in f]
    return sorted(data)


def resolve(
    dataset: Dataset,
    source: Source,
    selector: Selector | None = None,
    token: str | None = None,
) -> ResolvedSource:
    """Resolve a dataset + selector to a :class:`ResolvedSource` of readable URIs."""
    selector = selector or Selector()
    repo_id, url_subpath = parse_repo(dataset.url)
    files = list_files(dataset, token=token)
    if not files:
        raise ValueError(f"no parquet/jsonl files found in {repo_id}")

    fmt = selector.fmt or _classify(files)
    # keep only files of the chosen format
    exts = _PARQUET_EXT if fmt == "parquet" else _JSONL_EXT
    files = [f for f in files if f.lower().endswith(exts)]

    session_filter = None
    if selector.glob:
        files = [f for f in files if fnmatch.fnmatch(f, selector.glob)]
    if selector.session:
        by_basename = [f for f in files if Path(f).stem == selector.session]
        if by_basename:
            files = by_basename
        else:
            # session id isn't a filename here -> filter at query time
            session_filter = selector.session

    if not files:
        raise ValueError("selector matched no files")

    if source.mode == "download":
        uris = _download(repo_id, files, source, token)
        local = True
    else:
        uris = [f"hf://datasets/{repo_id}/{f}" for f in files]
        local = False

    log.info(
        "source_resolved", repo_id=repo_id, fmt=fmt, n_files=len(files),
        mode=source.mode, session_filter=session_filter,
    )
    return ResolvedSource(
        repo_id=repo_id, fmt=fmt, uris=uris, files=files,
        local=local, session_filter=session_filter,
    )


def _download(repo_id: str, files: list[str], source: Source, token: str | None) -> list[str]:
    """Materialize the selected files into cache_dir; return local paths."""
    dest = source.cache_path / repo_id.replace("/", "__")
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for f in files:
        local = hf_hub_download(
            repo_id=repo_id, filename=f, repo_type="dataset",
            local_dir=str(dest), token=token,
        )
        out.append(local)
    log.info("downloaded", repo_id=repo_id, n_files=len(out), dest=str(dest))
    return out
