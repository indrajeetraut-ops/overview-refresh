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
    PgOrgTokenStore,              # org ingest tokens for the OTLP routes
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
| `PgOrgTokenStore` | `issue / get / find_by_hash_prefix / list_for_org / set_revoked` | `tk_org_` keys for the OTLP intake routes. `issue()` returns the plaintext once; only its sha256 is stored. Admin surface is `cli_db org-token`. |

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

Producer store (selected via msging config `producer_store.backend: postgres`):
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
venv/bin/python -m pytest tests/server/storage/ -v

# full regression (local Postgres via docker compose — see docker/docker-compose.yml)
./utils/db_up.sh                 # starts postgres:16, waits until healthy
export TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:5432/econ_test
venv/bin/python -m pytest tests/server/storage/ -v
./utils/db_down.sh -v            # stop and wipe the data volume
```

The database container is defined in `docker/docker-compose.yml`; `utils/db_up.sh` /
`utils/db_down.sh` bring it up/down. (If the wrappers aren't executable yet, run them with
`bash utils/db_up.sh` or `chmod +x utils/db_*.sh` once.)

### Test report

Regenerate the detailed report:
```bash
mkdir -p docs/superpowers/reports
venv/bin/python -m pytest tests/server/storage/ \
    --junitxml=docs/superpowers/reports/storage-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Storage Module" \
    --junit docs/superpowers/reports/storage-junit.xml \
    --out   docs/superpowers/reports/2026-07-20-storage-test-report.md \
    --tests tests/server/storage/ \
    --note 'DB-backed tests show `skip` unless `TALIUM_TEST_DSN` points at a Postgres.'
```
(`gen_test_report.py` is module-agnostic — title, paths and footnotes are all
arguments. `msging` uses the same script.)
Report lands at **`docs/superpowers/reports/2026-07-20-storage-test-report.md`**.

## Forecast load

`forecast_runs` / `forecast_scope_series` hold the precomputed dashboard forecast
(docs/modules/storage.md §3.8). `forecast_load.py` reads a `forecast_export.json`
(`dashboard-forecast/1.x`; any other major version is refused) as plain dicts, validates its
structure before any database work, and replaces the loaded run in one transaction (one run at
a time; a malformed file or a failed write changes nothing).

```bash
utils/load_forecast.sh --export <forecast_export.json> \
    --dsn postgresql+psycopg://<user>@localhost:5433/econ_synth

utils/load_synthetic.sh --dataset <org-demo>/org --dsn ... --replace \
    --forecast-export <org-demo>/forecast/forecast_export.json
```

Synthetic data only: both refuse a database whose name does not end in `_synth` (D13). With
`--forecast-export` the synthetic loader also checks the export against the dataset (its org
scope is the manifest's org, every team scope a directory team, every repo scope a repository)
and writes the forecast rows in the same transaction as the rest of the load, before the
rollup check. The standalone CLI prints one JSON summary line (`run_id`, `scope_series_rows`,
`scope_count`, `preset_count`, `origin`, `data_through`, `duration_ms`) and exits 0, 2 (bad
file, version, dataset mismatch or refused database) or 1 (anything else). `utils/synth_dump.sh`
passes `<org-demo>/forecast/forecast_export.json` to the loader when it exists.
