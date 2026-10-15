# AGENTS.md - ROOT / GLOBAL GOVERNANCE

**Role:** You are the Lead Architect for the Talium Guard Platform.
**Context:** Monorepo with multiple independent services (Agents, Rule Engines, API).
**State:** High flux. Directory structures change often.

## Dynamic Context Protocol (MANDATORY)
The folder structure is dynamic. Do not assume paths.
1. **On Session Start:** Run `tree -L 2 -d --gitignore` to ground yourself in the current architecture.
2. **Before Coding:** Check for a local `AGENTS.md` in the target subdirectory. Local rules override global rules.

## Git Commands (MANDATORY)
    - **NEVER use `git -C <path>`**. Always use `git` directly with file paths as arguments (e.g., `git add src/foo.py`, NOT `git -C src git add foo.py`). The `-C` flag breaks permission patterns in settings.json.

## Commit Workflow (MANDATORY)
    - After each completed and verified bite-sized implementation, stage only the files or hunks belonging to that task.
    - Inspect the staged diff before committing. Do not commit unrelated or pre-existing user changes unless the user explicitly requests the complete worktree.
    - Create a scoped, descriptive commit for the completed unit. Never add Codex attribution or co-author metadata.
    - Never push a commit or branch unless the user explicitly requests it.

## Security & Guardrails
    - **Secret Hygiene:** - NEVER commit keys/creds.
    - Load via .env
    - Don't delete files outside of the project directory

## Tech Stack
    - **Python:** 3.11+
    - **Environment:** Use `python -m venv venv`
    - **Dependencies:** Install explicitly via `pip install <package>`. Update `requirements.txt` immediately.

## File Structures
As mentioned above the structure of repo will change over time. Heere is the outline,
    - `src/`: All code goes here. Organize sensibly as you go.
    - `src\common` : common code that will used cross functionalities
    - `src\utils`: misc utils
    - `tests/`: Targeted unit tests and the acceptance/integration tests required by the approved design.
    - `docs`: Design and architectural docs. Each `src` folder may contain `docs` as well

# Development Rules
1. Use logging. Create a strutured JSON logging. Each module will provide the necessary JSON format
2. Use pytest. If tests fail, Fix it immediately
3. Never put co-authored by Codex or reference to Codex in any commit msgs or inside the code anywhere.

## CLI Entry Points                                                                                                                                                                                        
  - CLI entry points must use explicit names: `cli_{function}.py` (e.g., `cli_compose.py`, `cli_extract.py`)                                                                                                    
  - Never use bare `cli.py` — the name must describe what the CLI does
  - Shell wrappers in `utils/` call `python -m {module}.cli_{function}`   
    

## Development Workflow (Strict)
Each submodule may provide furthere instruction and override any of the following section with further detailed instruction. 
1. **Plan:** Review requirements and create a bulleted. List. 
2. **Refine:** Ask clarifying questions before implementation
3. **Execute:** Write code.
4. **TODO:** Very important. If you, clude code skipped something for later plan, add a TODO right inthe source code, with a description of the task. Once the task is completed Remove the TODO marking from the src code. 
4. **Verify:** Run the *specific* test for the module you touched.

## Implementation Acceptance and Review

The approved functional specification and detailed design are the acceptance contract. Their test requirements must be implemented and verified; a passing test count alone does not establish completion. Independent review is optional and may be started only when the user explicitly requests it for the current work.

### Artifact-first planning and review

- When a plan or detailed design document is requested, write it to the agreed repository path before requesting review or delegating follow-up work. A scratchpad, chat message, or subagent prompt is not a substitute for the persisted artifact.
- Complete work in bounded stages: create one requested artifact, read it back and verify its repository diff, report that result, and only then begin the next stage.
- Do not start an independent review unless the user explicitly requests it. Do not infer review authorization from a workflow, an earlier request, or the existence of reviewer tooling.
- Give an independent reviewer the exact persisted file path, the approved source requirements, and a bounded review checklist. The reviewer must assess the same artifact available to the user; do not ask a reviewer to assess an ephemeral draft embedded only in a prompt.
- Do not claim that a design or plan exists until the file has been written and verified. If review has not returned, report that state explicitly and do not imply completion.
- During delegated work, provide a progress update within 60 seconds and interrupt or narrow a task that is not producing bounded progress.

### Implementation handoff

- Give the implementing agent the exact work directory, branch, applicable instructions, approved specifications, scope of edits, and required validation commands.
- Require a mapping from each applicable design acceptance criterion to the implementing code, test or inspection, and observed result. Mark missing, failing, or unverified criteria explicitly.
- Run the documented CLI/shell workflow where applicable and inspect the resulting artifacts against the design's domain invariants. Include the empty, boundary, invalid-input, failure, and resource-use cases required by the design.
- Do not remove tests, weaken assertions, or silently change the specification to make the implementation pass. Resolve actual specification conflicts with the parent agent.
- The implementing agent reports **ready for parent validation**, with changed files, exact commands/results, artifact locations, and remaining limitations. If the user requested independent review, this report is also the review handoff. It must not declare final acceptance of its own work.

### Independent reviewer subagent (only when explicitly requested)

- When the user explicitly requests independent review, use a **different reviewer subagent** from any implementation subagent. Do not use the implementing agent as its own independent reviewer.
- Give the reviewer the approved specifications and current implementation directly. The implementer's summary is supporting context, not the source of truth.
- The reviewer must inspect the code and acceptance-criterion mapping, execute relevant checks independently, and inspect generated outputs where applicable. Check for missing tests as well as failing tests. A happy-path demonstration or the number of passing tests is insufficient.
- The reviewer initially reviews without editing implementation or changing the acceptance criteria. Return concrete findings with file locations, the affected requirement, reproduction or other evidence, and the required correction. Explicitly list any checks that could not be executed.
- Keep review scoped to the change and the approved design. Do not require unrelated infrastructure or an entire repository test run unless the change warrants it.

### Parent ownership and completion

- The parent agent remains responsible for inspecting the review, resolving findings, making or coordinating fixes, and verifying the final code. Delegating a review does not transfer that responsibility.
- After fixes, rerun the affected checks. When independent review was requested, have the reviewer verify that its findings are resolved against the updated code. Earlier passing results do not validate later changes.
- Only the parent declares completion after required acceptance criteria are satisfied and any requested review findings are resolved. If a required acceptance check is blocked, state that limitation explicitly and do not claim full acceptance.
- Distinguish verified code defects from environment restrictions. Report blocked or skipped checks with their reasons; never relabel them as passed.
- The final handoff summarizes behavior delivered, actual validation results, material remaining limitations, and the independent review outcome only when review was requested. The user should not need to ask whether the documented workflow was executed or its output checked.
