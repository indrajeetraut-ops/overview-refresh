# Slideware Core Concepts — Talium Guard / code-econ

*2026-10-02. Handoff brief for a separate chat session building the deck. Self-contained: paste it in whole. Every claim is drawn from repo docs or built behavior; the source for each is named so you can ask for the detail. Items marked **[verify]** are not confirmed in the repo and need a number or a decision from the founder before they go on a slide.*

---

## 0. One-sentence positioning

**Talium measures what AI coding agents actually cost and what they actually produce, from the agent's own transcript — so engineering leaders can optimize token *efficiency and ROI* instead of just tracking token *spend*.**

Framing line: *tokenomics demands efficiency-maxing and ROI measurement, not tokenmaxing.*

---

## 1. The problem (the "why now")

Coding agents (Claude Code, Codex, Cursor) are adopted widely; cost is rising and in places running away (anecdote: a large company consuming a year's token budget in ~3 months). CIOs, CTOs and VPs of Eng are caught between adoption pressure and cost pressure.

Four buyer pains:
1. **No visibility** into usage by task, session, user, project. Usage patterns are the first ingredient of efficiency.
2. **No predictability** — customers can't forecast or plan usage.
3. **No ROI measurement** — velocity, quality/size, and waste are unmeasured.
4. **No control/governance** — sudden spikes (model change, abnormal usage); long-horizon loops and goals spawning unbounded sub-agents, tokens and security exposure.

Individual-developer pains: *which plan is cheapest (Max / Pro / Basic / combo)? where is my waste?*

**Why now — forces pushing cost up:**
- Frontier models use bigger reasoning budgets and more tokens.
- Vendor subsidies are temporary; monetization pressure is coming.
- Long-horizon autonomous loops compound context and drain budgets fast.
- Model proliferation with no neutral layer pairing the right model to the right task.
- Everyone is now a builder; only the largest enterprises can govern this in-house, so the rest need an external platform.

Field evidence: an 8-dev startup on Cursor — VPE/CTO feel the pain, nobody has bandwidth to find the "why". Devs will follow cost guidelines happily but don't want to lose model choice. Senior engineers already run goals/loops that spawn thousands of sub-agents.

*Source: `ProductSpec/Coding_Agent_Economics.md`*

---

## 2. Personas

| Persona | Role in the product | Slide message |
|---|---|---|
| **Buyer** — CIO/CTO/VP Eng | Feels the cost pressure; wants spend, ROI, governance | "See where the money goes and what it bought" |
| **Developer** — user and data source | Collector runs on their laptop; **must be able to opt out** | "A private coaching view that benefits *you*, never a manager-spy tool" |

Open question the deck must answer: *what is the direct value to the individual developer?* Devs resist being watched; this is the adoption friction. Current answer: a personal dashboard (own spend, rhythm, session stories, practice insights) with **non-judgmental language by design** (see §6).

---

## 3. The core insight — the Fidelity Advantage ⭐ (the differentiator)

Every competitor sees agent work from one of four layers, and the layer sets a ceiling on what they can ever measure:

| Vantage | Who | Structurally cannot see |
|---|---|---|
| Agent-vendor analytics API | DX, Jellyfish, Faros, Waydev | Anything sub-daily; ordering; causality; sub-agents; non-edit tools. Grain is *one user, one UTC day* |
| OTel telemetry | Grafana, Datadog | Metrics are aggregates; no file paths, tool inputs, per-turn identity |
| Router / gateway / proxy | Weave Router, Portkey, LiteLLM, Helicone | **Provenance** — sees a 40k-token prompt with no idea what assembled it; blind to local compaction, hooks, seat traffic |
| Git / repo | Onro, Exceeds, Jellyfish | Process and everything **uncommitted**; attribution is probabilistic (e.g. "confidence 0.96") |
| **JSONL transcript — us** | **Talium** | *Nothing structural* — the agent's own append-only causal record |

**Key line:** *The transcript isn't more of the same data at finer grain. It's a different kind of data — a causal graph with provenance, covering work that never reached the wire or the repo.*

**"We own the substrate"** (not "we own the brain"). Competitors' productive/inefficient/wasteful buckets can be derived by us *as a consequence* of a higher-resolution substrate.

**Second argument — the vendor skew:** the agent vendor defines the measurement surface its own product is judged on. Every metric in vendor APIs is an adoption-flattering numerator (lines added, commits, PRs, acceptance rate); none is a waste denominator (compaction churn, cache decay, retry burn, sub-agent fan-out, abandoned edits). Their ceiling is set by someone else's product decisions.

*Source: `ProductSpec/Fidelity-Advantage.md`*

---

## 4. What the substrate lets us do (capability slides)

Ordered by defensibility — each is something a competitor's architecture forbids, not just something they haven't built.

1. **Token provenance — cost as an accounting identity.** Decompose input tokens by origin (system prompt/CLAUDE.md, MCP schemas per server, re-read files, history, compaction survivors, sub-agent returns). Parts sum to the invoice.
   - Contrast slide: *Faros: "wasteful: $312k — patterns include duplicated context, loops."* vs. *Us: "Of $2.1M: $340k MCP schemas, $180k of it three servers with zero calls in 90 days; $220k files re-read in-session; $190k compaction churn; $95k retries on 429s."* **Illustrative copy from the spec, not real customer data — label it so.**
2. **Real-time convergence vs. thrash — no git, no waiting.** Hunk-overlap churn distinguishes a converging refactor from a thrashing loop live; vendor caps (e.g. a 200-sub-agent counter) can't discriminate. Competitors' quality proxies are 7–30-day git lookbacks.
3. **Exact attribution to the hunk.** prompt → turn → tool call → edit → file → commit as recorded facts; "this 12-line function cost $4.30 across 3 attempts."
4. **Dark waste — work never committed.** Agent-written code that never reached a commit; invisible to every git-layer tool by construction. Claim shape: "N% of what your agents wrote never reached a commit, at $X" — **[verify: needs a real measured number before use]**.
5. **Supervision-mode-adjusted quality.** Acceptance rate is confounded by auto-accept mode; nobody else knows the mode. We segment quality by permission/plan mode. A sales weapon.
6. **Cache-decay & compaction-regression detection.** Priced cache-TTL waste; detecting a *vendor's* bad release from the customer's own spend curve (e.g. documented Codex compaction regression ≈ tripling tokens). Proof of neutrality no vendor can supply.
7. **Retry / rate-limit waste.** Tokens burned on 429/529 attempts, separated from real work.

---

## 5. Product — what is built (as of 2026-10)

- **Collector** on dev machines reads agent JSONL transcripts (Claude Code first); optional **OTel** intake. Org chooses collector, OTel, or both — a standing deployment mode, not a warm-up state. OTel-only collapses the product to the headline cost/usage view (no session storytelling).
- **Causal graph fold** per session → every session figure derived from the graph. Reconciliation: deduped JSONL matches OTel exactly per-request; each source has a distinct gap, hence the hybrid.
- **GitHub integration** — session↔PR linkage via recorded commit shas; identity directory (roster ↔ GitHub login); issue-tracker linkage.
- **Manager dashboard (12 screens)** — spend, people, teams, repos, PRs, seats, recoverable spend, peer comparison, quality.
- **Individual dashboard** — rhythm (streak, heatmap), session "stories", practice insights; tone rule enforced.
- **Deployed** as a team demo (demo.talium.ai); a synthetic-org dataset exists for demos.

*Take screenshots from the live demo or `docs/design/mockups/` (`dashboard-ux-mockup.html`, `session-stories-concept.html`, `dashboard-s1-rhythm-concept.html`) — don't re-draw.*

---

## 6. Principles that must show up in the deck (binding product decisions)

1. **No judgmental language to developers.** Words like "waste" are banned in developer-facing UI ("recoverable", "signal", "opportunity"). Devs must never feel judged.
2. **No content in telemetry** — counts, paths and timings only; no prompts or code leave the machine. *(Slide-safe claim: "metadata, not source" — confirm exact wording with founder.)*
3. **Opt-out for developers.**
4. **Honest numbers over big numbers.** A headline must never exceed total spend. Real example: the first "recoverable" figure was $2,919 on $1,213 spend and was redefined — a story that shows rigor.
5. **Cache-read carry is never waste.** Telling users to avoid caching would increase cost. Recoverable = actual − do-it-well counterfactual.
6. **Compare like with like.** Peer metrics use *linked* work only (PRs with linked sessions), never spend ÷ all output; zero telemetry ≠ free.
7. **Spend in dollars, not tokens.** Tokens aren't money (cache reads inflate token counts); seat utilization is in USD.

---

## 7. The metric vocabulary (for a "how we measure" slide)

| Concept | Definition |
|---|---|
| **Recoverable spend** | Three disjoint, priced buckets: (1) cache expired on resume (write−read delta on the reload request), (2) cache busts (≥50% rewrite while warm, excluding compaction/model switch), (3) interrupted replies. Capped at session cost. |
| **Signals (no $)** | Rework / revision loops (a file edited ≥3× in a session); model-fit (over-sized model) — coaching, no dollar claim |
| **Cost per outcome** | $/KLOC and $/PR over **linked** work, with minimum-sample thresholds |
| **Quality 2×2** | Making-cost (PR cost vs. the repo's own 90-day p75) × outcome quality |
| **Seat utilization** | Cost-based: team budget ÷ members, scaled to the window; under 40% / over 95% bands; room-to-save needs ≥10% share *and* ≥$10/30d |
| **Session task types** | Research, debug, fix, implement, plan, misc |

---

## 8. Competitive landscape (one-slide summary)

- **Tier 1, same buyer:** **Weave** ($13.5M Series A, Jul 2026; learning router, "return on token spend") — closest competitor. **Onro** — closest architecturally (local collector, diff-level attribution, $25/dev/mo). **Faros** — Token Intelligence (productive/inefficient/wasteful), no dev-machine install.
- **Tier 2, incumbents extending in:** DX, Jellyfish, Waydev, Exceeds.
- **Commodity:** CodeBurn (OSS) scans for waste patterns free; vendor-native budget caps (Claude Code `--max-budget-usd`, sub-agent caps) arrived July 2026.
- **What's table stakes now (don't pitch as differentiators):** vendor neutrality, basic waste scan, first-order caps.
- **What survives as ours:** seat-plan economics, cost-per-successful-change, progress-aware loop discrimination, and — above all — the transcript-fidelity substrate (§3–4).

*Source: `ProductSpec/Competitive-Landscape-2026-07-30.md` (research dated 2026-07-30; competitor facts go stale — recheck before external use).*

---

## 9. Roadmap framing

- **Now (MVP):** visibility + productivity + quality: per-session metrics, PR linkage, task classification, manager and individual dashboards.
- **Next (Phase 2, needs LLM gateway + Talium Policy):** governance — real-time quota (hard stop/throttle/approval for long loops), model auto-selection (user can override), planning and cost budgeting.
- Note: governance is a *later* act on the same substrate; don't over-promise it. Competitors already ship routing, so lead with measurement.

---

## 10. Suggested deck spine (~12 slides)

1. Title + one-sentence positioning
2. The problem: spend is rising, ROI is invisible
3. Why now (four forces)
4. Who it's for (buyer / developer, with the opt-out promise)
5. How everyone else sees agent work (four vantage points)
6. The transcript: a different *kind* of data
7. Token provenance — cost that sums to the invoice (contrast slide)
8. Beyond cost: thrash vs. convergence, dark waste, supervision-adjusted quality
9. The product (screens)
10. How we keep numbers honest (§6 principles + recoverable-spend story)
11. Landscape and where we sit
12. Roadmap + ask

---

## 11. Gaps to resolve before the deck is final **[verify]**

- Real customer/synthetic **headline numbers** (spend, recoverable %, dark-waste %) — none are certified for external use; the §4.1 dollar example is illustrative.
- **Pricing / packaging**, go-to-market, TAM ("X companies" in the source is a placeholder).
- **Developer-value answer** (the open question in §2).
- Audience: investors, design partners, or customers? Changes slides 2, 10–12.
- Which agents beyond Claude Code are actually supported today.
- Vendor-neutrality and privacy claims: legal/security sign-off on wording.

---

## 12. Source map

`docs/ProductSpec/Coding_Agent_Economics.md` (problem, personas, spec) · `Fidelity-Advantage.md` (differentiation) · `Competitive-Landscape-2026-07-30.md` · `docs/design/dashboard-ux-blueprint.md`, `user-dashboard-design.md`, `session-storytelling.md` · `docs/design/notes/2026-08-28-efficiency-waste-signals.md` (recoverable definitions) · `docs/plan/2026-09-26-cost-based-seat-utilization.md` · `docs/design/mockups/` (visual contracts).
