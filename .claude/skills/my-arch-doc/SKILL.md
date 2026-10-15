---
name: my-arch-doc
description: Use ONLY when asked to write or revise a system-level Talium architecture document or RFC in docs/design/ — cross-module concerns such as overall system architecture, service boundaries between processes, pipeline topology, or scaling/concurrency posture. NOT for anything scoped to a single module or library — module design docs, module READMEs, usage/API docs (e.g. docs/modules/), or code-level documentation; write those without this skill. NOT for UI/artifact design or visual layout.
---

# Writing Architecture Docs

A design doc is a decision record for a reader who wasn't in the room. It has this shape:

## Required shape, in order

1. **Nutshell first.** Section 1 states the proposed solution in well under a page: what it
   is, the 2–3 load-bearing decisions, the headline cost/latency numbers, and the main
   overview diagram. A reader who stops after section 1 knows the design.
2. **Terms and notation defined upfront**, before first use. Define anything not universally
   known (p95, ev/s, at-least-once, idempotent…). No unexplained jargon anywhere in the doc.
3. **Main architecture and data flow next.** Deep dives, alternatives, and details after.
4. **Every major decision gets an alternatives table**: Option | Pros | Cons | Verdict —
   chosen option in the first row, and a "when we'd switch" note per rejected option.
5. **Back-of-the-envelope section, always.** Assumptions in their own table first, each with
   its basis. Then one-significant-figure arithmetic for data volume, storage, CPU/memory,
   latency, and money — show the multiplication so it's checkable. End with a per-unit
   conclusion a buyer understands ("~1¢/dev/day", "fits one Postgres").
6. **Clarifying questions table.** Never block on unanswered questions: Question | Working
   assumption used in this doc. Flag any assumption that contradicts an earlier doc.
7. **Phasing section last**: v0/v1/v2 and the named seams where deferred pieces slot in.

## Diagrams

- **Mermaid only — no ASCII art.** `flowchart` for block diagrams and data flow,
  `stateDiagram-v2` for lifecycles, `erDiagram` for data models.
- **Validate every block, then look at it.** Extract each block, render with
  `npx @mermaid-js/mermaid-cli` to PNG, and visually inspect the image. Fix what you see:
  - edges attaching to subgraph borders instead of nodes → remove `direction` overrides
    from subgraphs that have cross-boundary edges;
  - overlapping labels → shorten labels, move detail to prose, drop self-loops;
  - stages rendering out of order → declare the main chain's edges first.

## Style rules

- **Terse. Minimize sentence count.** Engineers read terse descriptions better than prose.
  Cut any sentence the reader does not act on. Long output carrying little information that
  matters is the specific failure mode to avoid.
- **Bullets and state transitions over paragraphs.** A longer discussion is almost always
  clearer as a bulleted sequence or a state-transition block. Fragments and arrow-chain
  shorthand are correct here, not sloppy.
- **Annotated JSON/YAML over markdown tables.** Field lists, config, and enums go in a code
  block with the note to the right of the field — a table takes too much space.
  Exception: 3–4 column data the reader scans vertically (code → trigger → state → action)
  stays an aligned monospace block, not JSON values.
- **No analogies.** "This is the HTTP 2xx pattern", "like a TCP window" — an analogy is not
  information. State the rule directly.
- **No per-field justification prose.** One line where the reason is non-obvious, otherwise
  nothing. Rationale belongs at the decision, not beside every field.
- **Contracts get full structural detail.** Schemas, message/event structures, wire formats,
  and module interfaces are the crispest part of the doc, not a summary. Spell each one out
  as an annotated JSON skeleton: every field present with its type and constraints stated as
  the field's value; mandatory vs. optional parts explicitly marked (`| null` = key present
  but nullable, `| absent` = key omitted when inapplicable); one concrete example instance
  with realistic values; and the normative evolution rules (additive-only, must-ignore
  unknown fields, namespacing). A reader must be able to implement the contract from the doc
  alone.
- **Algorithms as compact state or sequence blocks.** No SQL, no library snippets, no
  implementation code. A short pseudocode or state-transition block beats a paragraph
  describing the same steps.
- Libraries: recommend only widely used, actively maintained open source. Name rejected
  candidates and why (archived, commercial, single-maintainer).
- Report limits honestly: unknowns, unclassified residue, and accuracy caveats are stated
  in the doc, not hidden.
