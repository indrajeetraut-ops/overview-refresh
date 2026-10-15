# Framework for Internal Task Classification and Trajectory Analysis in Coding Agents

This document compiles a comprehensive architectural framework for classifying, tracking, and debugging the internal operations of long-horizon software engineering (SWE) agents (such as Claude Code). It covers initial intent routing, internal log state taxonomies, and academic breakthroughs in trajectory mining and failure attribution.

---

## 1. The Core Challenge: Intent vs. Trajectory

Traditional software agents rely on **Intent Classification** at the initial user prompt level to route tasks. However, for multi-turn, long-horizon coding assignments, prompt-based routing is highly error-prone. A coding session can last for dozens of turns, during which the agent's workflow is emergent and reactive. An agent might begin with a simple feature implementation, encounter an unexpected compiler error, and immediately pivot into deep root-cause debugging or codebase exploration.

To accurately classify what an agent is doing at any given moment, the signal source must shift from initial prompts to **Trajectory Analysis**—parsing the internal execution stream (e.g., Claude Code's `session.jsonl`) which logs the continuous **Thought-Action-Result (TAR)** cycle.

---

## 2. Coding Agent Task Taxonomy (Intent & Execution)

When designing a production-grade evaluation or monitoring suite for coding agents, tasks should be categorized by both functional domain and the specific toolsets they activate.

| Broad Category | Target Sub-Tasks | Context / Tooling Triggered |
| :--- | :--- | :--- |
| **1. Repository Exploration & Comprehension** | <ul><li>**Codebase Q&A:** High-level architectural exploration ("How is authentication structured?").</li><li>**Impact Analysis:** Scanning cross-file dependencies prior to modifying a file.</li><li>**Root Cause Localization:** Tracing error stack traces to precise source files and lines.</li></ul> | Read-only access, Abstract Syntax Tree (AST) parsing, grep/ripgrep execution, directory tree traversals. |
| **2. Code Mutation & Generation** | <ul><li>**Greenfield / Feature Coding:** Generating entirely new modules, components, or API endpoints from technical specifications.</li><li>**Bug Patching:** Surgical edits to existing blocks of code to resolve logic flaws or handle edge cases.</li><li>**Structural Refactoring:** Optimizing style, removing dead code, or reducing duplication without altering external program behavior.</li></ul> | Read-write file system access, structured AST editing or unified diff patching tools. |
| **3. Verification & Diagnostics** | <ul><li>**Test Suite Generation:** Writing new unit, integration, or end-to-end regression tests.</li><li>**Interactive Debugging:** Executing tests in a loop, capturing shell exit codes, and self-correcting based on failure strings.</li><li>**Lint & Type Fixing:** Cleaning up import formatting, resolving compiler/linter warnings, and addressing static type-checker errors post-generation.</li></ul> | Secure shell sandbox execution, language-specific test runners (`pytest`, `npm test`, `jest`), linters, and compilers. |
| **4. Maintenance & Lifecycle** | <ul><li>**Dependency Migration:** Bumping package versions and refactoring code to align with breaking upstream API updates.</li><li>**Documentation Maintenance:** Automatically generating or updating markdown files (`README.md`, `CLAUDE.md`), docstrings, and OpenAPI specs.</li><li>**PR Preparation:** Documenting changes, generating meaningful pull request descriptions, and verifying git diffs.</li></ul> | Git version control tools, semantic diff tools, package managers. |

---

## 3. Trajectory-Based Task Classification (Log Signals)

To classify an active agent's status retroactively or via a real-time monitoring dashboard, the runtime state machine must parse segments or windows of the agent's `session.jsonl` stream. By tracking **tool categories** and **environment exit codes**, the system maps raw actions to precise engineering phases.

| Active Task Phase | Dominant Tool Signals in Log | Context & Log Indicators |
| :--- | :--- | :--- |
| **Codebase Mapping & Research** | `view_file`, `grep`, `file_search`, `list_dir` | Read-only tools dominate. High volume of inbound text strings in `tool_output` paired with zero write mutations. |
| **Root-Cause Analysis (Early Debugging)** | Mixed `view_file` + `bash` (test runners) | Target test commands are executed (e.g., `pytest -k test_auth`). The `tool_output` block contains runtime stack traces or standard errors with shell exit codes $> 0$. |
| **Code Mutation & Patching (Coding/Fix)** | `edit_file`, `write_file`, `patch` | Explicit write/mutation utilities are invoked. The agent's structured `thought` log fields outline explicit implementation strategies. |
| **Verification & Regression Testing** | `bash` | System execution commands run immediately succeeding a write/patch operation (e.g., `npm run build` or `make test`). Exit code is evaluated for success ($= 0$). |
| **Housekeeping & Lifecycle** | `git_diff`, `git_commit`, `bash` (package installations) | Environment initialization commands (e.g., `pip install`) clustered at the start of the log, or git status/diff validations clustered at the terminal phase of the session. |

### Classification Architecture Patterns

1. **Heuristic Sliding-Window State Machine (Fast & Cost-Effective):** Maps each schema tool to an atomic operational bucket (Read, Write, Execute, System). A sliding window (e.g., 5-turn spans) evaluates tool density. If a window contains a write utility followed by sequential bash calls yielding non-zero exit codes, the system registers a transition from *Coding* to *Debugging*.
2. **LLM Trace-Judge (High Semantic Nuance):** Feeds discrete multi-turn chunks of the `session.jsonl` (including the agent's `thought` properties, tool parameters, and tool outputs) into a lightweight, long-context model. Controlled by a rigid JSON schema, the judge determines if the agent is making linear structural progress or trapped in an unrecoverable failure loop.

---

## 4. Academic & Open-Source Trajectory Frameworks

State-of-the-art research has operationalized trajectory logs to build automated diagnostics, visual tracking environments, and conversational debuggers for autonomous software developers.

### A. TAR (Thought-Action-Result) Trajectory Framework
* **Core Innovation:** Rather than formatting execution histories as monolithic text streams, this paradigm models multi-turn agent logs as a **Directed Acyclic Graph (DAG)**. Every individual turn is decoupled into three dependent nodes: *Thought* (the hidden internal planning state), *Action* (the tool execution command), and *Result* (the structured feedback returned by the operating system or runtime environment).
* **The Algorithm:** The framework reads sequential logs line-by-line and applies graph-traversal algorithms to compute **Semantic Relation Labels** across turns. It traces how a `Result` at turn *N* alters the latent state of a `Thought` at turn *N+2*. This allows the system to deterministically measure *repetition loop cycles* (when an agent repeats identical commands due to repeating environmental failures) and *divergence trajectories* (tracking when an agent shifts away from the core goal).
* **Open Source Status:** Yes. Developed and maintained under the SOAR Lab ecosystem as an interactive visualization and parsing suite (`SOAR-Lab/agent-traj-visualization`).

### B. AgentRx (Automated Failure Attribution)
* **Core Innovation:** Solves the difficult "propagation problem" in long-horizon coding trajectories. In complex engineering workflows, an agent might execute a single flawed tool command early in a session (e.g., reading an incorrect file or injecting a subtle typo) but continue executing tools for another 30 turns before a catastrophic failure or build crash occurs. 
* **The Algorithm:** AgentRx ingests varied, raw multi-turn execution records and normalizes them into a standardized schema. It dynamically **synthesizes behavioral constraints** extracted directly from the agent’s system configuration, prompt policies, and tool schemas. The trace engine replays the execution path step-by-step against these constraints, generating a structured validation log of explicit violations to isolate the precise *critical failure step* (the point of no return).
* **Open Source Status:** Yes. The underlying diagnostic tooling, constraint verification systems, and multi-domain evaluation datasets are fully public.

### C. AgentStepper (Interactive Agent Debugging)
* **Core Innovation:** Maps the conceptual paradigm of a low-level native program debugger (like GDB or LLDB) directly to autonomous LLM orchestration frameworks. It allows engineering teams to inspect the execution pipeline of an agent just as they would step through a binary.
* **The Algorithm:** The software intercepts the runtime harness of the agent framework, splitting communication paths into distinct transactional lanes (LLM $\leftrightarrow$ Agent Coordinator $\leftrightarrow$ Tool Registry). The execution engine provides engineers with the capability to insert **conditional breakpoints** on specific tool wrappers (e.g., pause execution if `edit_file` targets a sensitive architectural directory), perform step-by-step trace playbacks, and execute **live trace mutation** (modifying prompt vectors or mocking shell results on the fly to observe downstream behavior shifts).
* **Open Source Status:** Yes. Includes an extensible backend API and an intuitive web UI designed to latch onto active agent rigs (such as SWE-agent) with minimal friction.

---

## References

* **AgentRx:** Barke, S. *AgentRx: Diagnosing AI Agent Failures from Execution Trajectories*. arXiv.
* **AgentStepper:** Hutter, R. *AgentStepper: Interactive Debugging of Software Development Agents*. arXiv.
* **TraceView / TAR Visualizer:** Sajadi, A. *TraceView: Interactive Visualization of Agentic Program Repair Trajectories*. arXiv.
