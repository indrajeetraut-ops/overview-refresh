---
name: talium-impl-workflow
description: Use ONLY when starting hands-on code implementation of an already-planned/designed task in this Talium repo (e.g. econ-forecast), AND the user has explicitly entered implementation mode. This is the required orchestration for that: a sonnet subagent writes the code after a pre-coding handshake, work happens in a git worktree, tests are written and their results persisted as a report, then the coordinator reviews the work against the design doc and produces a Major/Minor issues list. NOT for design, planning, research, brainstorming, or doc writing (those stay coordinator-led, no subagent). NOT before the user says to enter implementation mode — default is design/docs only. Always size the change first (Step 0): small fixes that need no design/plan update are done directly without a subagent; only non-trivial work uses the subagent loop.
---

# Talium implementation workflow

Orchestrate non-trivial code implementation as coordinator. The coding is delegated; the
coordinator owns the brief, the review, and the guardrails. The value is the pre-coding
handshake (ambiguities surface before code is written) and an independent review against the
design (the person who reviews did not write the code).

## Step 0 — size the change first

Before anything else, estimate the change and say which path you take (one line to the user):

| Size | Signals | Path |
|---|---|---|
| **Small** | Minor fix or tweak; no design or plan update needed; a few files; behavior already specified. E.g. a bug fix, an extra CLI flag, a filter, a report-format adjustment, a rename. | **Do it directly** in the current checkout: edit, add/adjust tests, run the module's tests, persist the test report if the module has one, commit. No subagent, no brief, no handshake, no worktree. |
| **Non-trivial** | New module or feature; design/plan changes; many files or several components; new data contracts. | The full loop below. |

If unsure, say so and ask. When a "small" change turns out to need a design change, stop and
switch to the full loop.

## Guardrails (check before starting)

- **No implementation coding until the user has explicitly entered implementation mode.**
  Until then, stay in design/docs only. If unsure whether they've authorized it, ask — do
  not start.
- **Isolate the work in a git worktree**, off the main checkout. Research/prototype work
  also stays in a worktree.
- There must be an **authoritative design doc** (and usually a plan/brief) for the task
  before coding. If there isn't one, that's a design step, not this workflow.

## The loop

1. **Brief the subagent.** Spawn the implementer with `model: "sonnet"`
   (claude-sonnet-5). Give clear, written instructions — a short brief file is fine —
   pointing at the design doc, the plan, the files in scope, and the acceptance criteria.

2. **Handshake before any code.** Ask the subagent whether the instructions make sense and
   to raise concerns/ambiguities *before* writing code. Review its feedback, resolve every
   concern (correct the brief), and only then tell it to start. Do not skip this even when
   the task looks obvious — the point is to catch a wrong assumption cheaply.

3. **Subagent implements in the worktree.**

4. **Tests are written.** May be a separate task/subagent. Implementation is not done until
   the touched module has tests.

5. **Persist test results as a report.** Test reports go under `docs/test/reports/`
   (`<module>-junit.xml` + `<module>-test-report.md` via `utils/gen_test_report.py`). Never under
   `docs/superpowers/` — that folder belongs to a skill. econ-forecast's indexed forecasting runs
   keep their existing `econ-forecast/docs/test-results/` harness (`test_forecast.sh`). A run is
   evidence, not just a console pass.

6. **Coordinator reviews against the design.** Independently review the subagent's work
   against the design doc and produce a **Major / Minor** issues list. Ask the user for
   clarification where a decision is needed; fix the Minor items directly.

7. **Land it.** Once the work is reviewed and green, drive it to completion: commit on the
   feature branch, merge into the working branch, and clean up the worktree/branch. Report
   what landed.

## Keep progressing — do not park

Default to **continuing** until you hit something that genuinely needs the user: a real
decision only they can make, a Major issue that changes scope, or an action they must
authorize. Do **not** stop to ask permission for the obvious next step (running tests,
fixing your own Minor findings, committing reviewed+green work, merging, cleanup) — just do
it and report. Presenting the review is not a reason to halt; state the findings and keep
moving through step 7. Surface a short status as you go rather than waiting for a green light
at every seam. If the user says to skip something (e.g. the Minors), skip it and proceed to
landing — don't re-ask.

## Notes

- Direction of dependency and repo governance follow the root `CLAUDE.md` (venv, explicit
  `pip install` + `requirements.txt`, `cli_{function}.py` entry points, structured JSON
  logging, `git` with file-path args — never `git -C`, no Claude references in commit
  messages or code).
- Leave a `TODO` in source for anything deliberately deferred, with a description; remove
  the marker when done (root `CLAUDE.md` rule).
- Keep the coordinator/implementer split real: if you (the coordinator) end up writing the
  feature code yourself, you've lost the independent-review property this workflow exists to
  provide.
