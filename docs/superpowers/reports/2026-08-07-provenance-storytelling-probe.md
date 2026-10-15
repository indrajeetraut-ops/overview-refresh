# Feasibility probe — provenance (P4) and storytelling (A6/U5)

**Date:** 2026-08-07
**Scripts:** `utils/provenance_probe.py`, `utils/story_probe.py` (+ shell wrappers)
**Corpus:** the local stack DB — 72 sessions, 69 JSONL-capable, 44 with event blobs, one user's real Claude Code history bulk-uploaded 2026-08-06.
**Purpose:** decide whether the two deferred phases are worth building, before building either. P4.1 already required this for provenance; the storytelling half is the same question asked of A6/U5, which had no gate of its own.

---

## Verdict in one line each

| Phase | Result | Recommendation |
| :--- | :--- | :--- |
| **Provenance (P4)** | **FAILS the gate — p50 residual 70%, against a ≤10% bar** | Do not build P4.2 as designed. Build category J instead — it is what the residual turned out to be. |
| **Storytelling (A6/U5)** | **Beats pass, band fails on its specified axis, shape vocabulary is degenerate** | Build beats/acts/narrator. Re-specify the band on a pacing axis. Drop or rebuild shape classification. |

---

## 1. Provenance: the gate fails, and the reason is the finding

### The numbers

```
optimistic (turn 1 attributed)     p25  51.8%   p50  70.2%   p75  82.5%   p95  88.0%
pessimistic (turn 1 residual)      p25  64.3%   p50  75.6%   p75  84.0%   p95  89.7%
```

The gate is p50 ≤ 10%. We are at 70%. This is not a near miss to be tuned.

Where the corpus's 52M appended tokens actually landed:

| Category | Share | Meaning |
| :--- | ---: | :--- |
| E | 11.7% | assistant output re-entering as history |
| F1 | 2.5% | file reads, first read |
| F3 | 2.0% | other tool output |
| D | 1.1% | user messages |
| A+B+C+D | 0.9% | turn-1 fixed context (undecomposed) |
| **F2** | **0.5%** | **file re-reads — the flagship insight #1** |
| F4 | 0.0% | MCP tool results |
| **R** | **81.0%** | **unattributed** |

### Why it fails: the append-only premise is false

DASH §5.2 assumes context between compactions is append-only, so each request's `input + cache_write` is *new material* to be attributed. Measured against real data:

```
appended tokens (corpus):                  52,050,837
  of which prefix re-writes:               40,948,771   78.7%
  of which follow a >1h idle gap:          33,067,562   63.5%

appended / peak-context ratio:  p50 2.6x   p75 4.6x   p95 7.8x
  (1.0x is what "append-only" predicts)
```

**79% of appended tokens are re-writes of context already paid for.** The median session pays for its own context 2.6 times over. Delta attribution can only ever explain the remaining ~21% — the 70% residual isn't a bug in the algorithm, it's the algorithm's ceiling on this data.

Drilling into the largest session (863 requests) makes the mechanism unambiguous:

| Cache-write behaviour | Requests | Share of writes |
| :--- | ---: | ---: |
| small delta (<2k) | 685 | 5.5% |
| partial rewrite | 146 | 7.2% |
| **full rewrite (≥ half of context)** | **30** | **87.2%** |

And of those 30 full re-writes, **24 follow an idle gap over 1 hour** (mean gap 15h), accounting for 5.98M of the session's 8.54M appended tokens.

This is cache TTL expiry. It is **category J** in the design — currently a minor cross-cutting note under §5.3 and one Phase-1 insight (#4, cache decay).

### What this changes

The design's cost hierarchy is inverted by the data. Provenance was framed as the flagship because it powers insights #1 (re-reads), #6 (verbose output), #2 (unused MCP). Measured:

- file re-reads (F2): **0.5%** of appended tokens
- everything provenance can attribute at all: **~19%**
- cache decay (J): **~79%**, of which 63.5% is plainly attributable to idle gaps

**J needs none of P4's machinery.** No proportional distribution, no chars÷4 estimation, no calibration ratio, no residual, no ≤10% gate. It is timestamps, `cache_write_detail` TTL buckets (already in the blob), and a price delta. It is exact.

### A second, independent problem

```
sessions with an otel block:      37
sessions with matched requests:    0
```

Not one session in the corpus has a single request seen by **both** JSONL and OTel. The `both`-mode session `f59bca17` has 580 jsonl_only and 108 otel_only requests with zero overlap — OTel started recording after the JSONL portion (`otel_gap: pre_otel`).

The revised P4.1 gate specifically requires evaluating the residual against OTel truth, because a residual computed against JSONL alone is, in the design's own words, arithmetic rather than evidence. **That referee does not exist in this data.** Even a passing residual here would not have been decisive. Getting one requires a fresh hybrid capture with both collectors running over the same period.

### Recommendation

1. **Do not build P4.2/P4.3 as specified.** The gate says stop, and the probe says why.
2. **Build category J on its own**, promoted from a footnote to the primary cost decomposition. Insight #4 stops being one card in a catalog and becomes the headline: *"you paid to re-send the same context N times; ~$X of that followed idle gaps over an hour."*
3. **Revisit provenance later, scoped to what it uniquely answers** — the composition of the ~21% that is genuinely new material. That is still worth knowing, but it is not the flagship and does not justify a phase ahead of J.
4. If provenance is revisited, **capture a genuine hybrid fixture first**, or its gate remains unfalsifiable.

### Caveat, stated plainly

This is one user's history. Long idle gaps and multi-day resumed sessions are real for this user but may not generalize — a team doing short, continuous sessions would see far less J and a lower residual. The 79% figure should be re-measured on a second corpus before it hardens into a product assumption. What does generalize is the structural point: the append-only premise is an assumption the design never tested, and the gate cannot pass while it is violated.

---

## 2. Storytelling: two of three parts hold up

### Beats — healthy, build it

```
distinct beat kinds per session:  p25 3   p50 6   p75 7   max 11

setup            44/44        revision_loop    26/44
build            33/44        interruption     19/44
pause            30/44        check            19/44
survey           28/44        wrap_up          17/44
delegation        8/44        context_squeeze   5/44
stall             1/44
```

A median session fires **6 of the 11 kinds** — ample material for 2–5 acts and a narrator line that varies between sessions. The richest session yields 10 kinds and a legible arc: `survey:5 build:31 check:49 revision_loop:32 delegation:1 context_squeeze:3 pause:44 interruption:7 wrap_up:4`.

Two notes: `stall` fires in 1 of 44 sessions (rate-limit clusters are rare here — keep it, it costs nothing), and `revision_loop` fires in 26 of 44, which matters because it is the only *inference* in the taxonomy and the thing "mark as expected" exists for. It is common enough to be worth the correction affordance.

**A6's beat detection, acts and narrator line are worth building.** This part of the plan is sound.

### The personal band — fails on the specified axis, fixable

On absolute cumulative tokens, as STORY §3 specifies:

```
      at            p25            p50            p75   p75/p25
      0%         21,453         27,393         33,278      1.6x
     52%      2,963,588     16,790,172     45,689,175     15.4x
    100%      6,937,543     32,495,562    106,073,168     15.3x
```

**p75 is 15× p25.** A band that wide contains essentially every session — it cannot make "unusual" visible, which is the band's only job. The cause is that the axis mixes 20k-token sessions with 100M-token ones, so the envelope inherits the size range regardless of pacing.

Re-plotted on share-of-own-total (what fraction of this session's spend had happened by elapsed fraction *t*):

```
share-of-own-total  p75/p25 at mid-session: 3.03x
      at      p25      p50      p75
     26%     0.14     0.37     0.63
     52%     0.27     0.50     0.83
     77%     0.54     0.76     0.93
```

**15.4× → 3.0×.** The idea works; the axis doesn't. A pacing band says something real — "half your sessions have spent 27–83% of their total by the midpoint" — and a session outside it is genuinely unusual.

This is a spec change to STORY §3, not a build task. Worth making before U5 rather than discovering it during.

Also confirmed: **37 of 69 sessions have a usable curve**, one user clears `min_sessions_for_band=5`. The mixed-mode exclusion the plan warns about is real — 32 sessions would have silently dragged the envelope down had they been included as zeros.

### Shape classification — degenerate, don't ship as-is

```
cliff            34   87.2%
sawtooth          3    7.7%
short_spark       2    5.1%
```

Three of eight archetypes ever fire, and one covers 87% of sessions. The shape word next to the sparkline would say "cliff" almost every time — that is a label, not a vocabulary, and it does not earn its type role in the header or the S2 row.

Either retune the STORY §5 thresholds against this corpus (they were set analytically, never fitted) or cut the shape word and keep the sparkline. The glyph itself is fine; the naming is what's empty.

Worth noting the y-axis interacts with this: cumulative "consumption" is dominated by re-billed cache reads (the same finding as §1), so most curves are dominated by a few large re-write jumps — which is precisely what gets classified as "cliff". **Fixing the burn chart's y-axis may fix shape classification for free.** Both should be settled together.

---

## 3. What I'd do next

1. **Build category J** — exact cache-decay accounting and insight #4, promoted to primary. Highest value per unit of effort in the entire remaining scope, and no gate to clear.
2. **Settle the burn-chart y-axis** — cumulative billed tokens vs. cumulative new-material tokens. This one decision drives the band, the shape classifier and the S4 chart. Cheap to decide now, expensive after U5.
3. **Build A6's beats/acts/narrator** — validated, proceed as planned.
4. **Re-spec the band** on share-of-own-total before U5 builds against the absolute axis.
5. **Retune or cut shape archetypes** once (2) is decided.
6. **Park P4.2/P4.3.** Revisit only with a hybrid capture and a narrower goal.

Both probes are re-runnable against any DSN (`--dsn`, or `TALIUM_PROBE_DSN`) and should be re-run when a second corpus exists.
