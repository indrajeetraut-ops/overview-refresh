# Uploader ↔ Collector Integration — Implementation Plan

**Date:** 2026-09-22
**Status:** Ready for implementation
**Repos touched:** `talium-ai/upload` (Go, sibling checkout at `../upload`) and `code-econ` (Python, this repo)
**Audience:** an implementation session with no prior context, and a separate evaluation session afterwards

---

## 1. Why this exists

The user-side agent (`talium-sync`) and the batch uploader were rewritten as a
separate Go repo (`../upload`: `talium-sync` client + `talium-uploadd` tus server).
It replaces this repo's Python batch uploader (`src/server/uploader/`, still deployed
in `docker/docker-compose.stack.yml:125` and `docker/docker-compose.demo.yml:134`).

The rewrite's design doc asserts the data contract is preserved
(`../upload/docs/design/uploader-design.md:42`). It is not. Four breaks were found,
and one of them means GitHub repository identity is **never captured at all**.

### 1.1 The four breaks

| # | Break | Effect | Fixed in |
|---|---|---|---|
| 1 | `upload_id` → `endpoint_id` (required field rename) | Collector's Pydantic model hard-fails on every manifest; the whole sweep dies | Collector (Part B) |
| 2 | Repo identity mechanism replaced **and non-functional** | `repo` is `null` on every file; repo identity degrades to `local:<cwd>` forever | Uploader (Part A) |
| 3 | Manifest is now multi-vendor; collector is vendor-blind | A Codex file would be fed to the Claude Code normalizer | Collector (Part B) |
| 4 | New `TALIUM_S3_PREFIX` base prefix | Collector's hardcoded `uploads/{id}/{key}` join 404s | Collector (Part B) |

### 1.2 Break 2 in detail — this is the important one

**Old mechanism:** the upload script wrote a sidecar `.talium-git-remotes.json`
(`{cwd: raw_remote_url}`) built from a real `git remote get-url origin` on the
developer's machine. The collector read it (`src/collector/claude_code/source_reader.py:149`),
matched each session by its own `cwd`, and set `session_start.git_remote`
(`src/collector/engine.py:98`).

**New mechanism:** per-file `repo`/`branch` fields on each manifest `FileEntry`
(`../upload/internal/manifest/manifest.go:46-47`). The sidecar has **zero references**
anywhere in `../upload`.

**Why it never works:** `buildScan` (`../upload/cmd/talium-sync/run.go:264-266`) does
not have the session's real `cwd`. It reconstructs one from the Claude project
directory slug with `CWDFromSlug` (`../upload/internal/discover/claude.go:153`), a
blind `-` → `/` replace, then runs `git -C` on the result.

Measured on the developer machine this plan was written on:

```
Real cwd (from the JSONL): /Users/anil/workspace/Talium/code-econ
Slug:                      -Users-anil-workspace-Talium-code-econ
CWDFromSlug yields:        /Users/anil/workspace/Talium/code/econ   <- does not exist
```

`git -C` fails → `ResolveSessionGitInfo` fails closed → `repo: null`.
**13 of 13 project slugs on that machine resolve to a non-existent path.** Effective
capture rate: zero. The code comments correctly call the function "LOSSY"; what was
missed is that it fails closed *almost always*, not occasionally.

The encoding is worse than a hyphen problem. Claude Code encodes **both `/` and `.`**
as `-`, so `.claude/worktrees/...` paths are unrecoverable too. Verified:

```
real: /Users/anil/workspace/Talium/agent-guard/.claude/worktrees/flamboyant-easley-cf2002
slug: -Users-anil-workspace-Talium-agent-guard--claude-worktrees-flamboyant-easley-cf2002
                                              ^^ '/' + '.' both became '-'
```

The encoding is also **genuinely ambiguous**, not merely awkward: `/a/b-c` and `/a/b/c`
both encode to `-a-b-c`. No smarter inverse can fix this — a cleverer decoder can only
pick the wrong repo silently. Wrong repo attribution corrupts econ rollups; a null does
not. **So the slug must stop being treated as a source of truth entirely.**

### 1.3 Why the collector cannot fix break 2

The old sidecar worked because `git` ran on the developer's machine at upload time. The
collector runs server-side, where that filesystem no longer exists. If the client sends
`repo: null`, the information exists nowhere downstream. **Break 2 must be fixed in
`../upload`.** Breaks 1, 3 and 4 are compatibility work in the collector.

### 1.4 Decisions already taken (do not re-litigate)

- **Repo travels in the manifest per-file.** Raw JSONL files stay byte-identical to the
  vendor's. No Talium record is injected into session content.
- **The client reads the real `cwd` out of the JSONL** instead of inverting the slug.
  This is a deliberate, scoped exception to the "content-blind discovery" principle
  (`../upload` handoff §13(i)); §6.2 records the justification.
- **Active-session detection is in scope** — it shares the same broken slug assumption.

---

## 2. Scope

**In scope**
- `../upload`: real-cwd resolution, repo/branch capture, active-session detection,
  manifest delta condition, `repo_url` field.
- `code-econ`: manifest compat layer (both uploader generations), vendor filtering,
  base-prefix-safe key joins, repo plumbed to `session_start.git_remote`.

**Out of scope**
- Retiring `src/server/uploader/` or changing the docker deployments. The Python
  uploader keeps working; the collector must accept **both** manifest generations.
- Codex normalization. Codex files are recognised and skipped with a log, not parsed.
- Any change to `tus` upload, auth, enrollment, or the `plan` endpoint.
- Backfilling repo identity for sessions already collected.

---

## Part A — `../upload` (Go client)

### A1. Make the slug encoder correct, and delete the decoder

**File:** `internal/discover/claude.go:140-155`

`SlugFromCWD` currently replaces only `/`. Claude Code also replaces `.`. Fix:

```go
// SlugFromCWD converts an absolute cwd into a Claude project slug. Claude Code
// encodes BOTH '/' and '.' as '-', so ~/.claude/worktrees paths collapse two
// different characters into one -- which is why the reverse is not recoverable
// and why no CWDFromSlug exists (see §1.2 of the integration plan).
func SlugFromCWD(cwd string) string {
	return strings.NewReplacer("/", "-", ".", "-").Replace(cwd)
}
```

**Delete `CWDFromSlug` entirely** (`claude.go:146-155`). After A4 and A5 it has no
callers. It is a lossy trap and keeping it invites reuse. Remove its tests in
`internal/discover/claude_test.go` (the round-trip tests at `:212` and `:227` assert a
property that is false in general and must not be re-asserted).

> **Verification note for the evaluator:** the `.` → `-` rule was confirmed against two
> real slug directories, not inferred. If a future corpus shows another character being
> encoded (e.g. `_`), this function is the single place to update.

### A2. Read the real session metadata from the JSONL

**New file:** `internal/discover/sessionmeta.go`

```go
// SessionMeta is the per-session context Claude Code records inside its own
// JSONL but does not encode recoverably in the directory slug.
type SessionMeta struct {
	CWD       string
	GitBranch string
	SessionID string
}

// ReadSessionMeta returns the first record in path that carries a non-empty
// "cwd", or a zero SessionMeta if none does. Best-effort: every failure mode
// (missing file, unreadable, malformed JSON, no cwd) yields the zero value and
// a nil error -- resolving session context must never fail an upload.
func ReadSessionMeta(path string) SessionMeta
```

**Required implementation properties:**

1. **Bounded read.** Read at most `maxSessionMetaBytes = 1 << 20` (1 MiB) via
   `io.LimitReader` + `io.ReadAll`. Session files reach hundreds of MiB; never read
   the whole file.
2. **Do not use `bufio.Scanner`.** A single Claude Code JSONL line (an attachment
   record) can exceed `bufio.MaxScanTokenSize` (64 KiB), and `Scanner` aborts the whole
   scan on `ErrTooLong` rather than skipping the line. Split the limited buffer on
   `'\n'` and parse each complete line; discard the final partial line.
3. **Skip unparseable lines**, do not abort — the head of the file may contain records
   this struct does not model.
4. Unmarshal into a struct reading exactly three fields: `cwd`, `gitBranch`, `sessionId`.
   Nothing else is parsed, logged, or retained (consistent with the handoff §4
   never-list posture).
5. Return on the **first record with a non-empty `cwd`**.
6. **Only ever called on files that already survived `exclude.Matcher`.** `buildScan`
   operates on `WalkClaude`'s output, which has already enforced the hard never-list
   (`../upload/.claude/CLAUDE.md` "Non-negotiables"; handoff §4). `ReadSessionMeta`
   must not be given an arbitrary path, and must not be used to widen what gets read.

**Why "first record" is wrong and "first record *with a cwd*" is right:** the first
lines of a real session file are `queue-operation` records that carry no `cwd`:

```
line 1: type='queue-operation'  cwd=None
line 2: type='queue-operation'  cwd=None
line 3: type='user'             cwd='/Users/anil/workspace/Talium/code-econ'  gitBranch='feat/synthetic-org-data'
```

**Empirical basis for the 1 MiB cap** — measured across 73 real session files:

```
files=73   cwd_found=73   never=0
max line index  = 3
max byte offset = 8402
p95 line index  = 3
```

`cwd` appears within the first 3 lines and 8.4 KB in 100% of cases. 1 MiB is ~125×
headroom.

### A3. Resolve git at the real cwd, and carry the raw remote URL

**File:** `internal/discover/identity.go:93-126`

`ResolveSessionGitInfo(cwd)` keeps its signature and logic — it now simply receives a
**real** cwd rather than a fabricated one. Two changes:

**(a) Add the raw remote URL.**

```go
type SessionGitInfo struct {
	Repo    *string // normalized "owner/repo" (unchanged, still populated)
	RepoURL *string // NEW: the raw `git remote get-url origin` value, trimmed
	Branch  *string
}
```

Populate `RepoURL` with the trimmed raw output **before** `normalizeRepoURL` runs;
populate `Repo` exactly as today.

**Why both:** `normalizeRepoURL` (`identity.go:157`) drops the host via
`lastTwoSegments`, so a GitLab remote arrives as bare `owner/repo` and this repo's
`normalize_remote` (`src/common/git_ident.py:50`) then stamps it `github:` — a
wrong-but-plausible id, worse than a missing one. The raw URL preserves
`git:<host>/<owner>/<repo>`.

**Why not just repurpose `repo` to hold the URL:** the manifest's own evolution rule is
additive-only, and "changing what an existing field means" is explicitly a breaking
change (`uploader-design.md:174`). Adding a field respects that rule and costs nothing.

**(b) Prefer the JSONL's `gitBranch` over `git rev-parse`.** `ResolveSessionGitInfo`
currently reads `rev-parse --abbrev-ref HEAD`, which reports the branch **at upload
time** — not the branch the session actually ran on. The JSONL records `gitBranch` per
record, which is the branch during the session. In A4, set `Branch` from
`SessionMeta.GitBranch` when non-empty, falling back to `ResolveSessionGitInfo`'s
`rev-parse` value only when the JSONL had none.

### A4. Rewrite `buildScan` around real cwd, with per-slug caching

**File:** `cmd/talium-sync/run.go:255-279`. Call site is `run.go:199`; `root` is already
in scope there (`run.go:131`).

New signature:

```go
func buildScan(root string, files []discover.ClaudeFile, activeSessionIDs map[string]bool) []syncplan.ScanEntry
```

**Key the context by session, not by slug.** A slug is **not** guaranteed to map to one
working directory: because the encoder flattens both `/` and `.` to `-`, two real
directories can collide onto one slug (`/a/b-c` and `/a/b/c` both give `-a-b-c`), and
Claude Code then writes both sessions' transcripts into the *same* directory. Caching
resolved repo per slug would attribute one directory's sessions to the other's repo —
the same mis-attribution this change exists to eliminate, moved one step later.

So the context map is keyed by **session directory** — `<slug>/<sessionId>` — which is
exact, and the `git` calls are cached separately by **cwd**, which preserves the
one-call-per-repo saving.

**Algorithm (two passes — this ordering matters):**

**Pass 1 — build `map[sessionDir]sessionContext`.** For each file whose rel matches the
top-level `<slug>/<sessionId>.jsonl` pattern (use the existing `sessionIDFromRel`,
`run.go:290`, which already returns `""` for anything deeper):

- Absolute path: `filepath.Join(root, filepath.FromSlash(f.Rel))` — the same pattern
  already used at `run.go:234`.
- `meta := discover.ReadSessionMeta(path)`
- If `meta.CWD == ""`, skip this file as a context source.
- Resolve git for `meta.CWD`, **memoised in a `map[string]SessionGitInfo` keyed by
  cwd** — `ResolveSessionGitInfo` shells out to `git` twice per call, so without this a
  1,000-file scan spawns ~2,000 git processes. Sessions from the same repo share one
  entry regardless of which slug they live under.
- Store `{repo, repoURL, branch}` under `strings.TrimSuffix(f.Rel, ".jsonl")` (i.e.
  `<slug>/<sessionId>`), with `branch` taken from `meta.GitBranch` when non-empty
  (per A3(b)).

**Pass 2 — emit a `ScanEntry` per file**, looking up its owning session directory:

- `<slug>/<sessionId>.jsonl` → `strings.TrimSuffix(rel, ".jsonl")`
- `<slug>/<sessionId>/subagents/...` → the first two path segments

A file whose owning session directory has no entry gets `nil` repo/branch.

**Subagent inheritance:** files under `<slug>/<sessionId>/subagents/...` have no `cwd`
of their own and inherit from their parent session, which the first two path segments
identify exactly. This is correct — a subagent runs in its parent's working directory.
Do not attempt to resolve them independently, and do not fall back to "any session
under this slug," which is what reintroduces the collision bug.

**Failure posture:** a slug with no resolvable context yields `nil` repo/branch on all
its files, exactly as today. Never fail the run.

### A5. Fix active-session detection by matching on session ID

**File:** `internal/discover/active.go:67`

`ActiveClaudeRels` builds `SlugFromCWD(info.CWD) + "/" + info.SessionID + ".jsonl"` and
matches it against discovered rels. Even with A1's corrected encoder this is fragile —
it depends on this repo's encoder exactly tracking a vendor's undocumented one, forever.

**There is a robust alternative that needs no encoding at all.** Session IDs are UUIDs,
globally unique. Match on those and ignore the slug:

- Rename `ActiveClaudeRels` → `ActiveClaudeSessionIDs`, returning
  `map[string]bool` of **session IDs** (`info.SessionID`), dropping the `SlugFromCWD`
  call entirely.
- In `buildScan` pass 2, set `Active: activeSessionIDs[sessionIDFromRel(f.Rel)]`,
  guarding against the empty string so subagent files (which have no session id) never
  match.
- Update the caller at `run.go:149`.

After this change `SlugFromCWD` has no non-test callers. **Keep it** (A1 still applies)
— it is the documented statement of the vendor's encoding and `active_test.go:55` uses
it to construct fixtures. It must simply never be inverted.

### A6. Carry `repo_url` through the wire and into the manifest

Five structs, all additive. The existing `repo`/`branch` plumbing already works
end to end — this only adds one field alongside it.

| File | Change |
|---|---|
| `internal/syncplan/client.go:30-38` | `ScanEntry`: add `RepoURL *string` |
| `internal/syncplan/client.go:120-128` | `commitScanEntryWire`: add `RepoURL *string \`json:"repo_url"\`` |
| `internal/httpapi/types.go:53-60` | `commitScanEntry`: add `RepoURL *string \`json:"repo_url"\`` |
| `internal/httpapi/routes.go:164-171` | map `RepoURL` into `staging.ScanEntry` |
| `internal/staging/commit.go:16-24` | `ScanEntry`: add `RepoURL *string` |
| `internal/staging/commit.go:138-149` | set `RepoURL` on the `manifest.FileEntry` |
| `internal/manifest/manifest.go:38-50` | `FileEntry`: add `RepoURL *string \`json:"repo_url"\`` |
| `api/manifest.schema.json` | add `repo_url` (nullable string) to the file-entry properties; document `repo` as normalized `owner/repo` and `repo_url` as the raw remote |

> **Note on the struct conversions:** `client.go:102`, `:161` and `:166` use direct
> struct conversions (`commitScanEntryWire(e)`) that rely on **identical field sets in
> identical order**. Adding `RepoURL` to one side and not the other, or in a different
> position, breaks compilation. Add it in the same position in both.

### A7. Make a repo/branch change count as a manifest delta ⚠️

**File:** `internal/staging/commit.go:100-156`

**This is the subtlest item in the plan and the easiest to miss.** `Commit` writes the
manifest **only** when `anyFileDelta || declaredChanged` (`commit.go:159`), and
`anyFileDelta` is currently derived from `bytesChanged || stateChanged` alone.

Consequence if unfixed: after the client is fixed, an endpoint whose files have not
changed **never rewrites its manifest**, so the newly-resolved repo values never reach
S3. The fix would appear to work for brand-new sessions and silently do nothing for
every existing one.

Add a metadata comparison:

```go
metaChanged := !existed ||
	!strPtrEqual(prior.Repo, entry.Repo) ||
	!strPtrEqual(prior.RepoURL, entry.RepoURL) ||
	!strPtrEqual(prior.Branch, entry.Branch) ||
	prior.SessionID != entry.SessionID

if bytesChanged || stateChanged || metaChanged {
	anyFileDelta = true
}
```

`strPtrEqual` already exists at `commit.go:217`.

**This is also the backfill mechanism:** on the first run after deployment, every
already-uploaded file gets a non-nil repo for the first time, `metaChanged` fires once,
and the manifest is rewritten with correct identity for the whole history. It converges
— the second run sees equal values and writes nothing.

**Do not** let this reintroduce a write on every run. `metaChanged` must compare
values, never timestamps.

**Existence guard interaction:** the guard at `commit.go:117-130` only runs for
`bytesChanged` files. A meta-only change must **not** trigger a `HeadObject` — the
object is already known present from a prior run. Keep the guard's condition exactly as
it is.

---

## Part B — `code-econ` (Python collector)

### B1. Move the manifest contract into `src/common/`

Today `src/collector/claude_code/source_reader.py:34` and `src/collector/engine.py:32`
import their wire types from `server.uploader.models` — the collector depends on the
*old uploader's* module for a contract that now has two producers.

**Create `src/common/manifest.py`** holding `DeclaredFields`, `ManifestFileEntry`, and
`Manifest`, moved from `src/server/uploader/models.py`. `UploadResponse` stays in
`server/uploader/models.py`, which imports the three moved types from
`common.manifest`. Update both collector imports.

Per `CLAUDE.md`, do not leave re-export shims behind — move the definitions and fix the
importers.

### B2. Accept both manifest generations

**File:** `src/common/manifest.py`

```python
class Manifest(BaseModel):
    upload_id: str | None = None    # old Python uploader
    endpoint_id: str | None = None  # new Go uploader
    received_ts: str
    source: str = "batch"
    declared: DeclaredFields
    producer_id: str
    file_count: int
    files: list[ManifestFileEntry]
    uploader_version: str
    vendors: list[str] = []         # new uploader only

    @model_validator(mode="after")
    def _exactly_one_id(self) -> "Manifest":
        if not (self.upload_id or self.endpoint_id):
            raise ValueError("manifest carries neither upload_id nor endpoint_id")
        return self

    @property
    def scope_id(self) -> str:
        """The per-endpoint/per-upload identifier, whichever generation wrote this."""
        return self.endpoint_id or self.upload_id
```

`ManifestFileEntry` gains every new field, all optional so old manifests still validate:

```python
class ManifestFileEntry(BaseModel):
    key: str
    rel: str
    bytes: int
    sha256: str | None = None
    vendor: str | None = None       # None => old uploader => claude_code
    session_id: str | None = None
    repo: str | None = None         # normalized "owner/repo"
    repo_url: str | None = None     # raw remote URL; preferred over repo
    branch: str | None = None
    state: str | None = None        # "live" | "stale"
    active: bool = False
    mtime: str | None = None
```

`DeclaredFields` keeps `vendor: str = "claude_code"` for old-manifest compatibility, but
**nothing may read it** — the new generation carries vendor per file. Add a comment
saying so.

### B3. Make key joins base-prefix-safe

**Files:** `src/collector/engine.py:116-124`, `src/collector/claude_code/source_reader.py`

The collector currently reconstructs read keys as `f"uploads/{upload_id}/{key}"`
(`source_reader.py:105`, `:112`, `:140`, `:159`). With `TALIUM_S3_PREFIX` set, the real
key is `<base>uploads/<endpoint_id>/<key>` and every read 404s.

**Do not try to reproduce the prefix.** `_discover_manifests` already holds the
manifest's own full key. Derive the prefix by stripping the filename — it is correct by
construction under any base prefix, present or future:

```python
@dataclass
class ManifestRef:
    manifest: Manifest
    prefix: str  # everything before "manifest.json", e.g. "cc/logs/uploads/ep-1/"

def _discover_manifests(source_store, scope: str) -> list[ManifestRef]:
    refs = []
    for key in source_store.list_prefix(scope):
        if not key.endswith("manifest.json"):
            continue
        with source_store.open(key, 0, None) as f:
            content = f.read()
        refs.append(ManifestRef(
            manifest=Manifest.model_validate_json(content),
            prefix=key[: -len("manifest.json")],
        ))
    return refs
```

Every read becomes `ref.prefix + entry.key`. **Remove all four hardcoded
`uploads/{...}/` joins.** `--scope` (`src/collector/cli_collect.py:26`) stays as the
sweep root and must be set to `<base>uploads/` when a base prefix is configured;
document this in `config/collector.yaml`.

### B4. Filter by vendor

**File:** `src/collector/claude_code/source_reader.py:131-144`

`group()` takes **every** `.jsonl` in the manifest and the reader hardcodes
`vendor = "claude_code"` (`:126`). Against a multi-vendor manifest a Codex transcript
would be handed to the Claude Code normalizer.

In `group()`, skip any entry whose `vendor` is set and is not `"claude_code"`.
`vendor is None` means an old-generation manifest and must be treated as
`claude_code`. Log skipped non-Claude files once per manifest at INFO with the vendor
name and count — this is the signal that Codex support is needed, and it should be
visible rather than silent.

### B5. Replace the sidecar with per-file repo

**File:** `src/collector/claude_code/source_reader.py`

- **Delete** `read_git_remotes` (`:149-169`) and `GIT_REMOTES_MANIFEST_REL` (`:44`).
- Add `repo: str | None` to `SessionGroup` (`:47-55`), populated in `group()` from the
  file entry: **`entry.repo_url or entry.repo`** — prefer the raw URL so
  `normalize_remote` can preserve a non-GitHub host (§A3(a)).
- Do **not** plumb `branch`. `session_start.git_branch` is already derived from the
  vendor's own `gitBranch` record (`src/collector/normalizer/claude_code.py:984`).
  The manifest's `branch` is redundant for the collector; leave it unread.

**File:** `src/collector/engine.py`

- Replace `_apply_git_remote(nsession, git_remotes)` (`:98-113`) with
  `_apply_git_remote(nsession, repo: str | None)`. It keeps the same guard structure —
  no-op on empty events, only touches `events[0]` when that event is `session_start` —
  but assigns `session_start.payload["git_remote"] = repo` directly instead of doing a
  cwd lookup.
- Remove the `git_remotes = reader.read_git_remotes(manifest)` call (`:183`).
- At `:195`, pass `group.repo`.

Subagent sessions inherit the parent group's repo, which is correct and matches A4's
subagent inheritance.

**Do not branch on `state`.** A `stale` file is one that did not change since the last
manifest write — that is **not** the same as "already collected". A file uploaded
before the collector first ran would be `stale` and would be skipped forever. The
checkpoint identity (`src/collector/store.py:114`, size + head hash) is the
authoritative "have I processed this" signal and already handles re-runs correctly.
Leave `state` unread and add a comment recording this reasoning.

### B6. Update the event-contract documentation

**File:** `src/common/events/descriptions.py:166`

The `git_remote` description describes the sidecar mechanism, which no longer exists.
Rewrite it to say the value comes from the upload manifest's per-file `repo_url`/`repo`,
captured by `talium-sync` via a real `git remote get-url origin` at the session's real
working directory.

**File:** `src/common/events/__init__.py:30`

Update the `3.2.0` comment block the same way.

**Do not bump `SCHEMA_VERSION`.** The payload shape is unchanged and the field's meaning
is unchanged ("the cwd's real git remote"); only its provenance changed. That is neither
a new field nor a retyped one under the rules at `src/common/events/__init__.py:12-20`.

**Then regenerate the committed artifacts**, because the descriptions are part of the
contract and guarded by tests (`tests/collector/normalizer/test_shape_contract.py:166`):

```bash
python -m common.events.cli_publish_payload
```

This rewrites `src/common/events/normalized_schema.json` and
`src/common/events/payload.json`. Both must be committed.

### B7. Update module docs

- `docs/modules/batch-uploader.md` — document both manifest generations, the
  `endpoint_id`/`upload_id` split, the per-file vendor/repo/state fields, and the base
  prefix.
- `docs/modules/source-adapter.md` — replace the sidecar description with per-file repo.

---

## 3. Test plan

### 3.1 `../upload` (Go)

| Test | File | Asserts |
|---|---|---|
| `TestReadSessionMeta_SkipsRecordsWithoutCWD` | `internal/discover/sessionmeta_test.go` (new) | Two leading `queue-operation` lines then a `user` line ⇒ returns the third line's cwd/gitBranch |
| `TestReadSessionMeta_HandlesOversizedLines` | same | A line > 64 KiB before the cwd line is skipped, not fatal (guards the `bufio.Scanner` trap) |
| `TestReadSessionMeta_BoundedRead` | same | A file whose cwd appears only after 1 MiB returns the zero value and does not read to EOF |
| `TestReadSessionMeta_MissingOrMalformed` | same | Missing file / invalid JSON / no cwd ⇒ zero value, nil error |
| `TestSlugFromCWD_EncodesDotsAndSlashes` | `internal/discover/claude_test.go` | `/a/b-c/.claude/x` ⇒ `-a-b-c--claude-x` |
| *(removed)* | `internal/discover/claude_test.go:212,227` | Delete the `CWDFromSlug` round-trip tests |
| `TestBuildScan_ResolvesRepoFromJSONLCwd` | `cmd/talium-sync/main_test.go` | Real temp git repo + slug dir whose name does **not** invert to it ⇒ repo/repoURL populated |
| `TestBuildScan_SubagentsInheritParentSession` | same | `<slug>/<id>/subagents/agent-x.jsonl` gets **that session's** repo, not another session's under the same slug |
| `TestBuildScan_ResolvesGitOncePerCWD` | same | N sessions sharing one cwd ⇒ exactly one git resolution (inject a counting resolver) |
| `TestBuildScan_CollidingSlugKeepsSessionsDistinct` | same | Two sessions in one slug dir with **different** `cwd` values ⇒ each gets its own repo; neither inherits the other's |
| `TestBuildScan_ActiveMatchesBySessionID` | same | Active flag set from session id, with a slug that would not round-trip; empty session id never matches |
| `TestCommit_MetaOnlyChangeWritesManifest` | `internal/staging/commit_test.go` | Identical bytes, `repo` nil → non-nil ⇒ `Written: true` |
| `TestCommit_IdenticalScanStillWritesNothing` | same | Same bytes **and** same repo/branch ⇒ `Written: false` (guards against write-every-run) |
| `TestCommit_MetaOnlyChangeSkipsExistsCheck` | same | Meta-only change performs no `HeadObject` |

**The critical regression fixture:** at least one test must use a slug directory whose
name does **not** invert to the real path (e.g. real dir `repo-name`, slug
`-tmp-x-repo-name`). A fixture using a hyphen-free path would pass even with the old
broken code and prove nothing.

### 3.2 `code-econ` (Python)

| Test | File | Asserts |
|---|---|---|
| `test_manifest_accepts_endpoint_id` | `tests/common/test_manifest.py` (new) | New-generation manifest validates; `scope_id == endpoint_id` |
| `test_manifest_accepts_legacy_upload_id` | same | Old manifest validates; `scope_id == upload_id` |
| `test_manifest_rejects_neither_id` | same | Raises `ValidationError` |
| `test_manifest_ignores_unknown_fields` | same | An unknown future field does not fail validation |
| `test_group_skips_non_claude_vendor` | `tests/collector/claude_code/test_source_reader.py` | A `codex` entry is excluded; a `None`-vendor entry is included |
| `test_group_carries_repo_preferring_repo_url` | same | `repo_url` wins over `repo`; falls back when absent; `None` when both absent |
| `test_reads_resolve_under_a_base_prefix` | same | Manifest at `cc/logs/uploads/ep-1/manifest.json` ⇒ files read from `cc/logs/uploads/ep-1/<key>` |
| `test_apply_git_remote_sets_session_start` | `tests/collector/test_engine.py` | Repo lands on `session_start.git_remote` |
| `test_apply_git_remote_noop_when_absent` | same | `None` ⇒ field stays `None`, no crash |
| `test_subagent_sessions_inherit_repo` | same | Subagent `NormalizedSession` gets the parent's remote |
| `test_stale_state_is_still_collected` | same | A `state: "stale"` file with no checkpoint **is** processed |
| `test_end_to_end_new_generation_manifest` | same | Full `run_once` over a new-format manifest fixture ⇒ events emitted, checkpoint committed |
| `test_end_to_end_legacy_manifest` | same | Same over an old-format fixture — proves both generations work |

**Update existing tests:** `tests/collector/claude_code/test_source_reader.py:14` builds
manifests via `_make_manifest(..., upload_id="u1")`. Parametrize the helper over both
generations so every existing case runs against both.

**Fixtures:** add a new-generation manifest under `tests/fixtures/uploader/`, mirroring
the existing old-generation fixture.

### 3.3 Commands

```bash
cd ../upload && ./scripts/verify.sh
```

`scripts/verify.sh` is that repo's standard gate (`go build ./... && go vet ./... &&
go test ./...` plus the round-trip and determinism checks) and must be green after each
phase. `scripts/test_all.sh` adds `-race`, `staticcheck`, coverage gates and the
MinIO/tusd integration tests — run it once before opening the PR.

```bash
python -m pytest tests/collector tests/common tests/server/uploader -q
```

---

## 4. Acceptance criteria

The evaluation session should verify each of these independently.

**Functional**

1. A new-generation manifest (`endpoint_id`, no `upload_id`) is parsed by the collector
   without error. *This is the one that fails today — confirm it fails before the change.*
2. A legacy manifest (`upload_id`, no `endpoint_id`) still parses and still collects.
3. `talium-sync` run against a Claude project directory whose real path contains a
   hyphen produces a manifest whose `repo`/`repo_url` are **non-null and correct**.
4. The same, for a path containing a `.` segment (a `.claude/worktrees/...` session).
5. Collected sessions from such an upload carry a non-null `session_start.git_remote`.
6. `graph_fold.repo_info` resolves those sessions to `github:owner/repo` with
   `repo_id_provenance == "remote"`, **not** `local:<cwd>`.
7. A non-GitHub remote (e.g. a GitLab URL) resolves to `git:<host>/<owner>/<repo>`,
   not `github:...` — this proves `repo_url` is being preferred over `repo`.
8. A manifest containing a `codex` file entry collects the Claude files and skips the
   Codex file with a log line, without error.
9. With a base prefix configured, all object reads resolve; no 404s.

**Regression / convergence**

10. A run where nothing changed writes no manifest (`Written: false`) — the
    `metaChanged` addition must not cause a write on every run.
11. The first run after deploying the client fix **does** rewrite the manifest for
    pre-existing, unchanged files, backfilling their repo (criterion 10 then holds from
    the second run onward).
12. Existing collector checkpoints are **not** invalidated: `rel` semantics are
    unchanged, so `logical_key` (`org/user/rel`, `engine.py:185`) is stable and
    previously-collected sessions are not reprocessed. Verify with a checkpoint store
    populated before the change.
13. Raw JSONL objects in S3 are byte-identical to the developer's local files.
14. `SCHEMA_VERSION` is unchanged and
    `tests/collector/normalizer/test_shape_contract.py` passes with regenerated
    artifacts committed.

**Hygiene**

15. `CWDFromSlug` no longer exists anywhere in `../upload`.
16. No `uploads/{...}/` string join remains in the collector.
17. `read_git_remotes`, `GIT_REMOTES_MANIFEST_REL` and all references are gone.
18. `grep -rn "server.uploader.models" src/collector/` returns nothing.
19. Both test suites pass; `go vet` is clean.

---

## 5. Risks and known gaps

| Risk | Mitigation |
|---|---|
| **`metaChanged` causes a manifest write on every run** if implemented as anything other than a value comparison | Acceptance criterion 10 is the specific guard |
| **Fixtures that use hyphen-free paths** would pass against the broken code and prove nothing | §3.1 mandates a non-inverting slug fixture |
| **`bufio.Scanner` token limit** silently truncating the metadata scan | Explicitly forbidden in A2; dedicated test |
| **Struct-conversion breakage** in `syncplan/client.go` from adding `RepoURL` in mismatched positions | Called out in A6; `go build` catches it |
| The vendor's slug encoding may cover characters beyond `/` and `.` | A1 is the single point of change; only used forward, never inverted |
| **Two real directories colliding onto one slug** (`/a/b-c` and `/a/b/c`) sharing a transcript directory | §A4 keys context by `<slug>/<sessionId>`, never by slug; dedicated test in §3.1 |
| Sessions whose working directory was **deleted or moved** since the session ran still yield `repo: null` — `git` needs the directory to exist | Inherent; unchanged from the old sidecar, which had the same constraint |
| `.claude.json`-style vendor format drift renaming `cwd`/`gitBranch` | `ReadSessionMeta` fails closed to null repo — degraded, not broken. Prior drift incident is on record for this vendor |

**Known gap, deliberately not fixed:** sessions already collected keep their
`local:<cwd>` repo identity. Re-resolving them would require reprocessing with
checkpoints cleared, which is out of scope. Criterion 11 only backfills the *manifest*,
not already-emitted events.

---

## 6. Reference — verified facts

Every claim below was verified against the code or measured on a real machine while
writing this plan. An implementer should not need to re-derive them.

**6.1 Already compatible — do not "fix"**

- `rel` semantics are **identical** across generations: relative to `~/.claude/projects`,
  forward-slash (`../upload/internal/discover/claude.go:110-114` vs
  `src/server/uploader/unpack.py:26`). Checkpoints therefore survive.
- The storage key layout change (`raw/projects/<rel>` → `claude/raw/<rel>`) is harmless:
  the collector joins whatever `key` the manifest supplies.
- The manifest location (`uploads/<id>/manifest.json`) is unchanged, so the
  `endswith("manifest.json")` sweep still finds it.
- The `repo`/`branch` wire path **already works** end to end — client `ScanEntry` →
  commit wire → `staging.ScanEntry` → `manifest.FileEntry` (`commit.go:138-149`). Only
  the client's *resolution* of the values is broken. No protocol change is required for
  `repo`/`branch`; `repo_url` is the only additive field.
- `src/common/git_ident.py:50` already accepts **both** a raw remote URL and a bare
  `owner/repo`, so sending the raw URL needs no collector-side parsing change.
- `session_start.git_branch` is already populated from the vendor's own `gitBranch`
  record (`src/collector/normalizer/claude_code.py:984`).
- New manifest fields are ignored by Pydantic's default `extra="ignore"`, so the
  wire evolution really is additive apart from the `upload_id` rename.

**6.2 The content-blind exception**

`../upload`'s handoff §13(i) commits to discovery that never parses JSONL content.
A2 breaks that deliberately, for one bounded read of one field. The justification: the
slug encoding is lossy *and ambiguous* (`/a/b-c` and `/a/b/c` both encode to `-a-b-c`),
so no decoder can be correct — it can only be silently wrong. The cost is ≤1 MiB read
from a file the client is about to upload anyway. Record this in the `../upload` design
doc alongside the §13(i) statement so the exception is discoverable.

**6.3 Measurements**

Slug reversal, all 13 project directories on the reference machine — `CWDFromSlug`
output vs. reality:

```
NIL REPO  | -Users-anil-workspace-Talium-code-econ -> /Users/anil/workspace/Talium/code/econ (does not exist)
NIL REPO  | -Users-anil-workspace-Talium-agent-guard -> /Users/anil/workspace/Talium/agent/guard (does not exist)
NIL REPO  | -Users-anil-workspace-Talium-talium-infra -> /Users/anil/workspace/Talium/talium/infra (does not exist)
... 13 of 13 resolve to a non-existent path or a non-repo
```

Metadata scan depth, 73 real session files: `cwd` found in **73/73**, max line index
**3**, max byte offset **8402**, p95 line index **3**.

Manifest parse, new format against the current collector model:

```
PARSE FAILED: 1 validation error for Manifest
upload_id  Field required [type=missing]
```

---

## 7. Suggested commit sequence

Each step should build and test green on its own.

**`../upload`**
1. `fix(discover): encode '.' in slugs and drop the lossy CWDFromSlug`
2. `feat(discover): read real cwd/gitBranch from session JSONL`
3. `fix(discover): match active sessions by id, not slug`
4. `feat(manifest): carry raw remote URL as repo_url`
5. `fix(client): resolve repo/branch at the session's real cwd`
6. `fix(staging): treat a repo/branch change as a manifest delta`

**`code-econ`**
7. `refactor(common): move the manifest contract out of server/uploader`
8. `feat(collector): accept both manifest generations`
9. `fix(collector): derive object keys from the manifest's own prefix`
10. `feat(collector): filter manifest files by vendor`
11. `feat(collector): take git_remote from the manifest's per-file repo`
12. `docs(events): git_remote now comes from the upload manifest`
