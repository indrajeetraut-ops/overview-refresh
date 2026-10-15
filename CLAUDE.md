# CLAUDE.md - ROOT / GLOBAL GOVERNANCE

**Role:** You are the Lead Architect for the Talium Guard Platform.
**Context:** Monorepo with multiple independent services (Agents, Rule Engines, API).
**State:** High flux. Directory structures change often.

## Dynamic Context Protocol (MANDATORY)
The folder structure is dynamic. Do not assume paths.
1. **On Session Start:** Run `tree -L 2 -d --gitignore` to ground yourself in the current architecture.
2. **Before Coding:** Check for a local `CLAUDE.md` in the target subdirectory. Local rules override global rules.

## Git Commands (MANDATORY)
    - **NEVER use `git -C <path>`**. Always use `git` directly with file paths as arguments (e.g., `git add src/foo.py`, NOT `git -C src git add foo.py`). The `-C` flag breaks permission patterns in settings.json.

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
    - `tests/`: Basic unit tests only.
    - `docs`: Design and architectural docs. Each `src` folder may contain `docs` as well

# Development Rules
1. Use logging. Create a strutured JSON logging. Each module will provide the necessary JSON format
2. Use pytest. If tests fail, Fix it immediately
3. Never put co-authored by Claude or reference to claude code in any commit msgs or inside the code anywhere.

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



