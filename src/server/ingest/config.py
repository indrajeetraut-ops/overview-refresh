"""Config models for ingest. Layering: defaults here -> YAML -> env (arch §12, ingest.md §9).

TODO(ingest): switch load_config to the shared config loader in src/common when that
loader lands (arch §12 "one shared loader").
"""
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_ENV_REF = re.compile(r"\$\{(\w+)\}")


class HttpConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8443


class OtlpConfig(BaseModel):
    """The OTLP receiver's stanza (otel-intake design §4.1).

    Every number here is an **isolation guard**: the OTel routes share a process with the
    JSONL path (D-OT1), so what bounds them is what protects the other signal source. The
    guards are cheap to breach safely — cumulative metrics are self-healing, since each
    datapoint carries the running total, so a shed interval costs freshness and never totals.
    """

    enabled: bool = True
    configs_dir: str = "config/otel"     # the per-vendor mapping files (§4.6)
    max_body_bytes: int = 8 * 1024 * 1024
    #: In-flight OTLP requests before shedding, on these routes only. 16 against a peak of
    #: ~65 posts/s (§6.2) leaves two orders of magnitude of headroom; the shed counter is the
    #: measured trigger for the D-OT1 process split.
    max_inflight: int = 16
    org_rate_per_s: float = 200.0        # per-org token bucket; every request is attributable
    org_burst: float = 400.0             # one fleet's synchronized export tick
    retry_after_s: int = 5               # what a shed/throttled client is told to wait


class IngestConfig(BaseModel):
    # storage + msging configs load from their own module YAML (config/storage.yaml,
    # config/msging.yaml); ingest references them, never re-declares a DSN (ingest.md §9).
    # The wire, auth, body limits and the queue bound are msging's now (its server stanza),
    # not re-declared here.
    long_turn_threshold_s: int = 1800        # fold: a turn over 30 min is "long" (worker plan §1)
    otlp: OtlpConfig = Field(default_factory=OtlpConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    storage_config: str = "config/storage.yaml"
    msging_config: str = "config/msging.yaml"
    # R2.2: the primary fold's price table (model_pricing catalog) for info.carry.carry_usd_micros.
    #: Org id for channel selection in `common.model_pricing.Pricer`; None = vendor channels.
    pricing_org: str | None = None
    #: Catalog version pin; None = current version.
    pricing_version: str | None = None
    # git-source acquisition (git-source.md §3): a host route, not its own process. Absent
    # config/github.yaml degrades to "not mounted" (like an absent pricing table) rather than
    # failing ingest to boot -- a deployment with no GitHub App configured yet is normal.
    github_config: str = "config/github.yaml"
    github_enabled: bool = True


def load_config(path: str | Path) -> IngestConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    data = yaml.safe_load(text) or {}
    return IngestConfig.model_validate(data.get("ingest", data))
