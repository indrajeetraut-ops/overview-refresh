# GitHub delivery fixtures

**Status: synthetic, not recorded.** `docs/plan/2026-08-25-git-integration-implementation.md`
(A3) calls for these to be *recorded* from a live GitHub App installation and sanitized the way
`tests/fixtures/normalizer/recorded/sanitize.py` does for sessions. That needs a live org, a
tunnel, and a human clicking through `docs/manual-testing.md` §6b–6f — none of which an
automated pass can do. These fixtures are hand-authored instead, built to match GitHub's
publicly documented webhook payload shapes as closely as possible, so `map_github.py` (A4) has
something to map against now. **Replace them with real recordings when the manual capture is
next done** — that is the point at which "synthetic" should stop being true.

Each file is `{"event": ..., "action": ..., "payload": {...}}` — the three things
`map_github.map_delivery(event, action, payload, org_id)` needs, mirroring what the webhook
route reads off the request. `golden/<name>.json` holds the expected `MapResult.artifacts` for
`test_map_github.py`'s golden-artifact assertions (git-source.md §11).

No diff text, patch hunks, file contents, review comment bodies, or PR/issue body text appears
in any fixture beyond what's needed to exercise the two things this module parses out of body
text and discards otherwise: the "closes #n" reference and the "This reverts commit `<sha>`"
line (git-source.md §5.3, §5.5, §5.6).
