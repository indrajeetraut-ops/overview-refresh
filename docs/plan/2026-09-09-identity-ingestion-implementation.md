# Identity & Org-Directory Ingestion — Implementation Plan

**Date:** 2026-09-09 · **Author:** Lead Architect · **Status:** Plan (pre-build)

**Goal:** a provider-agnostic org directory — the authoritative roster, teams, hierarchy, roles, and
each user's repos — that the manager API reads. Ship the **CSV provider** now behind a sync port that
SCIM/LDAP/SSO later fill without a schema or API change.

**Spec (read before starting a task):** `docs/design/identity-ingestion.md` (**IDENT**) — §3.1 schema,
§3.2 contracts, §3.3 CSV provider, §3.4 user↔repo contribution, §4 alternatives, §8 phasing.

**Position:** this is the detailed expansion of **Track G** in
`docs/plan/2026-09-09-mvp-backend-implementation.md`. It writes the directory; F2 (the API role gate)
and every Track-A aggregation read it.

**Depends on:** existing `users` table, `session_info`, `git_artifacts`. **Blocks:** F2, Track A, F1
(budgets reference directory scopes). **Status:** planned 2026-09-09.

---

## 0. Ground rules specific to this module

1. **New module `src/server/directory/`**, mirroring `src/server/uploader/` layout: `config.py`
   (pydantic defaults → YAML → env), `sync.py` (the port), `reconcile.py`, `precedence.py`,
   `providers/csv.py`, `service.py` (reads for the API), `cli_directory_import.py`. Only the module's
   own `service.py` is imported by `api/`.
2. **`sync()` is the sole writer** of the directory tables. Providers never touch the DB; they emit
   `DirectoryRecord`s. This is what makes a later IdP provider a pure adapter.
3. **SQL in `config/queries/directory.yaml`** via `QueryCatalog` (`catalog.get("directory", "<name>")`);
   no inline SQL. Follows the existing `ingest.yaml`/`git.yaml` pattern.
4. **AuthN is out of scope** (IDENT §1). This module models the directory + authorization inputs; the
   viewer's identity comes from a trusted header/config in MVP, with the SSO seam left for F2.
5. **Structured logs** (`directory.sync`): provider, counts (created/updated/linked/skipped), duration
   — never PII values.
6. **Idempotent** — re-importing the same CSV is a no-op diff; verified per task.

---

## G1 — Directory schema + migration

**New:** extend `src/server/storage/schema/users.py`; new `directory.py` schema
(`identity_links`, `teams`, `team_members`, `role_assignments`); one Alembic migration.

- [x] Extend `users`: `display_name`, `status` (`active|disabled`, default `active`), `primary_team_id`
  (nullable), `source_provider` (nullable). Keep `github_user`/`account_uuid`/`email`.
- [x] `identity_links(provider, external_id, user_id)` — PK `(provider, external_id)`, index on
  `user_id`. The reconcile spine (IDENT §3.1).
- [x] `teams(team_id, org_id, name, parent_team_id NULL, source_provider)` — index `parent_team_id`.
- [x] `team_members(team_id, user_id, role_in_team NULL)` — PK `(team_id, user_id)`.
- [x] `role_assignments(user_id, role, scope_type, scope_id)` — role `dev|lead|admin`, scope_type
  `org|team|repo`; index `user_id`. No FKs on the write path (storage §3.1 posture).
- [x] Alembic migration in `src/server/storage/dbm/alembic/versions/` following the existing
  `<hash>_<description>.py` naming; upgrade + downgrade.

**Done when:** migration applies clean up/down; the five tables exist with the stated keys/indexes.

## G2 — DirectoryRecord + sync port + reconcile

**New:** `directory/models.py` (dataclass), `directory/sync.py`, `directory/reconcile.py`;
queries in `config/queries/directory.yaml`.

- [x] `DirectoryRecord` dataclass matching IDENT §3.2 exactly (`external_keys[]`, `email`,
  `display_name`, `status`, `teams[]`, `roles[]`, `source_provider`). Framework-free, directly testable.
- [x] `reconcile(record) -> user_id`: resolve each `external_keys` entry against `identity_links`;
  first match wins; else create a `users` row and insert the links. **A telemetry-derived stub
  (github/account_uuid only) is enriched, never duplicated** when a key matches.
- [x] `sync(records) -> SyncResult{created, updated, linked, skipped}`: reconcile, upsert user fields
  (precedence-gated, G4… see G-precedence below), upsert teams (+ parent), replace team_members and
  role_assignments for the synced set. Idempotent upserts (replace-on-conflict).
- [x] Reject a record whose `external_keys` resolve to nothing new **and** carry no email/name — never
  guess an identity (IDENT §3.2).

**Done when:** a fixture list of records syncs to the G1 tables; a record naming an existing
telemetry user by `github` enriches that row (same `user_id`), and a re-run is a no-op.

## G3 — Provider precedence

**New:** `directory/precedence.py`; `directory.precedence` config key.

- [x] Ordered chain in `config/directory.yaml`: `precedence: [scim, ldap, oidc, csv, telemetry]`
  (authoritative IdP > csv > telemetry). `wins(field, incoming_provider, existing_provider) -> bool`.
- [x] `sync()` writes a field only when the incoming provider outranks or equals the stored
  `source_provider`. A CSV re-import cannot clobber an IdP-owned field once IdP providers exist.

**Done when:** a CSV import does not overwrite a field stamped by a higher-precedence provider in a
fixture; same-provider re-import updates normally.

## G4 — CSV provider + import CLI

**New:** `directory/providers/csv.py`, `directory/cli_directory_import.py`; `utils/` shell wrapper.

- [x] Parse `users.csv` / `teams.csv` / `members.csv` (IDENT §3.3) → `DirectoryRecord`s. Map
  `github_login` → `external_keys[provider=github]`, `email` → `external_keys[provider=csv]`.
- [x] `cli_directory_import.py` (explicit CLI name per CLAUDE.md): `--dry-run` (default) prints a
  create/update/link **diff**; `--apply` calls `sync()`. Shell wrapper `utils/directory_import.sh`
  calling `python -m server.directory.cli_directory_import`.
- [x] Validation: unknown `team_id` in members → error with row number; a member row for an unknown
  user creates the user (bootstrap) with a warning.

**Done when:** a fixture CSV set imports to the G1 tables; `--dry-run` shows the diff and writes
nothing; `--apply` is idempotent on a second run.

## G5 — User↔repo contribution rollup *(IDENT §3.4)*

**New:** `directory/repo_rollup.py`; queries in `directory.yaml`.

- [x] Derive `users.info.repos[] = [{repo_id, provenance, sources[], session_count, pr_count,
  first_seen, last_seen}]` from **both** sources: `session_info(user_id, repo_id, repo_id_provenance)`
  (telemetry) and `git_artifacts(user_id, repo_id)` where `author` resolved (live GitHub).
- [x] Provenance discipline (REPO §1.1/§1.4): carry `repo_id_provenance`; a `path`-tier repo is marked
  local and never merged across users. `sources[]` records which side(s) evidence each repo.
- [x] Refresh: a lightweight rollup callable (invoked after fold / git apply); compute-on-read is
  acceptable for MVP if the rollup is deferred — pick one and state it in the module docstring.
- [x] **Do not** build a reverse `user_repos` table — "contributors of repo X" stays a `GROUP BY` over
  the base tables (record the trip-wire to promote from IDENT §3.4).

**Done when:** a fixture user's `repos[]` equals the union of their session repos and authored-PR
repos; a `path`-tier repo is flagged local; the reverse query still resolves without the list.

## G6 — Directory read service (what F2 and Track A consume)

**New:** `directory/service.py`.

- [x] `resolve_viewer(viewer_id) -> {user_id, roles[], visible_teams[], visible_repos[]}` — the input
  F2's dependency wraps. `visible_repos` draws on G5 + team/repo role scope.
- [x] `team_of(user_id)`, `descendants(team_id)` (recursive CTE over `parent_team_id`),
  `members_of(team_id, include_subteams)`, `users_in_scope(scope)`. Framework-free; unit-testable
  without a client.

**Done when:** hierarchy walk resolves a 3-level fixture; `resolve_viewer` returns a lead's teams only;
a dev's scope excludes other teams.

## G7 — Tests, logging

**New:** `tests/server/directory/`.

- [x] `reconcile`/`sync` unit tests (enrich-not-duplicate; reject-unresolvable; idempotent re-run).
- [x] Precedence tests (IdP field survives a CSV re-import; same-provider updates).
- [x] CSV import golden (dry-run diff, apply, re-apply no-op).
- [x] G5 rollup golden (union of both sources; provenance flags).
- [x] G6 hierarchy + scope tests.
- [x] `directory.sync` structured logging asserted (counts, no PII values).

**Done when:** `pytest tests/server/directory` green.

## G8 — Provider roadmap (design stub only, no code)

- [x] Confirm `docs/design/identity-ingestion.md` §8 records SCIM/LDAP/OIDC as later tracks writing the
  same `sync()`/`identity_links`. No implementation here — this task is a checklist that the seams
  (port, links, precedence config, F2 auth seam) are in place and named.

**Done when:** the four seams exist in code and are referenced from IDENT §8.

---

## Sequencing

```
G1 ──► G2 ──► G3 ──► G4 ──┬──► G6 ──► (F2, Track A in the backend plan)
                          └──► G5 (rollup; parallel, feeds G6.visible_repos)
G7 alongside every task · G8 is a sign-off checklist
```

- G1→G2 first: nothing works without the schema and the port.
- G4 (CSV) is the first usable slice — a real roster loads.
- G6 unblocks F2; G5 enriches G6's `visible_repos` but F2 can ship on team-scope first.
- Suggested PRs: (G1+G2+G3), (G4), (G5), (G6+G7), G8 sign-off.

## Out of scope (recorded)

- **SCIM / LDAP / AD providers and OIDC/SAML SSO** — later tracks behind the port + F2 auth seam
  (IDENT §8). Design-stubbed only.
- **Directory admin UI** — CSV import CLI only in MVP.
- **Multi-tenant (multiple orgs per deployment)** — `org_id` carried on every row; single tenant in MVP.
- **Reverse `user_repos` association table** — deferred; the forward list + base-table `GROUP BY`
  cover MVP (IDENT §3.4).
