"""Worker entry point. Run: python -m server.worker.cli_work [--config PATH]

Two kinds of work share this process:

- **Queue-driven** — the 'rollup' queue_jobs the ingest fold handler rings (ingest.md §5, §7):
  recompute a parent's subagent economics from its children. The primary fold is the ingest
  handler's now.
- **Timer-driven** — the OTel true-up and its prune (otel-intake design §4.4). No doorbell:
  the OTel stream is constant, so a timer is the honest trigger.
"""
import argparse

import structlog

from server.ingest.git.backfill import GapFillRunner
from server.ingest.git.client import GitHubClient
from server.ingest.git.config import load_config as load_github_config
from server.ingest.otlp.mapping import MappingRegistry
from server.storage.config import load_config as load_storage_config
from server.storage.engine import get_engine
from server.storage.queries import QueryCatalog

from .config import load_config
from .dispatch import Timer, run_forever
from .graph_fold import GraphFold, load_pricing_survivable
from .git_commit_stats import make_git_commit_stats_handler
from .git_map import git_map, prune_git_deliveries
from .liveness import close_idle_sessions
from .otel_trueup import OtelTrueUp
from .pr_class.config import load_config as load_pr_class_config
from .pr_class.correlate import make_git_correlate_handler
from .pr_class.pr_classify import make_pr_classify_handler
from .rollup import make_rollup_handler

log = structlog.get_logger(module="worker.cli_work")


def _load_github(path: str):
    try:
        return load_github_config(path)
    except FileNotFoundError:
        log.warning("github_config_absent", path=path)
        return None


def _load_pr_class(path: str):
    try:
        return load_pr_class_config(path)
    except FileNotFoundError:
        log.warning("pr_class_config_absent", path=path)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the rollup worker.")
    parser.add_argument("--config", default="config/worker.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    storage_cfg = load_storage_config(config.storage_config)
    get_engine(storage_cfg)
    catalog = QueryCatalog.load(storage_cfg.queries_dir)

    fold = GraphFold(pricing=load_pricing_survivable(config.pricing_org, config.pricing_version))
    available = {"rollup": make_rollup_handler(fold), "git_map": git_map}
    if "git_correlate" in config.topics or "pr_classify" in config.topics:
        pr_class_config = _load_pr_class(config.pr_class_config)
        if pr_class_config is not None:
            available["git_correlate"] = make_git_correlate_handler(pr_class_config)
            available["pr_classify"] = make_pr_classify_handler(pr_class_config)

    # One GitHubClient, shared by the git_commit_stats handler (Landing 2) and the gap-fill
    # timer below -- both authenticate the same installations against the same rate budget.
    github_client = None
    needs_github = "git_commit_stats" in config.topics or ("git_map" in config.topics and config.git_gapfill_enabled)
    if needs_github:
        github_config = _load_github(config.github_config)
        if github_config is not None:
            github_client = GitHubClient(github_config)
    if "git_commit_stats" in config.topics and github_client is not None:
        available["git_commit_stats"] = make_git_commit_stats_handler(github_client)
    handlers = {topic: available[topic] for topic in config.topics if topic in available}

    timers: list[Timer] = []
    if config.otel_trueup_enabled:
        trueup = OtelTrueUp(catalog, MappingRegistry.load(config.otel_configs_dir),
                           pricing_org=config.pricing_org, pricing_version=config.pricing_version)
        timers.append(("otel_trueup", config.otel_trueup_interval_s, trueup.tick))
        timers.append((
            "otel_prune", config.otel_prune_interval_s,
            lambda: trueup.prune(config.otel_points_retention_days),
        ))
    if config.idle_close_enabled:
        timers.append((
            "idle_close", config.idle_close_interval_s,
            lambda: close_idle_sessions(catalog, config.idle_close_after_s),
        ))
    if "git_map" in config.topics:
        timers.append((
            "git_prune", config.git_prune_interval_s,
            lambda: prune_git_deliveries(catalog, config.git_deliveries_retention_days),
        ))
        if config.git_gapfill_enabled and github_client is not None:
            gapfill = GapFillRunner(github_client, catalog)
            timers.append(("git_gapfill", config.git_gapfill_interval_s, gapfill.tick))

    run_forever(catalog, config, handlers, timers)


if __name__ == "__main__":
    main()
