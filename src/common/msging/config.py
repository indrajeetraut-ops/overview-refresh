"""Config loading for the msging library (design §5.1, §5.2, §10.1).

Layering: defaults in code -> YAML -> environment variables. `${VAR}` and
`${VAR:-default}` references are substituted directly in the YAML text before
parsing, so they can appear as a whole scalar value (`key: ${VAR}`) or embedded
inside one (`key: ${VAR}/sub/path`).

A "server alias" names one client binding = endpoint + service_name (design §5.1).
It is a role name, never an environment name -- that is why `default_server`
exists and why callers never hardcode an alias literal.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from common.msging.errors import ConfigError

_ENV_REF = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(:-(?P<default>[^}]*))?\}")


def _interpolate_env(text: str) -> str:
    """Substitute `${VAR}` / `${VAR:-default}` in raw YAML text.

    Decision: an unset `${VAR}` with no default substitutes to an empty string,
    never raises. A missing *optional* override should not crash startup. If the
    reference is the entire scalar value, the resulting blank line parses as
    YAML null (fine -- every such field in this module is Optional). If it is
    embedded in a larger scalar (e.g. a path prefix), it simply vanishes,
    leaving the remaining text as valid YAML. A field that is actually required
    (e.g. `base_path`) still fails loudly: it fails pydantic validation with a
    clear missing-field error, not a silent empty string.

    TODO: an embedded, unset `${VAR}` with no default (e.g.
    `path: ${TALIUM_DATA_DIR}/producers.json` when TALIUM_DATA_DIR is unset)
    silently substitutes to "", yielding a plausible-but-wrong path
    (`/producers.json`) instead of a config error -- it fails later with a
    confusing filesystem permission error, not a clear one here. A future
    change should distinguish "optional override" (fine to blank out) from
    "required substitution" (should fail config loading if unset).
    """

    def _replace(m: re.Match[str]) -> str:
        name = m.group("name")
        default = m.group("default")
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        return ""

    return _ENV_REF.sub(_replace, text)


class ClientDefaults(BaseModel):
    """Per-server settings, applied to every server unless overridden (§5.1)."""

    model_config = ConfigDict(extra="forbid")

    req_timeout_s: float = 10.0
    retry_num: int = 3
    max_pending: int = 10000
    slot_wait_s: float = 1.0
    max_in_flight: int = 64
    sent_timeout_s: float = 300.0
    close_timeout_s: float = 30.0
    msg_id_block: int = 1000
    # §4.2's idle-read timer, expressed as a MULTIPLE of the server's keepalive
    # rather than as an absolute.
    #
    # The absolute form could not be right in two files at once. The constraint
    # is a *relationship* -- the client must be willing to wait through several
    # heartbeats before calling a healthy link dead -- but the two numbers were
    # owned by two configs in two repos, so an operator raising the server's
    # `heartbeat_s` to cut chatter silently put every producer on the default
    # into a permanent teardown/rebuild cycle: reconnect every idle_timeout_s
    # forever, with the backoff curve never escalating (each stream outlives
    # backoff_base_s and resets the attempt counter) and nothing alarming but
    # an sse_reconnects_total nobody watches.
    #
    # `/register` already returns the server's `heartbeat_s` (§6.1: "the
    # response carries the server's limits so client and server configs cannot
    # drift"), so the timer is derived from it and the relationship holds by
    # construction. 5 heartbeats of grace: a single late or dropped keepalive
    # must never tear down a healthy stream, and §4.2's "~3x" is the floor
    # rather than the target.
    idle_heartbeats: float = Field(default=5.0, ge=2.0)
    #: Fallback only, for when the server's `heartbeat_s` is unknown -- an
    #: identity written before it was persisted, or a server that does not
    #: report it. Never used when a heartbeat is available; see
    #: `_idle_timeout_for` in clnt/api.py.
    idle_timeout_s: float = 45.0
    sweep_interval_s: float = 5.0
    gzip_min_bytes: int = 1024
    # Not in the §5.1 YAML example -- Task 7's durable store needs a root.
    base_dir: str = "~/.talium/msging"


class ServerBinding(BaseModel):
    """One alias's binding: endpoint + service_name, plus optional per-server
    overrides of any `ClientDefaults` key (design §5.1)."""

    model_config = ConfigDict(extra="allow")

    alias: str
    base_path: str
    service_name: str | None = None
    enabled: bool = True


class ClientConfig(BaseModel):
    default_server: str | None = None
    servers: dict[str, ServerBinding] = Field(default_factory=dict)
    defaults: ClientDefaults = Field(default_factory=ClientDefaults)

    @model_validator(mode="before")
    @classmethod
    def _fill_server_identity(cls, data: Any) -> Any:
        """`alias` and `service_name` both default to the servers-dict key."""
        if not isinstance(data, dict):
            return data
        servers = data.get("servers") or {}
        filled = {}
        for alias, spec in servers.items():
            spec = dict(spec or {})
            spec.setdefault("alias", alias)
            spec.setdefault("service_name", alias)
            filled[alias] = spec
        return {**data, "servers": filled}

    def settings_for(self, alias: str) -> ClientDefaults:
        """Per-server keys merged over `defaults`. Unknown alias -> ConfigError.

        Note: this does not check `enabled` -- a disabled binding's settings
        are still well-defined. The enabled/unknown/unset alias check belongs
        to the caller that resolves `default_server` for register() (§6.1),
        not to this merge.
        """
        binding = self.servers.get(alias)
        if binding is None:
            raise ConfigError(f"unknown server alias: {alias!r}")
        overrides = binding.model_extra or {}
        merged = self.defaults.model_dump()
        merged.update(overrides)
        try:
            return ClientDefaults.model_validate(merged)
        except ValidationError as exc:
            bad_keys = ", ".join(
                str(loc)
                for err in exc.errors()
                for loc in err.get("loc", ())
                if err.get("type") == "extra_forbidden"
            )
            if not bad_keys:
                bad_keys = str(exc)
            raise ConfigError(
                f"server alias {alias!r} has invalid override key(s): {bad_keys} "
                "-- not a recognized ClientDefaults setting (typo?)"
            ) from exc


class RegistrationConfig(BaseModel):
    """§10.1. v1 ships `mode: open`; `enrollment_key` requires `key`."""

    model_config = ConfigDict(extra="forbid")

    mode: str = "open"
    key: str | None = None
    # `mode: open` means anyone who reaches the port is issued an identity, and
    # every identity is a permanent row in the producer store that the auth path
    # reads. Capping the *size* of one descriptor bounds nothing on its own: the
    # number of them was unbounded, so a loop of anonymous POSTs grew
    # producers.json without limit and every request -- including a rejected one
    # -- paid for the growth. These two bound the count. Neither is a security
    # boundary (that is `enrollment_key`); they are the blast radius of a
    # hostile caller, and equally of a buggy client in a retry loop.
    #
    # 0 means unlimited on both, which is the escape hatch for a deployment that
    # genuinely has more producers than this and controls its own perimeter.
    #
    # 1000 is sized against the *file* backend, not against fleet ambition: at
    # the 8 KiB descriptor cap that is an 8 MB producers.json, and every upsert
    # and every `touch_last_seen` rewrites it whole. Ten times that would be a
    # 75 MB rewrite on the auth path. A deployment with more producers than this
    # wants `backend: postgres` and a raised cap, in that order -- raising it on
    # the file backend just moves the cliff.
    max_producers: int = Field(default=1000, ge=0)
    rate_per_min: int = Field(default=60, ge=0)


class ServerConfig(BaseModel):
    # A typo'd key must fail loudly, not be silently dropped -- same rule as
    # ClientDefaults. `workrs: 16` is a bug, not a comment.
    model_config = ConfigDict(extra="forbid")

    # Bounds are not decoration. A zero or negative here does not fail at load,
    # it surfaces much later as a crash or a black hole: notify_buffer=-1 reaches
    # deque(maxlen=-1) and makes publish() raise inside a queue worker, and
    # notify_ttl_s=0 expires every notice on arrival even with a live subscriber.
    queue_max: int = Field(default=10000, gt=0)
    # Concurrent handler tasks (§5.2). In-flight msgs sit outside queue_max, so
    # accepted-but-unprocessed peaks at queue_max + workers (§6.2).
    workers: int = Field(default=4, gt=0)
    retry_after_s: int = Field(default=5, ge=0)
    heartbeat_s: float = Field(default=15.0, gt=0)
    max_msg_bytes: int = Field(default=33554432, gt=0)
    # Ceiling on one serialised notify_ev (§8.2). Not symmetric with
    # max_msg_bytes: this one exists because the *client's* SSE parser caps one
    # event at 64 KiB and a frame over that is not resynchronisable, so an app
    # returning an oversized `info` bag would drop that producer's link on
    # every delivery attempt -- a permanent reconnect loop escaped only by
    # sent_timeout_s. Must stay comfortably under the client's cap, with room
    # for the `event:`/`data:` framing around the JSON.
    #
    # The `le` is the enforcement of that "must". The two constants live in
    # packages that are forbidden to import each other (clnt/sse.py's
    # MAX_EVENT_BYTES = 65536), so a comment was the only thing holding the
    # invariant and a comment does not survive an operator raising this for a
    # big `info` bag. 65000 leaves ~536 bytes for the `event: notify\ndata: `
    # framing and the trailing blank line. test_router.py imports the client
    # constant and asserts against this bound, so the pair cannot drift.
    max_notice_bytes: int = Field(default=32768, gt=0, le=65000)
    # 0 is legal and means "hold nothing" -- a setting, not the bug above.
    notify_buffer: int = Field(default=100, ge=0)
    notify_ttl_s: float = Field(default=60.0, gt=0)
    # Successful verify() results held in process (§10.3's short-TTL cache).
    # Bounded because the entries were not: one went in per distinct key and
    # nothing ever took one out, each pinning a whole ProducerRecord --
    # descriptor included -- for the life of the process. LRU + expiry sweep now
    # bound it at this many, so the worst case is a number you can multiply out
    # (`auth_cache_max` x descriptor size) rather than "however many keys were
    # ever presented". 0 disables the cache entirely, which is a legal setting:
    # every verify() then hits the store.
    auth_cache_max: int = Field(default=1024, ge=0)
    # §6.2/§6.3: how long srv_shutdown() lets a live /notify stream flush its
    # ring before the streams are ended so the ASGI server can drain. 0 = end
    # them at once, forfeiting whatever was still buffered (the client's
    # sent_timeout_s recovers it, §4.4).
    shutdown_drain_s: float = Field(default=5.0, ge=0)
    # Whether srv_start() chains srv_shutdown() onto the host's own SIGINT/
    # SIGTERM handler. On by default because without it the documented §6.3
    # wiring cannot terminate at all with a producer connected -- see
    # `api.srv_shutdown`. Set false when the host drives shutdown itself.
    shutdown_signals: bool = True
    registration: RegistrationConfig = Field(default_factory=RegistrationConfig)


class ProducerStoreFileConfig(BaseModel):
    path: str = "~/.talium/msging/producers.json"


class ProducerStoreConfig(BaseModel):
    """§5.2. `file` = default backend, `postgres` = option (Task 9 owns the
    postgres adapter; this stanza is a config placeholder only)."""

    backend: str = "file"
    file: ProducerStoreFileConfig = Field(default_factory=ProducerStoreFileConfig)
    postgres: dict = Field(default_factory=dict)


class MsgingConfig(BaseModel):
    client: ClientConfig = Field(default_factory=ClientConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    producer_store: ProducerStoreConfig = Field(default_factory=ProducerStoreConfig)


def load_config(path: str | Path) -> MsgingConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _interpolate_env(text)
    data = yaml.safe_load(text) or {}
    try:
        return MsgingConfig.model_validate(data)
    except ValidationError as exc:
        bad = ", ".join(
            ".".join(str(loc) for loc in err.get("loc", ()))
            for err in exc.errors()
            if err.get("type") == "extra_forbidden"
        )
        if bad:
            raise ConfigError(
                f"{path}: unrecognized config key(s): {bad} (typo?)"
            ) from exc
        raise ConfigError(f"{path}: {exc}") from exc
