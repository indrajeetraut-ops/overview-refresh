"""Step 0 of the causal-graph integration (docs/plan/2026-08-29-causal-graph-github-integration.md):
the `session_info` baseline oracle and the per-field differential runner.

The oracle runs today's `EconFold` over normalized sessions to snapshot `session_info` -- the
regression oracle every later step is judged against. The differential runner deep-diffs two
`session_info` snapshots per field path, honoring an allowlist of intended differences, so the
Step-3+ graph-backed extractor can be proven behavior-preserving except where we chose to change it.
"""
