# Directory Module

A provider-agnostic org directory — the authoritative roster, teams, hierarchy, and roles
the manager API reads. Full design:
[`docs/design/identity-ingestion.md`](../../../docs/design/identity-ingestion.md) (**IDENT**).
Implementation plan:
[`docs/plan/2026-09-09-identity-ingestion-implementation.md`](../../../docs/plan/2026-09-09-identity-ingestion-implementation.md)
(Track G).

## Shape

```
providers/csv.py      acquire records from CSV (SCIM/LDAP/OIDC adapters slot in here later)
models.py             DirectoryRecord / TeamRecord — the only thing a provider emits
reconcile.py          record -> one user_id (resolve on identity_links, enrich a
                      telemetry stub, or create; never guess)
precedence.py         authoritative IdP > csv > telemetry — who owns a field
sync.py               the SOLE writer of the directory tables
repo_rollup.py        derived users.info.repos[] (+ reverse repo_contributors); compute-on-read
service.py            reads for the API — resolve_viewer, hierarchy walk, users_in_scope;
                      also the F2 auth seam (viewer_id_from_headers)
config.py             defaults -> config/directory.yaml -> env
cli_directory_import.py   CSV import with a --dry-run diff (wrapper: utils/directory_import.sh)
```

## Public API

```python
from server.directory import (
    DirectoryRecord, TeamRecord, ExternalKey, TeamMembership, RoleGrant,
    SyncResult, sync, reconcile, Precedence, DirectoryConfig, load_config,
)
```

| Interface | Signature | Purpose |
|---|---|---|
| `DirectoryRecord.from_dict` | `from_dict(mapping) -> DirectoryRecord` | Build from the wire shape (IDENT §3.2); unknown keys ignored. |
| `reconcile` | `reconcile(session, catalog, record) -> ReconcileOutcome` | Map a record to one `user_id`; append its `identity_links`. |
| `sync` | `sync(session, catalog, records, *, teams=(), precedence=None, config=None) -> SyncResult` | Upsert teams, reconcile, precedence-gated user writes, replace membership + grants for the synced set. The only writer. |
| `Precedence.wins` | `wins(incoming, existing) -> bool` | True when `incoming` may write a field owned by `existing` (outranks or equals). |
| `compute_user_repos` | `compute_user_repos(session, catalog, user_id) -> list[dict]` | Derived `repos[]` — union of session + authored-artifact repos (IDENT §3.4). Compute-on-read. |
| `resolve_viewer` | `resolve_viewer(session, catalog, viewer_id, config=None) -> dict \| None` | `{user_id, roles, visible_teams, visible_repos}` — the F2 dependency payload. |
| `descendants` / `members_of` / `users_in_scope` | `(session, catalog, ...)` | Hierarchy walk (recursive CTE) and scope resolution for the role gate + Track A. |

## Invariants

- **`sync()` is the only writer** of `identity_links` / `teams` / `team_members` /
  `role_assignments` and the directory fields on `users`. Providers never touch the DB.
- **`identity_links(provider, external_id) -> user_id` is append-only.** A CSV-seeded user and
  a later IdP-provisioned one reconcile to one `user_id` (IDENT decision 2).
- **SQL lives in `config/queries/directory.yaml`** via `QueryCatalog` — no inline SQL.
- **Idempotent:** re-importing the same records writes identical rows and reports
  `created=0, updated=0`.
- **Structured logs** (`directory.sync`): provider set, counts, duration — never PII values.

## Tests

Unit tests (`models`, `precedence`, `schema`) need no database. DB-backed tests
(`reconcile`, `sync`, `migration`) skip unless `TALIUM_TEST_DSN` is set.

```bash
venv/bin/python -m pytest tests/server/directory/ -v          # unit only
./utils/db_up.sh
export TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:5432/econ_test
venv/bin/python -m pytest tests/server/directory/ -v          # full
```
