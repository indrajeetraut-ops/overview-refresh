# Synthetic Generator Implementation Handoff

## Assignment and ownership

Implement the approved synthetic generator in `/Users/sumandra/projects/talium/forecasting/code-econ`, on the existing `forecast` branch. Use GPT-5.6-sol for the implementation subagent. The parent agent owns independent code review, acceptance verification, and final fixes. Do not commit, switch branches, overwrite existing documentation, or implement the other forecasting modules.

The specification documents are currently untracked work belonging to this task. Preserve them. Set the repository working directory explicitly for shell commands; the conversation's initial working directory is a separate Docs repository.

## Required reading before editing

1. Root [AGENTS.md](../../../AGENTS.md) and any applicable local instructions.
2. [Functional specification](../design/synthetic-data-generation.md), including the readable JSON shapes in section 9.1.
3. [Detailed design](../design/synthetic-data-generation-design.md), in full, including configuration, typed interfaces, algorithms, CLI/report behavior, and the complete test matrix.
4. [Module architecture](../arch/module-architecture.md).
5. Existing root dependency/test configuration, `utils/test_all.sh`, its environment helper, and `src/common/msging/log.py` before changing their integration.

Inspect the repository structure first. Send the parent a short implementation plan after reading. Product clarification has already occurred and the user approved the design; do not repeat permission questions from generic workflow instructions. Send actual contract conflicts or unavailable dependencies to the parent with evidence.

## Scope and critical invariants

- Implement three independent operations: reusable fake users; fixed mutually exclusive Team roster; dated daily series using saved users and roster. Use the root Python environment and dependencies, without a new installable package.
- Implement under `econ-forecast/src/forecast/` with the exact module ownership in the design. Shared persistence is `artifact_io.py`, not `run_store.py`.
- Optional supplied Team IDs are preserved; omitted IDs are generated deterministically. Reject final ID collisions. Team profiles and Team event targets still use Team names.
- Assign profile counts with deterministic largest-remainder allocation, once per series. User membership and profiles remain fixed. A user has one Project per active day, shared by all its agent/model records.
- Generate daily total processed input once, then conserve it through agent/model allocation. Stored `input` includes ordinary uncached input plus cache writes; `cache_read` is separate; `output` is additional. Never simulate separate cache writes or cost.
- Use independent deterministic random streams, fixed profile seeds, feature-specific daily seeds, and canonical record ordering. Replaying saved snapshots must reproduce record bytes; extending duration preserves earlier days.
- Complete coverage exists on inactive days and for every configured agent, including zero-share agents. Inactivity emits no usage. Respect zero-period and dated/random spike precedence.
- API-only agent billing, with explicit `TODO(subscription)` at the validation boundary; reject unsupported modes.
- One `cli_synt_gen.py` and one `utils/synt_gen.sh` wrapper with `--op user-create`, `roster-create`, or `series-create`. Preserve separate callable runners. Support help, validation only, documented exit codes, JSON stdout summaries, and JSON stderr logging.
- Every successful operation writes a readable `report.md` before the completion manifest. Reports, counters, token breakdowns, and manifest totals must agree. Return the report path in the CLI summary. Validation only writes nothing.
- The user guide belongs at `src/forecast/synthetic/README.md`. Include working commands, configurations, reports, logging, reproducibility, and troubleshooting there.
- Never overwrite an existing output directory. Validate before creating it, clean up only directories created by the invocation on handled failure, and publish completion manifest last.

## Implementation and validation

Follow the design's implementation order and test matrix. Ship usable example configurations, meaningful pytest tests, executable shell wrapper/helper, report rendering, and root test discovery integration. Test without network/service dependencies at runtime; installing declared Python dependencies for development is permitted.

Run the targeted synthetic tests and shell syntax checks. Exercise the example user → roster → 60-day series → replay workflow in temporary output directories, including manual Team IDs and report generation. Check root pytest collection includes both existing and synthetic tests; inspect root test runner changes for regressions. Do not relax assertions or silently change the design to make tests pass.

Do not create other agents. Send progress and findings to the parent; flag incomplete or unverified items explicitly. The parent will inspect the final source and perform its own checks rather than accepting the test count alone.

## Required final handoff

Report changed files, public commands/APIs, exact checks run and outcomes, example workflow counts and replay result, dependency/runtime details, and any remaining concern or departure from the design. Finish implementation and its tests before declaring it complete. Do not claim success for commands that were not run.
