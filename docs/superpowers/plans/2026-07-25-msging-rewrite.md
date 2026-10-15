# Event Messaging Library (msging) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/common/msging/` — an at-least-once msg transport where a producer POSTs one msg at a time and receives its outcome asynchronously over SSE.

**Architecture:** Two halves that share only models, codes, config, and errors. The **client** (`clnt/`) is threads + blocking httpx: `send()` is synchronous to a `202` and never waits for the ACK; a durable file-per-msg store survives restart; an SSE reader thread and a cleaner loop run in the background. The **server** (`srv/`) is asyncio + FastAPI: it returns an `APIRouter` the host mounts, enqueues accepted msgs for a worker pool, and pushes outcomes over SSE. The server holds **no durable state** — `server_epoch` and `sent_timeout_s` recover every lost-outcome path.

**Tech Stack:** Python 3.11+, pydantic v2, httpx, FastAPI, uvicorn, structlog, PyYAML, pytest.

**Normative sources.** The design doc is authoritative. Where this plan and the design doc disagree, the design doc wins — stop and flag it.
- `docs/design/event-messaging-design.md`
- `docs/design/notes/event-messaging-clarifications.md` (decision index, D1–D53)

## Global Constraints

- **Python 3.11+.** Always run via `venv/bin/python`, never bare `python`.
- **Dependencies:** install explicitly with `pip install <pkg>` and update `requirements.txt` in the same commit (CLAUDE.md).
- **`clnt/` and `srv/` must never import each other.** They share only `models`, `codes`, `config`, `errors`, `log`, `metrics`. This is what makes "two halves, two concurrency models" (design §6.4) enforceable. A test asserts it.
- **msging must import cleanly without SQLAlchemy or psycopg installed.** The postgres producer-store adapter is lazy-imported inside the function that selects it (design §5.2). A test asserts it.
- **Never log an api_key** — `key_prefix` only. **Never log a payload** — `size_bytes` only (design §10.4).
- **Structured JSON logging** via structlog, per CLAUDE.md rule 1.
- **One error enum** (`codes.py`) used for `send()` return codes, wire body `code`, and NACK codes (design §8).
- **CLI entry points** are named `cli_{function}.py`. No bare `cli.py`. None are in scope for this plan.
- **Commit messages:** never mention Claude or Claude Code (CLAUDE.md rule 3).
- **Skipped work leaves a `TODO` in the source** describing the task; remove it when done (CLAUDE.md).
- **Out of scope, do not build:** lib-side dedup cache (§9.1), chunked payloads (§9.2), `batch_mode` (§9.3), `cli_producers.py` (gh #19), metrics exposure/endpoint/scraper (§10.5), enrollment-key enforcement beyond the config stanza (§10.1 ships `mode: open`).

---

## File Structure

```
src/common/msging/
  __init__.py          public surface: re-export clnt.* and srv.* lazily
  schema/
    envelope.json      canonical header contract (D50, task #6 in tracker)
    notify_ev.json     canonical outcome contract
    msg_record.json    canonical on-disk record
  models.py            Envelope, NotifyEv, MsgRecord, SendResult, CloseResult, ProducerRecord
  codes.py             Code enum, Action enum, TERMINAL/STORED classification
  errors.py            NotRegisteredError, NoCallbackError, InvalidEnvelopeError,
                       ProducerClosingError, ConfigError, RegistrationError, AuthError
  config.py            ClientConfig, ServerConfig, ProducerStoreConfig, load_config
  log.py               structlog JSON setup + redaction helpers
  metrics.py           in-process counters/gauges, metrics() -> dict

  clnt/
    __init__.py
    api.py             clnt_init register on_notify set_info send resolve flush close poll_notify
    binding.py         alias -> endpoint, identity.json, stale-binding check, pid -> binding
    store.py           pending/ sent/ nacked/, atomic durable write, state transitions
    msg_id.py          block-reservation allocator
    transport.py       one POST attempt, gzip, HTTP status -> Code
    sse.py             reader thread, idle timer, epoch detection, reconnect backoff
    window.py          max_in_flight accounting
    cleaner.py         sweep loop

  srv/
    __init__.py
    api.py             srv_init srv_start srv_stop on_event on_close send_notice make_app
    router.py          APIRouter: /register /event /notify /close
    queue.py           inbound queue + worker pool
    notify.py          per-producer SSE registry, ring buffer, heartbeat, server_epoch
    auth.py            issue/verify, key hash + prefix, TTL cache, revoked
    producer_store.py  ProducerStore protocol, FileProducerStore, make_store

  examples/
    srv_hosted.py srv_make_app.py srv_two.py clnt_full.py

tests/common/msging/
  test_models.py test_codes.py test_config.py test_boundaries.py
  test_msg_id.py test_store.py test_binding.py
  test_producer_store.py test_auth.py test_router.py test_notify.py
  test_transport.py test_sse.py test_window.py test_cleaner.py test_api.py
  test_integration.py test_failure_paths.py test_schema_parity.py
  conftest.py
```

**Boundary rule:** `clnt/` imports from the module root only. `srv/` imports from the module root only. Enforced by `test_boundaries.py`.

---

## Phase 1 — Shared foundation

### Task 1: Dependencies and package skeleton

**Files:**
- Modify: `requirements.txt`
- Create: `src/common/msging/clnt/__init__.py`, `src/common/msging/srv/__init__.py`
- Create: `tests/common/msging/__init__.py`, `tests/common/msging/conftest.py`
- Test: `tests/common/msging/test_boundaries.py`

**Interfaces:**
- Consumes: nothing
- Produces: importable `common.msging.clnt` and `common.msging.srv` packages

- [ ] **Step 1: Install the two missing dependencies**

```bash
venv/bin/pip install fastapi uvicorn pytest-asyncio
```

- [ ] **Step 2: Add them to requirements.txt**

Append to `requirements.txt`:
```
fastapi>=0.115
uvicorn>=0.30
pytest-asyncio>=0.24
```

- [ ] **Step 3: Write the boundary test**

Create `tests/common/msging/test_boundaries.py`:
```python
"""The two halves must not import each other, and msging must import without
SQLAlchemy installed. Both are load-bearing design constraints, not style."""
import ast
import pathlib

MSGING = pathlib.Path(__file__).resolve().parents[3] / "src" / "common" / "msging"


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_clnt_never_imports_srv():
    for path in (MSGING / "clnt").rglob("*.py"):
        for name in _imports(path):
            assert "srv" not in name.split("."), f"{path.name} imports {name}"


def test_srv_never_imports_clnt():
    for path in (MSGING / "srv").rglob("*.py"):
        for name in _imports(path):
            assert "clnt" not in name.split("."), f"{path.name} imports {name}"


def test_no_toplevel_sqlalchemy_anywhere():
    """The postgres adapter is lazy-imported inside make_store (design §5.2)."""
    for path in MSGING.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:            # module level only
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.dump(node)
                assert "sqlalchemy" not in text.lower(), f"{path.name}"
                assert "psycopg" not in text.lower(), f"{path.name}"
```

- [ ] **Step 4: Create the empty subpackages**

`src/common/msging/clnt/__init__.py`:
```python
"""Client half: threads + blocking httpx. Never imports srv (design §6.4)."""
```

`src/common/msging/srv/__init__.py`:
```python
"""Server half: asyncio + FastAPI. Never imports clnt (design §6.4)."""
```

`tests/common/msging/__init__.py`: empty file.

- [ ] **Step 5: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_boundaries.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt src/common/msging/clnt src/common/msging/srv tests/common/msging
git commit -m "feat(msging): package skeleton and import-boundary tests"
```

---

### Task 2: Schema JSON, models, and parity test

**Files:**
- Create: `src/common/msging/schema/envelope.json`, `notify_ev.json`, `msg_record.json`
- Modify: `src/common/msging/models.py` (currently holds only `ProducerRecord`)
- Test: `tests/common/msging/test_schema_parity.py`, `tests/common/msging/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Envelope(schema_version: str, event_id: str|None, dedup: bool, producer_id: str, msg_id: int, service_name: str, created_at: str, producer_desc: str|None, info: dict, payload: dict)`
  - `NotifyEv(msg_id: int, producer_id: str, event_id: str|None, code: str, reason: str, info: dict, ts: str)`
  - `MsgRecord(msg_id: int, envelope: Envelope, created_at: str, sent_at: str|None, attempts: int, nack_reason: str|None)`
  - `SendResult(err_code: str, reason: str, msg_id: int|None)`
  - `CloseResult(status: str, unresolved: int)`
  - `ProducerRecord` (unchanged, already present)

**Design refs:** §7 (envelope), §8.2 (notify_ev), §3 (msg record), §10.2 (schema_version is `"1.0"`, unenforced).

- [ ] **Step 1: Write the schema files**

`src/common/msging/schema/envelope.json`:
```json
{
  "schema_version": "1.0",
  "event_id": "sess-2026-07-25-abc",
  "dedup": false,
  "producer_id": "prodA",
  "msg_id": 42,
  "service_name": "ingest",
  "created_at": "2026-07-25T10:15:30Z",
  "producer_desc": "optional desc",
  "info": {},
  "payload": {}
}
```

`src/common/msging/schema/notify_ev.json`:
```json
{
  "msg_id": 42,
  "producer_id": "prodA",
  "event_id": "sess-2026-07-25-abc",
  "code": "ack",
  "reason": "",
  "info": {},
  "ts": "2026-07-25T10:15:31Z"
}
```

`src/common/msging/schema/msg_record.json`:
```json
{
  "msg_id": 42,
  "envelope": {},
  "created_at": "2026-07-25T10:15:30Z",
  "sent_at": "2026-07-25T10:15:30Z",
  "attempts": 1,
  "nack_reason": null
}
```

- [ ] **Step 2: Write the parity test**

Create `tests/common/msging/test_schema_parity.py`:
```python
"""JSON is the readable source of truth (D50); pydantic mirrors it. This test is
what makes drift impossible — edit the JSON, this fails and names the field."""
import json
import pathlib

import pytest

from common.msging.models import Envelope, MsgRecord, NotifyEv

SCHEMA = pathlib.Path(__file__).resolve().parents[3] / "src" / "common" / "msging" / "schema"


@pytest.mark.parametrize(
    "filename,model",
    [("envelope.json", Envelope), ("notify_ev.json", NotifyEv), ("msg_record.json", MsgRecord)],
)
def test_model_fields_match_schema_json(filename, model):
    keys = set(json.loads((SCHEMA / filename).read_text(encoding="utf-8")))
    assert set(model.model_fields) == keys
```

- [ ] **Step 3: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_schema_parity.py -v`
Expected: FAIL — `ImportError: cannot import name 'Envelope'`.

- [ ] **Step 4: Write the models**

Replace `src/common/msging/models.py` contents below the existing `ProducerRecord` — keep `ProducerRecord` exactly as it is (`src/server/storage` depends on it):
```python
"""Shared models. The JSON files in schema/ are canonical; these mirror them,
and tests/common/msging/test_schema_parity.py enforces the mirror (D50).

payload and info are deliberately unschema'd — that is what keeps payload churn
out of the transport (design §7)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Envelope(BaseModel):
    # extra="allow": receivers must ignore unknown keys (design §10.2)
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    event_id: str | None = None
    dedup: bool = False
    producer_id: str
    msg_id: int
    service_name: str
    created_at: str
    producer_desc: str | None = None
    info: dict = Field(default_factory=dict)
    payload: dict = Field(default_factory=dict)


class NotifyEv(BaseModel):
    model_config = ConfigDict(extra="allow")

    msg_id: int
    producer_id: str
    event_id: str | None = None
    code: str
    reason: str = ""
    info: dict = Field(default_factory=dict)
    ts: str


class MsgRecord(BaseModel):
    """The on-disk record. Stores the WHOLE envelope so a resend after restart
    rebuilds the request verbatim (design §3)."""

    msg_id: int
    envelope: Envelope
    created_at: str
    sent_at: str | None = None
    attempts: int = 0
    nack_reason: str | None = None


class SendResult(BaseModel):
    err_code: str
    reason: str = ""
    msg_id: int | None = None


class CloseResult(BaseModel):
    status: str          # drained | timeout
    unresolved: int
```

- [ ] **Step 5: Write the model behaviour test**

Create `tests/common/msging/test_models.py`:
```python
from common.msging.models import Envelope, MsgRecord


def _env(**kw):
    base = dict(producer_id="prodA", msg_id=1, service_name="ingest",
                created_at="2026-07-25T10:15:30Z")
    return Envelope(**{**base, **kw})


def test_unknown_keys_are_kept_not_rejected():
    """Must-ignore evolution: a newer producer's extra field must not break us."""
    env = Envelope.model_validate({
        "producer_id": "prodA", "msg_id": 1, "service_name": "ingest",
        "created_at": "2026-07-25T10:15:30Z", "future_field": "surprise",
    })
    assert env.model_dump()["future_field"] == "surprise"


def test_schema_version_defaults_to_1_0():
    assert _env().schema_version == "1.0"


def test_payload_and_info_default_empty():
    env = _env()
    assert env.payload == {} and env.info == {}


def test_msg_record_round_trips_the_whole_envelope():
    rec = MsgRecord(msg_id=1, envelope=_env(payload={"a": 1}, event_id="e1"),
                    created_at="2026-07-25T10:15:30Z")
    back = MsgRecord.model_validate_json(rec.model_dump_json())
    assert back.envelope.payload == {"a": 1}
    assert back.envelope.event_id == "e1"
```

- [ ] **Step 6: Run both test files**

Run: `venv/bin/python -m pytest tests/common/msging/test_schema_parity.py tests/common/msging/test_models.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/common/msging/schema src/common/msging/models.py tests/common/msging/test_schema_parity.py tests/common/msging/test_models.py
git commit -m "feat(msging): canonical schema JSON, pydantic mirrors, parity test"
```

---

### Task 3: Codes and errors

**Files:**
- Create: `src/common/msging/codes.py`
- Modify: `src/common/msging/errors.py` (currently deleted — create it)
- Test: `tests/common/msging/test_codes.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Code` — str enum: `OK="ok"`, `ALREADY_PROCESSED="already_processed"`, `STORED_TIMEOUT="stored_timeout"`, `STORED_UNREACHABLE="stored_unreachable"`, `STORED_THROTTLED="stored_throttled"`, `SERVER_BUSY="server_busy"`, `SLOT_FULL="slot_full"`, `TOO_LARGE="too_large"`, `UNAUTHORIZED="unauthorized"`, `REJECTED="rejected"`, `ACK="ack"`, `NACK="nack"`
  - `Action` — str enum: `PURGE="purge"`, `RETAIN="retain"`
  - `IS_STORED: frozenset[Code]`, `IS_REJECTION: frozenset[Code]`
  - Errors: `MsgingError`, `ConfigError`, `RegistrationError`, `AuthError(code)`, `NotRegisteredError`, `NoCallbackError`, `InvalidEnvelopeError`, `ProducerClosingError`

**Design refs:** §8.1 (code table), §8.3 (raises), §2.2 (Action default RETAIN).

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_codes.py`:
```python
import pytest

from common.msging.codes import IS_REJECTION, IS_STORED, Action, Code


def test_stored_codes_mean_durably_held_not_shipped():
    assert IS_STORED == {Code.STORED_TIMEOUT, Code.STORED_UNREACHABLE,
                         Code.STORED_THROTTLED, Code.SERVER_BUSY}


def test_only_two_codes_reject_outright():
    """slot_full and too_large are the only backpressure a caller must handle."""
    assert IS_REJECTION == {Code.SLOT_FULL, Code.TOO_LARGE}


def test_codes_are_plain_strings_on_the_wire():
    assert Code.OK == "ok"
    assert Code.SERVER_BUSY.value == "server_busy"


def test_action_default_is_retain():
    assert Action.RETAIN == "retain"


@pytest.mark.parametrize("code", list(Code))
def test_every_code_is_lowercase_snake(code):
    assert code.value == code.value.lower()
    assert " " not in code.value
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_codes.py -v`
Expected: FAIL — `ModuleNotFoundError: common.msging.codes`.

- [ ] **Step 3: Write codes.py**

```python
"""One enum everywhere: send() return codes, wire body `code`, NACK codes over
SSE (design §8). Two vocabularies for the same conditions is not allowed."""
from enum import StrEnum


class Code(StrEnum):
    OK = "ok"
    ALREADY_PROCESSED = "already_processed"   # reserved for §9.1; v1 never emits
    STORED_TIMEOUT = "stored_timeout"
    STORED_UNREACHABLE = "stored_unreachable"
    STORED_THROTTLED = "stored_throttled"
    SERVER_BUSY = "server_busy"
    SLOT_FULL = "slot_full"
    TOO_LARGE = "too_large"
    UNAUTHORIZED = "unauthorized"
    REJECTED = "rejected"
    ACK = "ack"
    NACK = "nack"


class Action(StrEnum):
    PURGE = "purge"
    RETAIN = "retain"


#: durably stored, not yet shipped — informational, the promise still holds
IS_STORED = frozenset({Code.STORED_TIMEOUT, Code.STORED_UNREACHABLE,
                       Code.STORED_THROTTLED, Code.SERVER_BUSY})

#: the only outright rejections — the caller must handle these
IS_REJECTION = frozenset({Code.SLOT_FULL, Code.TOO_LARGE})
```

- [ ] **Step 4: Write errors.py**

```python
"""Runtime conditions return a Code; programming errors raise (design §8.3)."""


class MsgingError(Exception):
    pass


class ConfigError(MsgingError):
    """Unknown, unset, or disabled server alias; malformed config."""


class RegistrationError(MsgingError):
    """Server rejected registration. Terminal — no retry loop."""


class AuthError(MsgingError):
    """Server side: verification failed. code: missing | unknown | revoked."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NotRegisteredError(MsgingError):
    """send() before register()."""


class NoCallbackError(MsgingError):
    """SSE connect attempted without on_notify() (design §4.1)."""


class InvalidEnvelopeError(MsgingError):
    """dedup=True with no event_id."""


class ProducerClosingError(MsgingError):
    """send() after close() — the producer is draining."""
```

- [ ] **Step 5: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_codes.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/common/msging/codes.py src/common/msging/errors.py tests/common/msging/test_codes.py
git commit -m "feat(msging): shared code enum and exception hierarchy"
```

---

### Task 4: Config loading

**Files:**
- Create: `src/common/msging/config.py`
- Create: `config/msging.yaml`
- Test: `tests/common/msging/test_config.py`

**Interfaces:**
- Consumes: `ConfigError` from `errors.py`
- Produces:
  - `ServerBinding(alias, base_path, service_name, enabled, **overrides)`
  - `ClientConfig.servers: dict[str, ServerBinding]`, `.default_server: str|None`, `.defaults: ClientDefaults`
  - `ClientDefaults(req_timeout_s=10.0, retry_num=3, max_pending=10000, slot_wait_s=1.0, max_in_flight=64, sent_timeout_s=300.0, close_timeout_s=30.0, msg_id_block=1000, idle_timeout_s=45.0, sweep_interval_s=5.0, gzip_min_bytes=1024, base_dir="~/.talium/msging")`
  - `ServerConfig(queue_max=10000, retry_after_s=5, heartbeat_s=15.0, max_msg_bytes=33554432, notify_buffer=100, notify_ttl_s=60.0, registration=RegistrationConfig(mode="open", key=None))`
  - `ProducerStoreConfig(backend="file", file=..., postgres=...)`
  - `load_config(path) -> MsgingConfig` with `${ENV}` and `${ENV:-default}` interpolation
  - `ClientConfig.settings_for(alias) -> ClientDefaults` — per-server overrides merged over defaults

**Design refs:** §5.1, §5.2, §10.1.

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_config.py`:
```python
import pytest

from common.msging.config import load_config
from common.msging.errors import ConfigError

YAML = """
client:
  default_server: ${TALIUM_SERVER:-ingest_prod}
  servers:
    ingest_prod:
      base_path: "https://ingest.prod:8443"
      service_name: ingest
    ingest_dev:
      base_path: "https://ingest.dev:8443"
      service_name: ingest
      enabled: false
      req_timeout_s: 30
  defaults:
    req_timeout_s: 10
    max_in_flight: 64
server:
  queue_max: 500
  registration:
    mode: open
producer_store:
  backend: file
  file:
    path: /tmp/producers.json
"""


@pytest.fixture()
def cfg_path(tmp_path):
    p = tmp_path / "msging.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p


def test_env_default_applies_when_unset(cfg_path, monkeypatch):
    monkeypatch.delenv("TALIUM_SERVER", raising=False)
    cfg = load_config(cfg_path)
    assert cfg.client.default_server == "ingest_prod"


def test_env_overrides_default(cfg_path, monkeypatch):
    monkeypatch.setenv("TALIUM_SERVER", "ingest_dev")
    cfg = load_config(cfg_path)
    assert cfg.client.default_server == "ingest_dev"


def test_service_name_defaults_to_alias(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("client:\n  servers:\n    ingest:\n      base_path: https://h:1\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.client.servers["ingest"].service_name == "ingest"


def test_per_server_override_beats_defaults(cfg_path):
    cfg = load_config(cfg_path)
    assert cfg.client.settings_for("ingest_prod").req_timeout_s == 10
    assert cfg.client.settings_for("ingest_dev").req_timeout_s == 30


def test_defaults_fill_unspecified_keys(cfg_path):
    """A key absent from YAML falls back to the code default, not None."""
    assert cfg_path and load_config(cfg_path).client.settings_for("ingest_prod").msg_id_block == 1000


def test_unknown_alias_raises(cfg_path):
    with pytest.raises(ConfigError):
        load_config(cfg_path).client.settings_for("nope")


def test_registration_defaults_to_open(cfg_path):
    assert load_config(cfg_path).server.registration.mode == "open"


def test_server_defaults_present_when_stanza_absent(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("client:\n  servers: {}\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.server.queue_max == 10000
    assert cfg.server.max_msg_bytes == 33554432
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: common.msging.config`.

- [ ] **Step 3: Implement config.py**

Write pydantic models matching the Interfaces block above. Requirements the tests pin down:
- `${VAR}` and `${VAR:-default}` interpolation applied to every string scalar before parsing.
- `service_name` defaults to the alias key when absent.
- `settings_for(alias)` returns `ClientDefaults` with per-server keys merged over `defaults`; unknown alias raises `ConfigError`.
- Absent `server:` / `producer_store:` stanzas still yield fully-defaulted objects.

- [ ] **Step 4: Write config/msging.yaml**

Copy the YAML blocks from design §5.1 and §5.2 verbatim, including the `registration` stanza.

- [ ] **Step 5: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_config.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/common/msging/config.py config/msging.yaml tests/common/msging/test_config.py
git commit -m "feat(msging): config loading with env interpolation and per-server overrides"
```

---

### Task 5: Logging and metrics

**Files:**
- Create: `src/common/msging/log.py`, `src/common/msging/metrics.py`
- Test: `tests/common/msging/test_log_metrics.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `get_logger(module: str) -> structlog.BoundLogger`
  - `redact_key(api_key: str) -> str` — returns the 12-char prefix, never the key
  - `counter(name, **labels) -> None` increment by 1; `add(name, n, **labels)`; `gauge(name, value, **labels)`; `observe(name, ms)` (sum+count)
  - `metrics() -> dict` — point-in-time snapshot; `reset_metrics()` for tests

**Design refs:** §10.4, §10.5. Metric names are the lists in §10.5 verbatim.

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_log_metrics.py`:
```python
from common.msging.log import redact_key
from common.msging.metrics import add, counter, gauge, metrics, observe, reset_metrics


def setup_function():
    reset_metrics()


def test_redact_key_never_returns_the_secret():
    key = "tk_prod_" + "s" * 40
    out = redact_key(key)
    assert out not in (key,) and len(out) == 12 and key.startswith(out)


def test_counter_increments_and_labels_separate_series():
    counter("msgs_sent_total", err_code="ok")
    counter("msgs_sent_total", err_code="ok")
    counter("msgs_sent_total", err_code="slot_full")
    snap = metrics()
    assert snap["msgs_sent_total"]['{"err_code":"ok"}'] == 2
    assert snap["msgs_sent_total"]['{"err_code":"slot_full"}'] == 1


def test_gauge_replaces_rather_than_accumulates():
    gauge("pending_count", 5)
    gauge("pending_count", 3)
    assert metrics()["pending_count"]["{}"] == 3


def test_observe_records_sum_and_count():
    observe("send_latency_ms", 10.0)
    observe("send_latency_ms", 30.0)
    assert metrics()["send_latency_ms"]["{}"] == {"sum": 40.0, "count": 2}


def test_add_increments_by_n():
    add("epoch_swept_msgs_total", 7)
    assert metrics()["epoch_swept_msgs_total"]["{}"] == 7
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_log_metrics.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement log.py and metrics.py**

`metrics.py` holds a module-level dict guarded by a `threading.Lock` (the client is threaded). Label sets serialize to a sorted JSON string as the inner key. `log.py` configures structlog for JSON output and exposes `redact_key`.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_log_metrics.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/log.py src/common/msging/metrics.py tests/common/msging/test_log_metrics.py
git commit -m "feat(msging): JSON logging with key redaction, in-process metrics"
```

---

## Phase 2 — Client durable layer

### Task 6: msg_id block allocator

**Files:**
- Create: `src/common/msging/clnt/msg_id.py`
- Test: `tests/common/msging/test_msg_id.py`

**Interfaces:**
- Consumes: nothing
- Produces: `MsgIdAllocator(path: Path, block: int = 1000)` with `.next() -> int`, `.close() -> None`

**Design refs:** §3.1. One fsync per block, never per send. Gaps are fine. Crash must never reuse an id.

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_msg_id.py`:
```python
import json

from common.msging.clnt.msg_id import MsgIdAllocator


def test_ids_are_monotonic(tmp_path):
    a = MsgIdAllocator(tmp_path / "msg_id.json", block=10)
    ids = [a.next() for _ in range(25)]
    assert ids == sorted(ids) and len(set(ids)) == 25


def test_only_one_disk_write_per_block(tmp_path):
    p = tmp_path / "msg_id.json"
    a = MsgIdAllocator(p, block=10)
    for _ in range(10):
        a.next()
    first = json.loads(p.read_text())["next_msg_id"]
    a.next()                                   # 11th crosses the block boundary
    assert json.loads(p.read_text())["next_msg_id"] > first


def test_crash_never_reuses_an_id(tmp_path):
    """Simulated crash: drop the allocator without close(). The persisted
    high-water mark must already be past everything handed out (§3.1)."""
    p = tmp_path / "msg_id.json"
    a = MsgIdAllocator(p, block=100)
    handed_out = [a.next() for _ in range(5)]
    del a                                      # no close()
    b = MsgIdAllocator(p, block=100)
    assert b.next() > max(handed_out)


def test_clean_close_does_not_burn_the_block(tmp_path):
    p = tmp_path / "msg_id.json"
    a = MsgIdAllocator(p, block=1000)
    last = [a.next() for _ in range(3)][-1]
    a.close()
    b = MsgIdAllocator(p, block=1000)
    assert b.next() == last + 1


def test_fresh_store_starts_from_a_nonzero_base(tmp_path):
    """Random base so a lost store under a surviving identity.json does not
    replay ids the server already saw (§3.1)."""
    a = MsgIdAllocator(tmp_path / "a.json", block=10)
    b = MsgIdAllocator(tmp_path / "b.json", block=10)
    assert a.next() > 0 and b.next() > 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_msg_id.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the allocator**

Reserve on construction and on exhaustion: read `next_msg_id`, write `next_msg_id + block`, `fsync`, then hand out from memory. `close()` writes back the true next id. Fresh store picks a random base (e.g. `random.randint(1, 10**6)`). Thread-safe via a lock — `send()` may be called from several producer threads.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_msg_id.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/clnt/msg_id.py tests/common/msging/test_msg_id.py
git commit -m "feat(msging): block-reserving msg_id allocator"
```

---

### Task 7: Durable msg store

**Files:**
- Create: `src/common/msging/clnt/store.py`
- Test: `tests/common/msging/test_store.py`

**Interfaces:**
- Consumes: `MsgRecord` from `models.py`
- Produces: `MsgStore(root: Path)` with
  - `.put_pending(rec: MsgRecord) -> None` — atomic durable create
  - `.mark_sent(msg_id) / .mark_nacked(msg_id, reason) / .demote_to_pending(msg_id) / .purge(msg_id)`
  - `.get(msg_id) -> MsgRecord | None`
  - `.list(state: str) -> list[int]` — sorted msg_ids in `pending|sent|nacked`
  - `.count(state) -> int`, `.total() -> int`
  - `.sweep_sent_to_pending() -> int` — bulk epoch sweep, returns count

**Design refs:** §3 (layout, folder = state), §3.1 (`write tmp -> fsync -> rename -> fsync dir`), §2.1 (`SENT -> PENDING` legal), §2.3 (reconciliation).

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_store.py`:
```python
import pytest

from common.msging.clnt.store import MsgStore
from common.msging.models import Envelope, MsgRecord


def _rec(msg_id: int) -> MsgRecord:
    return MsgRecord(
        msg_id=msg_id,
        envelope=Envelope(producer_id="prodA", msg_id=msg_id, service_name="ingest",
                          created_at="2026-07-25T10:15:30Z", payload={"n": msg_id}),
        created_at="2026-07-25T10:15:30Z",
    )


@pytest.fixture()
def store(tmp_path):
    return MsgStore(tmp_path)


def test_put_pending_is_visible_and_durable(store):
    store.put_pending(_rec(1))
    assert store.list("pending") == [1]
    assert store.get(1).envelope.payload == {"n": 1}


def test_state_is_the_folder(store, tmp_path):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert store.list("pending") == [] and store.list("sent") == [1]
    assert (tmp_path / "sent").exists()


def test_whole_envelope_survives_a_round_trip(store):
    rec = _rec(7)
    rec.envelope.event_id = "e7"
    rec.envelope.dedup = True
    store.put_pending(rec)
    got = store.get(7)
    assert got.envelope.event_id == "e7" and got.envelope.dedup is True


def test_purge_removes_it_everywhere(store):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.purge(1)
    assert store.get(1) is None and store.total() == 0


def test_nack_persists_the_reason(store):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.mark_nacked(1, "schema boom")
    assert store.get(1).nack_reason == "schema boom"
    assert store.list("nacked") == [1]


def test_demote_returns_a_sent_msg_to_pending(store):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.demote_to_pending(1)
    assert store.list("pending") == [1] and store.list("sent") == []


def test_sweep_moves_all_sent_and_reports_the_count(store):
    for i in (1, 2, 3):
        store.put_pending(_rec(i))
        store.mark_sent(i)
    assert store.sweep_sent_to_pending() == 3
    assert store.list("pending") == [1, 2, 3] and store.list("sent") == []


def test_listing_is_sorted_by_msg_id(store):
    for i in (30, 10, 20):
        store.put_pending(_rec(i))
    assert store.list("pending") == [10, 20, 30]


def test_reopening_the_store_sees_prior_state(store, tmp_path):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert MsgStore(tmp_path).list("sent") == [1]


def test_no_partial_file_is_ever_visible(store, tmp_path):
    """Records land via rename, so a reader never sees a half-written file."""
    store.put_pending(_rec(1))
    for path in (tmp_path / "pending").iterdir():
        assert not path.name.endswith(".tmp")
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_store.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement MsgStore**

- Directories `pending/`, `sent/`, `nacked/` created on construction.
- Filename is `{msg_id}.json` (zero-padded is fine but sorting must be numeric).
- `put_pending` writes `{msg_id}.json.tmp`, `fsync`s the file, `os.replace`s into `pending/`, then `fsync`s the directory fd. **The directory fsync is required or the rename is not durable.**
- State transitions are `os.replace` between directories — atomic, no rewrite.
- `mark_nacked` must rewrite the record to persist `nack_reason`, then rename.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_store.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/clnt/store.py tests/common/msging/test_store.py
git commit -m "feat(msging): durable file-per-msg store with atomic state transitions"
```

---

### Task 8: Binding registry

**Files:**
- Create: `src/common/msging/clnt/binding.py`
- Test: `tests/common/msging/test_binding.py`

**Interfaces:**
- Consumes: `ClientConfig` from `config.py`, `ConfigError`
- Produces: `BindingRegistry(cfg: ClientConfig, base_dir: Path)` with
  - `.resolve(alias: str | None) -> ServerBinding` — `None` → `default_server`; raises `ConfigError` if unset, unknown, or disabled
  - `.save_identity(alias, identity: dict) -> None` — writes `identity.json` at mode `0600`
  - `.load_all() -> dict[str, Binding]` — scans `<base>/*/*/identity.json`, rebuilds `producer_id -> binding`
  - `.dir_for(alias, producer_id) -> Path`
  - `.check_stale(alias, stored_base_path) -> bool` — True when the stored `base_path` differs from config

**Design refs:** §3 (layout, alias semantics, stale binding), §5.1, §6.1.

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_binding.py`:
```python
import json
import stat

import pytest

from common.msging.clnt.binding import BindingRegistry
from common.msging.config import load_config
from common.msging.errors import ConfigError

YAML = """
client:
  default_server: ingest_prod
  servers:
    ingest_prod:
      base_path: "https://ingest.prod:8443"
      service_name: ingest
    ingest_dev:
      base_path: "https://ingest.dev:8443"
      enabled: false
"""


@pytest.fixture()
def reg(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(YAML, encoding="utf-8")
    return BindingRegistry(load_config(p).client, tmp_path / "store")


def test_none_resolves_to_default_server(reg):
    assert reg.resolve(None).alias == "ingest_prod"


def test_unknown_alias_raises(reg):
    with pytest.raises(ConfigError):
        reg.resolve("nope")


def test_disabled_alias_raises(reg):
    with pytest.raises(ConfigError):
        reg.resolve("ingest_dev")


def test_identity_is_written_0600(reg):
    reg.save_identity("ingest_prod", {"producer_id": "p1", "api_key": "tk_prod_x"})
    path = reg.dir_for("ingest_prod", "p1") / "identity.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_two_aliases_are_isolated_directories(reg):
    reg.save_identity("ingest_prod", {"producer_id": "p1", "api_key": "k1"})
    a = reg.dir_for("ingest_prod", "p1")
    b = reg.dir_for("ingest_dev", "p2")
    assert a != b and "ingest_prod" in str(a) and "ingest_dev" in str(b)


def test_load_all_rebuilds_producer_to_binding(reg):
    reg.save_identity("ingest_prod", {"producer_id": "p1", "api_key": "k1"})
    assert reg.load_all()["p1"].alias == "ingest_prod"


def test_stale_binding_detected_when_base_path_changed(reg):
    assert reg.check_stale("ingest_prod", "https://old.example:8443") is True
    assert reg.check_stale("ingest_prod", "https://ingest.prod:8443") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_binding.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement binding.py**

`save_identity` writes `{producer_id, api_key, server_alias, base_path, service_name, registered_at}` and chmods `0600`. Store dir is `<base>/<server_alias>/<producer_id>/`.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_binding.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/clnt/binding.py tests/common/msging/test_binding.py
git commit -m "feat(msging): server-alias binding registry with 0600 identity files"
```

---

## Phase 3 — Server half

### Task 9: Producer store

**Files:**
- Create: `src/common/msging/srv/producer_store.py`
- Test: `tests/common/msging/test_producer_store.py`

**Interfaces:**
- Consumes: `ProducerRecord`, `ProducerStoreConfig`
- Produces: `ProducerStore` Protocol (`get`, `get_by_key_prefix`, `upsert`, `touch_last_seen`, `set_revoked`), `FileProducerStore(path)`, `make_store(cfg) -> ProducerStore`

**Recovery shortcut:** the deleted implementation was correct for this task. Recover it and adjust the import path:
```bash
git show 3c15d72^:src/common/msging/store.py > /tmp/old_store.py
```
Change `from .config import ProducerStoreConfig` → `from ..config import ProducerStoreConfig`, and `from .models import ProducerRecord` → `from ..models import ProducerRecord`. Keep the lazy postgres import exactly as it is.

- [ ] **Step 1: Recover the old test too, as a starting point**

```bash
git show 3c15d72^:tests/common/msging/test_store.py > tests/common/msging/test_producer_store.py
```
Rewrite its imports to `common.msging.srv.producer_store`. **Do not** let it collide with Task 7's `test_store.py` — different file, different module.

- [ ] **Step 2: Add the test the old code lacked**

Append to `tests/common/msging/test_producer_store.py`:
```python
import pytest

from common.msging.config import ProducerStoreConfig
from common.msging.srv.producer_store import make_store


def test_postgres_backend_fails_loudly_not_with_importerror():
    """Selecting postgres without the storage module must name the cause."""
    cfg = ProducerStoreConfig(backend="postgres")
    with pytest.raises((NotImplementedError, ImportError)):
        make_store(cfg)


def test_unknown_backend_raises_valueerror():
    with pytest.raises(ValueError):
        make_store(ProducerStoreConfig(backend="redis"))
```

- [ ] **Step 3: Run the tests**

Run: `venv/bin/python -m pytest tests/common/msging/test_producer_store.py tests/common/msging/test_boundaries.py -v`
Expected: all pass, including the no-toplevel-sqlalchemy boundary test.

- [ ] **Step 4: Commit**

```bash
git add src/common/msging/srv/producer_store.py tests/common/msging/test_producer_store.py
git commit -m "feat(msging): producer store protocol, file backend, config-selected adapter"
```

---

### Task 10: Auth

**Files:**
- Create: `src/common/msging/srv/auth.py`
- Test: `tests/common/msging/test_auth.py`

**Interfaces:**
- Consumes: `ProducerStore`, `ProducerRecord`, `AuthError`, `redact_key`
- Produces: `Auth(store: ProducerStore)` with
  - `.issue(descriptor: dict|None, producer_type: str|None) -> tuple[str, str, str]` → `(producer_id, api_key, registered_at)`
  - `.verify(authorization: str | None) -> ProducerRecord` — raises `AuthError("missing"|"unknown"|"revoked")`
  - `.set_revoked(producer_id, revoked)`
  - key format `tk_prod_<token_urlsafe(32)>`, stored as prefix (12 chars) + SHA-256

**Design refs:** §10.3 note, gh #19 (`verify()` must honor `revoked`; `touch_last_seen` throttled).

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_auth.py`:
```python
import pytest

from common.msging.errors import AuthError
from common.msging.srv.auth import Auth
from common.msging.srv.producer_store import FileProducerStore


@pytest.fixture()
def auth(tmp_path):
    return Auth(FileProducerStore(tmp_path / "producers.json"))


def test_issue_returns_a_usable_key(auth):
    pid, key, _ = auth.issue({"host": "laptop"}, "laptop")
    assert auth.verify(f"Bearer {key}").producer_id == pid


def test_plaintext_key_is_never_stored(auth, tmp_path):
    _, key, _ = auth.issue({}, None)
    assert key not in (tmp_path / "producers.json").read_text()


def test_missing_header_raises_missing(auth):
    with pytest.raises(AuthError) as e:
        auth.verify(None)
    assert e.value.code == "missing"


def test_unknown_key_raises_unknown(auth):
    with pytest.raises(AuthError) as e:
        auth.verify("Bearer tk_prod_nope")
    assert e.value.code == "unknown"


def test_revoked_key_is_rejected(auth):
    """This is what §8.1's `unauthorized` terminal code rests on (gh #19)."""
    pid, key, _ = auth.issue({}, None)
    auth.set_revoked(pid, True)
    with pytest.raises(AuthError) as e:
        auth.verify(f"Bearer {key}")
    assert e.value.code == "revoked"


def test_prefix_collision_is_disambiguated_by_hash(auth, monkeypatch):
    """Two keys sharing a prefix must not authenticate as each other."""
    pid_a, key_a, _ = auth.issue({}, None)
    pid_b, key_b, _ = auth.issue({}, None)
    assert auth.verify(f"Bearer {key_a}").producer_id == pid_a
    assert auth.verify(f"Bearer {key_b}").producer_id == pid_b


def test_verify_touches_last_seen(auth):
    pid, key, _ = auth.issue({}, None)
    auth.verify(f"Bearer {key}")
    assert auth.store.get(pid).last_seen_at is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_auth.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement auth.py**

Recover the shape from the deleted server: `git show 3c15d72^:src/common/msging/server.py`. Keep `issue`/`verify`/hashing/prefix/TTL cache; **add the `revoked` check**, which the old code had via the record but must now raise `AuthError("revoked")`. Drop everything ACK/NACK related — that is not auth.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_auth.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/srv/auth.py tests/common/msging/test_auth.py
git commit -m "feat(msging): producer auth with revocation and prefix-collision-safe verify"
```

---

### Task 11: Inbound queue and worker pool

**Files:**
- Create: `src/common/msging/srv/queue.py`
- Test: `tests/common/msging/test_queue.py`

**Interfaces:**
- Consumes: `Envelope`, `ServerConfig`, metrics
- Produces: `InboundQueue(maxsize: int)` with
  - `.offer(env: Envelope) -> bool` — False when full (caller returns `503 server_busy`)
  - `.start(handler: Callable[[Envelope], Awaitable[None] | None], workers: int = 4)`
  - `.stop() -> None` — drains in-flight, cancels workers
  - `.depth() -> int`

**Design refs:** §6.2 (async by design, `POST -> enqueue -> 202`), §5.2 (`queue_max`).

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_queue.py`:
```python
import asyncio

import pytest

from common.msging.models import Envelope
from common.msging.srv.queue import InboundQueue

pytestmark = pytest.mark.asyncio


def _env(n: int) -> Envelope:
    return Envelope(producer_id="p", msg_id=n, service_name="ingest",
                    created_at="2026-07-25T10:15:30Z")


async def test_offer_returns_false_when_full():
    q = InboundQueue(maxsize=2)
    assert q.offer(_env(1)) is True
    assert q.offer(_env(2)) is True
    assert q.offer(_env(3)) is False       # -> 503 server_busy


async def test_handler_runs_after_the_offer_returns():
    seen = []
    q = InboundQueue(maxsize=10)
    q.start(lambda env: seen.append(env.msg_id), workers=1)
    q.offer(_env(1))
    await asyncio.sleep(0.05)
    assert seen == [1]
    await q.stop()


async def test_async_handlers_are_supported():
    seen = []

    async def handler(env):
        seen.append(env.msg_id)

    q = InboundQueue(maxsize=10)
    q.start(handler, workers=2)
    for i in range(5):
        q.offer(_env(i))
    await asyncio.sleep(0.1)
    assert sorted(seen) == [0, 1, 2, 3, 4]
    await q.stop()


async def test_a_raising_handler_does_not_kill_the_worker():
    """One bad msg must not stop the queue draining."""
    seen = []

    def handler(env):
        if env.msg_id == 1:
            raise RuntimeError("boom")
        seen.append(env.msg_id)

    q = InboundQueue(maxsize=10)
    q.start(handler, workers=1)
    q.offer(_env(1))
    q.offer(_env(2))
    await asyncio.sleep(0.1)
    assert seen == [2]
    await q.stop()


async def test_depth_reflects_pending_work():
    q = InboundQueue(maxsize=10)
    q.offer(_env(1))
    assert q.depth() == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_queue.py -v`
Expected: FAIL — module not found. If asyncio tests error on collection, add `asyncio_mode = auto` under `[tool.pytest.ini_options]` in `pyproject.toml` or `pytest.ini`.

- [ ] **Step 3: Implement queue.py**

`asyncio.Queue` with `maxsize`; `offer` uses `put_nowait` inside `try/except asyncio.QueueFull`. Workers are tasks; a sync handler runs directly, an async one is awaited. **Wrap each handler call in try/except and log `event="handler_error"`** — a raising handler must never kill its worker.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_queue.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/srv/queue.py tests/common/msging/test_queue.py
git commit -m "feat(msging): inbound queue with bounded offer and resilient workers"
```

---

### Task 12: SSE notify registry

**Files:**
- Create: `src/common/msging/srv/notify.py`
- Test: `tests/common/msging/test_notify.py`

**Interfaces:**
- Consumes: `NotifyEv`, `ServerConfig`, metrics
- Produces: `NotifyHub(cfg: ServerConfig)` with
  - `.epoch: str` — generated once per process at construction
  - `.subscribe(producer_id) -> AsyncIterator[str]` — yields SSE frames; first frame is the epoch event; emits `: hb` every `heartbeat_s`
  - `.publish(ev: NotifyEv) -> None` — delivers live, or buffers into the producer's ring
  - `.drop(producer_id) -> None`
  - `.buffered(producer_id) -> int`

**Design refs:** §4.2 (heartbeat), §4.3 (epoch is the first event, per process), §6.2 (`notify_buffer`, `notify_ttl_s`, overflow drops).

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_notify.py`:
```python
import asyncio

import pytest

from common.msging.config import ServerConfig
from common.msging.models import NotifyEv
from common.msging.srv.notify import NotifyHub

pytestmark = pytest.mark.asyncio


def _ev(n: int, code: str = "ack") -> NotifyEv:
    return NotifyEv(msg_id=n, producer_id="p", code=code, ts="2026-07-25T10:15:31Z")


async def test_first_frame_carries_the_epoch():
    hub = NotifyHub(ServerConfig())
    stream = hub.subscribe("p")
    first = await anext(stream)
    assert hub.epoch in first


async def test_epoch_is_stable_within_a_process():
    cfg = ServerConfig()
    hub = NotifyHub(cfg)
    assert hub.epoch == hub.epoch


async def test_two_hubs_have_different_epochs():
    """A restart must be detectable — that is the whole point (§4.3)."""
    assert NotifyHub(ServerConfig()).epoch != NotifyHub(ServerConfig()).epoch


async def test_published_event_reaches_a_live_subscriber():
    hub = NotifyHub(ServerConfig())
    stream = hub.subscribe("p")
    await anext(stream)                       # epoch frame
    hub.publish(_ev(42))
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)
    assert '"msg_id":42' in frame.replace(" ", "")


async def test_events_buffer_while_nobody_is_subscribed():
    hub = NotifyHub(ServerConfig(notify_buffer=10))
    hub.publish(_ev(1))
    hub.publish(_ev(2))
    assert hub.buffered("p") == 2


async def test_buffer_overflow_drops_oldest_and_does_not_grow():
    hub = NotifyHub(ServerConfig(notify_buffer=3))
    for i in range(10):
        hub.publish(_ev(i))
    assert hub.buffered("p") == 3


async def test_buffered_events_flush_on_reconnect():
    hub = NotifyHub(ServerConfig(notify_buffer=10))
    hub.publish(_ev(7))
    stream = hub.subscribe("p")
    await anext(stream)                       # epoch frame
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)
    assert '"msg_id":7' in frame.replace(" ", "")


async def test_heartbeat_is_a_comment_frame():
    hub = NotifyHub(ServerConfig(heartbeat_s=0.05))
    stream = hub.subscribe("p")
    await anext(stream)                       # epoch frame
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)
    assert frame.startswith(":")              # SSE comment, ignored by parsers
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_notify.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement notify.py**

- `epoch = uuid.uuid4().hex` at construction.
- Per-producer `collections.deque(maxlen=notify_buffer)` — `maxlen` gives you drop-oldest for free.
- `subscribe` yields `event: epoch\ndata: {...}\n\n` first, then drains the ring, then awaits new events with a `heartbeat_s` timeout, emitting `: hb\n\n` on timeout.
- Expire ring entries older than `notify_ttl_s` when draining.
- Count `notices_dropped_total{reason}` on overflow and expiry.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_notify.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/srv/notify.py tests/common/msging/test_notify.py
git commit -m "feat(msging): SSE notify hub with epoch, heartbeat, and bounded per-producer ring"
```

---

### Task 13: Router and server API

**Files:**
- Create: `src/common/msging/srv/router.py`, `src/common/msging/srv/api.py`
- Test: `tests/common/msging/test_router.py`

**Interfaces:**
- Consumes: everything from Tasks 9–12
- Produces:
  - `srv_init(service_name: str) -> APIRouter` mounted at `talium/{service_name}/v1/*`
  - `srv_start() -> None` / `srv_stop() -> None` (async)
  - `on_event(fn)`, `on_close(fn)`
  - `send_notice(producer_id, msg_id, code, reason=None, info=None) -> None`
  - `make_app(*service_names) -> FastAPI`
  - Routes: `POST /register`, `POST /event`, `GET /notify`, `POST /close`

**Design refs:** §5 (paths), §6.2, §6.3 (host owns the app), §8.1 (status → code), §10.1 (registration mode).

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_router.py`:
```python
import httpx
import pytest
from fastapi import FastAPI

from common.msging import srv

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def client(tmp_path, monkeypatch):
    srv.reset_for_tests(base_dir=tmp_path)          # test hook, see Step 3
    app = FastAPI()
    app.include_router(srv.srv_init("ingest"))
    await srv.srv_start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await srv.srv_stop()


async def _register(c) -> tuple[str, str]:
    r = await c.post("/talium/ingest/v1/register", json={"host": "laptop"})
    assert r.status_code == 200
    return r.json()["producer_id"], r.json()["api_key"]


def _env(pid: str, msg_id: int) -> dict:
    return {"producer_id": pid, "msg_id": msg_id, "service_name": "ingest",
            "created_at": "2026-07-25T10:15:30Z", "payload": {"x": 1}}


async def test_register_returns_identity_and_server_limits(client):
    r = await client.post("/talium/ingest/v1/register", json={})
    body = r.json()
    assert body["producer_id"] and body["api_key"]
    assert body["max_msg_bytes"] == 33554432        # client checks size before storing


async def test_event_without_auth_is_401(client):
    pid, _ = await _register(client)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid, 1))
    assert r.status_code == 401


async def test_event_with_auth_is_202(client):
    pid, key = await _register(client)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid, 1),
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 202


async def test_oversized_event_is_413_too_large(client):
    pid, key = await _register(client)
    env = _env(pid, 1)
    env["payload"] = {"blob": "x" * 200}
    srv.set_max_msg_bytes_for_tests(100)
    r = await client.post("/talium/ingest/v1/event", json=env,
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 413
    assert r.json()["code"] == "too_large"


async def test_queue_full_is_503_with_retry_after(client):
    pid, key = await _register(client)
    srv.set_queue_max_for_tests(0)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid, 1),
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 503
    assert r.json()["code"] == "server_busy"
    assert "Retry-After" in r.headers


async def test_handler_receives_the_envelope_after_202(client):
    seen = []
    srv.on_event(lambda env: seen.append(env.msg_id))
    pid, key = await _register(client)
    await client.post("/talium/ingest/v1/event", json=_env(pid, 9),
                      headers={"Authorization": f"Bearer {key}"})
    import asyncio
    await asyncio.sleep(0.05)
    assert seen == [9]


async def test_close_is_200_and_fires_on_close(client):
    fired = []
    srv.on_close(lambda pid: fired.append(pid))
    pid, key = await _register(client)
    r = await client.post("/talium/ingest/v1/close", json={"producer_id": pid},
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and fired == [pid]


async def test_two_services_mount_side_by_side(tmp_path):
    """One process may host several named services — convention is one, not a limit."""
    srv.reset_for_tests(base_dir=tmp_path)
    app = FastAPI()
    app.include_router(srv.srv_init("ingest"))
    app.include_router(srv.srv_init("audit"))
    paths = {r.path for r in app.routes}
    assert "/talium/ingest/v1/event" in paths
    assert "/talium/audit/v1/event" in paths
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_router.py -v`
Expected: FAIL — `srv_init` not defined.

- [ ] **Step 3: Implement router.py and api.py**

- Module-level state in `api.py`: config, auth, hub, queue, callbacks, plus `reset_for_tests(base_dir)`, `set_max_msg_bytes_for_tests(n)`, `set_queue_max_for_tests(n)` — test hooks, kept minimal and clearly named.
- `/register` honors `registration.mode`: `open` issues; `enrollment_key` compares `X-Talium-Enroll-Key` and returns `401` on mismatch. Response includes `max_msg_bytes`.
- `/event`: `verify()` → `401` on `AuthError`; body size over `max_msg_bytes` → `413 {code: too_large}`; `queue.offer()` False → `503 {code: server_busy}` + `Retry-After`; else `202`.
- `/notify`: `StreamingResponse(hub.subscribe(pid), media_type="text/event-stream")`.
- `/close`: fire `on_close`, `hub.drop(pid)`, touch `last_seen`, `200`.
- `make_app(*names)` builds a `FastAPI` with a lifespan calling `srv_start`/`srv_stop`.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_router.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/srv tests/common/msging/test_router.py
git commit -m "feat(msging): FastAPI router, server API, and make_app convenience"
```

---

## Phase 4 — Client transport

### Task 14: Transport and window

**Files:**
- Create: `src/common/msging/clnt/transport.py`, `src/common/msging/clnt/window.py`
- Test: `tests/common/msging/test_transport.py`, `tests/common/msging/test_window.py`

**Interfaces:**
- Produces:
  - `post_once(base_url, service_name, api_key, env: Envelope, timeout_s, gzip_min_bytes, *, _transport=None, attempts=None) -> SendResult(code, reason, retry_after_s)` — exactly one attempt, never retries. `timeout_s` is one wall-clock deadline on the whole call, not one budget per socket operation. `retry_after_s` is what §8.1 has `server_busy` retry after; the cleaner loop (Task 16) reads it from here
  - `Window(maxsize: int)` with `.acquire() -> bool`, `.release() -> None`, `.in_flight() -> int`

**Design refs:** §2.2 (one attempt), §8.1 (status → code), §5.1 (`max_in_flight` never fails a send).

- [ ] **Step 1: Write the window test**

Create `tests/common/msging/test_window.py`:
```python
from common.msging.clnt.window import Window


def test_acquire_fails_when_full():
    w = Window(2)
    assert w.acquire() and w.acquire()
    assert w.acquire() is False


def test_release_frees_a_slot():
    w = Window(1)
    w.acquire()
    w.release()
    assert w.acquire() is True


def test_release_below_zero_is_ignored():
    """Demotion and ACK can both release; double-release must not create slots."""
    w = Window(1)
    w.release()
    w.release()
    assert w.in_flight() == 0
    assert w.acquire() is True
    assert w.acquire() is False
```

- [ ] **Step 2: Write the transport test**

Create `tests/common/msging/test_transport.py`:
```python
import httpx
import pytest

from common.msging.clnt.transport import post_once
from common.msging.codes import Code
from common.msging.models import Envelope


def _env() -> Envelope:
    return Envelope(producer_id="p", msg_id=1, service_name="ingest",
                    created_at="2026-07-25T10:15:30Z", payload={"x": 1})


def _post(handler, **kw):
    transport = httpx.MockTransport(handler)
    return post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
                     gzip_min_bytes=0, _transport=transport, **kw)


def test_202_maps_to_ok():
    code, _ = _post(lambda r: httpx.Response(202))
    assert code is Code.OK


def test_401_maps_to_unauthorized():
    code, _ = _post(lambda r: httpx.Response(401, json={"reason": "revoked"}))
    assert code is Code.UNAUTHORIZED


def test_400_maps_to_rejected():
    code, _ = _post(lambda r: httpx.Response(400, json={"reason": "bad"}))
    assert code is Code.REJECTED


def test_413_maps_to_too_large():
    code, _ = _post(lambda r: httpx.Response(413, json={"code": "too_large"}))
    assert code is Code.TOO_LARGE


def test_503_maps_to_server_busy():
    code, _ = _post(lambda r: httpx.Response(503, json={"code": "server_busy"}))
    assert code is Code.SERVER_BUSY


def test_timeout_maps_to_stored_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    code, _ = _post(handler)
    assert code is Code.STORED_TIMEOUT


def test_connection_error_maps_to_stored_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)
    code, _ = _post(handler)
    assert code is Code.STORED_UNREACHABLE


def test_body_is_gzipped_above_the_threshold():
    seen = {}

    def handler(request):
        seen["encoding"] = request.headers.get("content-encoding")
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=0, _transport=transport)
    assert seen["encoding"] == "gzip"


def test_bearer_token_is_sent():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    post_once("http://h", "ingest", "secret", _env(), timeout_s=1.0,
              gzip_min_bytes=1 << 30, _transport=transport)
    assert seen["auth"] == "Bearer secret"
```

- [ ] **Step 3: Run both to verify they fail**

Run: `venv/bin/python -m pytest tests/common/msging/test_window.py tests/common/msging/test_transport.py -v`
Expected: FAIL — modules not found.

- [ ] **Step 4: Implement both**

`post_once` takes an optional `_transport` for injection in tests — no live server needed. It catches `httpx.TimeoutException` → `STORED_TIMEOUT` and `httpx.TransportError` → `STORED_UNREACHABLE`, and **never retries**. `Window` is a counter behind a `threading.Lock`; `release()` clamps at zero.

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python -m pytest tests/common/msging/test_window.py tests/common/msging/test_transport.py -v`
Expected: 12 passed.

- [ ] **Step 6: Commit**

```bash
git add src/common/msging/clnt/transport.py src/common/msging/clnt/window.py tests/common/msging/test_window.py tests/common/msging/test_transport.py
git commit -m "feat(msging): single-attempt POST transport and in-flight window"
```

---

### Task 15: SSE reader thread

**Files:**
- Create: `src/common/msging/clnt/sse.py`
- Test: `tests/common/msging/test_sse.py`

**Interfaces:**
- Produces: `SseReader(url, api_key, on_event: Callable[[NotifyEv], None], on_epoch: Callable[[str], None], idle_timeout_s, _transport=None)` with `.start()`, `.stop()`, `.connected: bool`

**Design refs:** §4.1 (callback required first), §4.2 (idle timer), §4.3 (epoch change → sweep).

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_sse.py`:
```python
import threading
import time

import httpx
import pytest

from common.msging.clnt.sse import SseReader
from common.msging.errors import NoCallbackError


def _stream(*frames: str):
    def handler(request):
        body = "".join(frames).encode()
        return httpx.Response(200, content=body,
                              headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


def test_connect_without_callback_raises():
    with pytest.raises(NoCallbackError):
        SseReader("http://h/notify", "k", on_event=None, on_epoch=lambda e: None,
                  idle_timeout_s=1.0).start()


def test_epoch_frame_fires_on_epoch():
    got = []
    r = SseReader("http://h/notify", "k", on_event=lambda e: None,
                  on_epoch=got.append, idle_timeout_s=1.0,
                  _transport=_stream("event: epoch\ndata: {\"epoch\": \"abc\"}\n\n"))
    r.start()
    time.sleep(0.2)
    r.stop()
    assert got == ["abc"]


def test_data_frame_fires_on_event():
    got = []
    frame = ('event: notify\n'
             'data: {"msg_id": 42, "producer_id": "p", "code": "ack", '
             '"ts": "2026-07-25T10:15:31Z"}\n\n')
    r = SseReader("http://h/notify", "k", on_event=got.append,
                  on_epoch=lambda e: None, idle_timeout_s=1.0,
                  _transport=_stream("event: epoch\ndata: {\"epoch\":\"e\"}\n\n", frame))
    r.start()
    time.sleep(0.2)
    r.stop()
    assert got[0].msg_id == 42 and got[0].code == "ack"


def test_comment_frames_are_ignored():
    got = []
    r = SseReader("http://h/notify", "k", on_event=got.append,
                  on_epoch=lambda e: None, idle_timeout_s=1.0,
                  _transport=_stream(": hb\n\n", ": hb\n\n"))
    r.start()
    time.sleep(0.2)
    r.stop()
    assert got == []


def test_stop_is_idempotent_and_joins_the_thread():
    r = SseReader("http://h/notify", "k", on_event=lambda e: None,
                  on_epoch=lambda e: None, idle_timeout_s=1.0,
                  _transport=_stream(": hb\n\n"))
    r.start()
    r.stop()
    r.stop()
    assert not any(t.name.startswith("msging-sse") for t in threading.enumerate())
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_sse.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement sse.py**

Daemon thread named `msging-sse-{producer_id}`. Uses `httpx.Client.stream("GET", ...)` and iterates lines. Parses `event:` / `data:` pairs; lines starting with `:` are comments and reset only the idle timer. Reconnects with jittered exponential backoff on any read failure or idle timeout. `start()` raises `NoCallbackError` when `on_event` is None.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_sse.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/clnt/sse.py tests/common/msging/test_sse.py
git commit -m "feat(msging): SSE reader thread with epoch detection and idle timeout"
```

---

### Task 16: Cleaner loop

**Files:**
- Create: `src/common/msging/clnt/cleaner.py`
- Test: `tests/common/msging/test_cleaner.py`

**Interfaces:**
- Produces: `Cleaner(store, window, send_fn, sweep_interval_s, sent_timeout_s, retry_num)` with `.start()`, `.stop()`, `.sweep_once() -> dict` (returns `{"retried": n, "demoted": n, "purged": n}` — synchronous, for tests)

**Design refs:** §4.4. Never gives up. `retry_num` bounds a cycle, not a msg.

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_cleaner.py`:
```python
import time

from common.msging.clnt.cleaner import Cleaner
from common.msging.clnt.store import MsgStore
from common.msging.clnt.window import Window
from common.msging.codes import Code
from common.msging.models import Envelope, MsgRecord


def _rec(n: int, sent_at: str | None = None) -> MsgRecord:
    return MsgRecord(msg_id=n, created_at="2026-07-25T10:15:30Z", sent_at=sent_at,
                     envelope=Envelope(producer_id="p", msg_id=n, service_name="ingest",
                                       created_at="2026-07-25T10:15:30Z"))


def _cleaner(tmp_path, send_fn, **kw):
    store = MsgStore(tmp_path)
    opts = dict(sweep_interval_s=0.01, sent_timeout_s=300.0, retry_num=3)
    opts.update(kw)
    return store, Cleaner(store, Window(64), send_fn, **opts)


def test_pending_msgs_are_retried(tmp_path):
    sent = []
    store, c = _cleaner(tmp_path, lambda rec: (sent.append(rec.msg_id), Code.OK)[1])
    store.put_pending(_rec(1))
    assert c.sweep_once()["retried"] == 1
    assert sent == [1]


def test_stale_sent_is_demoted_to_pending(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=0.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    time.sleep(0.01)
    assert c.sweep_once()["demoted"] == 1
    assert store.list("pending") == [1]


def test_fresh_sent_is_left_alone(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert c.sweep_once()["demoted"] == 0
    assert store.list("sent") == [1]


def test_nacked_is_left_alone_pending_the_apps_decision(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.mark_nacked(1, "bad")
    c.sweep_once()
    assert store.list("nacked") == [1]


def test_a_msg_is_never_given_up_on(tmp_path):
    """retry_num bounds a cycle, not the msg — at-least-once depends on this."""
    attempts = []
    store, c = _cleaner(tmp_path, lambda rec: (attempts.append(1), Code.STORED_UNREACHABLE)[1],
                        retry_num=1)
    store.put_pending(_rec(1))
    for _ in range(5):
        c.sweep_once()
    assert len(attempts) >= 5
    assert store.list("pending") == [1]        # still there, still ours


def test_window_full_stops_the_sweep_sending(tmp_path):
    sent = []
    store = MsgStore(tmp_path)
    window = Window(1)
    window.acquire()                            # window is now full
    c = Cleaner(store, window, lambda rec: (sent.append(rec.msg_id), Code.OK)[1],
                sweep_interval_s=0.01, sent_timeout_s=300.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert sent == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_cleaner.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement cleaner.py**

`sweep_once()` runs one pass and returns the counts. `start()` runs it on a daemon thread named `msging-cleaner` every `sweep_interval_s`. Backoff is per-msg using `attempts` on the record. `sent_at` age drives demotion. Every demotion calls `window.release()`.

- [ ] **Step 4: Run the test**

Run: `venv/bin/python -m pytest tests/common/msging/test_cleaner.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging/clnt/cleaner.py tests/common/msging/test_cleaner.py
git commit -m "feat(msging): cleaner loop with demotion, retry, and no give-up rule"
```

---

### Task 17: Client API surface

**Files:**
- Create: `src/common/msging/clnt/api.py`
- Modify: `src/common/msging/__init__.py`
- Test: `tests/common/msging/test_api.py`

**Interfaces:**
- Produces the full §6.1 surface: `clnt_init`, `register`, `on_notify`, `set_info`, `send`, `resolve`, `flush`, `close`, `poll_notify`, plus `metrics` re-export.

**Design refs:** §6.1, §2.2, §2.4, §7 (info shallow merge), §8.3 (raises).

- [ ] **Step 1: Write the test**

Create `tests/common/msging/test_api.py`:
```python
import pytest

from common.msging import clnt
from common.msging.codes import Action, Code
from common.msging.errors import (
    InvalidEnvelopeError,
    NotRegisteredError,
    ProducerClosingError,
)


def test_send_before_register_raises(tmp_path, cfg_file):
    clnt.clnt_init(cfg_file)
    with pytest.raises(NotRegisteredError):
        clnt.send("nope", {"x": 1})


def test_dedup_without_event_id_raises(registered):
    pid = registered
    with pytest.raises(InvalidEnvelopeError):
        clnt.send(pid, {"x": 1}, dedup=True)


def test_send_after_close_raises(registered):
    pid = registered
    clnt.close(pid, timeout_s=0.1)
    with pytest.raises(ProducerClosingError):
        clnt.send(pid, {"x": 1})


def test_info_is_shallow_merged_per_msg_winning(registered, sent_envelopes):
    pid = registered
    clnt.set_info(pid, {"agent": "1.0", "host": "laptop"})
    clnt.send(pid, {"x": 1}, info={"agent": "2.0"})
    info = sent_envelopes[-1].info
    assert info == {"agent": "2.0", "host": "laptop"}


def test_send_returns_ok_on_202(registered):
    r = clnt.send(registered, {"x": 1})
    assert r.err_code == Code.OK and r.msg_id is not None


def test_send_returns_slot_full_and_does_not_store(registered_full_store):
    pid, store = registered_full_store
    before = store.total()
    r = clnt.send(pid, {"x": 1})
    assert r.err_code == Code.SLOT_FULL
    assert store.total() == before          # NOT stored — the one hard rejection


def test_too_large_is_checked_before_storing(registered_tiny_limit):
    pid, store = registered_tiny_limit
    r = clnt.send(pid, {"blob": "x" * 5000})
    assert r.err_code == Code.TOO_LARGE
    assert store.total() == 0


def test_resolve_purge_deletes_the_record(nacked):
    pid, msg_id, store = nacked
    clnt.resolve(pid, msg_id, Action.PURGE)
    assert store.get(msg_id) is None


def test_resolve_retain_returns_it_to_pending(nacked):
    pid, msg_id, store = nacked
    clnt.resolve(pid, msg_id, Action.RETAIN)
    assert msg_id in store.list("pending")


def test_resolve_defaults_to_retain(nacked):
    pid, msg_id, store = nacked
    clnt.resolve(pid, msg_id)
    assert msg_id in store.list("pending")


def test_close_reports_drained_when_empty(registered):
    result = clnt.close(registered, timeout_s=1.0)
    assert result.status == "drained" and result.unresolved == 0


def test_ack_callback_suppressed_by_default(registered_with_callback):
    pid, seen = registered_with_callback
    clnt._deliver_for_tests(pid, code="ack", msg_id=1)
    assert seen == []


def test_ack_callback_fires_when_opted_in(registered_with_ack_callback):
    pid, seen = registered_with_ack_callback
    clnt._deliver_for_tests(pid, code="ack", msg_id=1)
    assert [e.code for e in seen] == ["ack"]


def test_nack_callback_always_fires(registered_with_callback):
    pid, seen = registered_with_callback
    clnt._deliver_for_tests(pid, code="nack", msg_id=1, reason="bad")
    assert [e.code for e in seen] == ["nack"]
```

Write the fixtures (`cfg_file`, `registered`, `sent_envelopes`, `registered_full_store`, `registered_tiny_limit`, `nacked`, `registered_with_callback`, `registered_with_ack_callback`) in `tests/common/msging/conftest.py`. Each builds a client against a `MockTransport` that returns `202`, so no live server is needed at this layer.

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/common/msging/test_api.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement clnt/api.py and update the package `__init__`**

`__init__.py` exposes `clnt` and `srv` as lazy submodule attributes so importing `common.msging` does not drag FastAPI into a collector process.

- [ ] **Step 4: Run the whole suite**

Run: `venv/bin/python -m pytest tests/common/msging/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/common/msging tests/common/msging
git commit -m "feat(msging): client API surface with resolve, info merge, and close"
```

---

## Phase 5 — Integration, failure paths, examples

### Task 18: End-to-end integration

**Files:**
- Create: `tests/common/msging/test_integration.py`

Real client threads against a real ASGI server over `httpx.ASGITransport`. No mocks for the wire.

- [ ] **Step 1: Write the tests**

Cover: register → send → app handler runs → `send_notice` → SSE delivers → record purged; `notify_on_ack=True` sees the ACK; NACK → callback fires → `resolve(PURGE)` → record gone; `close()` returns `drained` after outcomes land.

- [ ] **Step 2: Run**

Run: `venv/bin/python -m pytest tests/common/msging/test_integration.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/common/msging/test_integration.py
git commit -m "test(msging): end-to-end client-server integration over a real ASGI app"
```

---

### Task 19: Failure paths

**Files:**
- Create: `tests/common/msging/test_failure_paths.py`

**These are the tests that justify the design.** Each maps to a decision:

- [ ] **Step 1: Write each test**

1. **Restart reconciliation (D19, §2.3)** — write records in `pending/` and `sent/`, construct a fresh client on the same directory, assert both resend.
2. **Epoch sweep (D16, §4.3)** — connect, note epoch; restart the hub so the epoch changes; assert all of `sent/` returned to `pending/` and `epoch_swept_msgs_total` incremented.
3. **`sent_timeout_s` backstop (D17, §4.4)** — a msg in `sent/` with no outcome and `sent_timeout_s=0` is demoted.
4. **`slot_full` (D22)** — fill `max_pending`; assert `send()` returns `slot_full` after roughly `slot_wait_s` and stores nothing.
5. **`server_busy` (D42)** — `queue_max=0`; assert `503`, record stays `PENDING`, cleaner retries.
6. **NACK PURGE and RETAIN (D38)** — both dispositions, asserting durable state after each.
7. **Crash mid-write** — a stray `*.tmp` in `pending/` is ignored by `list()` and never loaded as a record.

- [ ] **Step 2: Run**

Run: `venv/bin/python -m pytest tests/common/msging/test_failure_paths.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/common/msging/test_failure_paths.py
git commit -m "test(msging): failure paths — reconciliation, epoch sweep, timeouts, backpressure"
```

---

### Task 20: Examples

**Files:**
- Create: `src/common/msging/examples/srv_hosted.py`, `srv_make_app.py`, `srv_two.py`, `clnt_full.py`

Closes tracker task #2. Each must be runnable, not a snippet. `clnt_full.py` exercises register → set_info → on_notify → send → resolve → close, and prints `metrics()` at exit.

- [ ] **Step 1: Write the four examples**
- [ ] **Step 2: Run each one against a live local server, confirm output**
- [ ] **Step 3: Commit**

```bash
git add src/common/msging/examples
git commit -m "docs(msging): runnable server and client examples"
```

---

### Task 21: README reconciliation and test report

Closes tracker tasks #3, #4, #5, #6.

- [ ] **Step 1: Generalize `utils/gen_test_report.py`** — accept `--title` and module-agnostic env vars, keeping the storage invocation working.
- [ ] **Step 2: Write `utils/gen_schema_blocks.py`** — regenerate the JSON blocks in design §7 and the README from `schema/*.json`.
- [ ] **Step 3: Walk the README against the code** — every signature, every code, every config key, and run the quick example verbatim. Remove the "designed, not yet implemented" banner.
- [ ] **Step 4: Run the full suite and generate the report**

```bash
venv/bin/python -m pytest tests/ -v
mkdir -p docs/superpowers/reports
venv/bin/python -m pytest tests/common/msging/ --junitxml=docs/superpowers/reports/msging-junit.xml
venv/bin/python utils/gen_test_report.py --title "Event Messaging (msging)" \
    --junit docs/superpowers/reports/msging-junit.xml \
    --out docs/superpowers/reports/msging-test-report.md
```

- [ ] **Step 5: Commit**

```bash
git add utils/ src/common/msging/README.md docs/design/event-messaging-design.md docs/superpowers/reports/
git commit -m "docs(msging): reconcile README with implementation, generate test report"
```

---

## Self-Review

**Spec coverage.** Design §1 → T2/T14; §2.1–2.2 → T7/T17; §2.3 → T19; §2.4 → T17/T18; §3 → T7/T8; §3.1 → T6; §4.1 → T15; §4.2 → T12/T15; §4.3 → T12/T19; §4.4 → T16; §5.1 → T4; §5.2 → T4/T9; §6.1 → T17; §6.2 → T11/T13; §6.3 → T13; §6.4 → T1 (boundary test) /T15/T16; §7 → T2; §8.1 → T3/T14; §8.2 → T2/T12; §8.3 → T3/T17; §10.1 → T4/T13; §10.2 → T2; §10.3 → not code, gh #20; §10.4 → T5; §10.5 → T5.

**Deliberately not covered:** §9.1, §9.2, §9.3 (future), `cli_producers.py` (gh #19), metrics exposure (§10.5, out of scope).

**Known thin spots, flagged rather than hidden:**
- Tasks 18–21 specify behaviour and commands but not every line of test code. They are integration and documentation tasks whose content depends on the exact signatures Tasks 1–17 produce. The implementer should write them against the code that exists, not against a guess made now.
- `conftest.py` fixtures for Task 17 are named and described but not written out; they depend on the `clnt/api.py` shape from the same task.
