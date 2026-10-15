"""trace_utils CLI — agent trace viewer.

Run as: python -m trace_utils.cli_trace <command> ...
Subcommands: datasets · explore · search · show · extract
(shell wrapper: utils/trace.sh)
"""
from __future__ import annotations

import sys
from typing import Optional

import typer

from . import config as cfgmod
from . import extract as extractmod
from . import logging as logmod
from . import render, search as searchmod, sessions as sessmod, sources
from .engine import Engine
from .profiler import profile

app = typer.Typer(add_completion=False, help="Agent trace viewer for Hugging Face datasets.")

_DEFAULT_CONFIG = "config/datasets.yaml"


def _load(config_path: str) -> cfgmod.Config:
    try:
        return cfgmod.load_config(config_path)
    except FileNotFoundError:
        # allow running with only a URL and no config file
        return cfgmod.Config()


def _dataset(cfg: cfgmod.Config, ds: str) -> cfgmod.Dataset:
    d = cfg.dataset(ds)
    if d:
        return d
    if "/" in ds:  # looks like a repo id or URL
        return cfgmod.Dataset(name=ds, url=ds)
    typer.echo(f"error: dataset {ds!r} not found in config and is not a URL/repo id", err=True)
    raise typer.Exit(2)


def _prepare(config_path: str, ds: str, download: bool, selector: sources.Selector):
    cfg = _load(config_path)
    dataset = _dataset(cfg, ds)
    source = cfg.source
    if download:
        source = cfgmod.Source(mode="download", cache_dir=source.cache_dir,
                               hf_token_env=source.hf_token_env)
    token = cfgmod.token_from_env(source)
    rs = sources.resolve(dataset, source, selector, token=token)
    engine = Engine(token=token)
    return cfg, dataset, source, rs, engine


@app.callback()
def _main(debug: bool = typer.Option(False, "--debug", help="verbose JSON logs")):
    logmod.configure("DEBUG" if debug else "INFO")


@app.command()
def datasets(config: str = typer.Option(_DEFAULT_CONFIG, "--config", "-c")):
    """List configured datasets and their file inventory."""
    from huggingface_hub import HfApi

    cfg = _load(config)
    if not cfg.datasets:
        typer.echo("no datasets configured (edit config/datasets.yaml)", err=True)
        raise typer.Exit(1)
    token = cfgmod.token_from_env(cfg.source)
    rows = []
    for d in cfg.datasets:
        repo_id, _ = sources.parse_repo(d.url)
        try:
            files = sources.list_files(d, token=token)
            fmt = "parquet" if any(f.endswith(".parquet") for f in files) else "jsonl"
            size = "—"
            try:
                total = sum(
                    e.size or 0
                    for e in HfApi(token=token).list_repo_tree(
                        repo_id, repo_type="dataset", recursive=True
                    )
                    if getattr(e, "size", None)
                )
                size = f"{total / 1e6:.1f} MB"
            except Exception:
                pass
            rows.append({"name": d.name, "repo_id": repo_id, "fmt": fmt,
                         "files": len(files), "size": size})
        except Exception as e:
            rows.append({"name": d.name, "repo_id": repo_id, "fmt": "ERR",
                         "files": str(e)[:40], "size": "—"})
    render.render_datasets(rows)


@app.command()
def explore(
    ds: str,
    config: str = typer.Option(_DEFAULT_CONFIG, "--config", "-c"),
    full: bool = typer.Option(False, "--full", help="scan more rows (up to 50k)"),
    sessions: bool = typer.Option(False, "--sessions", help="list sessions instead of the shape"),
    fields: bool = typer.Option(False, "--fields", help="show discovered field paths (default)"),
    glob: Optional[str] = typer.Option(None, "--glob"),
    session: Optional[str] = typer.Option(None, "--session"),
    download: bool = typer.Option(False, "--download"),
    limit: int = typer.Option(50, "--limit"),
):
    """Show a dataset's discovered shape (default) or list its sessions."""
    sel = sources.Selector(glob=glob, session=session)
    _, dataset, _, rs, engine = _prepare(config, ds, download, sel)
    title = f"{dataset.name} [{rs.fmt}, {len(rs.files)} files]"
    if sessions:
        render.render_sessions(sessmod.list_sessions(engine, rs, limit=limit), title)
    else:
        n = 50_000 if full else 200
        render.render_shape(profile(engine, rs, sample=n), title)


@app.command()
def search(
    ds: str,
    config: str = typer.Option(_DEFAULT_CONFIG, "--config", "-c"),
    where: Optional[str] = typer.Option(None, "--where", help='e.g. "message.model=claude-opus-4 && permissionMode=default"'),
    in_field: Optional[str] = typer.Option(None, "--in", help="field path to full-text search"),
    term: Optional[str] = typer.Argument(None, help="full-text term (with --in)"),
    regex: bool = typer.Option(False, "--regex", help="treat --in term as a regex"),
    glob: Optional[str] = typer.Option(None, "--glob"),
    session: Optional[str] = typer.Option(None, "--session"),
    download: bool = typer.Option(False, "--download"),
    limit: int = typer.Option(50, "--limit"),
):
    """Find sessions matching structured filters and/or a full-text term."""
    if (in_field is None) != (term is None):
        typer.echo("error: --in <field> and the search term must be given together", err=True)
        raise typer.Exit(2)
    if not where and in_field is None:
        typer.echo("error: provide --where and/or --in <field> <term>", err=True)
        raise typer.Exit(2)
    sel = sources.Selector(glob=glob, session=session)
    _, dataset, _, rs, engine = _prepare(config, ds, download, sel)
    results = searchmod.search(engine, rs, where=where, in_field=in_field,
                               in_term=term, regex=regex, limit=limit)
    render.render_sessions(results, f"matches in {dataset.name}")


@app.command()
def show(
    ds: str,
    session_id: str,
    config: str = typer.Option(_DEFAULT_CONFIG, "--config", "-c"),
    raw: bool = typer.Option(False, "--raw", help="dump raw event JSON"),
    download: bool = typer.Option(False, "--download"),
):
    """Render one session's transcript."""
    sel = sources.Selector(session=session_id)
    _, dataset, _, rs, engine = _prepare(config, ds, download, sel)
    cols, rows = sessmod.session_events(engine, rs, session_id)
    if not rows:
        typer.echo(f"no events found for session {session_id!r}", err=True)
        raise typer.Exit(1)
    render.render_transcript(cols, rows, session_id, raw=raw)


@app.command()
def extract(
    ds: str,
    out: str = typer.Option(..., "--out", help="output dir or file (local or s3://)"),
    config: str = typer.Option(_DEFAULT_CONFIG, "--config", "-c"),
    fmt: str = typer.Option("jsonl", "--format", help="jsonl | json | parquet"),
    where: Optional[str] = typer.Option(None, "--where"),
    in_field: Optional[str] = typer.Option(None, "--in"),
    term: Optional[str] = typer.Option(None, "--term"),
    glob: Optional[str] = typer.Option(None, "--glob"),
    session: Optional[str] = typer.Option(None, "--session"),
    download: bool = typer.Option(False, "--download"),
):
    """Pull selected sessions/files out to disk or S3."""
    sel = sources.Selector(glob=glob, session=session)
    _, dataset, _, rs, engine = _prepare(config, ds, download, sel)
    where_sql = searchmod.build_where_sql(where, in_field, term)
    path, n = extractmod.extract(engine, rs, out, fmt=fmt, where_sql=where_sql)
    typer.echo(f"wrote {n} rows -> {path}")


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())  # typer handles exit codes
