# OTel Intake — Module Doc

**Spec:** `docs/design/2026-08-04-otel-intake-design.md` (frozen 2026-08-04; the authority).
**Status:** v1 implemented — receiver, mapping configs, org-token auth, true-up worker, prune.
**What it is:** the second signal source. Agent clients export OTLP straight to the existing
ingest service; a poller folds the result into session cost truth and joins it to the JSONL
path on `request_id`. **OTel is spend truth, JSONL is session fidelity.**

---

## 1. File map

| Path | What it owns |
|---|---|
| `src/server/ingest/otlp/router.py` | `POST /otlp/v1/metrics`, `/otlp/v1/logs`; the shed semaphore, the per-org rate limit, the OTLP response encoding |
| `src/server/ingest/otlp/decode.py` | Content-Type dispatch (protobuf ↔ JSON), gzip, the size gate; `encode_protobuf` for tests and gateways |
| `src/server/ingest/otlp/payload.py` | OTLP `AnyValue` unwrapping — the one place the wire's one-of becomes a plain dict |
| `src/server/ingest/otlp/receiver.py` | The pipeline: detect → parse → filter → series hash → insert-if-absent |
| `src/server/ingest/otlp/mapping.py` | The mapping-config models, the loader, `detect()`; the **only** place vendor dialect exists |
| `src/server/ingest/otlp/auth.py` | `OrgAuth.verify()` → `OrgPrincipal{org_id}`; TTL cache |
| `src/server/storage/org_token_store.py` | Mint / look up / revoke `tk_org_` keys |
| `src/server/storage/schema/otel_*.py`, `org_tokens.py` | The five tables (storage.md §3.7) |
| `src/server/worker/otel_trueup.py` | The fold, the three-way join, the cost block, the prune |
| `config/otel/{claude_code,gen_ai}.yaml` | Vendor mappings — **adding an agent is adding a file here** |
| `config/queries/otel.yaml` | Every SQL statement the worker runs |
| `tests/fixtures/otel/` | Anonymized recorded fixtures + the generator; see its README |

---

## 2. Enrollment

### Claude Code

Everything an org pushes via **managed settings** (or a solo dev sets in `settings.json`).
No Talium software on the machine — this is the zero-install tier.

```json
{
  "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
  "OTEL_METRICS_EXPORTER": "otlp",
  "OTEL_LOGS_EXPORTER": "otlp",
  "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
  "OTEL_EXPORTER_OTLP_ENDPOINT": "https://<ingest-host>/otlp",
  "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer tk_org_<token>"
}
```

The SDK appends `/v1/metrics` and `/v1/logs` to the endpoint itself. `http/json` also works;
`grpc` does not (D-OT5) — a fleet that must use it points at its own collector gateway, which
forwards as OTLP/HTTP.

Issue the token first:

```bash
python -m server.storage.cli_db org-token issue --org acme --descriptor '{"note":"laptop fleet"}'
```

The plaintext prints once and is never recoverable; only its sha256 is stored. To retire one:

```bash
python -m server.storage.cli_db org-token list --org acme
```

```bash
python -m server.storage.cli_db org-token revoke --hash-prefix a1b2c3d4e5f6
```

### Other agents

Enrollment is per vendor and lives with its mapping config. `gen_ai.yaml` ships as a
pseudo-vendor for the GenAI semantic conventions, so any semconv-compliant tool maps with no
work — but its rows fold with provenance `estimated` until it gets its own recorded-dump
reconciliation spike (design §4.6). Gemini CLI and Codex CLI are v1.1.

### Optional gateway

An org that wants buffering, attribute scrubbing or egress control puts **its own** OTel
Collector in front and points it at the same endpoint. Zero Talium code — that is what
serving spec-compliant OTLP buys.

---

## 3. Config reference

`config/ingest.yaml` → `ingest.otlp`:

| Key | Default | Why it exists |
|---|---|---|
| `enabled` | `true` | Turns the routes off entirely |
| `configs_dir` | `config/otel` | Where vendor mappings load from, at boot |
| `max_body_bytes` | `8388608` | 413 above it; real payloads are 2–50 KB |
| `max_inflight` | `16` | Shed 429 beyond it, **before** decode. The shed counter is the measured trigger for the D-OT1 process split |
| `org_rate_per_s` / `org_burst` | `200` / `400` | Per-org token bucket; the burst absorbs a fleet's synchronized export tick |
| `retry_after_s` | `5` | What a shed or throttled client is told; the OTel SDK honors it |

`config/worker.yaml` → `worker`:

| Key | Default | Why it exists |
|---|---|---|
| `otel_trueup_enabled` | `true` | Turns the timer off |
| `otel_trueup_interval_s` | `60` | With the client's 60 s export: spend truth ~1–2 min behind |
| `otel_configs_dir` | `config/otel` | The same mappings — the fold groups by canonical measure |
| `otel_points_retention_days` | `30` | `otel_metric_points` is staging, not a ledger |
| `otel_prune_interval_s` | `3600` | Deleting a day's points once an hour is plenty |

Mapping files (`config/otel/*.yaml`) are documented by design §4.6; the shipped
`claude_code.yaml` is the worked example. Files are evaluated in **filename order** and the
first `detect` hit wins, which is why `claude_code.yaml` beats `gen_ai.yaml` on a payload
carrying both.

---

## 4. Debugging: "show your work for session X"

**1. Did the batch even arrive, and what was dropped?** Every accepted body logs
`otlp_batch_accepted` with counts only — no payload content ever reaches a log line (arch §13):

```
otlp_batch_accepted signal=logs vendor=claude_code org_id=acme series=0 points=0
                    events_kept=1 events_dropped={'tool_result': 1, 'hook_execution_start': 1}
                    rejected=0
```

Other lines worth grepping: `otlp_shed` (over `max_inflight`), `otlp_throttled` (per-org rate
limit), `otlp_auth_failed` (reason only — never key material, not even a prefix),
`otlp_unknown_vendor` (**a real fleet arriving unclaimed means a mapping file is missing**),
`otlp_body_too_large`, `otlp_malformed`.

**2. What landed?**

```sql
SELECT vendor, metric_name, metric_kind, temporality, count(*)
FROM otel_series WHERE session_id = 'X' GROUP BY 1,2,3,4;
```

```sql
SELECT request_id, model, input_tokens, output_tokens, cost_usd_micros, query_source
FROM otel_api_requests WHERE vendor = 'claude_code' AND session_id = 'X' ORDER BY event_ts;
```

**3. Recompute the fold by hand.** The named query `otel:series_last_values` is the whole
fold — last value per reset epoch, one row each. Sum its `value` per canonical measure and
compare against `session_info.otel`:

```sql
SELECT s.metric_name, s.attrs ->> 'type' AS type, p.start_unix_nano, p.value
FROM otel_series s JOIN otel_metric_points p ON p.series_id = s.series_id
WHERE s.vendor = 'claude_code' AND s.session_id = 'X'
ORDER BY s.metric_name, p.start_unix_nano, p.time_unix_nano DESC;
```

More than one `start_unix_nano` for a series is a **counter reset**, not a duplicate: totals
sum across epochs, last-value within one. If a number looks ~5–150× too large, something
summed datapoints instead — that is the failure this table shape exists to make visible.

**4. Read the answer.**

```sql
SELECT otel FROM session_info WHERE session_id = 'X';
```

**Two labels per request, and they answer different questions.**

`sources` — which pipes saw it: `both` (matched on request_id), `otel_only` (a background
call, or JSONL that has not arrived yet — **transient**, it migrates on the tick the JSONL
lands), `jsonl_only` (OTel has no row).

`cost_basis` — where the dollar came from: `vendor` (the vendor's own figure, adopted),
`estimated` (priced from tokens via the `common.model_pricing` catalog (`Pricer`)), `unknown` (no vendor cost and no
price for that model on that date).

`otel_gap` explains a `jsonl_only` row: `pre_otel` means the request predates the first thing
OTel ever saw for this org — telemetry was not on yet, and no pipeline fault is implied.
`unobserved` means OTel *was* watching and still missed it, which for an interactive request
means the user interrupted it.

Three session totals, kept separate so nobody has to guess which they are reading:
`cost_usd_micros` (vendor only), `cost_est_usd_micros` (estimates only), and
`cost_effective_usd_micros` — vendor where it exists, estimate where it does not. That last
one is what a dashboard shows. `cost_complete: false` now means only the case we genuinely
cannot answer: some request has no cost from either route.

`calibration` re-prices every request the vendor already priced and reports the disagreement,
per model. It is how a stale price table announces itself.

**5. Not folding at all?** Check the watermark: `SELECT * FROM otel_watermarks;`. A session is
only revisited when it has OTel rows past that mark, when it still holds `otel_only`
requests, when its JSONL has moved since the last fold, or when its block predates the
current `block_version`.

---

## 5. Invariants worth not breaking

- **Never sum cumulative datapoints.** Last value per reset epoch, summed across epochs.
- **No vendor literal outside `config/otel/*.yaml`** and `mapping.py`'s tests.
- **Every write is insert-if-absent.** OTel SDKs retry; duplicate delivery is normal.
- **`org_id` comes from the token, never the payload.** The payload's `organization.id` rides
  `attrs` as reconciliation evidence.
- **Logs carry identifiers and counts, never attribute values.**
- **The prune never deletes above the fold watermark.**
- **`session_info.otel` has one writer.** `session_info.info` belongs to the JSONL fold and is
  replaced wholesale on every arrival.

---

## 6. Known limits (recorded, not bugs)

- Interrupted requests never appear in OTel — `sources: jsonl_only` with
  `otel_gap: unobserved` is permanent for them; their cost is estimated from tokens.
- Sessions whose JSONL never arrives are spend-true and fidelity-blank. That is the intended
  zero-install tier, not an error.
- Histogram datapoints contribute their own `sum`; bucket detail is dropped.
- `gen_ai.yaml` detection is written against the specification, not a recorded dump, and
  carries a TODO to that effect. A missed payload degrades to `vendor=unknown`: loud, and
  lossless for metrics.
- `max_series_per_org` (design §7's hostile-client cap) is v1.1.
- No Alembic migration: the repo is still in storage.md §6's phase-1 recreate era.
