# GitHub Acquisition — REST vs GraphQL for the Historic Pass

**Date:** 2026-08-19
**Purpose:** The verification pass `docs/plan/2026-07-28-github-app-integration.md` §0 prerequisite 2
demands, plus a decision record for the historic-backfill reader.
**Scope, as directed:** GitHub supplies **productivity** signal only. Cost comes from the collector's
Claude Code sessions. CI/Actions cost, Copilot metrics and billing APIs are **explicitly out of
scope** — they are noted in §5 as unexplored seams and nothing depends on them.
**Posture:** webhooks are the eventual steady state (`git-source.md` §4.1, unchanged). This note is
about the other half — the API reader that supplies history, which is what the first customer's ROI
number is actually computed from.
**Relationship to `docs/modules/git-source.md`:** that doc stays the authority on contracts. This
note records three factual corrections and one design decision it defers (§8.3 "GraphQL is a v2
seam").

---

## 1. Corrections to `git-source.md`

| # | Where | Recorded as | Actually | Impact |
|---|---|---|---|---|
| C1 | §5.4, §8.2, §10 | `push` payload "capped at 20 by GitHub" | **2048 commits.** "The array includes a maximum of 2048 commits. If necessary, you can use the Commits API to fetch additional commits." | Compare-fill drops from a routine path to an edge case (history imports). The mapper must not assume truncation at 20. |
| C2 | §10 | Operator redelivery "for a known outage window" | Redelivery reaches only the **past 3 days**; GitHub still does not auto-retry. | An outage found on day 4 is unrecoverable by redelivery. Gap-fill is the *primary* recovery path, and its cursor must walk back further than 3 days. |
| C3 | §8.3 | "thousands of requests per hour, scaling with the installation's size" | **5,000/hr** base per installation; +50/hr per repo over 20 and per user over 20, capped at **12,500/hr**; **15,000/hr** flat for Enterprise Cloud. Secondary: **100 concurrent** requests and **900 points/min** (GET=1, write=5). | §8.3's ~5,000/h assumption is right for a small org. The 900 pts/min secondary limit is unmentioned and must be in the client throttle. |

Additional facts worth holding: global webhook payload cap is **25 MB** (over it, GitHub delivers
*nothing* for that event); no `push` event is created if >5,000 branches are pushed at once. Both
reinforce C2 — gap-fill is a standing safety net, not an outage tool.

---

## 2. The asymmetry that drives the whole decision

| Source | `additions` / `deletions` / `changed_files` | File list | Reviews |
|---|---|---|---|
| `pull_request` **webhook** | **Yes** — carries the full PR object | No | No |
| `GET /pulls` (**list**) | **No** — returns "Pull Request Simple" | No | No |
| `GET /pulls/{n}` (**get**) | Yes | No | No |
| GraphQL `pullRequests` connection | Yes | **Yes**, nested | **Yes**, nested |

The live path gets PR stats free. The REST historic path does not: every stat, every file list and
every review is a separate call. **That N+1 is the entire cost of backfill**, and it is the only
thing REST and GraphQL genuinely differ on. Everything else below follows from this row.

---

## 3. REST vs GraphQL for the historic pass

### 3.1 What each one costs

REST, per repo, to fill the §5.3 artifact contract:

```
GET /repos/{o}/{r}/pulls?state=all&sort=updated&direction=desc&per_page=100   # 1 per 100 PRs
GET /repos/{o}/{r}/pulls/{n}            # additions, deletions, changed_files, merged_by
GET /repos/{o}/{r}/pulls/{n}/files      # paths + per-file counts   (100/page)
GET /repos/{o}/{r}/pulls/{n}/reviews    # review states             (100/page)
GET /repos/{o}/{r}/pulls/{n}/commits    # shas + authors            (100/page)
```

→ **~4 calls per PR** plus 1% for listing.

GraphQL, the same contract in one round trip:

```graphql
query Backfill($owner:String!, $name:String!, $cursor:String) {
  rateLimit { cost remaining nodeCount resetAt }
  repository(owner:$owner, name:$name) {
    databaseId                                   # the numeric repo_id of git-source §6.2
    pullRequests(first:25, after:$cursor, orderBy:{field:UPDATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title state isDraft merged
        createdAt updatedAt closedAt mergedAt
        additions deletions changedFiles
        author { login }
        headRefName baseRefName headRefOid
        files(first:100)   { pageInfo { hasNextPage } nodes { path additions deletions changeType } }
        reviews(first:20)  { totalCount nodes { state submittedAt author { login } } }
        commits(first:100) { totalCount nodes { commit {
            oid authoredDate additions deletions author { email user { login } } } } }
        comments { totalCount }
      }
    }
  }
}
```

**Scoring it** (GitHub's documented method: count the requests each unique connection would need
*assuming every one hits its `first:` limit*, divide by 100, round):

```
  1   pullRequests
+ 25  files    (one connection per PR)
+ 25  reviews
+ 25  commits
─────
  76  / 100  →  1 point
```

25 PRs per point. Nodes: 25 + 2,500 + 500 + 2,500 = **5,525**, well inside the 500,000 ceiling.

### 3.2 The wall-clock difference, for one 100-developer year (~25,000 PRs)

| | REST | GraphQL |
|---|---|---|
| Units consumed | ~100,000 requests | ~1,000 points |
| Budget | 5,000/hr (→12,500 scaled, 15,000 EC) | 5,000/hr (→12,500 scaled, 10,000 EC) |
| **Bound by** | **the hourly budget** | **the 10-second query timeout** |
| Serial wall clock | **~20 h** (mostly sleeping on the limit) | ~1,000 queries × ~3 s ≈ **50 min** |
| With modest concurrency | no help — budget-bound, not latency-bound | ~8 in flight (cap is 100) → **~10 min** |

The budgets are the *same size*. GraphQL simply packs ~75× more data into each unit. Note the
consequence: **concurrency helps GraphQL and does nothing for REST**, because REST is rationed by
the hour and GraphQL by round-trip latency.

### 3.3 What REST is genuinely better at

Not a formality — three of these are real.

1. **Conditional requests.** REST supports `ETag`/`If-None-Match`; a `304` does **not** count against
   the primary rate limit. GraphQL is a POST and has no conditional caching. For the **recurring
   gap-fill** pass — narrow, and mostly unchanged — REST is the cheaper reader. §8.3 already relies
   on this, and it is correct *for gap-fill*.
2. **Honest failure granularity.** REST fails per resource with an HTTP status. GraphQL returns
   **HTTP 200 with a partial `data` and an `errors[]` array**: a timeout or a permission problem
   nulls part of the response while the request "succeeds". For an ROI system this is a correctness
   hazard, not an inconvenience — a null `mergedAt` that means *"we timed out"* is indistinguishable
   from *"not merged"* unless the reader checks `errors[]`, and the difference silently corrupts a
   merge-rate metric. **Mandatory rule: the GraphQL reader treats any non-empty `errors[]` as a
   failed page and retries it; it never maps partial data.**
3. **Version pinning.** REST is pinned by `X-GitHub-Api-Version: 2026-03-10`. The GraphQL schema has
   no version pin — it evolves under deprecation notices.

Two lesser ones: the 10-second timeout means `first:` has to be tuned empirically and degraded on
big repos; and `files(first:100)`/`commits(first:100)` truncate on large PRs, so `hasNextPage` needs
either a follow-up query or a REST fallback for the tail.

### 3.4 The argument that actually settles it

Not the first backfill — the **re-backfills**. History gets re-read every time the mapper gains a
field, fixes a bug, or a new metric needs a column that was not captured. That is a handful of times
per year, not once. At 20 hours a run, REST makes "add a field and re-derive history" a scheduling
decision; at 10 minutes, GraphQL makes it a command. The §5.3 contract is explicitly
additive-and-evolving, which means re-reads are designed in.

Second consideration: this is customer-onboarding latency. "Your dashboard is ready tomorrow" and
"your dashboard is ready after this coffee" are different products.

### 3.5 Decision

**Hybrid, three readers behind one mapper.** This is not an architecture change — `git-source.md`
§5.3 already defines a provider-neutral artifact contract precisely so readers are swappable, and
§4.2 already has backfill and webhooks sharing the mapper. This adds a third reader to an existing
seam.

| Reader | Job | Why it wins there |
|---|---|---|
| **Webhook** | Live path, steady state | Free PR stats, no polling, sub-second |
| **GraphQL** | Bulk historic backfill (the deep one-time and every re-backfill) | ~75× denser per rate-limit unit; concurrency actually helps |
| **REST** | Recurring gap-fill; fallback when a GraphQL page times out or truncates; anything GraphQL exposes awkwardly | ETag-conditional (free 304s); per-resource failure semantics; version-pinned |

REST is **not** relegated to a fallback — it owns the recurring path, which runs forever, while
GraphQL owns the rare-but-heavy path. Each is used where its rationing model fits.

Amends `git-source.md` §8.3 ("Not v1 — REST's per-endpoint simplicity is worth more than the hours
saved on a one-time run") and §14, which lists GraphQL backfill as a v2 seam. The premise that
failed is **"a one-time run."**

### 3.6 Implementation consequences

- One `GitHubClient` (§7.4) gains a `graphql(query, variables)` method beside its REST verbs. Same
  app-JWT → installation-token flow, same `tenacity` retry. **No new dependency** — `httpx` posts
  JSON; a GraphQL client library would buy nothing for ~4 query documents.
- Query documents live as `.graphql` files, not inline strings, so they diff readably.
- **Always request `rateLimit { cost remaining nodeCount resetAt }` in every query.** GraphQL will
  tell you the exact score of the query you just ran — measure, never estimate. Feed it to the
  §11 `rate_limit_wait` log line.
- `repository.databaseId` is the numeric `repo_id` of §6.2, so identity is consistent with the
  webhook path with no extra call.
- Tests: `respx` matches REST by URL and GraphQL by **request body** (one endpoint, many queries).
  Record fixtures per query document. The idempotency bar of §11 is unchanged and is what proves the
  two readers agree: **a GraphQL-backfilled artifact and a REST-backfilled artifact must be
  byte-identical**, and that assertion is the reason both readers are safe to keep.
- The §5.4 last-writer-wins-on-`updated_at` rule already makes reader order irrelevant.

---

## 4. Scoping history depth (the two-speed plan, confirmed)

GitHub productivity is only meaningful here where a Claude Code session exists to price it against.
Backfilling three years of PRs when sessions go back 90 days produces artifacts with no counterpart.
`git-source.md` §8.3's two-speed plan is right and survives this note:

- **List pass over all available history** — cheap under either reader. Gives PR counts, merge rates
  and velocity trends immediately, and the long baseline that makes a "before agents / after agents"
  comparison possible. That baseline is arguably the most valuable single output of the historic
  pass, and it costs almost nothing.
- **Detail pass (files, reviews, per-file counts) only over the window where sessions exist.** File
  overlap is worthless without a session on the other side.

Under GraphQL the distinction weakens — the detail pass is nearly free — but it should be kept as a
configured window anyway, because it bounds the first run for a customer whose repos are enormous.

---

## 5. Deliberately not pursued

Recorded so the choice is visible rather than forgotten:

- **Actions/CI cost** (`workflow_run` + `/actions/runs/{id}/timing` for billable ms per OS, and
  `/organizations/{org}/settings/billing/usage` for actual dollars per repo). Out of scope by
  direction — cost comes from the collector. The seam is a `ci` artifact kind and a subscription.
- **Copilot usage metrics** (`/orgs/{org}/copilot/metrics`, GA 2026-02-27; older endpoints shut down
  2026-04-02). Relevant only if a customer's AI spend is split across Copilot and the agents this
  platform meters.
- **Audit log streaming** (Enterprise Cloud → S3/Splunk/Datadog/Event Hubs, REST-configurable). The
  only channel where GitHub manages delivery durability. Worth remembering for an enterprise that
  refuses an inbound HTTPS listener — an S3 bucket they already own can be an easier sell.
- **`git clone --filter=blob:none` + `git log`.** Full commit history, authorship and per-file churn
  at **zero API rate limit**. A cheap complement to the commit half of backfill and the only channel
  that works with no API access at all. Nothing about PRs, reviews or CI.
- **The `/events` API.** 90-day / 300-event ceiling, ~5-minute cache, no delivery guarantees.
  Strictly worse than webhooks + backfill.

---

## 6. On the grouping unit

Open, and expected to vary by team and project. That is an argument for a specific posture rather
than a blocker: **do not let the grouping unit reach the acquisition layer.** Artifacts stay at
PR / commit / review granularity carrying every dimensional key available — `repo_id`, `user_id`,
`author_login`, branch, base branch, labels, milestone, linked issue, and the §5.3 timestamps — and
grouping becomes a query-time `GROUP BY`. Sprint, project, team and epic then differ by which key a
report groups on, not by what was collected.

The corollary is a collection rule: **capture every plausible grouping key now, even where no report
uses it yet**, because a key not captured cannot be recovered without another backfill. Labels and
milestone are the cheap ones — both are already in the payloads §5.4 maps, and neither is currently
carried into the artifact. Adding them is a schema decision that wants making before G1, whereas
choosing the grouping unit does not have to be made at all.

---

## 7. Proposed amendments to `git-source.md`

| # | Change | Where | Urgency |
|---|---|---|---|
| A1 | 20-commit cap → 2048; demote compare-fill to an edge case | §5.4, §8.2, §10 | Before G4 |
| A2 | State the 3-day redelivery ceiling; gap-fill must walk back past it | §10, §8.2 | Before G5 |
| A3 | Concrete rate-limit numbers + the 900 pts/min secondary limit in the client throttle | §8.3, G2 | Before G5 |
| A4 | Promote GraphQL from v2 seam to the v1 bulk-historic reader; REST keeps gap-fill and fallback | §8.3, §14, G5 | Before G5 |
| A5 | Carry labels, milestone and linked-issue refs on the artifact as grouping keys | §5.3, §9, G1 | **Before G1** (schema) |
| A6 | Add the reader-agreement test: GraphQL and REST backfill of the same PR are byte-identical | §11, G5 | Before G5 |

A5 is the only one that is expensive to defer, because it is a schema change and the data it
captures cannot be recovered later without re-running history.

---

## 8. Connection mechanics — how an App actually reaches a repo

Reference material for G2 (`plan §1 G2`, credentials and client). The short version: **the webhook
channel and the API access are the same grant.** An installation is not a webhook subscription — it
is an authorization record. Installing the App is what creates both.

### 8.1 Three credential tiers

| Credential | Made from | Lifetime | Authenticates as | Used for |
|---|---|---|---|---|
| **App JWT** | The App's private key, RS256 | **≤10 min** | the App itself | Only app-management endpoints: list installations, mint installation tokens. Cannot read a repo. |
| **Installation access token** | `POST /app/installations/{id}/access_tokens` with the JWT | **1 hour** | one installation | **Everything.** All repo reads, REST *and* GraphQL. |
| User access token | OAuth-style user authorization | configurable | a user, via the App | Not needed here — nothing in this module acts on a user's behalf. |

JWT claims: `iss` = **client ID** (recommended over app ID), `iat` = now **minus 60 s** (clock-drift
guard), `exp` ≤ 600 s ahead. Header is `Authorization: Bearer <jwt>` — the `token` form is rejected
for JWTs.

### 8.2 The flow, end to end

```
  registration (once, by us)          installation (once, per customer)      runtime (per hour)
  ─────────────────────────           ─────────────────────────────────      ──────────────────
  create App                          customer opens the install URL         sign JWT  (private key)
   ├ permissions (read-only)          selects org + repos (all | subset)      │
   ├ subscribed events                approves the permission list            ▼
   ├ webhook URL + secret             GitHub fires `installation.created`     POST /app/installations
   └ private key (PEM)  ──────────►   we record installation_id → org_id           /{id}/access_tokens
                                                                                   │
                                                                                   ▼
                                                             Authorization: Bearer ghs_… (1 h)
                                                              ├─ REST     api.github.com/…
                                                              └─ GraphQL  api.github.com/graphql
```

The same `ghs_…` token is the bearer for both APIs. There is no separate "API connection" to
establish — **`git-source.md` §7.3 already describes this flow; the point worth making explicit is
that it is not additional work beyond the webhook setup.** Once G2's client mints tokens, REST and
GraphQL are both reachable.

### 8.3 Discovering what a token can see

| Question | Call | Auth |
|---|---|---|
| Which orgs installed us? | `GET /app/installations` | App JWT |
| Which repos can this installation see? | `GET /installation/repositories` | Installation token |
| Which repos changed? | `installation_repositories` webhook | — |

Repo selection is **the customer's choice at install time** (all repos, or a subset) and cannot be
overridden by us. `git_repos` must therefore be populated from these sources, never assumed to be
the org's full repo list. This is what `cli_installations` (G2) is for.

Optional narrowing at mint time: `repository_ids` (max 500) and `permissions` body parameters scope a
token below the installation's grant. Not needed for v1 — noted because it is the mechanism if a
customer ever demands per-run least privilege.

### 8.4 The registration decision that is expensive to reverse

**Adding a repository or organization permission after customers exist requires every one of them to
approve it.** GitHub emails each org owner, and the new permission does not take effect on that
installation until an owner acts. Removing a permission takes effect immediately.

So the asymmetry is: **over-requesting is cheap to undo, under-requesting is a customer-blocking
migration.** The §7.2 permission set should be settled *before* the first customer install, and
should include anything plausibly needed — the same argument as §6 for grouping keys, for the same
reason. A permission added in year two arrives only as fast as customers click approve, and some
never will.

The one countervailing force is the security review: a longer permission list is a harder sell to a
customer's security team. All permissions here are read-only, which is the mitigation, and the
metadata-only storage posture (§5.3 "what is never stored") is the argument to make alongside it.

### 8.5 Distribution and onboarding

- **Private App** (default) — installable only on the account that owns it. Correct for the pilot.
- **Public App** — installable by any account via a shared URL: `https://github.com/apps/<slug>/installations/new`. Required the moment a customer installs it themselves. Public ≠ Marketplace; listing is separate and optional.
- Pass `?state=<opaque>` on the install URL to correlate the install back to a signup, and read
  `installation_id` + `state` from the setup-callback query. This is how §6.1's `installation_id →
  org_id` mapping gets populated **automatically** instead of by the operator, and it retires
  git-source Q1's working assumption.
- **App Manifest flow** — a customer (or we, per environment) creates a pre-configured App by
  following one URL; GitHub returns the app id, private key and webhook secret via a code exchange
  that must complete within **one hour**. Relevant for GHES customers who need the App to live in
  *their* enterprise rather than ours, and for standing up dev/staging registrations reproducibly.

### 8.6 REST vs GraphQL at the connection layer

Identical auth; the differences are small and worth knowing before G5:

| | REST | GraphQL |
|---|---|---|
| Endpoint | `https://api.github.com/...` | `https://api.github.com/graphql` (POST only) |
| Version pin | `X-GitHub-Api-Version: 2026-03-10` | none — schema evolves under deprecation |
| GHES base | `https://HOST/api/v3` | `https://HOST/api/graphql` |
| Permissions | per-endpoint, documented per route | same underlying grants, but the mapping from field → required permission is less explicit; expect to discover a missing permission as a null node plus an `errors[]` entry, not a 403 |

That last row is the practical trap: an installation missing a permission does not fail loudly in
GraphQL. It is another instance of §3.3 item 2, and the same rule covers it — **non-empty `errors[]`
means the page failed; never map partial data.**
