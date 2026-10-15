"""Config models for the worker (ingest.md §9).

TODO(worker): switch load_config to the shared config loader in src/common when that
loader lands (arch §12 "one shared loader").
"""
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_ENV_REF = re.compile(r"\$\{(\w+)\}")


class WorkerConfig(BaseModel):
    topics: list[str] = Field(default_factory=lambda: ["rollup"])
    poll_interval_s: float = 1.0     # bounds rollup latency between triggers (§5)
    claim_batch: int = 1             # jobs claimed per poll per process
    max_attempts: int = 5            # then status='failed' + alarm
    retry_backoff_s: float = 5.0     # flat backoff between attempts
    storage_config: str = "config/storage.yaml"
    # R2.2: the rollup-path fold's price table (model_pricing catalog) for
    # info.carry.carry_usd_micros -- loaded independently of otel_trueup's, since the two
    # consumers can tolerate different availability (an absent catalog degrades each on its own).
    #: Org id for channel selection in `common.model_pricing.Pricer`; None = vendor channels.
    pricing_org: str | None = None
    #: Catalog version pin; None = current version.
    pricing_version: str | None = None

    # OTel true-up (otel-intake design §4.4). Timer-driven, not queue-driven: the OTel stream
    # is constant, so there is nothing a doorbell would tell us that the next tick would not.
    otel_trueup_enabled: bool = True
    otel_trueup_interval_s: float = 60.0     # with the client's 60 s export: spend truth ~1-2 min behind
    otel_configs_dir: str = "config/otel"    # the vendor mappings the fold groups measures by
    otel_points_retention_days: int = 30      # staging buffer, not a ledger (§4.3)
    otel_prune_interval_s: float = 3600.0     # deleting a day's points once an hour is plenty

    # Idle close-out (system-architecture.md §8.5). Timer-driven for the same reason as the
    # OTel true-up: idle is an absence of events, so no doorbell ever rings for it.
    idle_close_enabled: bool = True
    idle_close_after_s: float = 3600.0        # 1h with no event -> live becomes idle_closed
    idle_close_interval_s: float = 300.0      # check every 5 min; cheap, well under the 1h window

    # git-source delivery retention (git-source.md §10.3). Timer-driven, same reasoning as the
    # OTel prune: `done`/`unmapped` rows are pruned on a schedule, not by a doorbell.
    git_deliveries_retention_days: int = 14
    git_prune_interval_s: float = 3600.0

    # git-source gap-fill (git-source.md §8.2, §10 -- the PRIMARY delivery-loss recovery path,
    # not an optimization: GitHub's own redelivery reaches only 3 days back). Timer-driven, one
    # REST sweep per known repo per tick. Absent config/github.yaml degrades to "disabled",
    # same posture as the pricing-table loader.
    git_gapfill_enabled: bool = True
    git_gapfill_interval_s: float = 900.0     # 15 min: cheap ETag-conditional sweep
    github_config: str = "config/github.yaml"

    # pr-work-classification (docs/modules/pr-work-classification.md, implementation plan B).
    # `git_correlate` in `topics` activates the SHA correlator; absent config/pr_class.yaml
    # degrades to "not registered," same posture as the github/pricing config loaders.
    pr_class_config: str = "config/pr_class.yaml"


def load_config(path: str | Path) -> WorkerConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    data = yaml.safe_load(text) or {}
    return WorkerConfig.model_validate(data.get("worker", data))
