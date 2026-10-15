---
name: my-design-doc
description: Use when asked to write or revise an architecture/design document, RFC, or "design thoughts" doc — e.g., a markdown doc in a docs/design folder describing a system, service, module, or pipeline.
---

# Writing Design Docs

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

- Terse and compact. Selective about content, but what's included is complete sentences —
  no fragments or arrow-chain shorthand.
- **Contracts get full structural detail.** Schemas, message/event structures, wire formats,
  and module interfaces are the crispest part of the doc, not a summary. Spell each one out
  as an annotated JSON skeleton: every field present with its type and constraints stated as
  the field's value; mandatory vs. optional parts explicitly marked (`| null` = key present
  but nullable, `| absent` = key omitted when inapplicable); one concrete example instance
  with realistic values; and the normative evolution rules (additive-only, must-ignore
  unknown fields, namespacing). A reader must be able to implement the contract from the doc
  alone.
- **Algorithms and logic stay prose** — described with prose, tables, and diagrams; no SQL,
  no library snippets, no implementation code. At most one compact pseudocode block for a
  genuinely core algorithm.
- Libraries: recommend only widely used, actively maintained open source. Name rejected
  candidates and why (archived, commercial, single-maintainer).
- Report limits honestly: unknowns, unclassified residue, and accuracy caveats are stated
  in the doc, not hidden.
