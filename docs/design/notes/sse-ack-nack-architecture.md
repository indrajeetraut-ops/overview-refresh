# SSE-Based ACK/NACK Messaging Architecture

A single-producer messaging library providing at-least-once delivery over an
SSE channel, with durable local storage and application-driven NACK handling.

## Message Lifecycle (states)

```
PENDING  → written to durable store, not yet sent (or queued for resend)
SENT     → POST succeeded, awaiting outcome, still durable
ACKED    → outcome known good        (transient — purged immediately)
NACKED   → outcome known bad, reason attached   (transient — resolved by nack_fn)
```

`ACKED` and `NACKED` are not resting states — they exist only for the
duration of their callback's execution. Nothing should be found sitting in
either state except mid-crash.

## Steady-State Flow

### Send path

1. `msg_lib.send(payload)` → write `PENDING` to durable store.
2. POST to server with `msg_id`.
3. On `202 Accepted`, transition record → `SENT`.
4. Return control to producer immediately (no blocking).

### ACK path

1. SSE delivers `ack {msg_id}`.
2. Purge durable record for `msg_id`.
3. Fire `ack_fn(msg_id)`.

Purge happens before the callback. This is an accepted tradeoff: an ACK is
cheap and informational, and because the server dedupes on `msg_id` + unique
key, a lost/redundant resend is harmless.

### NACK path

1. SSE delivers `nack {msg_id, reason}`.
2. Mark durable record `NACKED`, persist `reason`.
3. Fire `nack_fn(msg_id, reason)` — this call blocks on the app's decision.
4. App decides:
   - **PURGE** → delete durable record. Terminal.
   - **RETAIN** → write state back to `PENDING`, durably, then resend.
5. Only after that state-write is confirmed on disk does the library
   consider this `msg_id` "handled."

If RETAIN's resend lands on a `msg_id` the server already has (dedup
constraint hit), the server responds with the equivalent of ACK, and the
block is purged on the next round-trip — so RETAIN is always safe to call
even when it turns out to be a no-op server-side.

## Restart Reconciliation

On startup, scan the durable store:

| State found | Meaning | Action |
|---|---|---|
| `PENDING` | never sent, or resend queued | send/resend |
| `SENT` | in flight when process died, outcome unknown | resend (server dedupes if already done) |
| `ACKED` | crashed after ack, before purge completed | purge, fire `ack_fn` |
| `NACKED` | crashed mid-decision (before app's PURGE/RETAIN write committed) | treat as `PENDING`, resend |

No ambiguity remains in `NACKED`: since it's never a resting state in normal
operation, finding one at startup only ever means "the decision didn't
finish committing" — always safe to fold into `PENDING`.

## Session vs. Message Correlation

- `session_id` is ephemeral, tied to one SSE connection's lifetime —
  regenerated on every reconnect, never persisted as part of message state.
- `msg_id` is the only durable correlation key.
- Server-side: ack/nack routing must be keyed on producer identity → current
  live session, not on the session that originally received the POST, since
  that session may no longer exist by the time an outcome is ready.

## Invariants

- **At-least-once delivery** — safe because server-side dedup (`msg_id` +
  unique key) makes redundant resends inert.
- **No silent loss of NACK reasoning** — the app always sees the reason
  before any retry decision is made, and that decision is durably recorded
  before being considered final.
- **No retry storms** — a NACK never auto-resends unless the app explicitly
  says RETAIN; PURGE is truly terminal.
- **Crash safety** — every crash point maps to an unambiguous restart
  action, because no state is allowed to persist "at rest" except `PENDING`
  and `SENT`.
