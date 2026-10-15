"""Binding registry: maps a server alias to its endpoint and owns the
on-disk identity for each `(server_alias, producer_id)` pair (design §3, §5.1).

A "server alias" names ONE binding = endpoint + `service_name`. Two aliases
are two fully independent bindings: separate `producer_id`, api_key, store
directory, and SSE channel. A client may use several at once. The alias is a
role name, never an environment name -- switching staging -> prod is a
config/env-var change, never a code change, which is why `default_server`
exists and `resolve(None)` is the normal path (design §3, §5.1).

Store layout (design §3):

    <base>/<server_alias>/<producer_id>/
      identity.json     # { producer_id, api_key, server_alias,        0600
                         #   base_path, service_name, registered_at }
      msg_id.json
      pending/ sent/ nacked/

`identity.json` records not just the identity but the binding it was issued
against, so the mapping survives someone renaming a directory on disk.
`load_all()` scans `<base>/*/*/identity.json` at startup to rebuild the
`producer_id -> binding` map; that map is why `send()`, `close()`, `flush()`,
`set_info()` and `on_notify()` need only a `producer_id`, never an alias.

`config.settings_for()` deliberately does not raise on a disabled alias --
only on unknown (see config.py docstring). `resolve()` here is where
`enabled: false` raises `ConfigError` (design §6.1: register() raises on
unset, unknown, or disabled).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from common.msging.durable import private_dir, write_atomic
from common.msging.config import ClientConfig, ServerBinding
from common.msging.errors import ConfigError
from common.msging.log import get_logger

_logger = get_logger(__name__)

_IDENTITY_FILENAME = "identity.json"


def _utc_now_iso() -> str:
    """Matches the `Z`-suffixed, second-precision style used elsewhere in
    this store's own JSON examples (design §3), e.g. store.py's `created_at`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Binding:
    """One producer's identity, as recorded in its `identity.json` (design
    §3). This is the `load_all()` return value -- deliberately distinct from
    `ServerBinding` in config.py, which is *configured* endpoint info with no
    producer_id/api_key. A `Binding` is what was actually issued by the
    server and persisted to disk.
    """

    producer_id: str
    api_key: str
    alias: str
    base_path: str
    service_name: str | None
    registered_at: str
    store_dir: Path
    #: The server's `max_msg_bytes`, as returned by `/register` (design §6.1:
    #: "the response carries the server's limits so client and server configs
    #: cannot drift"). Persisted because §8.1's `too_large` is checked
    #: client-side against it, and a process that reuses a stored identity
    #: never sees a `/register` response to learn it from. `None` for an
    #: identity written before this field existed -- the caller falls back and
    #: the server's 413 backstops it.
    max_msg_bytes: int | None = None
    #: The server's SSE keepalive interval (§4.2), also from `/register`.
    #: Persisted for the same reason as `max_msg_bytes`: a process that
    #: reattaches to a stored identity never sees a `/register` response, and
    #: this is the value `register()` checks `idle_timeout_s` against. `None`
    #: for an identity written before this field existed -- the check is
    #: skipped rather than guessed at.
    heartbeat_s: float | None = None


class BindingRegistry:
    """Resolves server aliases to config, and owns the identity files under
    `base_dir` (design §3). One registry per process; `clnt/` code should not
    need to know the on-disk layout beyond going through this class.
    """

    def __init__(self, cfg: ClientConfig, base_dir: Path) -> None:
        self._cfg = cfg
        self._base_dir = Path(base_dir)

    # -- config resolution --------------------------------------------------

    def resolve(self, alias: str | None) -> ServerBinding:
        """`None` -> `client.default_server`. Raises `ConfigError` if the
        alias is unset, unknown, or disabled (design §6.1).

        This is the one place `enabled: false` is enforced -- `config.py`'s
        `settings_for()` intentionally does not check it (a disabled
        binding's settings are still well-defined; only the *use* of it as a
        live binding is illegal, and that check belongs here).
        """
        if alias is None:
            alias = self._cfg.default_server
            if alias is None:
                raise ConfigError(
                    "no server alias given and client.default_server is unset"
                )
        binding = self._cfg.servers.get(alias)
        if binding is None:
            raise ConfigError(f"unknown server alias: {alias!r}")
        if not binding.enabled:
            raise ConfigError(f"server alias {alias!r} is disabled")
        return binding

    def base_path_for(self, alias: str) -> str:
        """The **configured** `base_path` for `alias`, enabled or not.

        `check_stale()` answers "is this stored one wrong?" one identity at a
        time, and it logs when the answer is yes. A caller choosing *between*
        several stored identities for the same alias needs the configured
        value itself, without emitting an error line per candidate it looks
        at -- picking the right one is not an incident. Same lookup rule as
        `check_stale`: only a genuinely unknown alias raises.
        """
        binding = self._cfg.servers.get(alias)
        if binding is None:
            raise ConfigError(f"unknown server alias: {alias!r}")
        return binding.base_path

    # -- on-disk layout -------------------------------------------------

    def dir_for(self, alias: str, producer_id: str) -> Path:
        """`<base>/<server_alias>/<producer_id>/` (design §3 sketch)."""
        return self._base_dir / alias / producer_id

    # -- identity persistence ------------------------------------------

    def save_identity(self, alias: str, identity: dict) -> None:
        """Write `identity.json` for `identity['producer_id']` under
        `alias`'s store directory, at mode 0600 (design §3 -- this is the
        one file in the store that holds a secret, the plaintext api_key).

        The record persists not just the identity but the binding it was
        issued against (`server_alias`, `base_path`, `service_name`,
        `registered_at`) so the mapping survives someone renaming a
        directory, and so `check_stale()` has something to compare against.
        `alias` must resolve (unknown/disabled aliases cannot be saved
        against) -- `save_identity` is only ever called right after a
        successful `register()`, which already resolved it, but re-checking
        here means this method has no way to write a record for a binding
        that is not actually live.
        """
        binding = self.resolve(alias)
        producer_id = identity["producer_id"]
        record = {
            "producer_id": producer_id,
            "api_key": identity["api_key"],
            "server_alias": alias,
            "base_path": binding.base_path,
            "service_name": binding.service_name,
            "registered_at": identity.get("registered_at") or _utc_now_iso(),
            # The server's limits travel with the identity: see Binding.
            "max_msg_bytes": identity.get("max_msg_bytes"),
            "heartbeat_s": identity.get("heartbeat_s"),
        }

        store_dir = self.dir_for(alias, producer_id)
        # 0700, not the umask default: identity.json is 0600, but a file's mode
        # does not stop anyone with write access to the DIRECTORY from deleting
        # and replacing it -- with an attacker's base_path and api_key, which
        # silently redirects this producer's whole msg stream.
        private_dir(store_dir)
        self._write_identity(store_dir, record)

    def _write_identity(self, store_dir: Path, record: dict) -> None:
        """Durable, never-briefly-world-readable create of `identity.json`.

        `durable.write_atomic`'s `write tmp -> fsync tmp -> rename -> fsync
        dir` sequence (design §3.1), for the same reason every other
        persistent file here uses it: a partially written file must never be
        visible under its real name, and a crash mid-write must not leave a
        corrupt `identity.json` on disk in the file's place.

        Mode safety is the point that differs from an ordinary record: this
        file holds a plaintext api_key, so it is never acceptable for it to
        exist -- even for a moment -- with default (world-readable) mode.
        `write_atomic` creates the tmp file 0600 *at creation time*, and the
        closing `os.replace` preserves that mode; see its module docstring for
        why that beats a later `os.chmod` and why umask cannot widen it.
        """
        write_atomic(store_dir / _IDENTITY_FILENAME,
                     json.dumps(record).encode("utf-8"))

    # -- startup rebuild --------------------------------------------------

    def load_all(self) -> dict[str, Binding]:
        """Scan `<base>/*/*/identity.json` and rebuild `producer_id ->
        Binding` (design §3). This is why `send()`, `close()`, `flush()`,
        `set_info()` and `on_notify()` take only a `producer_id`.

        A malformed or unreadable `identity.json` is logged loudly at error
        level and skipped -- matching store.py's corrupt-record policy --
        rather than raising and taking down startup for every other
        producer. Only the file path is logged, never exception text: a
        JSON decode error's message can include a raw snippet of the
        offending content, and this file holds a plaintext api_key.
        """
        result: dict[str, Binding] = {}
        if not self._base_dir.is_dir():
            return result

        for identity_path in sorted(self._base_dir.glob("*/*/" + _IDENTITY_FILENAME)):
            binding = self._load_identity(identity_path)
            if binding is None:
                continue
            result[binding.producer_id] = binding
        return result

    def _load_identity(self, path: Path) -> Binding | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Binding(
                producer_id=data["producer_id"],
                api_key=data["api_key"],
                alias=data["server_alias"],
                base_path=data["base_path"],
                service_name=data.get("service_name"),
                registered_at=data.get("registered_at", ""),
                store_dir=path.parent,
                max_msg_bytes=data.get("max_msg_bytes"),
                heartbeat_s=data.get("heartbeat_s"),
            )
        except (OSError, ValueError, KeyError):
            # ValueError covers json.JSONDecodeError. Deliberately no
            # exception text in the log -- see load_all() docstring.
            _logger.error("binding_identity_corrupt", path=str(path))
            return None

    # -- stale binding detection ------------------------------------------

    def check_stale(self, alias: str, stored_base_path: str) -> bool:
        """True when a stored `base_path` no longer matches the configured
        one for `alias` (design §3): someone repointed the alias at a
        different endpoint. The stored `producer_id`/api_key are worthless
        against the new server and the old directory may hold undelivered
        msgs -- the rule is log loudly, leave the old directory for
        inspection, register fresh. Never silent, so this also logs at
        error level on a stale hit rather than leaving the boolean as the
        only signal.

        Looks up `alias` directly rather than through `resolve()`: staleness
        is a property of the configured endpoint, independent of whether the
        alias is currently enabled. A caller auditing an alias that was
        disabled *after* being repointed should still learn it is stale.
        Only a genuinely unknown alias raises.
        """
        binding = self._cfg.servers.get(alias)
        if binding is None:
            raise ConfigError(f"unknown server alias: {alias!r}")

        stale = stored_base_path != binding.base_path
        if stale:
            _logger.error(
                "binding_stale_base_path",
                alias=alias,
                stored_base_path=stored_base_path,
                configured_base_path=binding.base_path,
            )
        return stale
