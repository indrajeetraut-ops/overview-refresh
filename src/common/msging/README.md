# Event Messaging Library (`msging`)

At-least-once msg transport between a producer (client) and a named server, over HTTPS,
with outcomes delivered asynchronously over SSE.

- Design: [`docs/design/event-messaging-design.md`](../../../docs/design/event-messaging-design.md) — authoritative for behaviour
- Decisions: [`docs/design/notes/event-messaging-clarifications.md`](../../../docs/design/notes/event-messaging-clarifications.md)
- Test report: [`docs/superpowers/reports/msging-test-report.md`](../../../docs/superpowers/reports/msging-test-report.md)

Payloads are opaque. One msg = one POST; the producer decides batching.

## Importing

```python
from common import msging as msg          # both halves, resolved lazily
from common.msging import clnt            # or import the half you want
from common.msging import srv
```

**Both halves are lazy, and that is load-bearing.** `common.msging.srv` pulls FastAPI;
`common.msging.clnt` pulls httpx and nothing heavier. A laptop collector installs the same
library the ingest service runs, so importing the package must not import the server half —
`msg.send` imports `clnt` on first touch, `msg.srv_init` imports `srv` on first touch.

`from common.msging import *` therefore gives you the enums and `ProducerRecord` only
(`Action`, `Code`, `PURGE`, `RETAIN`, `ProducerRecord`). `import *` resolves every name in
`__all__`, so listing the lazy ones there would import **both** halves on a machine that has
one. `dir(msg)` still advertises the full surface.

`msg.metrics` is the metrics **module**, not a callable: `msg.metrics()` is a `TypeError`.
Use `msg.metrics.metrics()` or `msg.clnt.metrics()`. (`__getattr__` runs only on an attribute
*miss*, and importing `common.msging.metrics` — every `clnt` module does — binds the submodule
onto the package, so a same-named lazy function would win or lose on import order. The
submodule keeps the name.)

## Quick example

Two files, both runnable as they stand. Save them in the repo root.

**Server** — the host owns the app, `msging` is one router on it (§6.3):

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import msging as msg


def store_it(payload: dict) -> int:        # stand-in for your own insert
    return len(payload)


def handle(env):                           # runs on a worker task, not in the request
    # `env` is an Envelope (§7) -- a pydantic model, so attributes, not keys.
    row_id = store_it(env.payload)
    msg.send_notice(env.producer_id, env.msg_id, "ack", info={"row_id": row_id})


@asynccontextmanager
async def lifespan(app):
    await msg.srv_start()                  # workers up, server_epoch minted (§4.3),
    try:                                   #   srv_shutdown() chained onto SIGINT/SIGTERM
        yield
    finally:
        await msg.srv_stop()               # workers drained, SSE channels closed


msg.on_event(handle)                       # before srv_init(), so its config still wins
app = FastAPI(lifespan=lifespan)
app.include_router(msg.srv_init("ingest"))         # -> talium/ingest/v1/*
```

Dedicated process, same thing: `app = msg.make_app("ingest")`.

Ctrl-C stops it, with a producer connected or without, on uvicorn's own defaults — no
`timeout_graceful_shutdown` needed. `srv_start()` chains `msg.srv_shutdown()` onto the signal,
which ends the `/notify` streams *before* uvicorn starts draining connections; the lifespan
then runs and `srv_stop()` finishes the drain. See `msg.srv_shutdown()` under
[Server API](#server-api) for why that ordering is the whole trick.

**Client** — register, notify, send, close:

```python
from common import msging as msg

CONFIG = "src/common/msging/examples/msging.example.yaml"


def on_outcome(msg_id, ev):                    # ev is a NotifyEv (§8.2)
    if ev.code != "ack":                       # the default fires on anything but ack
        msg.resolve(pid, msg_id, msg.PURGE)    # ... the default action is RETAIN


msg.clnt_init(CONFIG)                          # once per process
p   = msg.register({"host": "laptop-7", "kind": "collector"})
pid = p["producer_id"]                         # register() returns a dict

msg.on_notify(pid, on_outcome)                 # BEFORE anything is sent (§4.1)
msg.set_info(pid, {"agent_version": "1.4.2"})

r = msg.send(pid, {"session": "...whole file..."})
if r.err_code == "slot_full":                  # the only rejection you must handle
    pass                                       # back off and retry, or drop it

if msg.producer_status(pid)["unauthorized"]:   # §8.1: the key was revoked
    p   = msg.re_register(pid, {"host": "laptop-7"})    # new producer_id + api_key
    pid = p["producer_id"]

print(msg.close(pid))
```

Running them, verbatim, from the repo root:

```bash
export PYTHONPATH=src:.
export TALIUM_MSGING_CONFIG=src/common/msging/examples/msging.example.yaml

venv/bin/python -m uvicorn quick_server:app --port 8099    # terminal 1
venv/bin/python quick_client.py                            # terminal 2
```

The client prints `status='drained' unresolved=0`. `TALIUM_MSGING_CONFIG` is how the *server*
half finds its config when the host does not pass one to `srv_init()`; the client is given its
path explicitly. The examples' config is used here because the shipped `config/msging.yaml`
points at a production endpoint and cannot serve a local demo.

## Examples

The same four wirings, expanded into runnable programs, in [`examples/`](examples/). Each file
says at the top how to run it and what to expect; they share `examples/msging.example.yaml`
and need no editing.

```bash
# terminal 1 -- pick one; they take turns on the same port
venv/bin/python src/common/msging/examples/srv_hosted.py      # §6.3: the host owns the app
venv/bin/python src/common/msging/examples/srv_make_app.py    # make_app(), dedicated process
venv/bin/python src/common/msging/examples/srv_two.py         # two services, one process

# terminal 2 -- the whole client lifecycle; the alias picks the service
venv/bin/python src/common/msging/examples/clnt_full.py
venv/bin/python src/common/msging/examples/clnt_full.py audit_local
```

`clnt_full.py` runs `clnt_init → register → set_info → on_notify → send → resolve → close` and
prints `metrics()` on the way out. It does not stop at the happy path: the example server
rejects deliberately, so the run also shows `too_large`, `slot_full`, and a NACK resolved both
ways — `PURGE` for a msg that will never be accepted, `RETAIN` for one whose failure has
passed, which is redelivered and ACKed. Library logs are JSON on stderr, the narrative is on
stdout; `2>/dev/null` gives you the narrative alone.

`tests/common/msging/test_examples.py` keeps them honest: everything imports, each server
example mounts the routes it claims, and `clnt_full.py` really is run against a live
`srv_hosted.py` (`-m slow`, ~3 s).

## Client API

Every call below takes the `producer_id` `register()` returned — §3's startup scan of
`<base>/*/*/identity.json` rebuilds `producer_id -> binding`, so nothing else is needed.

```python
msg.clnt_init(config: MsgingConfig | str | Path) -> None
    # Loads YAML (with ${VAR} / ${VAR:-default} interpolation) or takes a built
    # config object. Call once per process, before anything else.
    # Raises ConfigError: unreadable, malformed, or an unknown key.
    # A second call replaces the config for SUBSEQUENT register() calls only;
    # producers already registered keep the settings they were built with.

msg.register(producer_description: dict, server_alias: str | None = None) -> dict
    # None -> client.default_server. One register() per binding.
    # Returns { producer_id, api_key, max_msg_bytes, server_alias, base_path,
    #           service_name, registered_at } -- a dict, so p["producer_id"].
    #   max_msg_bytes is the SERVER's limit, handed back so the two configs
    #   cannot drift.
    # `producer_description` is free-form and stored by the server verbatim; a
    #   "producer_desc" string in it rides on every envelope (§7).
    # Idempotent: a stored identity.json for the alias is reused, not re-issued
    #   -- a new producer_id each start would orphan the previous run's backlog.
    # A stale binding (stored base_path != configured) logs loudly, leaves the
    #   old directory for inspection, and registers fresh. So an alias
    #   accumulates one identity per endpoint it has pointed at, and
    #   reattachment picks the one whose base_path matches the config, never
    #   just the newest.
    # Also runs §2.3 restart reconciliation and starts the cleaner loop. It
    #   does NOT open the SSE channel -- that needs on_notify() (§4.1).
    # Raises ConfigError        the alias is unset, unknown, or disabled
    #        RegistrationError  the server refused -- terminal, no retry loop
    #        OSError            the store directory is unusable. Not one of
    #                           §8.3's codes; see the TODO in clnt/api.py.

msg.re_register(producer_id: str, producer_description: dict) -> dict
    # §8.1's `unauthorized` -> "re-register", as an explicit call. Mints a NEW
    # producer_id + api_key for the same alias, and returns the new Producer.
    # Never automatic: a producer_id owns a store directory, so re-registering
    # behind your back would orphan everything still in pending/ + sent/ +
    # nacked/ -- silently turning at-least-once into lossy.
    # The old binding's threads stop and it leaves the process; its directory
    # stays on disk and NOTHING is migrated. No /close is POSTed -- that call
    # would authenticate with the revoked key.
    # Raises NotRegisteredError, and ConfigError / RegistrationError as
    #        register().

msg.producer_status(producer_id: str) -> dict
    # { producer_id, server_alias, base_path, unauthorized, closing,
    #   pending, sent, nacked }   -- counts are live file counts.
    # `unauthorized` is §8.1's "PENDING, halted": the key was refused, nothing
    # will ship, and re_register() is the way out. A snapshot, like metrics().
    # Raises NotRegisteredError.

msg.on_notify(producer_id: str, notify_callback_fn: Callable,
              notify_on_ack: bool = False) -> None
    # Registers the outcome callback AND opens the SSE channel. Required
    # before anything is sent (§4.1): on restart the server may push the
    # previous run's outcomes the instant the stream opens.
    # notify_callback_fn(msg_id, notify_ev) -- a NotifyEv, see §8.2 below.
    # Default fires on anything not `ack`. Set notify_on_ack=True if the
    #   producer gates on confirmed delivery (e.g. marking a source file done).
    #   Deliberately not a YAML knob -- config could silently break it.
    # The callback runs on the SSE reader thread: thread-safe and quick. An
    #   exception in it is caught and logged by type and location; its message
    #   is never logged, because it is app data (§10.4).
    # Idempotent in the way that matters: calling it again swaps the callback
    #   and leaves the one reader running.
    # Raises NotRegisteredError, NoCallbackError (notify_callback_fn is not
    #        callable).

msg.set_info(producer_id: str, info: dict) -> None
    # Producer-level info bag (§7). Replaces wholesale, copied not aliased,
    # affects subsequent sends only. Raises NotRegisteredError.

msg.send(producer_id: str, event: dict, info: dict | None = None,
         *, event_id: str | None = None, dedup: bool = False) -> SendResult
    # Synchronous to a definitive answer (202/200/401/400) or a timeout. Never
    # waits for the ACK. Exactly one POST attempt; retry is the cleaner loop's,
    # so send() latency is bounded at one req_timeout_s.
    # Per-msg `info` SHALLOW-merges over the producer-level dict, per-msg keys
    #   winning. Deep merge is where this gets surprising.
    # event_id: optional app-supplied key, carried on the §7 envelope. It is
    #   not the transport's key -- msg_id is.
    # dedup: reserved for §9.1's lib-side cache; v1 never acts on it. Requires
    #   event_id.
    # Returns SendResult(err_code, reason, msg_id) -- see Error codes below.
    #   msg_id is None for the two codes that mean nothing was stored.
    #   `reason` is free-form and human-only; code must never branch on it.
    # Raises NotRegisteredError, ProducerClosingError, InvalidEnvelopeError
    #        (dedup without event_id, or an `event` that is not a dict).
    # Never raises for a runtime condition -- a dead network, a refused
    #   connection and a full server queue all come back as codes (§8.3).

msg.resolve(producer_id: str, msg_id: int, action: Action = RETAIN) -> None
    # Resolves a NACKed msg. PURGE deletes (terminal). RETAIN returns it to
    # PENDING and the cleaner loop resends it. No resolve() call at all ==
    # RETAIN, and §2.3 folds it into PENDING on the next start.
    # Does not POST. Tolerant of a msg_id it cannot find (an ACK may have
    #   purged the record first). Still legal after close(), unlike send() and
    #   flush(): it writes no msg and makes no request.
    # Raises NotRegisteredError.

msg.flush(producer_id: str) -> None
    # One cleaner pass, run synchronously on the caller's thread: it ships
    # while in_flight < max_in_flight, so a backlog larger than the window
    # drains over several passes rather than ignoring the limit.
    # Raises NotRegisteredError, ProducerClosingError (flush is a send, and
    #        close() already stopped the threads and reported those msgs).

msg.close(producer_id: str, timeout_s: float | None = None) -> CloseResult
    # Client-driven drain (§2.4): stop accepting sends, flush, wait for
    # pending/ + sent/ to empty, POST /close, return. timeout_s defaults to
    # close_timeout_s.
    # Returns CloseResult(status, unresolved); status is "drained" exactly when
    #   unresolved == 0, else "timeout".
    #   unresolved = pending/ + sent/. nacked/ is excluded -- it waits on the
    #   app's resolve(), which draining cannot supply.
    # timeout_s bounds the whole call, flush included, not just the gaps
    #   between sweeps; one POST already on the wire still finishes, so the
    #   honest bound is timeout_s + req_timeout_s.
    # A send already past the gate is waited for, not raced: `drained` is never
    #   reported with a msg still in the store.
    # Msgs outstanding at the timeout stay durable -- nothing is dropped.
    # Idempotent: a second call re-reports the state and re-POSTs nothing.
    # Raises NotRegisteredError.

msg.poll_notify(producer_id: str) -> list[NotifyEv]
    # Drain notifications on your own thread instead of via the SSE callback.
    # Returns everything queued since the last call, oldest first, and clears
    # the queue. Holds exactly what the callback is handed, so `ack` appears
    # only with notify_on_ack=True. Bounded (1000); a callback is still
    # required before the channel opens, so a polling app registers a no-op.
    # Raises NotRegisteredError.

msg.clnt.metrics() -> dict
    # §10.5 snapshot of every counter and gauge: { name: { labels: value } }.
    # Same function as common.msging.metrics.metrics(). NOT `msg.metrics()`.
```

Programming errors raise; runtime conditions return a code (§8.3):

```
NotRegisteredError     any client call with an unknown producer_id
NoCallbackError        on_notify() with a non-callable
InvalidEnvelopeError   dedup=True with no event_id, or a non-dict event
ProducerClosingError   send() or flush() after close(), draining
ConfigError            no clnt_init(); unset/unknown/disabled alias; bad config
RegistrationError      the server refused /register -- terminal
```

## Server API

```python
msg.srv_init(service_name: str, config: MsgingConfig | None = None) -> APIRouter
    # Registers one service and returns its router, carrying /register,
    # /event, /notify and /close under talium/{service_name}/v1/*. The host
    # includes it; the library never owns the app.
    # service_name must match [A-Za-z0-9][A-Za-z0-9_-]* -- it becomes a URL
    #   path segment.
    # `config` is accepted on the FIRST srv_init() of a process only: the
    #   machinery (queue, hub, producer store, server_epoch) is process-wide,
    #   so a second, different config would apply to nothing. Omitted, the
    #   config comes from $TALIUM_MSGING_CONFIG (msg.CONFIG_ENV), and failing
    #   that from the built-in defaults.
    # Raises ValueError    service_name is not path-safe
    #        RuntimeError  already mounted in this process, or a conflicting
    #                      config after the first call
    #        ConfigError   the §10.1 registration stanza is unusable

await msg.srv_start() -> None      # ASYNC. Once per process, from the lifespan.
    # Binds the event loop and starts `workers` handler tasks. Deliberately not
    # idempotent: a second call raises, because two lifespans on one process is
    # a bug worth surfacing.

await msg.srv_stop() -> None       # ASYNC. Drains the worker pool and the SSE
    # registry. Safe on a process that never started. Joins a srv_shutdown()
    # still in flight rather than racing it, and restores the signal handlers
    # srv_start() chained onto. The machinery survives: server_epoch stays the
    # process boot id across a stop/start cycle, since a new one would make
    # every client sweep sent/ back to pending/ and resend.

msg.srv_shutdown() -> None         # SYNC. Begin shutdown. Idempotent, safe from
    # any thread and from a signal handler -- which is where it is meant to be
    # called. Returns immediately; the drain runs on the event loop:
    #   1. stop accepting -- /register, /event, /notify answer 503 server_busy
    #      + Retry-After. /close still works: the client has already drained.
    #   2. drain the worker pool, so in-flight msgs finish.
    #   3. wait up to server.shutdown_drain_s for live streams to flush their
    #      rings, so a connected producer receives its outcomes instead of
    #      waiting out sent_timeout_s.
    #   4. close every stream -- which is what lets the ASGI shutdown run.
    # YOU USUALLY DO NOT CALL THIS. srv_start() chains it onto the host's
    #   SIGINT/SIGTERM handler (uvicorn's, normally) and srv_stop() restores
    #   that handler. Set server.shutdown_signals: false and call it yourself
    #   if your host owns its own signal handling.
    # A no-op before srv_start() and after srv_stop().
    # WHY IT EXISTS: srv_stop() runs from the lifespan, and ASGI runs lifespan
    #   shutdown only AFTER connections drain -- but /notify is endless, so it
    #   never drains, so srv_stop() never runs. Without this a server with one
    #   producer connected could not be stopped at all.

msg.on_event(ev_callback_fn: Callable[[Envelope], None | Awaitable]) -> fn
    # Async by design: POST -> enqueue -> 202, a worker calls the app later.
    # The queue is in memory, capped at queue_max; full -> 503 + server_busy +
    # Retry-After. Sync and async handlers both work. Returns fn, so it also
    # works as a decorator, and may be called before or after srv_start().
    # A msg arriving with no handler registered is dropped, loudly and once.

msg.on_close(close_callback_fn: Callable[[str], None | Awaitable]) -> fn
    # Optional. Fired with the producer_id when a producer closes, so the app
    # can finalize. Whatever it raises is absorbed: /close is a courtesy call
    # and the client has already drained.

msg.send_notice(producer_id: str, msg_id: int, code: str,
                reason: str | None = None, info: dict | None = None,
                event_id: str | None = None) -> None
    # The outcome (§8.2). SSE down -> held in that producer's ring, delivered
    # on reconnect; overflow or expiry drops it and sent_timeout_s resends.
    # code is a §8 value -- "ack", "nack", or an app extension.
    # reason is human-only. Machine-readable facts belong in `info`.
    # info is echoed to the client verbatim, and is bounded: one serialised
    #   notify_ev must fit server.max_notice_bytes or the client's frame parser
    #   drops the link. An oversized bag is shed (info first, then `reason`
    #   truncated) rather than wedging that producer's channel.
    # event_id is echoed back when the msg carried one.
    # THE ONE ENTRY POINT THAT MAY BE CALLED OFF THE EVENT LOOP THREAD: a
    #   handler that offloads to an executor and reports from there is normal,
    #   and this bridges with loop.call_soon_threadsafe().
    # Raises RuntimeError: before srv_start() or after srv_stop() -- there is
    #        no loop to deliver on, and dropping it silently would strand the
    #        client in SENT until sent_timeout_s.

msg.make_app(*service_names: str, config: MsgingConfig | None = None,
             **fastapi_kwargs) -> FastAPI
    # Convenience for a dedicated process: sugar over srv_init() + the §6.3
    # lifespan. A host that wants its own middleware, auth, CORS or /healthz
    # uses srv_init() and keeps ownership of the app.

msg.CONFIG_ENV      # "TALIUM_MSGING_CONFIG"
msg.ENROLL_HEADER   # "X-Talium-Enroll-Key" -- §10.1 enrollment_key mode
```

Wiring order is `on_event()` → `srv_init(config=...)` → lifespan `srv_start()`/`srv_stop()`.
`on_event()`/`on_close()` deliberately do **not** build the machinery: they park the handler
until something else does, so `srv_init(config=...)` is still the first call and the host's
config still applies.

## Msg shape

The envelope the library puts on the wire (§7). `payload` and `info` are opaque to it — that
is what keeps payload churn out of the transport.

<!-- gen:schema envelope -->
```json
{
  "schema_version": "1.0",                   // major.minor; unenforced in v1 (§10.2)
  "event_id":       "sess-2026-07-25-abc",   // optional, app-supplied
  "dedup":          false,                   // true requires event_id
  "producer_id":    "prodA",
  "msg_id":         42,                      // the transport's key, per producer
  "service_name":   "ingest",
  "created_at":     "2026-07-25T10:15:30Z",
  "producer_desc":  "optional desc",
  "info":           {},                      // free-form; opaque to the library
  "payload":        {}                       // the dict you passed to send()
}
```
<!-- /gen:schema -->

The outcome your callback receives (§8.2):

<!-- gen:schema notify_ev -->
```json
{
  "msg_id":      42,                         // correlates to the sent msg
  "producer_id": "prodA",
  "event_id":    "sess-2026-07-25-abc",      // echoed if the msg carried one
  "code":        "ack",                      // ack | nack | extensible
  "reason":      "",                         // free-form, server-populated, human only
  "info":        {},                         // whatever the app returned
  "ts":          "2026-07-25T10:15:31Z"      // outcome timestamp
}
```
<!-- /gen:schema -->

Both blocks are generated from [`schema/`](schema/) by `utils/gen_schema_blocks.py`, which
also regenerates the copies in the design doc. The JSON files are canonical: the pydantic
models in `models.py` mirror them and `tests/common/msging/test_schema_parity.py` enforces
the mirror. **Edit the schema file, then run the generator** — do not hand-edit these blocks.

```bash
venv/bin/python utils/gen_schema_blocks.py           # rewrite the docs in place
venv/bin/python utils/gen_schema_blocks.py --check   # exit 1 if a block is stale
```

## Error codes

One enum everywhere — `send()` return code, `code` in a `200`/`202` body, NACK code over SSE.
Every return carries `err_code` + a free-form `reason`; **code must never branch on `reason`**.

```
err_code             trigger                         msg state        caller action
-----------------------------------------------------------------------------------------
ok                   202                             SENT             await SSE outcome
already_processed    200                             purged inline    none, terminal (*)
stored_timeout       no response in req_timeout_s    PENDING          none, loop retries
stored_unreachable   conn refused / DNS / TLS        PENDING          none, loop retries
stored_throttled     max_in_flight reached           PENDING          none, ships on free slot
server_busy          503, server queue full          PENDING          none, honors Retry-After
slot_full            max_pending after slot_wait_s   NOT stored       back off or drop
too_large            over max_msg_bytes              NOT stored       split at the producer
unauthorized         401                             PENDING, halted  re-register
rejected             400 malformed envelope          NACKED           fix + resend, or purge
```

(*) `already_processed` is reserved for the future lib-side dedup cache (§9.1). **v1 never
emits it** — neither this client nor this server produces it. It is honoured on the receiving
side only so a future server cannot create a msg that retries forever.

`slot_full` and `too_large` are the only outright rejections — the only backpressure a caller
must handle. Both mean the msg was **not** stored, so the caller still holds the event.

`too_large` is checked client-side *before* storing, against the `max_msg_bytes` that
`register()` returned; the server's `413` is a backstop for a stale limit and is handled the
same way (the record is purged).

`unauthorized` also halts the SSE reader (logged as `sse_halted`): the library will not
re-register on your behalf, because a new `producer_id` would orphan this binding's durable
backlog. Recovering is your call and there is one way to do it — `producer_status(pid)` reports
`unauthorized: true`, and `re_register(pid, descriptor)` mints a new identity. `register()`
cannot serve this: it is idempotent by design, so it hands back the same revoked identity.

## Configuration

All config-driven. Every timeout is configurable — no hardcoded waits. `${VAR}` and
`${VAR:-default}` are substituted in the YAML text before parsing, so they work as a whole
value or embedded in one.

An **unset `${VAR}` with no default becomes an empty string, not an error.** That is right for
an optional override and a trap for a path: with `TALIUM_DATA_DIR` unset,
`path: ${TALIUM_DATA_DIR}/producers.json` loads as `/producers.json` and fails later with a
permission error rather than a config error. Set it, or give the reference a default. (Tracked
as a TODO in `config.py`.)

### Client — `config/msging.yaml`

```yaml
client:
  default_server: ${TALIUM_SERVER:-ingest_prod}   # used when register() omits the alias
  servers:
    ingest_prod:
      base_path:    "https://ingest.prod:8443"
      service_name: ingest                        # defaults to the alias
    ingest_dev:
      base_path:    "https://ingest.dev:8443"
      service_name: ingest
      enabled:      false                         # declared but off
      req_timeout_s: 30                           # per-server override
  defaults:                  # applied to every server unless overridden above
    req_timeout_s:   10      # per-POST deadline; expiry -> stored_timeout, stays PENDING
    retry_num:       3       # cleaner-loop retries only, never inline in send()
    max_pending:     10000   # slot limit: msgs allowed in the durable store
    slot_wait_s:     1.0     # send() waits this long for a slot, then fails slot_full
    max_in_flight:   64      # cap on SENT awaiting an outcome
    sent_timeout_s:  300     # SENT older than this -> demoted to PENDING
    close_timeout_s: 30      # close() drain deadline
    msg_id_block:    1000    # ids reserved per fsync of msg_id.json
    idle_heartbeats:  5      # idle-read timer = this x the server's heartbeat_s (§4.2)
    idle_timeout_s:  45      # fallback only, when the server reports no heartbeat_s
    sweep_interval_s: 5      # cleaner loop period
    gzip_min_bytes:  1024    # below this, gzip costs more than it saves
    base_dir: "~/.talium/msging"   # root of the durable store: <base_dir>/<alias>/<producer_id>/
```

That is the complete key set — `client.defaults` is `extra="forbid"`, so an unrecognized key
there is a `ConfigError` at load, not a silent no-op. A per-server override may be any of the
same keys (a `servers.<alias>` block is `extra="allow"` and is validated when it is merged, so
a typo there is reported as "invalid override key(s)").

**Switching environments is a config or env-var change, never a code change.** The alias names
a *binding* (endpoint + service), not an environment; code never names one.

### Server — `config/msging.yaml`

```yaml
server:
  queue_max:      10000      # queued-and-not-yet-picked-up cap; full -> 503 server_busy
  workers:        4          # concurrent handler tasks; in-flight msgs sit outside queue_max
  retry_after_s:  5          # Retry-After sent with a 503
  heartbeat_s:    15         # SSE keepalive interval
  max_msg_bytes:  33554432   # 32 MiB; larger -> 413 too_large
  max_notice_bytes: 32768    # one serialised notify_ev; must stay under the client's
                             # 64 KiB frame cap, or an oversized info bag wedges the link
  notify_buffer:  100        # per-producer notifications held while SSE is down
  notify_ttl_s:   60         # buffered notification expiry
  auth_cache_max: 1024       # successful verify() results held in process, as an LRU
                             # with an expiry sweep. 0 = no cache, every verify()
                             # hits the store
  shutdown_drain_s: 5        # how long srv_shutdown() lets a live /notify stream flush
                             # its ring before the streams are ended. 0 = end at once
  shutdown_signals: true     # chain srv_shutdown() onto the host's SIGINT/SIGTERM.
                             # false when the host drives shutdown itself
  registration:
    mode: open               # open | enrollment_key  (§10.1)
    key:  ${TALIUM_ENROLL_KEY}   # required when mode=enrollment_key
    max_producers: 1000      # store rows this server will ever issue; 0 = unlimited
    rate_per_min:  60        # /register attempts per source per minute; 0 = unlimited
```

The complete key set again — `server` is also `extra="forbid"`, and the numeric bounds are
checked at load (`workers`, `queue_max`, `heartbeat_s`, `max_msg_bytes`, `max_notice_bytes`,
`notify_ttl_s` must be > 0; `notify_buffer`, `retry_after_s`, `auth_cache_max`,
`shutdown_drain_s`, `max_producers` and `rate_per_min` may be 0). A zero `notify_buffer` means
"hold nothing"; a negative one used to reach `deque(maxlen=-1)` and raise inside a queue
worker, which is why they are bounds and not decoration.

**`auth_cache_max` bounds process memory, not throughput.** A successful `verify()` is cached
for 5 s keyed by the key's sha256 — never by the key itself, which is supposed to exist exactly
once, in `register()`'s return value. Each entry pins a whole `ProducerRecord`, descriptor
included, so the cap is what turns the worst case into a number you can multiply out
(`auth_cache_max` × descriptor size) instead of "however many keys were ever presented". `0`
disables the cache: every request then hits the store, and the cross-process revocation window
(see below) closes.

**`max_producers` and `rate_per_min` bound the anonymous `/register` path.** Under `mode: open`
every identity is a permanent store row that the auth path reads, and capping the *size* of a
descriptor bounds nothing on its own — the count was unbounded, so a loop of anonymous POSTs
grew `producers.json` without limit. Over either cap → `503 server_busy` + `Retry-After`,
counted as `registrations_refused_total{reason}` with `reason` in `rate | capacity`. Neither is
a security control; that is `mode: enrollment_key`. `rate_per_min` is a fixed window keyed on
the peer address (never `X-Forwarded-For` — caller-controlled), so behind a reverse proxy it
becomes a global limit. The default `1000` is sized against the **file** backend: at the 8 KiB
descriptor cap that is an 8 MB file, rewritten whole on every upsert. More producers than that
means `backend: postgres` first, then a raised cap.

**`shutdown_drain_s` / `shutdown_signals` are §6.2's shutdown.** See `msg.srv_shutdown()` in
the Server API above — the defaults need no attention, and `shutdown_signals: false` is for a
host that installs its own SIGINT/SIGTERM handler.

**`mode: enrollment_key` is server-side only in v1.** The server will demand
`X-Talium-Enroll-Key` on `/register` and reject without it, but this client has no config key
for the secret to present, so only `mode: open` works end to end. (TODO in
`clnt/api.py::_post_register`.)

`max_msg_bytes` sizes **payloads only**. The `/register` producer descriptor and the `/close`
body get their own, much smaller cap (`router.MAX_DESCRIPTOR_BYTES`, 8 KiB, not configurable):
the descriptor is accepted unauthenticated under `mode: open`, stored verbatim and forever, and
re-read on the auth path, so sizing it at 32 MiB made anonymous registration a disk-fill with
read amplification. Over the cap -> `413 too_large`.

### Producer store — DB configuration

The server persists producer records (id, key prefix + hash, `last_seen_at`, `revoked`) behind
a config-selected adapter. **Backend selection is config, never code.**

```yaml
producer_store:
  backend: file                # file | postgres  (future adapters add a stanza)
  file:
    path: ${TALIUM_DATA_DIR}/producers.json
  postgres: {}                 # adapter-specific overrides only -- no DSN here
```

- **`file`** is the default: one JSON file, atomic replace. No database needed. Default path
  `~/.talium/msging/producers.json`.
- **`postgres`** reuses the storage module's process-wide engine. **The DB connection string
  and user are not configured here** — they live in `config/storage.yaml` as `${TALIUM_DB_DSN}`,
  which carries user, host, port, database and SSL mode:
  `postgresql+psycopg://USER@HOST:5432/econ?sslmode=require`. Pooling (`pool_size`,
  `max_overflow`, `pool_recycle_s`, `pool_pre_ping`) is likewise storage's, documented in
  [`src/server/storage/README.md`](../../server/storage/README.md).
- The postgres adapter is **lazy-imported**, so a laptop collector importing this package never
  needs SQLAlchemy or psycopg installed. Selecting it in an environment without
  `server.storage` fails with a message that names the backend, not an `ImportError`.
- Adding a backend = new adapter class + config stanza. Never edit the server.

## Observability

- **Metrics** (§10.5) — `msg.clnt.metrics()` returns `{ name: { labels: value } }`. Counters:
  `msgs_stored_total`, `msgs_sent_total`, `msgs_acked_total`, `msgs_nacked_total`,
  `msgs_resolved_total{action}`, `slot_full_total`, `cleaner_retries_total`,
  `cleaner_demotions_total`, `epoch_sweeps_total`, `epoch_swept_msgs_total`,
  `sse_reconnects_total`, `registrations_total`, `registrations_refused_total{reason}`
  (`rate | capacity`, §10.1's caps), `msgs_accepted_total`, `queue_full_total`,
  `auth_failures_total`, `notices_sent_total`, `notices_dropped_total{reason}`. Gauges:
  `pending_count`, `in_flight_count`, `sse_connections`. Exposure (endpoint, scraper, file) is
  explicitly out of scope.
- **Logging** (§10.4) — structured JSON via structlog. **The library never configures logging;
  the entry point does**: `common.msging.log.configure_logging(json=True, logger_factory=...)`.
  An api_key is only ever logged as `redact_key()`'s prefix, and a payload, an `info` bag or an
  app exception *message* is never logged at all — `size_bytes`, exception type and raising
  location are what debugging gets.

## Known limitations

Real, current, and none of them is a reason to hold the library back — but you should know
them before you hit them.

- **`nacked/` is not watched.** `send()`'s slot check counts all three directories, so
  unresolved NACKs really do consume `max_pending` — but the `cleaner_max_pending` alarm and
  the `pending_count` gauge look at `pending/` only. A store filling with NACKs nobody
  `resolve()`s is therefore silent until a `send()` comes back `slot_full`. Watch
  `producer_status(pid)["nacked"]` if your app can leave NACKs unresolved.
- **`mode: enrollment_key` is not reachable from the client.** See the server config section.
- **`rate_per_min` collapses to a global limit behind a reverse proxy.** The rate key is the
  peer address, and `X-Forwarded-For` is deliberately not trusted — a caller that could set its
  own key could hand itself a fresh one per request. Still the right bound (the loop it stops
  is the same shape either way), but size it for the whole fleet, not per host.
- **A revocation takes up to 5 s to be seen by *other* processes.** `Auth`'s verify cache is
  per process: `set_revoked()` purges the revoking process's entry immediately, but another
  process holding a cached result keeps accepting that key until its entry expires. Bounded by
  the 5 s TTL; `auth_cache_max: 0` closes it entirely, at a store read per request.
- **`already_processed` is never emitted by v1.** See the error-code table.
- **A duplicate delivery is possible on a concurrent cleaner pass.** A record is in `pending/`
  from `put_pending()` until its POST returns, and a cleaner pass in that window can send it
  again. That is at-least-once behaving as specified (§1 gives no ordering guarantee, §9.1
  makes the app idempotent); closing it would need a "claimed" state the durable store
  deliberately does not have.
- **`re_register()` migrates nothing.** The retired producer's `pending/`, `sent/` and
  `nacked/` are left on disk under the old `producer_id`. An operator moves or drops them.
- **An unusable store directory raises a bare `OSError` from `register()`.** §8.3 enumerates no
  code for "the disk is full or unwritable", so it is left raw rather than mislabelled as a
  `ConfigError`.

## Running tests

No server, no database, no network: the suite starts everything it needs on loopback.

```bash
# fast path -- skips the tests that boot a real uvicorn or wait out a real timeout
venv/bin/python -m pytest tests/common/msging/ -v -m "not slow"

# full regression, including the restart/epoch cases and the live example run
venv/bin/python -m pytest tests/common/msging/ -v

# the whole repo
venv/bin/python -m pytest tests/ -v
```

`-m "not slow"` deselects 7 tests (4 in `test_integration.py`, 2 in `test_shutdown.py`, 1 in
`test_examples.py`). The marker means "starts a real server, restarts one, or waits out a real
timeout" — it is not a proxy for "the slow half of the suite", because most of this suite's
wall time is spread across sweep intervals and reconnect backoffs everywhere else.

The regression suite covers the failure paths, not just the happy path
(`tests/common/msging/test_failure_paths.py`, `test_integration.py`, `test_shutdown.py`):

- restart reconciliation — kill mid-flight, restart, assert `PENDING`/`SENT` resend
- `server_epoch` sweep — restart the server, assert `sent/` returns to `pending/`
- `sent_timeout_s` backstop — drop an outcome, assert demotion
- backpressure — `slot_full` and `server_busy`
- NACK resolution — both `PURGE` and `RETAIN`
- **shutdown** — a real uvicorn on a real port with uvicorn's *default*
  `timeout_graceful_shutdown` and a real producer holding `/notify` open, killed with a real
  SIGTERM and a real SIGINT, asserting a bounded exit that reaches `srv_stopped`. That is a
  subprocess on purpose: the defect it guards was a process that would not exit, which cannot
  be asserted from inside the process that will not exit
- the §6.4 boundary — `clnt` must not import `srv`, and the postgres adapter must stay lazy

**No test in `tests/common/msging/` needs `TALIUM_TEST_DSN`, and none skips without it.** The
postgres producer-store adapter is exercised here only as far as backend *selection*
(`make_store(backend="postgres")` resolves to `PgProducerStore`, and says so clearly when
`server.storage` is not importable). The DB-backed exercise of that store lives in
`tests/server/storage/test_producer_store.py`, which does skip without a DSN:

```bash
./utils/db_up.sh                 # starts postgres:16 via docker/docker-compose.yml, waits
export TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:5432/econ_test
venv/bin/python -m pytest tests/server/storage/ -v
./utils/db_down.sh -v            # stop and wipe the data volume
```

### Test report

Report lands at
**[`docs/superpowers/reports/msging-test-report.md`](../../../docs/superpowers/reports/msging-test-report.md)**
— per-module summary and the full per-test table, plus a `Skipped` section (test + reason) and
a `Failures` section (test + message) whenever the run produces any. The current run has
neither: 758 tests, 758 passed.

```bash
mkdir -p docs/superpowers/reports
venv/bin/python -m pytest tests/common/msging/ \
    --junitxml=docs/superpowers/reports/msging-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Event Messaging (msging)" \
    --junit docs/superpowers/reports/msging-junit.xml \
    --out   docs/superpowers/reports/msging-test-report.md \
    --tests tests/common/msging/
```

`utils/gen_test_report.py` is module-agnostic — title, paths and footnotes are arguments.
`src/server/storage/README.md` drives the same script.
