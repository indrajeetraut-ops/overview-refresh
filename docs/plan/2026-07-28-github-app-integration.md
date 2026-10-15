# GitHub App Integration — Implementation Plan

**Status:** Plan for execution. Pre-implementation.
**Primary spec:** `docs/modules/git-source.md` — the authority for this work (webhook contract,
artifact contract, mapping table, identity rules, backfill). Every phase below implements numbered
sections of it; this plan only sequences and tests them.
**Milestone:** M4 (`docs/plan/roadmap.md` §M4, tasks 1–2 and 4–6's inputs). This plan covers
**acquisition only** — the correlator itself (roadmap M4 tasks 4–6) is a separate plan against a
separate module doc.
**Depends on (must land first):**
- **P3 server-side redesign** (`docs/plan/2026-07-28-p3-server-side-redesign.md`) — the webhook route
  mounts on the rewritten `src/server/ingest/app.py`, and the mapper runs on the rewritten
  `src/server/worker/dispatch.py`. Starting before P3 means rebasing onto a moving file.
- `docs/modules/storage.md` → `src/server/storage/` (`session_scope`, `QueryCatalog`, `Base`,
  `schema/`, `cli_db.py`).
**Context specs (read for boundaries, do not modify blindly):**
- `docs/design/system-architecture.md` — §5 (why git is *not* on the msging wire), §6.4 (thin front
  door), §8.4 (git correlation as a worker responsibility), §13 (content-free logs), §18.2 (webhook
  signature verification, secrets), §19 (library selection).
- `docs/design/coding-econ-design-draft.md` §6.4 (webhook posture, scheduled poll) and §11.3
  (correlation signals — the consumer this work feeds).
- `docs/modules/ingest.md` §12 item 6 (the reserved seam this plan fills).
**Governance:** `CLAUDE.md` — `cli_{function}.py` naming, structured JSON logging, pytest, TODO
markers, `requirements.txt` updated at install time, no Claude references anywhere.
**Code to be created:** `src/server/ingest/git/`, `src/server/worker/git_map.py`,
`src/server/storage/schema/git.py` + migration, `src/common/git_ident.py`, `config/github.yaml`,
`config/queries/git.yaml`, `utils/git_backfill.sh`, `tests/server/ingest/git/`,
`tests/fixtures/github/`.
**New dependencies:** `PyJWT` (app JWT signing), `respx` (test-only, httpx recorder).

---

## 0. Scope, sequencing, prerequisites

**What this delivers:** repository facts flowing into Postgres from two directions — live webhook
deliveries and REST backfill of history — normalized into one artifact contract, with author→user
mapping and honestly reported coverage. **Not delivered here:** session↔PR scoring and linking. This
plan's acceptance is "the correlator has everything it needs," not "PRs are attributed."

**Sequencing (dependency order):** Schema → App & credentials → Webhook route → Mapper → REST &
backfill → Identity & `repo_id` → End-to-end. Do not start a phase before its predecessor's tests are
green.

**Prerequisites before writing code:**

1. Read `docs/modules/git-source.md` in full — §5 (contracts) and §6 (identity) are the parts that
   cost time if skimmed.
2. **Verify the GitHub specifics against current GitHub documentation before coding against them**
   (the design records them as of writing, and they are the kind of detail that drifts): header
   names, app-JWT expiry bound, installation-token TTL, per-installation rate limits, the 20-commit
   cap on `push` payloads, the absence of a file list on `pull_request` payloads, and the redelivery
   API. Same convention as arch §11.2's verification note for LiteLLM.
3. Register a **test GitHub App** against a scratch org with the §7.2 permissions, and set up local
   delivery tunneling (`smee.io` or `cloudflared tunnel`). Development against recorded fixtures is
   the norm, but the first end-to-end needs real deliveries.
4. Test DB per storage §9: real Postgres via `TALIUM_TEST_DSN`, no SQLite — JSONB and `ON CONFLICT`
   are load-bearing here too.
5. Skim the patterns to mirror: `src/server/storage/schema/producers.py` (model + JSONB shape),
   `src/server/worker/dispatch.py` (the claim loop this plugs a topic into),
   `tests/fixtures/normalizer/recorded/sanitize.py` (how recorded fixtures get sanitized).

---

## 1. Phases

### G1 — Schema and queries

- [ ] `src/server/storage/schema/git.py`: `git_installations`, `git_repos`, `git_artifacts`,
      `git_deliveries` per git-source §9. Storage conventions apply — plain indexed columns, no FKs,
      `timestamptz` UTC, JSONB for `stats`/`files`/`info`/`payload`.
- [ ] Indexes: `git_artifacts` on `(repo_id, kind, updated_at)`, `(user_id)`, `(author_login)`,
      `(merged_at)`; `git_deliveries` on `(status, received_at)` for the claim and the prune.
- [ ] Alembic migration, reviewed as SQL (`alembic upgrade --sql`) before it runs — arch §10.2.
- [ ] `config/queries/git.yaml`: `land_delivery` (insert-if-absent), `claim_delivery`,
      `upsert_artifact` (last-writer-wins on `updated_at`), `upsert_repo`, `upsert_installation`,
      `prune_deliveries`, `coverage_counts`.
- [ ] Amend `docs/modules/storage.md` §3.6 — move "Goods / PR — **TBD**" to adopted, pointing at
      `git_artifacts`; record `session_git_links` as DEFERRED to the correlator.

**Tests:** migration up/down on a scratch DB; `upsert_artifact` applied twice and out of order (older
`updated_at` second) leaves the row identical to the newer payload.

### G2 — App registration, credentials, config

- [ ] `config/github.yaml`: app id, base URL (GHES-ready from day one), webhook path, body cap,
      subscribed-event allowlist, delivery retention days, backfill window/page size, gap-fill
      interval, `installation_id → org_id` map, bot-login patterns. Secrets are `${VAR}` references
      only — never values (arch rule 8).
- [ ] `src/server/ingest/git/config.py`: pydantic config model, loaded through the shared loader.
- [ ] `src/server/ingest/git/client.py`: `GitHubClient` — app JWT (RS256, `PyJWT`) → installation
      token, in-process cache to expiry, `tenacity` retry, rate-limit awareness, ETag support. Tokens
      never touch disk or DB.
- [ ] `cli_installations.py` (`python -m server.ingest.git.cli_installations`): verifies credentials,
      lists installations and their selected repos, seeds `git_installations`/`git_repos`. This is
      the "are the secrets right" smoke test, and it must exist before G3 is debuggable.
- [ ] `pip install PyJWT` → `requirements.txt` (project rule).

**Tests:** JWT claims (issuer, expiry bound) asserted against a generated test key; token cache
re-mints exactly once across expiry; 401 from GitHub marks the installation rather than looping.

### G3 — Webhook receiver

- [ ] `src/server/ingest/git/verify.py`: constant-time HMAC-SHA256 over the **raw** body.
- [ ] `src/server/ingest/git/routes.py`: `POST talium/git/v1/webhook/github` — raw body read (no
      pydantic body model, it would re-serialize before verification), signature check, size cap,
      land delivery insert-if-absent, ring `queue_jobs` topic `git_map`, `202`. `ping` → `204`.
      Responses exactly as git-source §5.1; failure reasons never echo the payload.
- [ ] Mount the router in the rewritten `src/server/ingest/app.py` as a **host route** alongside the
      msging router (`event-messaging-design.md` §6.3).
- [ ] Record and sanitize fixture deliveries into `tests/fixtures/github/` — one per mapped event and
      action (git-source §5.4), plus `ping`.

**Tests:** valid signature accepted; invalid, absent, and post-signing-mutated body all `401`;
oversize `413`; the same delivery twice yields one row and two `202`s; storage down yields `503` and
no partial row.

### G4 — Mapper

- [ ] `src/server/ingest/git/map_github.py`: payload → artifact records per git-source §5.3/§5.4.
      Pure function, no I/O — it is shared verbatim with backfill (G5), which is the whole reason it
      is not written inside the worker handler.
- [ ] `src/server/worker/git_map.py`: the `git_map` topic handler — claim, read delivery, map, upsert
      artifacts/repos/installations, mark the delivery `done` or `unmapped`, **one transaction**.
- [ ] Register topic `git_map` in `config/worker.yaml`.
- [ ] Installation lifecycle: created/deleted/suspend/unsuspend/added/removed update rows; nothing is
      row-deleted (history survives an uninstall).
- [ ] `is_bot` flagging from the configured login patterns.
- [ ] Prune step for `done`/`unmapped` deliveries past the retention window.
- [ ] TODO markers in source for the deliberately unmapped `issues`/`issue_comment` paths, naming
      git-source §5.4 as the reason — removed when v1.5 maps them.

**Tests:** golden artifact JSON per fixture delivery; same delivery mapped twice → byte-identical
state; `closed` arriving before `opened` converges to the newer payload; a >20-commit push records
the range for compare-fill; an unmapped event lands `unmapped` and is *not* an error.

### G5 — REST backfill and gap-fill

- [ ] `src/server/ingest/git/backfill.py`: per-repo list pass (PRs `state=all` by update time, 100 per
      page; commits), windowed detail pass (files, reviews), compare-fill for truncated pushes.
      Cursor persisted on `git_repos.backfill_cursor` — resumable, never restarts from zero.
- [ ] `cli_backfill.py` (`python -m server.ingest.git.cli_backfill`) + `utils/git_backfill.sh`:
      `--repo`/`--org`, `--since`, `--list-only`, `--dump` for a single PR's mapped output.
- [ ] Gap-fill: the list pass on the configured timer, restricted to "updated since cursor," as a
      scheduled worker topic. This is the recovery path for deliveries GitHub failed to deliver
      (git-source §10) — it is not optional polish.
- [ ] Rate-limit handling: back off on secondary limits, conditional requests via ETag, per-run
      budget log line.

**Tests:** paging across a multi-page recorded response set (`respx`); cursor resume after a
mid-run abort maps each PR exactly once; a backfill row over an existing webhook row leaves state
identical (both directions); rate-limit response triggers backoff, not failure.

### G6 — Identity: `repo_id` and author→user

**This phase is what turns acquired data into ROI data. It is not a follow-on.**

- [ ] `src/common/git_ident.py`: one canonical remote-URL → `github:<owner>/<name>` normalization
      (SSH, HTTPS, with and without `.git`), used by **both** the collector and the mapper. Server
      side resolves it to the numeric `repo_id` via `git_repos` (git-source §6.2).
- [ ] Collector: derive `repo_id` in the `session_start` payload — removes the TODO at
      `src/collector/normalizer/claude_code.py:251`. Same change for the Codex normalizer when it
      lands.
- [ ] Collector: discover the developer's GitHub login where available (`gh` CLI auth, or
      `git config user.email`) and ship it at registration (arch §7.2 sanctions this).
- [ ] Mapper: resolve `author_login` → `users.github_user` → `user_id`; auto-create the user row on
      first sighting of a login with no match; **never guess** — unresolved stays null.
- [ ] Email fallback: org members listing matched against commit author emails, with the `noreply`
      privacy-address caveat recorded in the code comment.
- [ ] Coverage counters exposed by a named query: PRs with a `user_id`, PRs without, per repo.

**Tests:** URL normalization table test (SSH/HTTPS/`.git`/trailing slash/uppercase owner all collapse
to one id); a session and a PR from the same developer resolve to the same `user_id` through the
fixture path; a `noreply` email yields *unmapped*, and the coverage count reflects it.

### G7 — End-to-end and handoff

- [ ] Replay integration test: fixture repo timeline (deliveries + backfill responses) plus fixture
      sessions → artifacts and repo/user rows in the state the correlator expects. Run twice;
      assert identical state (arch design rule 3).
- [ ] Manual-testing section appended to `docs/manual-testing.md`: tunnel setup, real delivery
      walkthrough, `cli_installations` and `cli_backfill` invocations, how to redeliver from GitHub.
- [ ] Architecture amendment PR against `docs/design/system-architecture.md` §2/§5.1: git data does
      not travel as event envelopes; retire or re-scope the `git_event` `event_type` member
      (git-source §3, Q8).
- [ ] Roadmap M4 updated: tasks 1–2 done, correlator tasks 4–6 now unblocked and pointed at
      `git_artifacts` rather than `git_event` rows.

**Acceptance:** a real PR opened, reviewed, and merged on the test org appears as `pr` + `review`
artifacts with correct state and file list, within seconds; a year of that repo's history backfills
through the same mapper; running both twice changes nothing; coverage counters report a real number
rather than an assumed 100%.

---

## 2. Test plan summary

| Area | Bar |
|---|---|
| Signature | Valid / invalid / absent / mutated-after-signing — all four asserted. |
| Idempotency | Every write path run twice, byte-identical state: delivery landing, mapping, backfill, backfill-over-webhook. |
| Ordering | Out-of-order actions and backfill-after-webhook converge to the newer payload. |
| Coverage honesty | Unmappable authors produce a reported number, never a silent drop or a guessed `user_id`. |
| Content discipline | A test asserts no diff text, patch, comment body, or issue body reaches any table or log line. |
| Rate limits | Backoff and cursor resume proven against recorded responses, not against live GitHub. |

---

## 3. Open decisions to confirm before G2

| # | Decision | Working assumption |
|---|---|---|
| 1 | Which GitHub org hosts the test App, and who holds the private key | A scratch org; key in the operator's `.env`, never committed. |
| 2 | `installation_id → org_id` mapping surface | `config/github.yaml` map plus a `cli_installations` verb; no UI (git-source Q1). |
| 3 | Delivery retention window | 14 days (git-source §10). Long enough for a mapper fix-and-replay. |
| 4 | Whether the collector may shell out to `gh` for identity | Assumed yes, best-effort and silent on failure (G6). If not, email fallback and operator import carry the mapping. |
