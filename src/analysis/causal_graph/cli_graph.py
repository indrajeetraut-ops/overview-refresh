"""Management CLI for building and loading causal-graph families.

This is the v0 trigger for the graph module (a Postgres hook lands in v1, §10). It wires the
already-built layers together and owns no graph logic of its own:

    build      <normalized_envelope.json> -o <out_dir>   builder + store: whole family to files
    build-all  <src_dir> -o <out_dir>                    every envelope under src_dir; dumps the
                                                          `common.metrics` anomaly-counter
                                                          snapshot at the end (plan Step 3/4)
    load       <master.json | out_dir>                   store -> in-memory Family, summarised
    api         <api_name> <src> [filters]               stable named-API JSON envelope

A family on disk is a master envelope beside a directory named for it (see
the private CLI file helpers), so a subagent is found from its master's path and many
families can share one output directory.

`<src>` for `query`/`load` is one of three things, told apart by inspecting it rather than by
guessing from the extension:

    a graph envelope .json      (has `nodes`)     -> loaded with its family from disk
    a normalizer envelope .json (has `sessions`)  -> built on the fly
    a directory                                   -> a single family's persisted store

Usage:
    python -m analysis.causal_graph.cli_graph build envelope.json -o graphs/
    python -m analysis.causal_graph.cli_graph build-all nrm_sess/ -o graphs/
    python -m analysis.causal_graph.cli_graph load graphs/<session>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

from common import metrics
from analysis.causal_graph.builder import build_graph
from analysis.causal_graph.context import GraphContext
from analysis.causal_graph.family import build_family
from analysis.causal_graph.models import Family
from analysis.causal_graph.named_api import (
    NamedApiError,
    budget,
    gitops,
    goods,
    list_tools,
    memory_usage,
    subagents,
    tool_usage,
    turns,
)
from analysis.causal_graph.query import query
from analysis.causal_graph._cli_store import SUBAGENT_DIR, dump_graph, family_paths, load_graph_dict

log = structlog.get_logger(module="analysis.causal_graph.cli_graph")

def _load_graph_context(src: str | Path) -> GraphContext:
    """Reassemble a :class:`Family` from persisted envelopes.

    Two forms:

    * **a master envelope file** — the primary one. Children are read from the
      ``<master>/subagents/`` directory beside it (:func:`store.family_paths`), so a family is
      addressed by one path and many families can share an output directory.
    * **a directory** — a single family's store, kept for callers that hold a directory rather
      than a file. The master is the graph whose ``parent_session_id`` is null.

    The directory form is only safe when the directory holds exactly one family. A flat
    directory of several masters cannot be disentangled -- every non-master file there looks
    like a child of whichever master sorts first -- so more than one master is an error rather
    than a warning: silently attributing another session's subagents to this master corrupts
    every rollup computed from it.
    """
    src = Path(src)
    if src.is_file():
        master = load_graph_dict(src)
        if master.get("parent_session_id") is not None:
            raise SystemExit(
                f"[graph] {src} is a subagent envelope (parent_session_id "
                f"{master.get('parent_session_id')!r}); load its master instead"
            )
        _, children_dir = family_paths(src.parent, master["session_id"])
        children: dict[str, dict] = {}
        if children_dir.is_dir():
            for path in sorted(children_dir.glob("*.json")):
                child = load_graph_dict(path)
                children[child["session_id"]] = child
        context = GraphContext()
        context.load_self(master)
        context.load_tree(lambda ids: {sid: children[sid] for sid in ids if sid in children})
        return context

    if not src.is_dir():
        raise SystemExit(f"[graph] source not found: {src}")

    graphs = [load_graph_dict(p) for p in sorted(src.glob("*.json"))]
    if not graphs:
        # The commonest way to land here: passing `<master-id>/` (which holds only
        # `subagents/`) instead of `<master-id>.json` beside it. Say so rather than
        # reporting an empty directory, which reads as "nothing was built".
        sibling = src.with_suffix(".json")
        if (src / SUBAGENT_DIR).is_dir() and sibling.is_file():
            raise SystemExit(
                f"[graph] {src} is a family's subagent directory, not a graph store. "
                f"Pass the master envelope instead: {sibling}"
            )
        raise SystemExit(f"[graph] no *.json envelopes found in {src}")

    masters = [g for g in graphs if g.get("parent_session_id") is None]
    if not masters:
        raise SystemExit(f"[graph] no master envelope (parent_session_id null) in {src}")
    if len(masters) > 1:
        raise SystemExit(
            f"[graph] {src} holds {len(masters)} master envelopes "
            f"({', '.join(g['session_id'] for g in masters[:3])}...); a directory load cannot tell "
            "which subagents belong to which. Pass one master .json instead."
        )
    master = masters[0]
    # Children may sit beside the master (a single-family dir) or under its subagents/ dir.
    children = {g["session_id"]: g for g in graphs if g is not master}
    _, children_dir = family_paths(src, master["session_id"])
    if children_dir.is_dir():
        for path in sorted(children_dir.glob("*.json")):
            child = load_graph_dict(path)
            children[child["session_id"]] = child
    context = GraphContext()
    context.load_self(master)
    context.load_tree(lambda ids: {sid: children[sid] for sid in ids if sid in children})
    return context


def load_family(src: str | Path) -> Family:
    """CLI compatibility helper; materialize through GraphContext and release ownership."""
    context = _load_graph_context(src)
    family = context.family
    context.unload_tree()
    return family


def _load_envelope(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _context_from_normalized(envelope: dict) -> GraphContext:
    sessions = envelope.get("sessions") or []
    masters = [session for session in sessions if session.get("parent_session_id") is None]
    if not masters:
        raise SystemExit("[graph] normalizer envelope has no master session")
    documents = {session["session_id"]: build_graph(session) for session in sessions}
    context = GraphContext()
    context.load_self(documents[masters[0]["session_id"]])
    context.load_tree(lambda ids: {sid: documents[sid] for sid in ids if sid in documents})
    return context


def _context_from_src(src: str | Path) -> GraphContext:
    """Load a persisted family or build a normalized envelope for a query."""
    path = Path(src)
    if path.is_dir():
        return _load_graph_context(path)
    if not path.is_file():
        raise SystemExit(f"[graph] source not found: {path}")
    envelope = _load_envelope(path)
    if "nodes" in envelope:
        return _load_graph_context(path)
    if "sessions" in envelope:
        return _context_from_normalized(envelope)
    raise SystemExit(f"[graph] {path} is neither a graph nor normalizer envelope")


# --- subcommands -----------------------------------------------------------

def dump_family(family: Family, out_dir: str | Path) -> Path:
    """Write a family to ``out_dir`` in the layout :func:`store.family_paths` defines.

    Master at the top, children under ``<master-id>/subagents/``. Returns the master's path,
    which is the handle every other command takes.
    """
    master_path, children_dir = family_paths(out_dir, family.master.session_id)
    master_path.parent.mkdir(parents=True, exist_ok=True)
    dump_graph(family.master, master_path)
    if family.children:
        children_dir.mkdir(parents=True, exist_ok=True)
        for graph in family.children.values():
            dump_graph(graph, children_dir / f"{graph.session_id}.json")
    return master_path


def _cmd_build(args: argparse.Namespace) -> None:
    envelope = _load_envelope(Path(args.envelope))
    family = build_family(envelope)
    master_path = dump_family(family, Path(args.out))

    summary = {
        "master": family.master.session_id,
        "children": len(family.children),
        "nodes": sum(len(g.nodes) for g in family.all_graphs()),
        "out": str(master_path),
    }
    log.info("graph_build", **summary)
    print(json.dumps(summary))


def _is_subagent_envelope(rel: Path) -> bool:
    """True for a normalized envelope that sits under a ``subagents/`` directory.

    Those sessions are already carried inside their master's envelope (the normalizer inlines
    the whole family), so building them again would emit orphan graphs whose spawns resolve to
    nothing and whose tokens double-count in any cross-session rollup.
    """
    return SUBAGENT_DIR in rel.parts


# Directory names that never hold a buildable session. Matched against every path part.
#
# `memory/` is the memory-directory transcript store. A master envelope can land there while
# its `subagents/` tree stays beside the ordinary sessions, which leaves the master with every
# spawn a dead end -- measured on the corpus: 62 spawn nodes, 0 carrying a `child_session_id`,
# because the normalizer could not resolve them either. Building it emits a graph nothing can
# traverse, so it is skipped as an input.
#
# This does NOT recover the 62 orphaned children: their master is in the wrong place, and
# `_is_subagent_envelope` correctly declines to build a subagent standalone. Fixing that is a
# source-layout question, not a builder one.
EXCLUDED_DIRS = frozenset({"memory"})


def _is_excluded_dir(rel: Path) -> bool:
    """True for an envelope under a directory that never holds a buildable session."""
    return bool(EXCLUDED_DIRS.intersection(rel.parts))


RESULTS_FILE = "run_results.json"


def _cmd_build_all(args: argparse.Namespace) -> None:
    """Build every normalizer envelope under a tree, and write a run report.

    The report mirrors the normalizer's `run_results.json` field-for-field -- `generated_at`,
    `source`, `target`, `total`, `counts`, `files[]` with `input`/`output`/`status`/`message`
    -- so the two batch tools can be read, diffed and consumed the same way. Graph-specific
    numbers (`master`, `children`, `nodes`) ride on the built rows as extras.
    """
    src_dir, out_dir = Path(args.src_dir), Path(args.out)
    if not src_dir.is_dir():
        raise SystemExit(f"[graph] source directory not found: {src_dir}")

    rows: list[dict] = []
    counts = {"built": 0, "skipped": 0, "error": 0}
    totals = {"masters": 0, "subagents": 0, "nodes": 0}

    # Both exclusions happen before the count, so `total` is inputs, not files:
    #   * subagent envelopes are already inside their master's file; building them again
    #     emits orphan graphs
    #   * EXCLUDED_DIRS never hold a buildable session
    all_json = sorted(src_dir.rglob("*.json"))
    candidates = [
        p for p in all_json
        if not _is_subagent_envelope(p.relative_to(src_dir))
        and not _is_excluded_dir(p.relative_to(src_dir))
    ]
    excluded = [p for p in all_json if _is_excluded_dir(p.relative_to(src_dir))]
    for p in excluded:
        print(f"[graph] excluded (never buildable): {p.relative_to(src_dir)}", flush=True)

    for path in candidates:
        rel = path.relative_to(src_dir)
        row: dict = {"input": str(rel), "output": None, "status": "error"}

        try:
            envelope = _load_envelope(path)
        except (OSError, json.JSONDecodeError) as exc:
            counts["error"] += 1
            rows.append({**row, "message": f"unreadable: {exc}"})
            print(f"[graph] ERROR reading {rel}: {exc}", flush=True)
            continue

        if not isinstance(envelope, dict) or "sessions" not in envelope:
            # run_results.json and anything else sharing the tree. Skipped by shape, so no
            # filename convention has to be maintained.
            counts["skipped"] += 1
            rows.append({**row, "status": "skipped", "message": "not a normalizer envelope"})
            print(f"[graph] skip (not an envelope): {rel}", flush=True)
            continue

        # Mirror the source tree so projects stay apart and session ids cannot collide.
        try:
            family = build_family(envelope)
            master_path = dump_family(family, out_dir / rel.parent)
        except Exception as exc:  # noqa: BLE001 -- one bad session must not stop the batch
            counts["error"] += 1
            rows.append({**row, "message": str(exc)})
            print(f"[graph] ERROR building {rel}: {exc}", flush=True)
            continue

        nodes = sum(len(g.nodes) for g in family.all_graphs())
        out_rel = master_path.relative_to(out_dir)
        counts["built"] += 1
        totals["masters"] += 1
        totals["subagents"] += len(family.children)
        totals["nodes"] += nodes
        rows.append({
            "input": str(rel),
            "output": str(out_rel),
            "status": "built",
            "master": family.master.session_id,
            "children": len(family.children),
            "nodes": nodes,
        })
        print(f"[graph] built {rel} -> {out_rel} "
              f"({len(family.children)} subagents, {nodes} nodes)", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / RESULTS_FILE
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(src_dir),
        "target": str(out_dir),
        "total": len(candidates),
        "counts": counts,
        "totals": totals,
        "files": rows,
    }
    results_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    log.info("graph_build_all", **counts, **totals,
             source=str(src_dir), target=str(out_dir))
    print(
        f"[graph] {counts['built']} built, {counts['skipped']} skipped, "
        f"{counts['error']} error(s) -- {totals['masters']} masters, "
        f"{totals['subagents']} subagents, {totals['nodes']} nodes "
        f"-- results: {results_path}"
    )

    # The anomaly-counter snapshot for the whole batch (plan Step 3/4): every `causal_graph.*`
    # series accumulated across every session built above, plus whatever msging series this
    # process happened to touch (one shared registry, see `common/metrics.py`). No exposure
    # machinery here by decision -- an HTTP endpoint, a scraper or a periodic dump thread is
    # explicitly out of scope; this is the one place the registry is read back and shown.
    snapshot = metrics.metrics()
    print(f"[graph] metrics: {json.dumps(snapshot, indent=2, sort_keys=True)}")
    log.info("graph_build_all_metrics", metrics=snapshot)

    if counts["error"]:
        raise SystemExit(1)


def _cmd_load(args: argparse.Namespace) -> None:
    context = _load_graph_context(args.src)
    try:
        family = context.family
        summary = {
            "master": family.master.session_id,
            "children": len(family.children),
            "nodes": sum(len(g.nodes) for g in family.all_graphs()),
        }
        log.info("graph_load", **summary)
        print(json.dumps(summary))
    finally:
        context.unload_tree()


def _scope_spec(value: str) -> dict:
    return {"kind": value} if value.casefold() in {"self", "tree", "subagents"} else {"kind": "session", "session_id": value}


def _inline_spec(args: argparse.Namespace) -> dict:
    if not args.select:
        raise SystemExit("[graph] --select is required unless --query is provided")
    result: dict = {
        "version": "1", "scope": _scope_spec(args.scope),
        "select": {"type": args.select},
        "return": {"kind": "groups" if args.group_by else "nodes", "verbosity": args.return_kind},
    }
    if args.where: result["select"]["where"] = args.where
    if args.group_by: result["group_by"] = args.group_by
    if args.having: result["having"] = args.having
    if args.limit: result["limit"] = args.limit
    if args.aggregate:
        aggregates = []
        for value in args.aggregate:
            name, separator, function = value.partition("=")
            if not separator: raise SystemExit("[graph] --aggregate must be name=FUNCTION[:field]")
            function, _, field = function.partition(":")
            aggregate = {"name": name, "function": function}
            if field: aggregate["field"] = field
            aggregates.append(aggregate)
        result["aggregates"] = aggregates
    if args.order_by:
        result["order_by"] = [{"field": value.rsplit(":", 1)[0], "direction": value.rsplit(":", 1)[1] if ":" in value else "asc"} for value in args.order_by]
    if args.group_by: result["return"]["include_members"] = args.include_members
    return result


def _cmd_query(args: argparse.Namespace) -> None:
    if args.query_file and args.select:
        raise SystemExit("[graph] use either --query or inline flags, not both")
    try:
        spec = json.loads(Path(args.query_file).read_text(encoding="utf-8")) if args.query_file else _inline_spec(args)
        context = _context_from_src(args.src)
        try:
            result = query(context.family, spec)
        finally:
            context.unload_tree()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"[graph] {exc}") from None
    print(json.dumps(result.model_dump(mode="json", by_alias=True, exclude_none=True)))


def _named_scope(value: str) -> str | dict[str, str]:
    """Translate the CLI's explicit session form into the named-API scope object."""
    if value.startswith("session:"):
        return {"kind": "session", "session_id": value.removeprefix("session:")}
    return value


def _cmd_api(args: argparse.Namespace) -> None:
    """Invoke one stable named API and serialize its public envelope."""
    context = _context_from_src(args.src)
    family = context.family
    scope = _named_scope(args.scope) if hasattr(args, "scope") else None
    try:
        if args.api_name == "budget":
            turn = None
            if args.turn_session_id is not None or args.turn_num is not None:
                turn = {"session_id": args.turn_session_id, "turn_num": args.turn_num}
            result = budget(
                family,
                scope=scope,
                by=args.by,
                model=args.model,
                permission_mode=args.permission_mode,
                turn=turn,
            )
        elif args.api_name == "goods":
            result = goods(
                family,
                scope=scope,
                artifact_class=args.artifact_class,
                extension=args.extension,
            )
        elif args.api_name == "memory_usage":
            result = memory_usage(family, scope=scope)
        elif args.api_name == "list_tools":
            result = list_tools(family, scope=scope)
        elif args.api_name == "tool_usage":
            result = tool_usage(
                family,
                scope=scope,
                tool_class=args.tool_class,
                name=args.name,
                outcome=args.outcome,
                invocation_matches=args.invocation_matches,
                limit=args.limit,
            )
        elif args.api_name == "turns":
            result = turns(
                family,
                turn_id={"session_id": args.session_id, "turn_num": args.turn_num},
                node_type=args.node_type,
            )
        elif args.api_name == "subagents":
            result = subagents(family, scope=scope)
        elif args.api_name == "gitops":
            result = gitops(
                family,
                scope=scope,
                operation=args.operation,
                limit=args.limit,
            )
        else:  # pragma: no cover - argparse constrains the subcommand
            raise AssertionError(f"unhandled named API: {args.api_name}")
    except NamedApiError as exc:
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        context.unload_tree()
    print(json.dumps(result))


def _add_scope_argument(parser: argparse.ArgumentParser, *, default: str = "tree") -> None:
    parser.add_argument(
        "--scope",
        default=default,
        help="self | tree | subagents | session:<session_id>",
    )


def _add_named_api_parsers(sub: argparse._SubParsersAction) -> None:
    api = sub.add_parser("api", help="invoke a stable named API")
    apis = api.add_subparsers(dest="api_name", required=True)

    budget_parser = apis.add_parser("budget", help="report token usage")
    budget_parser.add_argument("src")
    _add_scope_argument(budget_parser)
    budget_parser.add_argument("--by", default="model")
    budget_parser.add_argument("--model")
    budget_parser.add_argument("--permission-mode")
    budget_parser.add_argument("--turn-session-id")
    budget_parser.add_argument("--turn-num", type=int)
    budget_parser.set_defaults(func=_cmd_api)

    goods_parser = apis.add_parser("goods", help="report produced local artifacts")
    goods_parser.add_argument("src")
    _add_scope_argument(goods_parser)
    goods_parser.add_argument("--artifact-class")
    goods_parser.add_argument("--extension")
    goods_parser.set_defaults(func=_cmd_api)

    memory_parser = apis.add_parser("memory_usage", help="report classified harness-memory activity")
    memory_parser.add_argument("src")
    _add_scope_argument(memory_parser)
    memory_parser.set_defaults(func=_cmd_api)

    list_tools_parser = apis.add_parser("list_tools", help="list distinct harnesses and commands")
    list_tools_parser.add_argument("src")
    _add_scope_argument(list_tools_parser)
    list_tools_parser.set_defaults(func=_cmd_api)

    tool_usage_parser = apis.add_parser("tool_usage", help="inspect individual tool invocations")
    tool_usage_parser.add_argument("src")
    _add_scope_argument(tool_usage_parser)
    tool_usage_parser.add_argument("--tool-class")
    tool_usage_parser.add_argument("--name")
    tool_usage_parser.add_argument("--outcome", default="all")
    tool_usage_parser.add_argument("--invocation-matches")
    tool_usage_parser.add_argument("--limit", type=int)
    tool_usage_parser.set_defaults(func=_cmd_api)

    turns_parser = apis.add_parser("turns", help="report one turn")
    turns_parser.add_argument("src")
    turns_parser.add_argument("--session-id", required=True)
    turns_parser.add_argument("--turn-num", type=int, required=True)
    turns_parser.add_argument("--node-type")
    turns_parser.set_defaults(func=_cmd_api)

    subagents_parser = apis.add_parser("subagents", help="list master-session spawn records")
    subagents_parser.add_argument("src")
    _add_scope_argument(subagents_parser, default="self")
    subagents_parser.set_defaults(func=_cmd_api)

    gitops_parser = apis.add_parser("gitops", help="list structured Git operations")
    gitops_parser.add_argument("src")
    _add_scope_argument(gitops_parser)
    gitops_parser.add_argument("--operation")
    gitops_parser.add_argument("--limit", type=int)
    gitops_parser.set_defaults(func=_cmd_api)


def _configure_logging() -> None:
    """Send logs to stderr so stdout carries the answer and nothing else.

    structlog's default factory prints to stdout. Left alone, a `budget` result and a
    `budget_computed` debug line interleave on the same stream and `| jq` fails on the log
    line -- and redirecting stderr does not help, because the log was never on stderr. A CLI
    whose output cannot be piped is not usable in a pipeline, so this is a correctness fix,
    not cosmetics.
    """
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def main(argv: list[str] | None = None) -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(
        prog="cli_graph",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="build a family from an envelope and persist it")
    p_build.add_argument("envelope", help="normalizer envelope JSON {meta_info, sessions[]}")
    p_build.add_argument("-o", "--out", required=True, help="output directory for envelopes")
    p_build.set_defaults(func=_cmd_build)

    p_all = sub.add_parser(
        "build-all", help="build every normalizer envelope under a directory tree"
    )
    p_all.add_argument("src_dir", help="directory of normalized sessions (recursed)")
    p_all.add_argument("-o", "--out", required=True, help="output root; source tree is mirrored")
    p_all.set_defaults(func=_cmd_build_all)

    p_load = sub.add_parser("load", help="load a persisted family and summarise it")
    p_load.add_argument("src", help="a master graph .json (subagents found beside it), or a dir")
    p_load.set_defaults(func=_cmd_load)

    p_query = sub.add_parser("query", help="run a versioned QuerySpec over a family")
    p_query.add_argument("src", help="a graph .json, a normalizer envelope .json, or a graph dir")
    p_query.add_argument("--query", dest="query_file", help="complete QuerySpec JSON file")
    p_query.add_argument("--select", help="initial node type for an inline QuerySpec")
    p_query.add_argument("--where", help="inline select filter expression")
    p_query.add_argument("--scope", default="tree", help="self | tree | subagents | session id")
    p_query.add_argument("--return", dest="return_kind", default="summary", choices=("ref", "summary", "full"), help="node/group verbosity")
    p_query.add_argument("--group-by", action="append", help="group field (repeat up to three times)")
    p_query.add_argument("--aggregate", action="append", help="name=FUNCTION[:field]")
    p_query.add_argument("--having", help="group filter expression")
    p_query.add_argument("--order-by", action="append", help="field[:asc|desc]")
    p_query.add_argument("--limit", type=int)
    p_query.add_argument("--include-members", action="store_true")
    p_query.set_defaults(func=_cmd_query)

    _add_named_api_parsers(sub)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
