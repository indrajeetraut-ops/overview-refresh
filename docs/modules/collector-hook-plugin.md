# Collector Hook Plugin — Design (client-side, `TBD src/`)

**Status:** Design notes (2026-09-03). **Not built.** This is the "(TBD) own design doc" that
`docs/modules/git-source.md` §6.4 and `docs/plan/2026-08-25-git-integration-implementation.md`
Part C anticipate. It records the plugin's scope, the Claude Code hook facts it must be built
around, and the constraints that a naïve "one hook ships the transcript" design gets wrong.
**Parent architecture:** `docs/design/system-architecture.md` (§6.4 collector posture, §18.1 auth).
**Consumers of its output:** `docs/modules/git-source.md` §6.4 (repo identity, trailers, shas),
`docs/modules/pr-work-classification.md` §4.2 (trailer > sha > heuristic precedence),
`docs/modules/batch-uploader.md` (the upload contract), `docs/modules/source-adapter.md` (the
collector sweep the plugin feeds).
**Verification note:** the Claude Code hook contract below (events, reasons, `transcript_path`
semantics, the sub-agent transcript layout) was checked against `code.claude.com/docs/en/hooks`
and against real on-disk sessions in `~/.claude/projects` on 2026-09-03. Claude Code internals
drift — re-verify the flagged items in §7 before building.

---

## 1. In a Nutshell

One installable plugin on the developer's machine, carrying **two hook families** that share one
identity and one credential:

1. **Claude Code lifecycle hooks** (`SessionStart`, `Stop`, `SessionEnd`) — trigger an
   **authenticated, per-session JSONL upload** to the uploader, and capture real repository
   identity at session start. This subsumes today's manual `utils/upload_claude_sessions.sh`.
2. **Git hooks** (`prepare-commit-msg`, `post-commit`) — write the `Talium-Session:` **commit
   trailer** and record commit shas, the rewrite-proof correlation key git-source §6.4 consumes.

The two converge because both originate at the same machine, for the same session, and both need
the same session→repo→user→credential binding. Building them as one plugin avoids two enrollment
paths, two credential stores, and two ways to learn "which session am I in."

**The load-bearing correction this doc exists to record:** the JSONL uploader cannot be "a hook
that ships its `transcript_path`." There is no reliable end-of-session event, and `transcript_path`
is the *main* transcript only — sub-agent streams live in a separate nested tree the hook is never
handed. Completeness comes from **server-side idempotent folding + a safety-net sweep**, not from
firing at the perfect moment. §3 and §4 are the whole reason for the doc.

---

## 2. Why one plugin, not two components

| Need | Hook family | Shared dependency |
|---|---|---|
| Ship the session's JSONL (the fidelity half of the hybrid) | CC lifecycle | credential, org/user identity, repo id |
| Learn the real repo (remote, not path-guess) | CC `SessionStart` | git remote resolution |
| Rewrite-proof PR↔session correlation | git `prepare-commit-msg` | the current `session_id` |
| Record commit shas the session created | git `post-commit` | the current `session_id` |
| Authenticate ingest (close the unauthenticated upload surface) | both | one enrolled key |

Every row needs "which session, which repo, which user, which credential." That binding is the
plugin's core; the hooks are thin shells over it. OTel is **out of scope** — Claude Code emits it
natively straight to ingest; the plugin owns only the JSONL half.

---

## 3. Claude Code hook facts the design must respect

### 3.1 There is no natural "end of session"

`SessionEnd` exists, but it fires only on **teardown actions**, with documented `reason` values:
`clear`, `resume`, `logout`, `prompt_input_exit`, `other`. None means "the work finished." The
common terminations — walked away / idle, closed the terminal, laptop slept, `kill`, crash — do
**not** reliably fire it (and `resume` is a save-for-later, i.e. mid-work). The docs also warn that
`transcript_path` **"is written asynchronously and may lag the in-memory conversation."**

**Consequence:** `SessionEnd` is a *best-effort final flush when it happens*, never the primary
trigger and never a guaranteed one. The design cannot depend on "ship the final transcript on
session end."

### 3.2 `transcript_path` is the main transcript only — sub-agents are a separate nested tree

For a session `<stem>`, Claude Code writes:

```
<project>/
  <stem>.jsonl                          ← transcript_path (main conversation only)
  <stem>/subagents/
      agent-<id>.jsonl  + agent-<id>.meta.json     (one pair per sub-agent)
      …
      <agent-stem>/subagents/           ← and it NESTS, recursively
```

Verified on a real session: a single `<stem>.jsonl` alongside a `<stem>/subagents/` directory
holding five `agent-*.jsonl` + `.meta.json` pairs, with a further nested `subagents/` one level
down. The normalizer already encodes exactly this shape:
`src/collector/normalizer/local_loader.py` derives `subagents_dir = session_path.with_suffix("") /
"subagents"`, globs `agent-*.jsonl`, keys each into its parent by `tool_use_id` from the
`.meta.json`, and **recurses**.

Sub-agent token spend is the fidelity signal that differentiates this product. A hook that ships
`transcript_path` alone **drops every sub-agent stream.** The reason the current whole-tree tar in
`utils/upload_claude_sessions.sh` is complete is precisely that it captures the `subagents/` tree
too, regardless of hooks.

`SubagentStop` does carry `agent_id`/`agent_type`, and hooks fire inside sub-agent context — but
whether `SubagentStop`'s `transcript_path` points at the sub-agent's *own* file or the parent's is
**not clearly documented** (§7), and the async write-lag plus nesting make per-`SubagentStop`
single-file upload fragile regardless.

### 3.3 Hooks must not block the session

Lifecycle hooks run synchronously and are time-bounded. A network upload inside the hook adds
latency to the developer's session and can fail it. Every upload path must be **fire-and-forget**:
the hook hands off (spawns a detached process, or drops the session path into a tiny local spool a
detached worker drains) and returns immediately.

### 3.4 The hook command must be dependency-light

Hooks are shell commands that run on the developer's machine. If the upload command needs Python,
every developer needs Python — which reopens the M5 "zero-Python install" packaging problem. The
command should be `curl + tar` (the shape `upload_claude_sessions.sh` already proves) or a small
self-contained binary. This is a hard constraint, not a preference — see §6.

---

## 4. The JSONL uploader design that survives §3

**The upload unit is the session *tree*, not `transcript_path`.** From `transcript_path` the hook
derives the tree deterministically: the file `<stem>.jsonl` **plus** its sibling `<stem>/subagents/**`
(all `agent-*.jsonl`, all `.meta.json`, all nested levels). That is a per-session scoped bundle —
strictly better than re-tarring all of `~/.claude/projects`, and it carries the sub-agent fidelity
§3.2 requires.

**Completeness comes from two mechanisms already in place, not from perfect timing:**

- **Server-side idempotency.** The ingest fold is append-only and recomputes from the full deduped
  blob (`src/server/worker/graph_fold.py`; positional dedup). Re-shipping an unchanged or
  partially-grown tree is free — only net-new records take effect. So the plugin may over-ship
  freely.
- **A safety-net sweep.** Because §3.1 gives no reliable flush and §3.2 files lag, a `SessionStart`
  (and/or periodic) pass re-ships session trees modified within a recent window, catching
  abandoned/killed sessions and late-flushing sub-agent files that no single trigger captured.

**Trigger plan:**

| Event | Action |
|---|---|
| `SessionStart` | resolve repo identity (§5); run the safety-net sweep of recently-modified session trees |
| `Stop` | debounced fire-and-forget upload of the current session tree (near-live arrival) |
| `SessionEnd` | best-effort final flush of the current session tree — taken when it fires, never relied on |

This is, honestly, **"a smarter, authenticated, per-session sweep triggered by hooks"** — not a
clean single-file event stream. The manual whole-directory re-tar is what it eliminates; the sweep
and the server-side dedup are what it keeps.

**No local checkpoint manifest is required** (an explicit scope decision, 2026-09-03): the JSONL
files on disk are the durable source of truth, and server idempotency makes re-shipping cheap. The
only cost forgone is upload bandwidth and a whole-file re-parse server-side, both accepted.

---

## 5. Repository identity and the git hooks (what git-source §6.4 consumes)

This half is unchanged from `git-source.md` §6.4 and is summarized here so the plugin's contract
lives in one place; §6.4 remains the consumer-side authority on degradation.

1. **Real repository identity.** At `SessionStart`, resolve the session's **git remote**
   (`git remote get-url`, `gh` where available), normalize to `github:<owner>/<name>`, and attach
   it to the session's upload declared-fields — replacing the unreliable working-directory
   path-guess behind the `repo_id: None` TODO in `src/collector/normalizer/claude_code.py`.
2. **Commit trailer.** A `prepare-commit-msg` git hook writes `Talium-Session: <session_id>` into
   each commit the session creates. The trailer survives `rebase -i`, `--amend`, squash, and
   cherry-pick where the raw sha does not — the rewrite-proof correlation key
   (`pr-work-classification.md` §4.2, precedence `trailer > sha > heuristic`).
3. **Commit shas.** A `post-commit` hook records the sha reliably (from the hook, not by scraping
   agent shell output).
4. **Metadata seam.** Branch/base-branch, the `gh`-authenticated GitHub login (a strong
   author→user signal, git-source §6.3), and future local metadata ride the same plugin.

The binding "which `session_id` is this git hook running under" is the non-obvious part — a bare
git hook does not know the Claude Code session. The plugin's shared session state (established by
the CC `SessionStart` hook and the plugin's `cwd`→session map) is what supplies it. This is a §7
item to nail down.

---

## 6. Auth and enrollment

The plugin carries the ingest credential, which closes the one unauthenticated ingest surface (the
batch upload path, `src/server/uploader/app.py`) as a side effect of existing:

- **Enrollment once, at install.** The plugin registers (the msging `.../v1/register` flow, or the
  uploader's equivalent) and stores the issued key locally (0600, mirroring the msging client
  binding). For a workplace deployment, registration runs in `enrollment_key` mode
  (`config/msging.yaml` → `server.registration.mode`) with a pre-shared `TALIUM_ENROLL_KEY`.
- **Presented on every upload.** The `curl` upload sends the key; the uploader verifies it, reusing
  the producer-key or org-token pattern the other two ingest paths already use rather than
  inventing a third credential kind.
- **Dependency-light (§3.4).** Enrollment and upload must not pull in a Python runtime.

The plugin does not touch OTel auth (org token, already built) — OTel bypasses the plugin.

---

## 7. Open questions — verify before building

1. **`SubagentStop.transcript_path` semantics.** Does it point to the sub-agent's own
   `agent-*.jsonl`, or the parent's transcript? Docs are unclear (§3.2). The answer decides whether
   sub-agent capture can be event-driven at all, or must stay tree-scoped + sweep (the current
   assumption, which is safe regardless).
2. **`SessionEnd` reliability across `kill`/crash/terminal-close.** Confirm it does *not* fire in
   those paths (§3.1 assumes it does not); the sweep's necessity rests on this.
3. **`Stop` payload fields.** Confirm `session_id`, `transcript_path`, `cwd` are present on `Stop`
   as used in §4, and whether `last_assistant_message` (doc-recommended over reading the lagging
   transcript) is needed here — it likely is not, since we ship the file, not the last message.
4. **Session→cwd binding for git hooks (§5).** How the `prepare-commit-msg`/`post-commit` hooks
   learn the current `session_id` — the plugin's own state file keyed by repo path, refreshed by
   the CC `SessionStart` hook. Prove it survives concurrent sessions in sibling worktrees.
5. **Cross-platform hook command.** Validate the `curl + tar` command (or the binary) on macOS
   (bash 3.2, BSD tar) and Linux; the existing script's `COPYFILE_DISABLE` / empty-array guards are
   prior art.

---

## 8. Status

**Designed-in, not built.** The server side runs today against degraded inputs (path-guessed
`repo_id`, `sha`-only correlation, unauthenticated upload behind a shared Caddy password) and
integrates the plugin's richer, authenticated signal when it lands — a collector-side workstream
(git plan Part C) with no server-side rework required. This doc supersedes the "(TBD)" placeholder
in git-source §6.4 and adds the JSONL-uploader role and the §3 hook constraints that §6.4 did not
cover.
