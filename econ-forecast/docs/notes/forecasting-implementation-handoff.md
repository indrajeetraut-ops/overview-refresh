# Forecasting implementation handoff

> Historical handoff for the earlier ETS implementation. The current model is documented in [the weekday-resampling design](../design/forecasting-weekday-bootstrap.md).

**Prepared:** 2026-09-13.
**Status:** Released for implementation 2026-09-13. Parent contract freeze completed in [release contract](../design/forecasting-release-contract.md) and [numerical preflight](forecast-numerical-preflight.md). Required source snapshot: forecasting-release-snapshot.json. Runtime acceptance is pending.
**Agent model:** `gpt-5.6-sol` for implementation and independent validation/review agents, as requested by the user.
**Acceptance owner:** Parent agent. Delegation never transfers responsibility for correctness, test quality, fixes, or final acceptance.

## 1. Workspace and source of truth

Work in `/Users/sumandra/projects/talium/forecasting/code-econ`, branch `forecast`. Pass this working directory explicitly to shell tools: the conversation starts in the separate `/Users/sumandra/projects/talium/Docs` repository. Read root [AGENTS.md](../../../AGENTS.md), run the prescribed directory inventory, and check for applicable local instructions. Do not use `git -C`.

Use the existing shared checkout with assigned file ownership for the initial implementation. The current forecasting documents, config, and README files are untracked; an ordinary new worktree from HEAD would omit them. If the parent later selects isolated worktrees, the parent must first provide and verify the exact same frozen specification snapshot and approved base state in each worktree, including untracked files. A branch name alone is not sufficient evidence of identical instructions.

Read the following in full before proposing edits; do not rely solely on this handoff summary:

1. [Forecasting functional spec](../design/forecasting.md): input/outputs, scope, config, horizon, CLI, failures, resources, acceptance scenarios.
2. [Forecasting detailed design](../design/forecasting-design.md): algorithms, allocation, sampling, artifact storage, Plotly utility, and every verification criterion.
3. [Decision checkpoint](forecasting-design-decisions.md): final agreements and superseded proposals. Do not restore rejected behavior.
4. [Module architecture](../arch/module-architecture.md): boundaries, especially forecasting/budget/reporting.
5. [Commented forecast.yaml](../../config/forecasting/forecast.yaml).
6. [Implementation release contract](../design/forecasting-release-contract.md) and [numerical preflight](forecast-numerical-preflight.md), including the exact interfaces, schemas, dependency pins, resource targets and commands.
7. [Forecasting README](../../src/forecast/forecasting/README.md) and [Plotly utility README](../../src/forecast/utils/README.md).
7. [Shared daily contracts](../../src/forecast/contracts.py), [artifact I/O](../../src/forecast/artifact_io.py), [synthetic FS](../design/synthetic-data-generation.md), and [synthetic implementation](../../src/forecast/synthetic/README.md). Inspect actual synthetic output metadata/snapshots; never infer a nonexistent adapter contract.
8. Root `requirements.txt`, `pytest.ini`, `src/common/msging/log.py`, and `econ-forecast/utils/_forecast_env.sh` before integrating dependencies, tests, or wrappers.

Background only: `/Users/sumandra/projects/talium/Docs/ProductSpec/Forecasting.md` and `/Users/sumandra/projects/talium/Docs/research/forecast-model-notes.md`. Later approved module decisions override earlier research suggestions. If documents conflict, identify exact passages and ask the parent; never silently change behavior or edit acceptance criteria.

The parent records hashes of the required specification snapshot before dispatch. The full prompts below must reference that snapshot and this handoff explicitly; a fresh agent must not need access to conversation history to implement correctly.

The release appendix must list the exact checkout/base revision; frozen source files and SHA-256 hashes; schema/interface versions; ownership down to test files; fixture/config paths; executable commands; numerical tolerances; supported platforms; resource targets; and the snapshot-verification command. Each agent must run that verification and acknowledge the result before edits. A missing appendix or hash mismatch blocks coding. Parent-approved contract changes require a refreshed snapshot and acknowledgment from every affected agent.

If a contract conflict is found, stop work on the affected interface or behavior until the parent resolves it. Continue only unrelated work within assigned ownership. This applies to every implementation and review agent; reporting a conflict is not permission to choose a behavior independently.

## 2. Contract freeze — parent responsibility before coding

The checklist below has been closed for dispatch by the linked release contract and executable Mac numerical preflight. Linux validation remains a required implementation check, with no current platform-success claim. Implementers may provide bounded feasibility evidence, but do not own unstated behavioral choices.

| Must be fixed before release | Required concrete output |
|---|---|
| Prepared input and synthetic compatibility | Versioned population/attribution/coverage mapping; exact synthetic metadata adapter; Organization/model enumeration and startup evidence; unknown/outage mapping; rule for old never-qualified sparse activity with recent zeros. |
| Producer/consumer artifact contract | Typed schemas for manifest, index, expectations, summaries, shares, diagnostics, and statuses; array axes/shape/dtype; inactive/missing representation; safe explanatory filename/collision policy; interrupted-write lifecycle. |
| Python interfaces and file ownership | Exact shared reader and batch result/request interfaces that engine, plotting, and acceptance fixtures use; final core file layout. |
| Numerical implementation | Tested dependency pins; fit initialization/bounds/warning handling; residual scaling convention and deterministic seed streams; quantile/replay tolerances. Consistent workloads are ordinary supported input, not a separate business rejection rule. |
| Runtime and resource acceptance | Explicit horizon/path/workload limits; batching/thread policy; reference raw dataset size; CPU/memory/runtime/storage targets and measurement commands on supported environments. |
| Reporting integration scope | Exact minimal run report/default graphical output required by this implementation, using the agreed separate presentation boundary. Full reporting module functionality remains subject to its own spec/design. Do not silently omit required default output or implement an unapproved reporting service. |
| Test readiness | Exact fixture schema, executable test/benchmark entrypoints and dependency/browser setup; source-to-test acceptance matrix covering FS sections as well as named design rows. |

This is a parent preflight gate, not permission for implementation agents to label requested work TODO and declare it complete. The parent resolves routine engineering choices with rationale; only a material change to agreed behavior goes back to the user. Refresh spec hashes and the acceptance matrix after freezing.

## 3. Scope and deliverables

Implement the forecast engine and local Plotly utility specified by the frozen documents, their required wrappers, tests, example workflows, output/report artifacts, dependency integration, and module-owned READMEs.

Do not implement PostgreSQL extraction, dollar pricing/budget arithmetic, scenario optimization, production reporting/query services, or a separate Python package/install process. The $40K walkthrough documents downstream interpretation; it is not permission to implement budget here. Preserve the synthetic generator and its regression suite. Changes to shared I/O/contracts must retain existing behavior or explicitly migrate it under the frozen design.

Keep errors meaningful, logs structured, code readable and decomposed by responsibility. Do not catch broad exceptions to turn numerical/programming failures into fabricated zeros or successful results. Do not replace runtime validation with Python `assert` statements. Never configure global logging, plot backends, or run computation merely on library import.

## 4. Assignment and sequencing

The parent creates separate agents with `model="gpt-5.6-sol"` and self-contained prompts. Use three child agents at most alongside the parent. Agents do not spawn further agents or change another agent's files without parent reassignment.

| Agent | Responsibility and initial write ownership |
|---|---|
| A — forecast implementation | `src/forecast/forecasting/`, including accurate usage/implementation-status README updates under frozen semantics; frozen shared contracts/readers and required compatibility changes; forecast shell wrapper; forecasting unit tests. Owns the minimal `report.md` and default chart integration, producers and tests under the frozen presentation boundary; exact producer paths must be assigned in the release appendix. Sole owner of root dependency/pytest/environment integration. |
| B — Plotly implementation | `src/forecast/utils/`, `utils/plot_forecast.sh`, and plotting unit/CLI/browser tests under `tests/utils/`. Consume the frozen public reader; do not independently redefine schemas or modify forecast core. Send dependency requests to A/parent. |
| C — independent acceptance and review | Read the specs and implementations independently. Initially read-only: audit requirement/test coverage and return concrete findings. With a separate parent assignment, add independently authored acceptance fixtures/tests in `tests/acceptance/`. Never edit implementation or weaken the source contract. Re-review the final fixed revision. |

All paths in that table are relative to `econ-forecast` except explicitly root-owned files. Unit-test file names and acceptance directories must be assigned before launch so agents cannot overwrite each other's work. Production reporting implementation is not implicitly assigned to B.

Sequence:

1. Parent closes contract freeze and snapshots sources; prepares the acceptance matrix.
2. A and B acknowledge the exact snapshot, summarize their scoped plan and required checks, then implement against the same interfaces. B may proceed in parallel using schema-valid fixed fixtures even before A completes.
3. C can independently audit the frozen requirements/test plan while implementation proceeds. Review findings go to the parent, not into silent contract edits.
4. A and B run their unit and real command workflows, inspect saved results, and submit **ready for independent review** evidence.
5. C reviews actual current source/test code, executes checks independently, opens exported Plotly HTML in an offline browser, and reports missing coverage as well as failures. The implementers' reports are pointers, not proof.
6. Parent inspects code and test quality, fixes or assigns findings, reruns affected checks, and requests C's verification of the fixes on the final source snapshot.
7. Only the parent declares acceptance. Any blocked required check is described explicitly; it is not silently counted as passing.

## 5. Behavioral invariants that must survive implementation

- Fit per `(org_id, scope_type, scope_id, model)` for Organization/Team/Project. Agent is a joint allocation dimension. IDs from different Organizations must never mix; overlapping scope views must not be summed.
- Target equals `input + cache_read + output`. Input already includes cache writes. Category quantities are derived using saved joint agent/category shares; totals reconcile without independent integer rounding.
- Covered absence is zero; declared outage truncates continuous history. Null/malformed required token totals are errors. No future data enters startup, fitting, allocation, or error sampling.
- Startup requires configured X consecutive positive calendar days. Start is the first day of the qualifying streak; 14 covered days are still required. Later inactivity/low usage does not impose another streak. Preserve minimal positive usage and previously established qualification.
- Recent fully zero allocation history means assumed current inactivity; no historical-share fallback. Inactive components permit explicitly conditional scope risk alongside qualified models. Nonzero unsupported components cannot silently become zero or disappear. A wholly inactive result is distinguished from empirical certainty.
- 14–27 days use level-only additive ETS. 28+ try weekly+damped trend, weekly without trend, then level-only on numerical failure. Each attempt is bounded at 1,000 iterations, checked, and reported. Normal constant/near-constant workloads are supported without invented noise or a special low-variation rejection gate.
- Fit each model on its full usable history; sample centered error vectors on common valid historical dates, with at least 14 shared dates. Keep same-day cross-model errors together. Inactive models do not shorten overlap. Report individual fit and shared sampling windows, including the loss of older sampling events.
- Simulate recursively from the end of fitted history, including offset days internally. Preserve date/sample identities. Publish requested dates only and calculate period summaries after within-sample aggregation.
- Correct negative outputs after simulation, then derive summaries from corrected paths. The requested-window correction ratio and configurable threshold follow the exact agreed numerator/denominator/equality rules. Never convert NaN/Inf to a valid zero.
- Same frozen request/data/seed/software reproduces numerical outputs under the frozen tolerance and seed policy. Do not use randomized Python `hash()` as persistent identity or seed.
- Artifacts use the frozen JSON/NumPy contract, explicit statuses, explanatory filenames, authoritative index, checksums, and safe completion publication. Preserve inputs/existing outputs on failures.
- Forecast CLI, callable API, and shell wrapper agree. Help reads no data; validation writes no output directory; stdout JSON and stderr JSON logs stay separate; non-success states use the specified exits.
- Plotting reads saved results only. Displayed sample count does not change statistics. Mean/p10/p90/date/model arrays must match the independent oracle; offline hover/zoom/reset/legend controls must actually work. No source run mutation or model re-execution.

## 6. Required tests and evidence

“Exhaustive” means every applicable functional requirement, named design criterion, documented boundary, and failure mode has a justified test or direct acceptance check. A large test count or implementation-derived expected values do not substitute for coverage.

Start from [the acceptance matrix](forecasting-acceptance-matrix.md). Add rows for functional requirements not captured in the named design table, especially config/CLI, calendar boundaries, multi-Organization input, resource behavior, and root integration. Each row must contain:

```text
requirement and source section
implementation path/symbol
test name and independent oracle or direct inspection method
exact command and environment
observed result and evidence artifact
status: unimplemented / unverified / passed / failed / blocked / explicitly out of scope
```

Independence requirements:

- Construct small expected totals/dates/shares/statuses by hand; do not call the production helper to compute the expected answer for its own test.
- Unit tests may inject a convergence/write failure to exercise failure handling. Integration tests must also execute the real statsmodels fit/simulation, real filesystem outputs, actual CLI/shell, and real Plotly browser rendering.
- Test startup streaks, inactive/retiring models, overlap windows, calendar offsets, allocations, correction ratios, partial scopes, replay, and errors. Preserve ordinary consistent workload behavior; do not invent new eligibility gates for tests.
- Validate input-to-output invariants independently from the generated JSON/NumPy arrays: row/date axes, model selection, sample IDs, finite nonnegative values, means, category conservation, scope totals, quantiles, checksums, statuses, and report counts.
- For Plotly use the design's explicit numerical oracle, then test exported HTML offline in a provisioned browser. Capture interaction results and screenshots. Confirm no uncaught browser errors or external network requests. A static screenshot is not a numerical oracle; an HTML file existing is not an interaction test.
- Run the reference benchmark and record input rows/bytes, fit/simulation/render stage times, memory definition, output bytes, workers/thread limits, platform and dependency/browser versions. Memory estimates are not measurements; Mac execution is not proof of AWS/Linux compatibility.
- A network/sandbox/platform block is reported with the exact command/error and the checks still possible. Do not remove/skip a required test to report success. Do not request unrelated infrastructure or run the entire repository suite without a change-based reason.

## 7. Mandatory command workflows

Use the repository virtual environment. Freeze any additional test entrypoint names before implementation; do not leave executable placeholders in the released handoff.

```bash
venv/bin/python -m pytest econ-forecast/tests/synthetic -q
venv/bin/python -m pytest econ-forecast/tests/forecasting -q
venv/bin/python -m pytest econ-forecast/tests/utils -q
venv/bin/python -m pytest econ-forecast/tests/acceptance -q
bash -n econ-forecast/utils/forecast.sh
bash -n econ-forecast/utils/plot_forecast.sh
./econ-forecast/utils/forecast.sh --help
./econ-forecast/utils/plot_forecast.sh --help
```

Record collection counts for each listed suite and verify that every assigned acceptance and browser test is collected. An empty or omitted required suite is a failure, not a successful check. Run existing shared logging/integration checks if those shared files change. Finish with dependency compatibility checks (`python -m pip check` in the tested environment) and `git diff --check`.

In a new temporary artifact directory, execute the real synthetic `user-create` → `roster-create` → `series-create` workflow using committed example configs, then forecast validation and generation, then the Plotly wrapper. Pass dynamically created paths through config copies outside tracked files. Also run both wrappers from a different working directory with spaces in paths. Inspect actual report, JSON/JSONL, NumPy arrays and exported HTML; do not stop after process exit 0. Keep fixtures for inactive, partial and invalid histories alongside the happy-path run. Freeze exact commands/config paths in the release appendix before dispatch.

Browser tests run with an explicitly provisioned browser and network blocked during HTML use; browser provisioning is setup, not part of user plot execution. Run Linux compatibility/resource checks on an actual suitable Linux environment; never relabel a local Mac check as AWS validation.

## 8. Agent prompts to send after release

### Agent A — forecast implementation

You are implementing forecasting in `/Users/sumandra/projects/talium/forecasting/code-econ` on `forecast`, using GPT-5.6-sol. Read `AGENTS.md` and every required source in `econ-forecast/docs/notes/forecasting-implementation-handoff.md`, including the frozen spec snapshot and contract-release appendix. Follow that handoff's Agent A ownership, behavioral invariants, validation commands, and evidence requirements. Acknowledge the source snapshot and send a short scoped implementation/test plan before edits. Implement the actual agreed algorithm and runtime interfaces; do not replace acceptance with mock-only tests, invent thresholds, or change the contract. Report concrete conflicts to the parent while continuing independent authorized work. Run real synthetic-to-forecast workflows and inspect saved outputs. Submit a complete criterion-to-code/test/result matrix and conclude only “ready for independent review.” Do not commit, switch branches, spawn agents, or change Agent B/C files. The parent owns final acceptance and fixes.

### Agent B — Plotly utility implementation

You are implementing the local Plotly utility in the same specified checkout, using GPT-5.6-sol. Read root instructions and every required source in the handoff, especially the frozen artifact-reader contract, forecasting FS plotting CLI, detailed design `PLOT-01`–`PLOT-09`, and utility README. Follow Agent B file ownership. Build saved-artifact-to-figure code plus `cli_plot_forecast.py` and `plot_forecast.sh`; preserve numerical values, scope/sample identity, offline operation, logs and exit semantics. Acknowledge the snapshot and scoped test plan first. Do not invent a second schema, run the forecaster from plotting, or add the production reporting/query service. Run numerical tests, actual shell commands, offline browser interaction and visual inspection on the exported artifact. Send dependency needs to the parent/A rather than editing their files. Submit the acceptance mapping and actual evidence as “ready for independent review.” Do not commit, switch branches or spawn agents.

### Agent C — independent acceptance/review

You are the independent reviewer using GPT-5.6-sol; you did not author production implementation. Read the frozen specifications directly and root `AGENTS.md`, then inspect the actual source and test code. Begin read-only, including tests that do not edit tracked state. Audit requirements missing from the matrix and tests, not just failing tests. Verify expected values come from independent oracles. Run relevant unit/integration/shell/benchmark/browser checks yourself and inspect outputs, including Plotly offline interactions. Return findings with file/line, requirement, reproduction/evidence, severity, and required correction. Report unavailable checks honestly. Do not alter production code, acceptance criteria, or assertions. The parent may separately assign independent acceptance-test additions in your owned directory after this initial audit. After fixes, inspect the updated source and rerun affected checks; report whether each finding is resolved against that snapshot. Do not declare user-facing final acceptance; the parent owns it.

## 9. Parent acceptance and final return

The parent must personally inspect the changed source and test assertions, compare evidence with the frozen contract, run the principal shell workflow, inspect selected numerical outputs and HTML, and resolve material review findings. Check for truncated/skipped tests, accidental schema drift, fake benchmarks, hidden fallbacks, logging side effects, unbounded materialization, and mismatched report/array values. Review fixes on the final revision, not only on the initially tested source.

Require each implementation submission to include changed files, APIs/commands, exact tests and environment, result counts without conflating skipped with passed, acceptance matrix, reproducibility evidence, artifact paths, benchmark measurements, and any deviations/limitations. Required failed or blocked work prevents a claim of complete acceptance. Only explicitly out-of-scope future work, such as the budget implementation, may be marked deferred with a source citation.

The user approved dispatch. Use the completed release contract and verified snapshot; implementation and review evidence must still be produced.
