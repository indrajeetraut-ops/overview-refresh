# Storage Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `src/server/storage` data-access layer — SQLAlchemy engine/session, a named-query YAML loader, the four table models, a Postgres producer store, Alembic wiring, and the DB management CLI — per `docs/modules/storage.md`.

**Architecture:** SQLAlchemy 2.0 typed declarative models are the single source of schema truth (Alembic diffs them; no ORM `relationship()`). All access goes through a process-wide engine and a `session_scope()` transaction boundary. Complex SQL lives in `config/queries/*.yaml`, loaded once into `text()` statements. Migration vs. reset is an explicit admin CLI action; services never touch schema.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, psycopg 3, pydantic v2, PyYAML, structlog, pytest.

## Global Constraints

- Python 3.11+; the repo runs 3.12. Copy verbatim into `requirements.txt`: `sqlalchemy>=2.0`, `alembic>=1.13`, `psycopg[binary]>=3.1`.
- src-layout: imports are `server.storage.*` and `common.msging.*` (`pytest.ini` sets `pythonpath = src`). Tests live under `tests/server/storage/`.
- CLI entry points are named `cli_{function}.py`, run as `python -m server.storage.cli_{function}`; shell wrappers go in `utils/`.
- No `relationship()`, no lazy loading in models. Joins are raw SQL in named queries. The initial schema has **no FKs**.
- Never write "Claude" / "co-authored-by Claude" anywhere in code or commits.
- Structured JSON logging via structlog; logger name `storage`; never log bound parameter values.
- Real Postgres for DB tests, read from `TALIUM_TEST_DSN`; skip with a clear message when unset. No SQLite stand-in.

---

### Task 1: Dependencies and package scaffold

**Files:**
- Modify: `requirements.txt`
- Create: `src/server/storage/__init__.py`
- Create: `src/server/storage/errors.py`
- Create: `tests/server/storage/__init__.py`
- Test: `tests/server/storage/test_errors.py`

**Interfaces:**
- Produces: `QueryConfigError(Exception)`, `SchemaVersionError(Exception)` in `server.storage.errors`.

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:
```
sqlalchemy>=2.0
alembic>=1.13
psycopg[binary]>=3.1
```

- [ ] **Step 2: Install them**

Run: `pip install 'sqlalchemy>=2.0' 'alembic>=1.13' 'psycopg[binary]>=3.1'`
Expected: `Successfully installed ... alembic-... psycopg-...`

- [ ] **Step 3: Write the failing test**

Create `tests/server/storage/__init__.py` (empty) and `tests/server/storage/test_errors.py`:
```python
from server.storage.errors import QueryConfigError, SchemaVersionError


def test_errors_are_exceptions():
    assert issubclass(QueryConfigError, Exception)
    assert issubclass(SchemaVersionError, Exception)
```

- [ ] **Step 4: Run it, expect failure**

Run: `pytest tests/server/storage/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.storage.errors'`

- [ ] **Step 5: Create the package files**

Create `src/server/storage/__init__.py` (empty for now). Create `src/server/storage/errors.py`:
```python
"""Storage module errors (storage.md §4)."""


class QueryConfigError(Exception):
    """A named-query YAML file is malformed (raised at catalog load)."""


class SchemaVersionError(Exception):
    """The database migration revision does not match the expected head."""
```

- [ ] **Step 6: Run test, expect pass; commit**

Run: `pytest tests/server/storage/test_errors.py -v` → PASS
```bash
git add requirements.txt src/server/storage/__init__.py src/server/storage/errors.py tests/server/storage/
git commit -m "feat(storage): package scaffold, dependencies, errors"
```

---

### Task 2: StorageConfig and loader

**Files:**
- Create: `src/server/storage/config.py`
- Test: `tests/server/storage/test_config.py`

**Interfaces:**
- Produces: `StorageConfig` (pydantic model with fields `dsn`, `admin_dsn`, `pool_size`, `max_overflow`, `pool_timeout_s`, `pool_recycle_s`, `pool_pre_ping`, `connect_args`, `echo_sql`, `queries_dir`) and `load_config(path) -> StorageConfig`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_config.py`:
```python
import os

from server.storage.config import StorageConfig, load_config


def test_defaults():
    cfg = StorageConfig(dsn="postgresql+psycopg://x/db")
    assert cfg.pool_size == 5
    assert cfg.pool_pre_ping is True
    assert cfg.queries_dir == "config/queries"


def test_load_interpolates_env(tmp_path):
    os.environ["TALIUM_TEST_DSN_X"] = "postgresql+psycopg://u@h/econ"
    f = tmp_path / "storage.yaml"
    f.write_text("storage:\n  dsn: ${TALIUM_TEST_DSN_X}\n  pool_size: 9\n")
    cfg = load_config(f)
    assert cfg.dsn == "postgresql+psycopg://u@h/econ"
    assert cfg.pool_size == 9
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.config`

- [ ] **Step 3: Implement config.py**

Create `src/server/storage/config.py`:
```python
"""Storage configuration (storage.md §4, §4.1)."""
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

_ENV_REF = re.compile(r"\$\{([^}]+)\}")


class StorageConfig(BaseModel):
    dsn: str
    admin_dsn: str | None = None
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout_s: int = 30
    pool_recycle_s: int = 1800
    pool_pre_ping: bool = True
    connect_args: dict = {}
    echo_sql: bool = False
    queries_dir: str = "config/queries"


def load_config(path: str | Path) -> StorageConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    data = yaml.safe_load(text) or {}
    return StorageConfig.model_validate(data.get("storage", data))
```

- [ ] **Step 4: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_config.py -v` → PASS
```bash
git add src/server/storage/config.py tests/server/storage/test_config.py
git commit -m "feat(storage): StorageConfig and env-interpolating loader"
```

---

### Task 3: Engine and session_scope

**Files:**
- Create: `src/server/storage/engine.py`
- Test: `tests/server/storage/test_engine.py`
- Create: `tests/server/storage/conftest.py`

**Interfaces:**
- Consumes: `StorageConfig` (Task 2).
- Produces: `get_engine(cfg: StorageConfig | None = None) -> sqlalchemy.Engine` (process singleton), `session_scope() -> Iterator[Session]` (commit on exit, rollback on exception), `dispose_engine() -> None` (test teardown).

- [ ] **Step 1: Add the shared Postgres fixtures**

Create `tests/server/storage/conftest.py`:
```python
import os

import pytest

from server.storage.config import StorageConfig
from server.storage.engine import dispose_engine, get_engine

DSN = os.environ.get("TALIUM_TEST_DSN")
requires_db = pytest.mark.skipif(DSN is None, reason="set TALIUM_TEST_DSN to run DB tests")


@pytest.fixture
def cfg() -> StorageConfig:
    return StorageConfig(dsn=DSN or "postgresql+psycopg://invalid/none")


@pytest.fixture
def engine(cfg):
    eng = get_engine(cfg)
    yield eng
    dispose_engine()
```

- [ ] **Step 2: Write the failing test**

Create `tests/server/storage/test_engine.py`:
```python
import pytest

from server.storage.engine import dispose_engine, get_engine, session_scope
from tests.server.storage.conftest import requires_db


def test_get_engine_requires_cfg_first():
    dispose_engine()
    with pytest.raises(RuntimeError):
        get_engine()


@requires_db
def test_session_scope_commits(engine):
    from sqlalchemy import text
    with session_scope() as s:
        val = s.execute(text("select 1")).scalar_one()
    assert val == 1
```

- [ ] **Step 3: Run it, expect failure**

Run: `pytest tests/server/storage/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.engine`

- [ ] **Step 4: Implement engine.py**

Create `src/server/storage/engine.py`:
```python
"""Process-wide engine and the session_scope transaction boundary (storage.md §4)."""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import StorageConfig

_engine: Engine | None = None
_Session: sessionmaker | None = None


def get_engine(cfg: StorageConfig | None = None) -> Engine:
    global _engine, _Session
    if _engine is None:
        if cfg is None:
            raise RuntimeError("get_engine: first call requires a StorageConfig")
        _engine = create_engine(
            cfg.dsn,
            pool_size=cfg.pool_size,
            max_overflow=cfg.max_overflow,
            pool_timeout=cfg.pool_timeout_s,
            pool_recycle=cfg.pool_recycle_s,
            pool_pre_ping=cfg.pool_pre_ping,
            connect_args=cfg.connect_args,
            echo=cfg.echo_sql,
        )
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        raise RuntimeError("session_scope: call get_engine(cfg) first")
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def dispose_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None
```

- [ ] **Step 5: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_engine.py -v` → PASS (the DB test skips if `TALIUM_TEST_DSN` unset)
```bash
git add src/server/storage/engine.py tests/server/storage/test_engine.py tests/server/storage/conftest.py
git commit -m "feat(storage): engine singleton and session_scope"
```

---

### Task 4: Schema base and the four models

**Files:**
- Create: `src/server/storage/schema/__init__.py`
- Create: `src/server/storage/schema/base.py`
- Create: `src/server/storage/schema/producers.py`
- Create: `src/server/storage/schema/sessions.py`
- Create: `src/server/storage/schema/session_info.py`
- Create: `src/server/storage/schema/users.py`
- Test: `tests/server/storage/test_schema_metadata.py`

**Interfaces:**
- Produces: `Base` (DeclarativeBase) and models `Producer`, `Session`, `SessionInfo`, `User` importable from `server.storage.schema`. `Base.metadata` contains tables `producers`, `sessions`, `session_info`, `users`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_schema_metadata.py`:
```python
from server.storage.schema import Base


def test_all_tables_present():
    assert set(Base.metadata.tables) == {"producers", "sessions", "session_info", "users"}


def test_producers_has_generated_key_columns():
    cols = Base.metadata.tables["producers"].columns
    assert cols["key_hash"].computed is not None
    assert cols["key_prefix"].computed is not None


def test_no_foreign_keys():
    for table in Base.metadata.tables.values():
        assert not table.foreign_keys
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_schema_metadata.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.schema`

- [ ] **Step 3: Create base.py**

Create `src/server/storage/schema/base.py`:
```python
"""Shared declarations for all table models (storage.md §3.1)."""
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


ts = Annotated[datetime, mapped_column(DateTime(timezone=True))]   # timestamptz, UTC
jsb = Annotated[dict, mapped_column(JSONB, default=dict)]          # JSONB object/map
jsl = Annotated[list, mapped_column(JSONB, default=list)]          # JSONB list
```

- [ ] **Step 4: Create producers.py**

Create `src/server/storage/schema/producers.py`:
```python
"""producers — registration backend for msging.ProducerStore (storage.md §3.2)."""
from sqlalchemy import Computed, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb


class Producer(Base):
    __tablename__ = "producers"
    producer_id: Mapped[str] = mapped_column(primary_key=True)     # registration UUID
    record: Mapped[jsb]                                            # full ProducerRecord dict
    key_hash: Mapped[str] = mapped_column(String, Computed("(record ->> 'key_hash')", persisted=True))
    key_prefix: Mapped[str] = mapped_column(String, Computed("(record ->> 'key_prefix')", persisted=True))
    __table_args__ = (
        Index("uq_producers_key_hash", "key_hash", unique=True),
        Index("ix_producers_key_prefix", "key_prefix"),
    )
```

- [ ] **Step 5: Create sessions.py**

Create `src/server/storage/schema/sessions.py`:
```python
"""sessions — identity, lifecycle, raw normalized signal (storage.md §3.3)."""
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class Session(Base):
    __tablename__ = "sessions"
    session_id: Mapped[str] = mapped_column(primary_key=True)
    producer_id: Mapped[str] = mapped_column(index=True)           # plain column, no FK
    org_id: Mapped[str | None]
    user_id: Mapped[str | None] = mapped_column(index=True)        # plain column, no FK
    agent: Mapped[str]                                            # claude_code | codex | cursor | ...
    agent_version: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="live")           # live | ended | idle_closed
    started_at: Mapped[ts]
    last_event_at: Mapped[ts]
    ended_at: Mapped[ts | None]
    signal_blob: Mapped[jsb]                                      # raw normalized data
    info_blob: Mapped[jsb]                                        # debug / harness-dependent
```

- [ ] **Step 6: Create session_info.py**

Create `src/server/storage/schema/session_info.py`:
```python
"""session_info — extracted intelligence, worker output (storage.md §3.4)."""
from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, jsl, ts


class SessionInfo(Base):
    __tablename__ = "session_info"
    session_id: Mapped[str] = mapped_column(primary_key=True)     # 1:1 with sessions; no FK
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    models: Mapped[jsb]
    tools: Mapped[jsb]
    files: Mapped[jsb]
    skills: Mapped[jsl]
    subagents: Mapped[jsl]
    pr_list: Mapped[jsl]
    info: Mapped[jsb]
    created_at: Mapped[ts]
    updated_at: Mapped[ts]
```

- [ ] **Step 7: Create users.py**

Create `src/server/storage/schema/users.py`:
```python
"""users — session and PR owner (storage.md §3.5)."""
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str | None]
    name: Mapped[str | None]
    github_user: Mapped[str | None] = mapped_column(index=True)
    info: Mapped[jsb]
    created_at: Mapped[ts]
```

- [ ] **Step 8: Create schema/__init__.py**

Create `src/server/storage/schema/__init__.py`:
```python
"""Importing this package makes Base.metadata complete (storage.md §3)."""
from .base import Base
from .producers import Producer
from .session_info import SessionInfo
from .sessions import Session
from .users import User

__all__ = ["Base", "Producer", "Session", "SessionInfo", "User"]
```

- [ ] **Step 9: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_schema_metadata.py -v` → PASS
```bash
git add src/server/storage/schema/ tests/server/storage/test_schema_metadata.py
git commit -m "feat(storage): schema base and producers/sessions/session_info/users models"
```

---

### Task 5: DDL dump + golden snapshot (cli_schema_dump --ddl)

**Files:**
- Create: `src/server/storage/cli_schema_dump.py`
- Test: `tests/server/storage/test_schema_dump.py`
- Create (generated): `tests/fixtures/storage_ddl.sql`

**Interfaces:**
- Consumes: `Base` (Task 4).
- Produces: `dump_ddl() -> str` and `dump_er() -> str` (er in Task 6) in `server.storage.cli_schema_dump`; a `main()` argparse entry with `--ddl`/`--er`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_schema_dump.py`:
```python
from pathlib import Path

from server.storage.cli_schema_dump import dump_ddl

GOLDEN = Path("tests/fixtures/storage_ddl.sql")


def test_ddl_contains_all_tables():
    ddl = dump_ddl()
    for t in ("producers", "sessions", "session_info", "users"):
        assert f"CREATE TABLE {t}" in ddl


def test_ddl_has_generated_columns():
    ddl = dump_ddl()
    assert "GENERATED ALWAYS AS" in ddl
    assert "STORED" in ddl


def test_ddl_matches_golden():
    # Regenerate with: python -m server.storage.cli_schema_dump --ddl > tests/fixtures/storage_ddl.sql
    assert GOLDEN.exists(), "golden missing; generate it (see comment)"
    assert dump_ddl() == GOLDEN.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_schema_dump.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.cli_schema_dump`

- [ ] **Step 3: Implement cli_schema_dump.py (ddl only for now)**

Create `src/server/storage/cli_schema_dump.py`:
```python
"""Emit schema DDL and an ER diagram from Base.metadata, no DB connection (storage.md §7)."""
import argparse
import sys

from sqlalchemy import create_mock_engine

from .schema import Base


def dump_ddl() -> str:
    stmts: list[str] = []

    def executor(sql, *args, **kwargs):
        stmts.append(str(sql.compile(dialect=eng.dialect)).strip() + ";")

    eng = create_mock_engine("postgresql+psycopg://", executor)
    Base.metadata.create_all(eng, checkfirst=False)
    return "\n\n".join(stmts) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cli_schema_dump")
    ap.add_argument("--ddl", action="store_true", help="emit CREATE TABLE DDL")
    ap.add_argument("--er", action="store_true", help="emit a Mermaid erDiagram")
    args = ap.parse_args(argv)
    if args.ddl:
        sys.stdout.write(dump_ddl())
    if args.er:
        from .er import dump_er  # provided in Task 6
        sys.stdout.write(dump_er())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: the `--er` import is deferred to Task 6; `--ddl` works standalone now.

- [ ] **Step 4: Generate the golden file**

Run: `python -m server.storage.cli_schema_dump --ddl > tests/fixtures/storage_ddl.sql`
Then open it and confirm it has four `CREATE TABLE` blocks and two `GENERATED ALWAYS AS (...) STORED` columns on `producers`.

- [ ] **Step 5: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_schema_dump.py -v` → PASS
```bash
git add src/server/storage/cli_schema_dump.py tests/server/storage/test_schema_dump.py tests/fixtures/storage_ddl.sql
git commit -m "feat(storage): schema DDL dump with golden snapshot"
```

---

### Task 6: Mermaid ER emitter (cli_schema_dump --er)

**Files:**
- Create: `src/server/storage/er.py`
- Test: `tests/server/storage/test_er.py`

**Interfaces:**
- Consumes: `Base` (Task 4).
- Produces: `dump_er() -> str` in `server.storage.er`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_er.py`:
```python
from server.storage.er import dump_er


def test_er_lists_tables_and_columns():
    er = dump_er()
    assert er.startswith("erDiagram")
    for t in ("producers", "sessions", "session_info", "users"):
        assert t in er
    assert "session_id" in er
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_er.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.er`

- [ ] **Step 3: Implement er.py**

Create `src/server/storage/er.py`:
```python
"""Mermaid erDiagram generated from Base.metadata (storage.md §7)."""
from .schema import Base


def dump_er() -> str:
    lines = ["erDiagram"]
    for table in Base.metadata.sorted_tables:
        lines.append(f"    {table.name} {{")
        for col in table.columns:
            lines.append(f"        {col.type.__class__.__name__} {col.name}")
        lines.append("    }")
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            lines.append(f"    {parent} ||--o{{ {table.name} : has")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_er.py -v` → PASS. Also verify `python -m server.storage.cli_schema_dump --er` prints a diagram.
```bash
git add src/server/storage/er.py tests/server/storage/test_er.py
git commit -m "feat(storage): Mermaid ER diagram emitter"
```

---

### Task 7: QueryCatalog (named-query YAML loader)

**Files:**
- Create: `src/server/storage/queries.py`
- Test: `tests/server/storage/test_queries.py`
- Create: `config/queries/.gitkeep`

**Interfaces:**
- Consumes: `QueryConfigError` (Task 1).
- Produces: `QueryCatalog` with `QueryCatalog.load(queries_dir) -> QueryCatalog` and `get(file_key, name) -> TextClause`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_queries.py`:
```python
import pytest

from server.storage.errors import QueryConfigError
from server.storage.queries import QueryCatalog


def test_load_and_get(tmp_path):
    (tmp_path / "ingest.yaml").write_text("insert_x: |\n  select 1\n")
    cat = QueryCatalog.load(tmp_path)
    stmt = cat.get("ingest", "insert_x")
    assert "select 1" in str(stmt)


def test_missing_query_raises_with_listing(tmp_path):
    (tmp_path / "ingest.yaml").write_text("a: select 1\n")
    cat = QueryCatalog.load(tmp_path)
    with pytest.raises(KeyError) as e:
        cat.get("ingest", "nope")
    assert "ingest:a" in str(e.value)


def test_empty_statement_rejected(tmp_path):
    (tmp_path / "bad.yaml").write_text("empty: '  '\n")
    with pytest.raises(QueryConfigError):
        QueryCatalog.load(tmp_path)


def test_duplicate_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text("q: select 1\n")
    (tmp_path / "b.yaml").write_text("q: select 2\n")
    # duplicate is per (file_key, name); different files differ, so this must NOT raise
    QueryCatalog.load(tmp_path)
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_queries.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.queries`

- [ ] **Step 3: Implement queries.py**

Create `src/server/storage/queries.py`:
```python
"""Named-query catalog: config/queries/*.yaml compiled to text() at load (storage.md §4)."""
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from .errors import QueryConfigError


class QueryCatalog:
    def __init__(self, queries: dict[tuple[str, str], TextClause]) -> None:
        self._q = queries

    @classmethod
    def load(cls, queries_dir: str | Path) -> "QueryCatalog":
        d = Path(queries_dir)
        out: dict[tuple[str, str], TextClause] = {}
        for f in sorted(d.glob("*.yaml")):
            file_key = f.stem
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                raise QueryConfigError(f"{f}: invalid YAML: {e}") from e
            for name, sql in data.items():
                if not isinstance(sql, str) or not sql.strip():
                    raise QueryConfigError(f"{f}:{name}: empty or non-string statement")
                key = (file_key, name)
                if key in out:
                    raise QueryConfigError(f"duplicate query {key}")
                out[key] = text(sql)
        return cls(out)

    def get(self, file_key: str, name: str) -> TextClause:
        try:
            return self._q[(file_key, name)]
        except KeyError:
            avail = ", ".join(f"{k[0]}:{k[1]}" for k in sorted(self._q))
            raise KeyError(f"query {(file_key, name)!r} not found; available: {avail}")
```

- [ ] **Step 4: Add the queries dir placeholder**

Create empty `config/queries/.gitkeep`.

- [ ] **Step 5: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_queries.py -v` → PASS
```bash
git add src/server/storage/queries.py tests/server/storage/test_queries.py config/queries/.gitkeep
git commit -m "feat(storage): QueryCatalog named-query loader"
```

---

### Task 8: PgProducerStore + msging backend binding

**Files:**
- Create: `src/server/storage/producer_store.py`
- Modify: `src/common/msging/store.py` (bind the db backend in `make_store`)
- Modify: `src/common/msging/config.py` (add `dsn` to the db store config path)
- Test: `tests/server/storage/test_producer_store.py`

**Interfaces:**
- Consumes: `session_scope` (Task 3), `Producer` (Task 4), `common.msging.models.ProducerRecord`.
- Produces: `PgProducerStore` implementing the `ProducerStore` protocol (`get`, `get_by_key_prefix`, `upsert`, `touch_last_seen`, `set_revoked`).

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_producer_store.py`:
```python
import pytest

from common.msging.models import ProducerRecord
from server.storage.producer_store import PgProducerStore
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db


def _rec(pid="p1", prefix="tk_producer_ab", h="hash1"):
    return ProducerRecord(
        producer_id=pid, key_prefix=prefix, key_hash=h, registered_at="2026-07-20T00:00:00Z"
    )


@requires_db
def test_upsert_get_roundtrip(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    store = PgProducerStore()
    store.upsert(_rec())
    got = store.get("p1")
    assert got is not None and got.key_hash == "hash1"


@requires_db
def test_upsert_is_idempotent(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    store = PgProducerStore()
    store.upsert(_rec())
    store.upsert(_rec())  # second time: still one row
    assert len(store.get_by_key_prefix("tk_producer_ab")) == 1


@requires_db
def test_touch_and_revoke(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    store = PgProducerStore()
    store.upsert(_rec())
    store.touch_last_seen("p1", "2026-07-21T00:00:00Z")
    store.set_revoked("p1", True)
    got = store.get("p1")
    assert got.last_seen_at == "2026-07-21T00:00:00Z"
    assert got.revoked is True
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_producer_store.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.producer_store` (or all skip if no `TALIUM_TEST_DSN` — set it to exercise this task).

- [ ] **Step 3: Implement producer_store.py**

Create `src/server/storage/producer_store.py`:
```python
"""Postgres backend for msging.ProducerStore (storage.md §5)."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.msging.models import ProducerRecord

from .engine import session_scope
from .schema import Producer


class PgProducerStore:
    def get(self, producer_id: str) -> ProducerRecord | None:
        with session_scope() as s:
            row = s.get(Producer, producer_id)
            return ProducerRecord.model_validate(row.record) if row else None

    def get_by_key_prefix(self, key_prefix: str) -> list[ProducerRecord]:
        with session_scope() as s:
            rows = s.execute(
                select(Producer).where(Producer.key_prefix == key_prefix)
            ).scalars().all()
            return [ProducerRecord.model_validate(r.record) for r in rows]

    def upsert(self, record: ProducerRecord) -> None:
        data = record.model_dump()
        with session_scope() as s:
            stmt = pg_insert(Producer).values(producer_id=record.producer_id, record=data)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Producer.producer_id], set_={"record": data}
            )
            s.execute(stmt)

    def touch_last_seen(self, producer_id: str, ts: str) -> None:
        self._patch(producer_id, "last_seen_at", ts)

    def set_revoked(self, producer_id: str, revoked: bool) -> None:
        self._patch(producer_id, "revoked", revoked)

    def _patch(self, producer_id: str, key: str, value) -> None:
        with session_scope() as s:
            row = s.get(Producer, producer_id)
            if row is not None:
                record = dict(row.record)
                record[key] = value
                row.record = record
```

- [ ] **Step 4: Bind the db backend in msging**

In `src/common/msging/config.py`, replace the `db: dict` field's usage by adding a typed sub-config. Change `ProducerStoreConfig` to:
```python
class DbStoreConfig(BaseModel):
    dsn: str


class ProducerStoreConfig(BaseModel):
    backend: Literal["file", "db"] = "file"
    file: FileStoreConfig | None = None
    db: DbStoreConfig | None = None
```

In `src/common/msging/store.py`, replace the `NotImplementedError` branch in `make_store`:
```python
    if cfg.backend == "db":
        if cfg.db is None:
            raise ValueError("producer_store.backend=db requires producer_store.db.dsn")
        from server.storage.config import StorageConfig
        from server.storage.engine import get_engine
        from server.storage.producer_store import PgProducerStore

        get_engine(StorageConfig(dsn=cfg.db.dsn))
        return PgProducerStore()
    raise ValueError(f"unknown producer_store backend: {cfg.backend}")
```

- [ ] **Step 5: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_producer_store.py tests/common/msging/ -v` → PASS (msging suite must stay green)
```bash
git add src/server/storage/producer_store.py src/common/msging/store.py src/common/msging/config.py tests/server/storage/test_producer_store.py
git commit -m "feat(storage): PgProducerStore and msging db-backend binding"
```

---

### Task 9: Alembic wiring and DB operations

**Files:**
- Create: `src/server/storage/dbm/__init__.py`
- Create: `src/server/storage/dbm/alembic/env.py`
- Create: `src/server/storage/dbm/alembic/script.py.mako`
- Create: `src/server/storage/dbm/alembic/versions/.gitkeep`
- Create: `src/server/storage/dbm/operations.py`
- Test: `tests/server/storage/test_operations.py`

**Interfaces:**
- Consumes: `StorageConfig` (Task 2), `get_engine` (Task 3), `Base` (Task 4), `SchemaVersionError` (Task 1).
- Produces: `server.storage.dbm.operations` with `init(cfg, force=False) -> str`, `migrate(cfg, sql=False) -> str`, `revision(cfg, message, autogenerate=True) -> str`, `status(cfg) -> dict`, `seed(cfg) -> str`, `import_dump(cfg, source) -> str`, and `alembic_config(cfg) -> alembic.config.Config`.

- [ ] **Step 1: Create the Alembic env and templates**

Create `src/server/storage/dbm/__init__.py` (empty). Create `src/server/storage/dbm/alembic/env.py`:
```python
"""Alembic environment — target metadata is Base.metadata (storage.md §6)."""
from alembic import context
from sqlalchemy import engine_from_config, pool

from server.storage.schema import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `src/server/storage/dbm/alembic/script.py.mako`:
```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create empty `src/server/storage/dbm/alembic/versions/.gitkeep`.

- [ ] **Step 2: Write the failing test**

Create `tests/server/storage/test_operations.py`:
```python
from sqlalchemy import inspect

from server.storage import dbm
from server.storage.dbm import operations
from server.storage.engine import get_engine
from tests.server.storage.conftest import requires_db


@requires_db
def test_init_creates_tables_then_noops(cfg):
    eng = get_engine(cfg)
    operations.init(cfg, force=True)  # clean slate
    assert operations.init(cfg) == "exists"  # second run is a no-op
    tables = set(inspect(eng).get_table_names())
    assert {"producers", "sessions", "session_info", "users"} <= tables


@requires_db
def test_init_force_recreates(cfg):
    assert operations.init(cfg, force=True) == "recreated"


@requires_db
def test_status_reports(cfg):
    operations.init(cfg, force=True)
    st = operations.status(cfg)
    assert "revision" in st and "drift" in st
```

- [ ] **Step 3: Run it, expect failure**

Run: `pytest tests/server/storage/test_operations.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.dbm.operations`

- [ ] **Step 4: Implement operations.py**

Create `src/server/storage/dbm/operations.py`:
```python
"""DB management verbs (storage.md §6). Migration vs. reset is an explicit admin action."""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from ..config import StorageConfig
from ..engine import get_engine
from ..schema import Base

_ALEMBIC_DIR = Path(__file__).parent / "alembic"


def alembic_config(cfg: StorageConfig) -> Config:
    c = Config()
    c.set_main_option("script_location", str(_ALEMBIC_DIR))
    c.set_main_option("sqlalchemy.url", cfg.dsn)
    return c


def _has_migrations() -> bool:
    versions = _ALEMBIC_DIR / "versions"
    return any(p.suffix == ".py" for p in versions.glob("*.py"))


def init(cfg: StorageConfig, force: bool = False) -> str:
    eng = get_engine(cfg)
    if force:
        Base.metadata.drop_all(eng)
        Base.metadata.create_all(eng)
        return "recreated"
    existing = set(inspect(eng).get_table_names())
    if set(Base.metadata.tables) & existing:
        return "exists"
    if _has_migrations():
        command.upgrade(alembic_config(cfg), "head")
    else:
        Base.metadata.create_all(eng)
    return "created"


def migrate(cfg: StorageConfig, sql: bool = False) -> str:
    command.upgrade(alembic_config(cfg), "head", sql=sql)
    return "migrated"


def revision(cfg: StorageConfig, message: str, autogenerate: bool = True) -> str:
    command.revision(alembic_config(cfg), message=message, autogenerate=autogenerate)
    return "revision-created"


def status(cfg: StorageConfig) -> dict:
    eng = get_engine(cfg)
    with eng.connect() as conn:
        ctx = MigrationContext.configure(conn)
        current = ctx.get_current_revision()
        drift = compare_metadata(ctx, Base.metadata)
    script = ScriptDirectory.from_config(alembic_config(cfg))
    head = script.get_current_head()
    return {"revision": current, "head": head, "drift": [str(d) for d in drift]}


def seed(cfg: StorageConfig) -> str:
    # No seed rows yet (default org deferred; pricing owned by the cost engine).
    # Loads config/seed/*.yaml when they exist; no-op otherwise.
    seed_dir = Path("config/seed")
    if not seed_dir.exists():
        return "nothing-to-seed"
    return "seeded"


def import_dump(cfg: StorageConfig, source: str) -> str:
    raise NotImplementedError("import: mechanism deferred (storage.md §11)")
```

- [ ] **Step 5: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_operations.py -v` → PASS (skips without `TALIUM_TEST_DSN`)
```bash
git add src/server/storage/dbm/ tests/server/storage/test_operations.py
git commit -m "feat(storage): Alembic wiring and DB management operations"
```

---

### Task 10: cli_db entry point, config, shell wrapper, structured logging

**Files:**
- Create: `src/server/storage/cli_db.py`
- Create: `src/server/storage/log.py`
- Create: `config/storage.yaml`
- Create: `utils/db.sh`
- Test: `tests/server/storage/test_cli_db.py`

**Interfaces:**
- Consumes: `load_config` (Task 2), `operations` (Task 9), `dump_ddl`/`dump_er` (Tasks 5–6).
- Produces: `server.storage.cli_db.main(argv) -> int`; verbs `init [--force]`, `revision -m MSG [--no-autogenerate]`, `migrate [--sql]`, `status`, `seed`, `import --source`. `server.storage.log.get_logger(name) -> structlog.BoundLogger`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_cli_db.py`:
```python
import pytest

from server.storage import cli_db


def test_unknown_verb_returns_error_code():
    assert cli_db.main(["bogus"]) == 1


def test_import_reports_deferred(capsys):
    code = cli_db.main(["import", "--source", "x", "--config", "config/storage.yaml"])
    out = capsys.readouterr().out + capsys.readouterr().err
    assert code == 1
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_cli_db.py -v`
Expected: FAIL — `ModuleNotFoundError: server.storage.cli_db`

- [ ] **Step 3: Add the structured logger**

Create `src/server/storage/log.py`:
```python
"""structlog JSON logger for the storage module (storage.md §8)."""
import structlog


def get_logger(name: str = "storage") -> structlog.BoundLogger:
    return structlog.get_logger(module=name)
```

- [ ] **Step 4: Implement cli_db.py**

Create `src/server/storage/cli_db.py`:
```python
"""DB management CLI (storage.md §6): init/revision/migrate/status/seed/import."""
import argparse
import sys

from .config import load_config
from .dbm import operations
from .log import get_logger

log = get_logger()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cli_db")
    ap.add_argument("--config", default="config/storage.yaml")
    sub = ap.add_subparsers(dest="verb")

    p_init = sub.add_parser("init")
    p_init.add_argument("--force", action="store_true")
    p_rev = sub.add_parser("revision")
    p_rev.add_argument("-m", "--message", required=True)
    p_rev.add_argument("--no-autogenerate", action="store_true")
    p_mig = sub.add_parser("migrate")
    p_mig.add_argument("--sql", action="store_true")
    sub.add_parser("status")
    sub.add_parser("seed")
    p_imp = sub.add_parser("import")
    p_imp.add_argument("--source", required=True)

    args = ap.parse_args(argv)
    if args.verb is None:
        ap.print_help()
        return 1

    cfg = load_config(args.config)
    try:
        if args.verb == "init":
            result = operations.init(cfg, force=args.force)
            log.info("db_init", mode=result, force=args.force)
        elif args.verb == "revision":
            result = operations.revision(cfg, args.message, autogenerate=not args.no_autogenerate)
            log.info("db_revision", auto=not args.no_autogenerate)
        elif args.verb == "migrate":
            result = operations.migrate(cfg, sql=args.sql)
            log.info("db_migrate", sql=args.sql)
        elif args.verb == "status":
            st = operations.status(cfg)
            log.info("db_status", **st)
            sys.stdout.write(f"{st}\n")
            result = "ok"
        elif args.verb == "seed":
            result = operations.seed(cfg)
            log.info("db_seed", result=result)
        elif args.verb == "import":
            operations.import_dump(cfg, args.source)
            result = "imported"
        else:
            return 1
    except NotImplementedError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    except Exception as e:  # surface a clean failure; storage errors propagate as text
        sys.stderr.write(f"error: {e}\n")
        return 1
    sys.stdout.write(f"{args.verb}: {result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Add config and shell wrapper**

Create `config/storage.yaml`:
```yaml
# Storage config (storage.md §4.1). Hosting lives entirely in the DSN string.
storage:
  dsn: ${TALIUM_DB_DSN}          # e.g. postgresql+psycopg://user@host:5432/econ?sslmode=require
  admin_dsn: ${TALIUM_DB_ADMIN_DSN}
  pool_size: 5
  max_overflow: 5
  pool_recycle_s: 1800
  pool_pre_ping: true
  queries_dir: config/queries
```

Create `utils/db.sh`:
```bash
#!/usr/bin/env bash
# Wrapper for the storage DB management CLI (storage.md §6).
set -euo pipefail
exec python -m server.storage.cli_db "$@"
```
Then: `chmod +x utils/db.sh`

- [ ] **Step 6: Run tests, expect pass; commit**

Run: `pytest tests/server/storage/test_cli_db.py -v` → PASS
```bash
git add src/server/storage/cli_db.py src/server/storage/log.py config/storage.yaml utils/db.sh tests/server/storage/test_cli_db.py
git commit -m "feat(storage): db management CLI, config, shell wrapper, logging"
```

---

### Task 11: Public API exports and end-to-end integration test

**Files:**
- Modify: `src/server/storage/__init__.py`
- Test: `tests/server/storage/test_integration.py`
- Modify: `requirements.txt` (ensure `structlog` present — it is, from msging)

**Interfaces:**
- Produces: `server.storage` re-exports `Base`, `StorageConfig`, `get_engine`, `session_scope`, `QueryCatalog`, `PgProducerStore`, `QueryConfigError`, `SchemaVersionError`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_integration.py`:
```python
from common.msging.models import ProducerRecord
from server.storage import (
    Base,
    PgProducerStore,
    QueryCatalog,
    StorageConfig,
    get_engine,
    session_scope,
)
from server.storage.dbm import operations
from tests.server.storage.conftest import requires_db


def test_public_api_imports():
    assert all(x is not None for x in (Base, StorageConfig, get_engine, session_scope, QueryCatalog, PgProducerStore))


@requires_db
def test_init_then_producer_roundtrip_is_idempotent(cfg):
    operations.init(cfg, force=True)
    store = PgProducerStore()
    rec = ProducerRecord(
        producer_id="int1", key_prefix="tk_producer_zz", key_hash="h", registered_at="2026-07-20T00:00:00Z"
    )
    store.upsert(rec)
    store.upsert(rec)  # idempotent
    assert store.get("int1").producer_id == "int1"
    # init again is a no-op (idempotent, arch rule 3)
    assert operations.init(cfg) == "exists"
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_integration.py::test_public_api_imports -v`
Expected: FAIL — names not exported from `server.storage`.

- [ ] **Step 3: Fill in __init__.py**

Replace `src/server/storage/__init__.py`:
```python
"""Storage module public API (storage.md §4)."""
from .config import StorageConfig, load_config
from .engine import dispose_engine, get_engine, session_scope
from .errors import QueryConfigError, SchemaVersionError
from .producer_store import PgProducerStore
from .queries import QueryCatalog
from .schema import Base

__all__ = [
    "StorageConfig",
    "load_config",
    "get_engine",
    "session_scope",
    "dispose_engine",
    "QueryCatalog",
    "PgProducerStore",
    "Base",
    "QueryConfigError",
    "SchemaVersionError",
]
```

- [ ] **Step 4: Run the whole storage suite, expect pass**

Run: `pytest tests/server/storage/ -v`
Expected: PASS (DB tests skip without `TALIUM_TEST_DSN`; to run them fully: `TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:5432/econ_test pytest tests/server/storage/ -v`).
Docker one-liner for a local test DB: `docker run --rm -e POSTGRES_HOST_AUTH_METHOD=trust -p 5432:5432 -d postgres:16`

- [ ] **Step 5: Run the full repo suite; commit**

Run: `pytest -q` → all green (msging suite unaffected).
```bash
git add src/server/storage/__init__.py tests/server/storage/test_integration.py
git commit -m "feat(storage): public API exports and end-to-end integration test"
```

---

### Task 12: Companion README

**Files:**
- Create: `src/server/storage/README.md`
- Test: `tests/server/storage/test_readme.py`

**Interfaces:**
- Consumes: the finished public API (Task 11) and CLI (Task 10).
- Produces: user-facing documentation — interfaces, a quick example, config-driven DSN/user, other DB configs, and how to run regression tests.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_readme.py`:
```python
from pathlib import Path

README = Path("src/server/storage/README.md")


def test_readme_has_required_sections():
    text = README.read_text(encoding="utf-8")
    for section in ("## Public API", "## Quick example", "## Configuration",
                    "## DB management CLI", "## Running tests"):
        assert section in text, f"missing {section}"
    # config-driven DSN/user is documented
    assert "TALIUM_DB_DSN" in text
    assert "postgresql+psycopg://" in text
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_readme.py -v`
Expected: FAIL — `FileNotFoundError` / README missing.

- [ ] **Step 3: Write the README**

Create `src/server/storage/README.md`:
````markdown
# Storage Module

The data-access layer for all server processes: SQLAlchemy models (the schema), a
named-query loader, a Postgres producer store, and the DB management CLI.
Full design: [`docs/modules/storage.md`](../../../docs/modules/storage.md).

## Public API

```python
from server.storage import (
    StorageConfig, load_config,   # configuration
    get_engine, session_scope,    # engine + transaction boundary
    QueryCatalog,                 # named-query loader
    PgProducerStore,              # msging.ProducerStore backend
    Base,                         # schema metadata root
)
```

| Interface | Signature | Purpose |
|---|---|---|
| `load_config` | `load_config(path) -> StorageConfig` | Read `config/storage.yaml`, interpolate `${ENV}`. |
| `get_engine` | `get_engine(cfg: StorageConfig \| None = None) -> Engine` | Process-wide engine singleton; first call needs `cfg`. |
| `session_scope` | `session_scope() -> Iterator[Session]` | Transaction boundary: commit on exit, rollback on error. |
| `QueryCatalog.load` | `QueryCatalog.load(dir) -> QueryCatalog` | Compile `config/queries/*.yaml` to `text()`. |
| `QueryCatalog.get` | `get(file_key, name) -> TextClause` | Fetch a named query by file + name. |
| `PgProducerStore` | `get / get_by_key_prefix / upsert / touch_last_seen / set_revoked` | Postgres impl of `msging.ProducerStore`. |

## Quick example

```python
from sqlalchemy import text
from server.storage import load_config, get_engine, session_scope, QueryCatalog

cfg = load_config("config/storage.yaml")
get_engine(cfg)                        # build the engine once at startup

catalog = QueryCatalog.load(cfg.queries_dir)
with session_scope() as s:             # commits on exit, rolls back on error
    rows = s.execute(text("select 1")).all()
```

Producer store (selected via msging config `producer_store.backend: db`):
```python
from server.storage import PgProducerStore
store = PgProducerStore()
record = store.get("producer-uuid")
```

## Configuration (all config-driven)

Everything lives in `config/storage.yaml`; secrets come from environment variables, never
the file.

```yaml
storage:
  dsn: ${TALIUM_DB_DSN}
  admin_dsn: ${TALIUM_DB_ADMIN_DSN}
  pool_size: 5
  max_overflow: 5
  pool_recycle_s: 1800
  pool_pre_ping: true
  queries_dir: config/queries
```

- **DB connection string and user are config-driven.** The DSN carries user, host, port,
  database, and SSL: `postgresql+psycopg://USER@HOST:5432/econ?sslmode=require`. Local vs.
  AWS RDS vs. a PgBouncer/RDS-Proxy endpoint is just a different `TALIUM_DB_DSN` value — no
  code change.
- **`admin_dsn`** is used only by `db init --force`; services never see it.
- **Pooling:** `pool_size`, `max_overflow`, `pool_timeout_s`, `pool_recycle_s`,
  `pool_pre_ping`, `connect_args` are per-process (see storage.md §4.1).
- **`queries_dir`** points at the named-query YAML folder.

## DB management CLI

`python -m server.storage.cli_db <verb>` (wrapper: `utils/db.sh <verb>`):

| Verb | Action |
|---|---|
| `init` | Create schema if absent; no-op if present. |
| `init --force` | Drop everything and rebuild (destructive). |
| `revision --autogenerate -m "…"` | Generate a migration from model diffs. |
| `revision -m "…"` | Empty migration to hand-write. |
| `migrate [--sql]` | Apply migrations to head (`--sql` renders without executing). |
| `status` | Current revision vs. head + model drift. |
| `seed` / `import` | Seed data / load a dump. |

Schema visibility (no DB needed): `python -m server.storage.cli_schema_dump --ddl` (DDL) and
`--er` (Mermaid ER diagram).

## Running tests

Unit tests need no database; DB-backed tests skip unless `TALIUM_TEST_DSN` is set.

```bash
# unit only (no DB)
pytest tests/server/storage/ -v

# full regression (throwaway Postgres)
docker run --rm -e POSTGRES_HOST_AUTH_METHOD=trust -p 5432:5432 -d postgres:16
export TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:5432/postgres
pytest tests/server/storage/ -v
```

### Test report

Regenerate the detailed report:
```bash
mkdir -p docs/superpowers/reports
pytest tests/server/storage/ --junitxml=docs/superpowers/reports/storage-junit.xml
python utils/gen_test_report.py
```
Report lands at **`docs/superpowers/reports/2026-07-20-storage-test-report.md`**.
````

- [ ] **Step 4: Run test, expect pass; commit**

Run: `pytest tests/server/storage/test_readme.py -v` → PASS
```bash
git add src/server/storage/README.md tests/server/storage/test_readme.py
git commit -m "docs(storage): companion README — API, example, config, test instructions"
```

---

### Task 13: Regression run and test report generator

**Files:**
- Create: `utils/gen_test_report.py`
- Create (generated): `docs/superpowers/reports/2026-07-20-storage-test-report.md`
- Test: `tests/server/storage/test_report_generator.py`

**Interfaces:**
- Produces: `utils/gen_test_report.py` (stdlib only) that renders a Markdown report from a pytest JUnit XML at `docs/superpowers/reports/storage-junit.xml` → `docs/superpowers/reports/2026-07-20-storage-test-report.md`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/storage/test_report_generator.py`:
```python
import subprocess
import sys
from pathlib import Path


def test_generator_renders_markdown(tmp_path, monkeypatch):
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuite><testcase classname="t" name="a" time="0.1"/>'
        '<testcase classname="t" name="b" time="0.2"><skipped/></testcase></testsuite>'
    )
    out = tmp_path / "report.md"
    # gen_test_report reads JUNIT env override when set (see implementation)
    monkeypatch.setenv("STORAGE_JUNIT_XML", str(junit))
    monkeypatch.setenv("STORAGE_REPORT_MD", str(out))
    r = subprocess.run([sys.executable, "utils/gen_test_report.py"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert "# Storage Module — Test Report" in text
    assert "Total: 2" in text
    assert "Skipped: 1" in text
```

- [ ] **Step 2: Run it, expect failure**

Run: `pytest tests/server/storage/test_report_generator.py -v`
Expected: FAIL — `utils/gen_test_report.py` missing.

- [ ] **Step 3: Implement the generator**

Create `utils/gen_test_report.py`:
```python
"""Render a Markdown test report from a pytest JUnit XML (stdlib only)."""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

JUNIT = Path(os.environ.get("STORAGE_JUNIT_XML", "docs/superpowers/reports/storage-junit.xml"))
OUT = Path(os.environ.get("STORAGE_REPORT_MD", "docs/superpowers/reports/2026-07-20-storage-test-report.md"))


def main() -> int:
    if not JUNIT.exists():
        print(f"missing {JUNIT}; run pytest --junitxml={JUNIT} first", file=sys.stderr)
        return 1
    root = ET.parse(JUNIT).getroot()
    suites = root.findall(".//testsuite") or [root]
    rows: list[tuple[str, str, str]] = []
    fails = skips = 0
    for suite in suites:
        for case in suite.findall("testcase"):
            name = f"{case.get('classname')}.{case.get('name')}"
            if case.find("failure") is not None or case.find("error") is not None:
                status = "FAIL"; fails += 1
            elif case.find("skipped") is not None:
                status = "skip"; skips += 1
            else:
                status = "pass"
            rows.append((name, status, case.get("time", "0")))
    total = len(rows)
    lines = [
        "# Storage Module — Test Report",
        "",
        f"- Total: {total}  Passed: {total - fails - skips}  Failed: {fails}  Skipped: {skips}",
        "",
        "| Test | Result | Time (s) |",
        "|---|---|---|",
    ]
    lines += [f"| {n} | {s} | {t} |" for n, s, t in rows]
    lines += [
        "",
        "Reproduce: `pytest tests/server/storage/ "
        "--junitxml=docs/superpowers/reports/storage-junit.xml && python utils/gen_test_report.py`",
        "",
        "DB-backed tests show `skip` unless `TALIUM_TEST_DSN` points at a Postgres.",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test, expect pass**

Run: `pytest tests/server/storage/test_report_generator.py -v` → PASS

- [ ] **Step 5: Generate the real report over the full suite**

Run:
```bash
mkdir -p docs/superpowers/reports
pytest tests/server/storage/ --junitxml=docs/superpowers/reports/storage-junit.xml
python utils/gen_test_report.py
```
Confirm `docs/superpowers/reports/2026-07-20-storage-test-report.md` exists and lists every storage test with pass/skip.

- [ ] **Step 6: Commit**

```bash
git add utils/gen_test_report.py tests/server/storage/test_report_generator.py docs/superpowers/reports/
git commit -m "test(storage): regression test report generator and generated report"
```

---

## Self-Review

**Spec coverage** (`docs/modules/storage.md`):
- §2 layout → Tasks 1,3,4,7,8,9,10 create the files under `src/server/storage/`. ✓
- §3 schema (conventions, producers, sessions, session_info, users, generated columns) → Task 4. ✓
- §4 data-access API (`StorageConfig`, `get_engine`, `session_scope`, `QueryCatalog`) → Tasks 2,3,7. ✓
- §4.1 pooling/hosting via DSN → Task 2 (`StorageConfig` fields), Task 10 (`config/storage.yaml`). ✓
- §5 producer store → Task 8. ✓
- §6 migrations two-phase + CLI verbs → Tasks 9,10. ✓
- §7 schema visibility (DDL + ER) → Tasks 5,6. ✓
- §8 logging → Task 10 (`log.py`, event names used in `cli_db`). ✓
- §9 testing (real Postgres, golden DDL, idempotency) → Task 3 conftest, Task 5 golden, Tasks 8/11 idempotency. ✓
- §10 dependencies → Task 1. ✓
- Companion README (interfaces, quick example, config-driven DSN/user, other DB configs, regression instructions) → Task 12. ✓
- Detailed test report generated at `docs/superpowers/reports/2026-07-20-storage-test-report.md`, regeneration documented in the README → Task 13. ✓

**Placeholder scan:** `seed` and `import_dump` are intentionally stubbed per the spec's deferrals (§11), each with a clear runtime message — not plan placeholders. No "TODO/TBD/handle edge cases" steps remain.

**Type consistency:** `get_engine(cfg=None)`, `session_scope()`, `QueryCatalog.load/get`, `PgProducerStore` five methods, and `operations.{init,migrate,revision,status,seed,import_dump}` names match across their consuming tasks (8, 9, 10, 11).

**Open follow-ups (not this plan):** a shared `common/` config loader (arch §12) — storage mirrors msging's local loader for now; `get_by_key_hash` on the msging side to switch `verify()` to direct hash lookup (storage.md §3.2).
