---
name: talium-design-doc
description: Use when brainstorming, writing, revising, or reviewing a module design doc in this Talium repo (e.g. econ-forecast/docs/design/<module>-design.md such as cost, budget, forecasting) and its implementation plan. Covers the brainstorm process, the MUST sections and their order, schema/formula/example style, and the review loop. NOT for system-level architecture docs (use my-arch-doc) and NOT for implementation coding (use talium-impl-workflow).
---

# Talium module design doc

A module design doc lets a standalone engineer, with repo access and no conversation history, implement
the module without guessing. Reference example: `econ-forecast/docs/design/cost-module-design.md`.

## 1. Process

1. **No code.** The output of the design stage is a design doc (and later a plan). Implementation starts
   only when the user explicitly says so.
2. **Ground first.** Before proposing interfaces or schemas, read the upstream design docs, the reader
   code, and a real example artifact. Check actual shapes, field names, and enum values in code — never
   design against an assumed shape.
3. **Brainstorm as senior architect.** One topic at a time. Critique, take one position, give the reason.
   No option menus, no AskUserQuestion pickers. Ask the user only for facts or intent you cannot know.
4. **Keep a scratchpad**: `docs/notes/<topic>-brainstorm.md` with
   - Decisions table (`D#`, decision, notes/who/date),
   - Open questions table (`Q#`, question, status),
   - External-module items to decide together,
   - Drafts of schemas/configs under discussion.
   Update it after every exchange; strike withdrawn items instead of deleting.
5. **Do not re-raise settled facts.** Check memory and the scratchpad first.
6. **Write the doc** from the scratchpad once the user says the design is done.
7. **Review loop.** Give the doc to a fresh subagent (no history) to check clarity, redundancy,
   standalone implementability (checked against real code/artifacts), and terseness; Major/Minor
   findings. Report the findings with your own agree/disagree per item, then walk them with the user one
   at a time before editing.
8. **Commit** doc, plan, and scratchpad with `Refs #<issue>`; no tool attribution in commit messages.

## 2. Design criteria (check every section against these)

| Criterion | Meaning |
|---|---|
| Simple | Fewest modules, files, fields, and flags that answer the purpose. No forced intermediate steps. |
| Easy to change | Versioned interface and versioned output. Consumers read only through a reader API, never files. |
| External choices itemized | Every dependency and consumer change is listed and decided with the user. |
| Every field has a consumer | A field exists only if something acts on it or shows it. No informational extras, no fields reserved for future needs, no field that is always the same value. |
| Readable identity | Identify things by names, never hashes. Hashes are hidden integrity checks only. |
| Fits the system | Mirror the upstream artifact's structure when the output is the same kind of thing; state deliberate departures once. |
| Scales | State the unit of work and the memory bound (e.g. one series at a time, one scope in memory). |

Versioning rule to reuse: schema ids `<name>/<major>.<minor>`, recorded once per file in the manifest;
minor = additive; readers accept the same major and ignore unknown fields.

## 3. MUST sections, in order

```text
# <Module> module — design
Header table: Status · Scope (V1) · Related · Issues · Updated

1. Flow            mermaid system diagram + 1–2 lines on what the module produces
2. Core logic      2.1 Terms (entity diagram + term table)  2.2 pseudo-code of the whole module + worked example
3. Interfaces       3.1 Inputs table: Source | Access (function → type) | Fields used (mark NEW upstream fields)
                   3.2 CLI (flags, stdout/stderr, exit codes) and Python API incl. the reader API consumers use
4..n. Mechanisms   one section per mechanism: formulas, rules, validation, each with a worked example
Statuses           per-item and aggregate status tables, how each is derived
Artifacts          layout tree · mapping to upstream artifact (if mirrored) · naming · versioning ·
                   one schema block per file · field rule/consumer table · report and charts
Assumption labels  label | attached to
Source layout      src/<module>/ tree with one-line purpose per file, config/, utils/<cli>.sh, tests/
Dependencies and changes to other modules   module | change
Key decisions      decision | pros | cons — only where a real choice was made
Deferred           bullets
```

- **Core logic is mandatory.** A reader must understand the whole module from §1–§2 alone; everything
  after is detail. Schemas are not a substitute for logic.
- **Report and charts are mandatory** for any module that produces numbers (as forecasting does).
- No "[open]" markers unless open items exist; unresolved design questions go back to the brainstorm,
  not into the doc.

## 4. Style

- **Terse.** Tables, snippets, formulas, diagrams over prose. No meta text (doc rules, restated house
  rules). Each fact stated once; cross-reference with `§`.
- **Define every term and symbol** before use. No abbreviations that can be misread (`tt` was read as
  "total tokens"; write `token_type`). No jargon (say "applying a change at every scope level", not
  "percolation"). One word per concept throughout (e.g. always "parent scope", never also "ancestor").
- **Formulas as rendered math** (`$$ … $$`) with a symbol table first; link symbols to their output fields
  (e.g. λ = `own_weight`). Pseudo-code only for the core logic.
- **Examples first** when explaining a mechanism: a small table with real numbers usually explains more
  than the rule. Numbers must check; worked examples become required unit tests.
- **JSON / YAML pretty-printed, one key per line.** One block per file, titled with file name and schema
  id. JSONL: one expanded row with a note that the file has one row per line. Configs in block style.
- **Mermaid only** for diagrams; flowcharts for flow, entity relations as small `flowchart`s.
- Interfaces name the exact function, type, and enum values as they exist in code.

## 5. Plan (after the design is agreed)

`docs/plan/<module>-plan.md`:

```text
Context (design link, what the plan covers, process: talium-impl-workflow after approval)
Deliverables
Conventions (deps, logging, CLI naming, identity, versioning, TODO rule, commit issue ref)
Tasks T0..Tn — each: [dependencies], Files, bullets citing design §, Accept: concrete checks (worked examples)
  T0 = outward-facing steps needing approval (issues to raise)
  upstream-module changes as their own tasks; README + docs last
Tests table (file | covers)
Out of scope
Verification (end to end, numbered)
```

Keep the plan's `§` references in sync whenever the design is restructured.
