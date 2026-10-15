# Event Messaging — Clarification Outcomes

Decision record from the design review of `docs/design/event-messaging-design.md`.
Each decision names the section of the design doc that carries it. The two documents
must agree; this one is the index.

Date: 2026-07-25

---

## 1. Decisions

```
id   decision                                                        design doc
------------------------------------------------------------------------------
D1   1 msg = 1 POST. Producer owns batching, not the lib             §1, §9.3
D2   No wire ordering guarantee. Many msgs may be in flight          §1
D3   msg_id = INT, per producer, monotonic, gaps fine, random base   §3.1, §7
D4   `seq` retired as a term. msg_id everywhere                      whole doc
D5   event_id = optional, app-supplied, survives re-registration     §7
D6   App dedups in v1. Lib holds no durable server state             §9.1
D7   Lib-side dedup cache = future, opt-in per msg via `dedup` flag  §9.1
D8   Dedup hit answers 200 + code, synchronous. Not 208              §9.1
D9   Dedup cache in-memory, best-effort. App stays the arbiter       §9.1
D10  One free-form `info` bag, not two. Never interpreted by the lib §7
D11  set_info() sets producer-level info; per-msg shallow-merges     §6.1, §7
D12  SSE stream at talium/{service}/v1/notify                        §4, §5
D13  on_notify() required before the channel opens. Raises otherwise §4.1
D14  Keepalive `: hb` every heartbeat_s; client idle-read timer      §4.2
D15  server_epoch = per-process boot id, first event on the stream   §4.3, §6.3
D16  Epoch change sweeps all of sent/ back to pending/               §4.3
D17  sent_timeout_s backstops every other lost-outcome path          §4.4
D18  Thundering herd: jitter reconnect + cap at max_in_flight        §4.3
D19  send() synchronous to a definitive answer (202/200/401/400)     §2.2
     or timeout. Never waits for the ACK
D20  send() makes exactly one POST attempt. No inline retry          §2.2
D21  max_in_flight throttles -> stored_throttled, never fails a send §5.1
D22  max_pending rejects -> slot_full after slot_wait_s. Not stored  §5.1
D23  One error enum across return codes, wire body, and NACK codes   §8
D24  err_code + free-form reason. Code never branches on reason      §8
D25  Runtime conditions return; programming errors raise             §8.1, §8.3
D26  Cleaner loop owns retry, demotion, purge, max_pending alarm     §4.4
D27  Client never gives up. retry_num bounds a cycle, not a msg      §4.4
D28  close() is client-driven. Server fence and pending_set removed  §2.4
D29  msg_id allocated by block reservation, 1 fsync per block        §3.1
D30  msg file: write tmp -> fsync -> rename -> fsync dir             §3.1
D31  No received-side cursor. rcv_seq.json dropped                   §3
D32  Store layout <base>/<server_alias>/<producer_id>/               §3
D33  server_alias = one binding (endpoint + service_name)            §3, §5.1
D34  default_server in config. Code never names an environment       §5.1, §6.1
D35  Stale binding detected via stored base_path. Never silent       §3
D36  notify_ev carries `code` only. No separate ack boolean          §8.2
D37  reason is server-populated, free-form, human only               §8.2
D38  resolve() is an explicit call. Default RETAIN                   §2.2, §6.1
D39  notify_on_ack=False default, set at on_notify(), not in YAML    §6.1, §2.2
D40  ProducerStore kept. file default, postgres option, extensible   §5.2
D41  on_event is async: POST -> enqueue -> 202, worker calls app     §6.2
D42  Queue full -> 503 + server_busy + Retry-After                   §5.2, §6.2, §8.1
D43  Outbound notify ring while SSE is down. Overflow drops          §6.2
D44  max_msg_bytes is server config, returned by register(),        §5.2, §6.1, §8.1
     enforced client-side before storing
D45  Lib returns a router. Host owns the ASGI app                    §6.3
D46  One server per process = convention, not enforced               §6.3
D47  Client = threads, server = asyncio. Clients not forced async    §6.4
D48  poll_notify() escape hatch for apps owning their threading      §6.1, §6.4
D49  Registration config-driven. mode: open for v1, knowingly interim §10.1, §5.2
     Evolves to signed-app authorization presenting an API key
D50  schema_version = major.minor, present day one, unenforced in v1  §10.2, §7
     Receivers must ignore unknown keys
D51  org_id / user_id live in the payload, never the envelope         §10.3
     msging never reads them. ProducerRecord columns unused -> gh #20
D52  Structured JSON logging. Never log an api_key or a payload       §10.4
D53  Metrics accumulated in process, msg.metrics() -> dict.           §10.5
     Exposure (endpoint, scraper, CLI) is out of scope
```

## 2. Reversals from the prior implementation

The prior module doc and implementation described a different design. Both were deleted on
`feature/msging-rewrite`; see git history for `docs/modules/event-messaging.md` and
`src/common/msging/`. These are the load-bearing reversals, i.e. what was torn out.

```
was                                    now
------------------------------------------------------------------------------
batch of envelopes per POST            one msg per POST
whole-batch ACK, correlated batch_id   per-msg outcome, correlated msg_id
ACK/NACK in the HTTP response body     202 receipt; outcome later over SSE
lib classifies retryable vs terminal   app decides via resolve() PURGE/RETAIN
JSONL segments, deleted on ACK         file per msg, folder = state, atomic rename
uuid5 event_id, per-session seq        INT msg_id per producer, block-reserved
vendor/session_id/event_type in header header carries none of it; payload is opaque
one server per client                  many servers, alias = binding
open_session()/SessionSender           no session concept in the transport
```

## 3. Deferred, with the design accommodating them

```
§9.1  lib-side dedup cache      TTL, in-flight race, multi-replica still open
§9.2  chunked payloads          all-or-nothing; chunk seq is not session seq
§9.3  batch_mode                per-msg record and outcome unchanged, body differs
```

## 4. Carried into implementation

- Example servers and clients covering both wiring styles and the full client lifecycle,
  doubling as the integration test surface and the README quick example.
- README: interfaces, quick example, config (including DB connection and user), and how to
  run the regression tests.
- Test report, generated, with its location stated in the README.
