# Token Intelligence — Competitive Landscape & Differentiation

*Research date: 2026-07-30. Companion to `Coding_Agent_Economics-review.md`, which this document updates in several material ways. All competitor claims below are from vendor sites, docs, or press as of this date — marked **[verified]** where read directly, **[reported]** where sourced from secondary coverage.*

> **Superseded in part by `Fidelity-Advantage.md`.** §4 below derives differentiation from market whitespace and treats the local collector primarily as an actuation path. That framing undersells the JSONL transcript layer as an *information* asset — the asymmetry that makes the differentiation architecturally defensible rather than merely unoccupied. Read §3 (table stakes) here as written; read §4 alongside `Fidelity-Advantage.md` §4, which demotes D3, reframes D2, and replaces "we own the brain" with "we own the substrate."

---

## 1. Headline: the landscape moved since our review doc

Three claims in `Coding_Agent_Economics-review.md` are now out of date and are credibility risks if a technical buyer or investor hears them:

| Our claim | Status as of 2026-07-30 | Evidence |
| :---- | :---- | :---- |
| "Nobody in the competitive landscape owns the brain" (§2.1) | **False.** Faros ships session-quality token classification; Weave ships a learning router. Both are brain-layer products. | Faros Token Intelligence; Weave Router |
| M-10 model-usage evidence is a moat, routing deferred to Ph2 | **Shipped by a funded competitor.** Weave Router auto-routes prompts to cost-efficient models, ~3ms overhead, learns from org feedback, and recommends cheaper models from production data. Our Phase 2 is their Phase 1. | weaveos.com |
| M-05 success metric: "identify ≥15% recoverable waste" | **Commoditized at $0.** CodeBurn (OSS, 8.9k★, 36 tools) scans for 11 waste patterns, each with a dollar estimate and a paste-ready fix. | getagentseal/codeburn |
| M-08 first-order governance (caps, rate limits) as MVP differentiation | **Vendor-native since July 2026.** Claude Code v2.1.216–220 added `--max-budget-usd`, `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` (default 200), `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` (default 200); Model Catalog does per-team budget caps. | Claude Code release notes [reported] |

The incentive-immunity argument (§2.3) survives as *true* but no longer as *distinctive* — Onro, Weave, Jellyfish and Faros all market vendor-neutrality explicitly. Onro even runs the same analogy we use ("same reason Datadog beat CloudWatch"). Treat neutrality as table stakes messaging, not a differentiator.

**What survives intact:** seat-plan economics (§2.5), cost-per-successful-change on the DORA axis (§2.4), and progress-aware loop discrimination (the useful half of §2.1).

---

## 2. Competitor profiles

### Tier 1 — direct, funded, same buyer

**Weave** (weaveos.com, formerly workweave.dev) — *the closest competitor.* **[verified]**
- $13.5M Series A led by Standard Capital, July 2026; $4.2M seed prior (Moonfire, Burst, YC). ~$17.7M total.
- Customers: Robinhood, PostHog, Reducto; 500+ orgs, 20,000+ engineers, 50M+ prompts, 2M+ PRs analyzed.
- Surface: AI usage across 50+ tools, agent observability, "Dev FinOps," DORA + SPACE + surveys, per-engineer AI impact, code output/quality/review analytics.
- **Weave Router** — classifies each prompt, routes to cost-efficient model, learns from feedback, ~3ms overhead, proxies Anthropic/OpenAI/Google.
- **Wooly** — NL agent over your data, available in-app and via MCP.
- Metrics: cost-per-hour of completed engineering work; "return on token spend"; benchmarks across 1000+ orgs.
- Posture: "Tokens show consumption, not value." Explicitly anti-tokenmaxxing — our exact framing.
- SOC 2 Type II, GDPR, HIPAA, SSO (SAML/OIDC), SCIM.

**Onro** (onro.ai) — *closest architecturally.* **[verified]**
- Local collector on dev machines reading telemetry AI tools already emit; "no source code leaves the machine, no root, equivalent to git log metadata." Same install model as ours.
- **Diff-level attribution with confidence scores** (shows `confidence 0.96`), line-by-line AI vs HUMAN, with the originating prompt attached. High-confidence for Copilot/Claude Code, inference-augmented for Cursor/Windsurf.
- Headline KPIs: AI-attributed commits %, throughput lift (AI vs human), rework on AI code %, cost per accepted suggestion.
- Screens: Overview / Teams / Repos / Tools / People / Settings; code-authored-by-source breakdown; **teams ranked by AI ROI** with a prescriptive Recommendation column ("Pair review on Go", "Expand seats", "Consider Claude Code").
- Differentiators claimed: champion detection, best-practice playbook transfer, **per-developer private coaching view — opt-in, "never a manager spy tool."**
- Coverage table: Copilot, Claude Code, Cursor, Windsurf, Codex supported; Gemini Code Assist Q3 2026.
- Pricing, public: free 14 days ≤25 devs; **Pro $25/dev/month** ≤200 devs; Enterprise custom with SSO/SAML + self-hosted. 15-min setup, 10→5,000+ devs.

**Faros AI** — *enterprise incumbent, launched Token Intelligence as a feature.* **[verified]**
- Tokens bucketed **productive / inefficient / wasteful**, each with a dollar figure, efficiency ratio trended over time, team vs company baseline.
- Session-level analysis: duplicated context, agent loops, frontier-model overuse, caching/retry opportunities.
- **Keep / scope / cut verdicts** per tool and model; cost-efficiency ranking by model×tool.
- Spend by team/tool/model, MoM growth, automatic outlier detection, budget variance per team.
- NL querying over the whole Faros dataset. 100+ integrations (GitHub, Jira, ADO, GitLab, CI/CD, PagerDuty, Slack).
- **Explicitly no developer-machine install** — relies on built-in agent telemetry and vendor APIs.
- SOC 2 Type II, ISO 27001, GDPR, CSA STAR; SaaS / hybrid / on-prem; RBAC.

### Tier 2 — engineering-intelligence incumbents extending into token spend

| Vendor | What they ship | Notable |
| :---- | :---- | :---- |
| **Jellyfish** AI Impact | Multi-tool comparison, adoption insights (auto-detected, no tagging), enablement/power-user patterns, **AI token spend dashboard** by tool/team/initiative, impact insights, AI workflow insights, **auto report builder** for execs. Dedicated Claude Code dashboard. | Published benchmark: cost per merged PR **$0.28 → $89.32** across 12,000 devs / 200 companies. Owns the reference dataset. |
| **Waydev** | AI Adoption / AI Impact / AI ROI modules + **Waydev Agent** (plain-English questions like "which team gets the most ROI from Cursor?"). **AI Checkpoints**: which agent wrote each change, tokens consumed, cost per PR, acceptance rate, and whether it deployed. Cost per commit / PR / feature. Also DORA, project costs, cost capitalization. | Priced per active contributor, annual. YC-backed. |
| **DX** | Claude Code connector via Anthropic Console/Analytics API: daily user metrics, acceptance rates, model breakdowns, per-model token consumption and cost. Core 4 framework. | API-only ingest — no session interiors. Published median PR-throughput gain of 7.76%. |
| **Swarmia** | Unifies **seat licenses + token usage** in one view; tracks licenses, active users, **idle seats**; ties usage to review speed, cycle time, deploy flow. | Closest anyone gets to seat economics today — but it's utilization, not effective-cost modelling. |
| **Exceeds AI** | Repo-access only (GitHub/GitLab read-only, 15 min, never stores code). AI-usage diff mapping (AI vs human lines), acceptance / rework / incident / **code survival** rates over 7–30 days, leaderboards, per-engineer profiles. | Runs an aggressive comparison-content engine ("DX vs Jellyfish: why neither tracks AI code impact"). |

### Tier 3 — adjacent layers (channels or partial overlap)

- **FinOps:** CloudZero (Claude Code plugin showing **in-session live cost** — "$47 of your $75 daily budget"; CostFormation tag-free allocation to teams/projects/sprints; anomaly detection for the 2am agentic loop; first integration with Anthropic's Usage & Cost Admin API; AI Hub links spend spikes to commits and PagerDuty incidents). Attribute (attrb.io), Vantage, Finout.
- **Gateways / actuators:** Portkey (hard USD caps on virtual keys), LiteLLM, Bifrost, Cloudflare AI Gateway (dollar spend limits, June 2026), Kong, OpenRouter.
- **Routers:** OrcaRouter (production LinUCB bandit, independently confirmed cost/quality tradeoff), RouteLLM, Not Diamond, MTRouter, Plano (prices warm-cache discards, vetoes switches exceeding overhead caps), vLLM Semantic Router. Cursor Router and Copilot Auto are vendor-native.
- **Runtime spend control:** Revenium (spend to the cent at runtime, auto-shutoff on runaway budgets); AI Inside (budget circuit breaker: alert 70% / throttle 90% / kill 100%) [reported].
- **Governance:** Credal (uniform policy across Claude/Cursor/ChatGPT/Gemini, human approval on destructive shell commands, agent registry to revoke rogue installs).
- **SaaS management:** Torii, Zylo, Spendflo — shadow AI spend on personal cards, seat+token tracking, contract benchmarking.
- **OSS / dev-local:** **CodeBurn** (36 tools, `codeburn optimize` → 11 waste patterns with $ savings and paste-ready fixes, fully local), ccusage (15 agents), Claude Code Usage Monitor, claude-tap, ClaudeBar/CodexBar.
- **Context/token reduction tooling** (adjacent supply, shapes buyer expectations): LLMLingua, headroom, rtk, TOON, Context Mode, semantic caches (GPTCache, khazad, Redis LangCache), memory layers (Mem0, Zep, Letta, claude-mem).

---

## 3. Table stakes — what a user sees on those platforms

Anything below appears on two or more competing products. Absent it, we look unfinished in a demo. Column 3 flags where our current spec has nothing.

| # | What the user sees | Who ships it | Our RQ | Gap? |
| :---- | :---- | :---- | :---- | :---- |
| 1 | Spend by team / user / project / tool / model, MoM trend | Faros, Jellyfish, Waydev, Swarmia, CloudZero | M-07 | — |
| 2 | Team benchmark vs org baseline + automatic outlier detection | Faros, Onro | M-07 | partial |
| 3 | Cost per unit of work (per PR / commit / accepted suggestion / eng-hour) | Jellyfish, Waydev, Onro, Weave | M-06 | — |
| 4 | **Line/diff-level AI vs human attribution with a confidence score** | Onro, Exceeds, Waydev | M-06 is session→PR only | **GAP** |
| 5 | **Adoption & license economics: active users, idle seats, utilization** | Swarmia, Jellyfish, Waydev, DX | none | **GAP** |
| 6 | Rework / revert / code-survival rate on AI code over 7–30 days | Onro, Exceeds, Jellyfish | M-06 | — |
| 7 | Multi-tool side-by-side on one yardstick + published coverage table | Onro, Jellyfish, Weave (50+), Faros | M-01 | breadth risk |
| 8 | Waste findings with $ savings **and a ready-to-apply fix** | CodeBurn (free), Faros | M-05 | must beat free |
| 9 | Cache efficiency, context bloat, duplicated-context detection | Faros, CodeBurn, Langfuse | M-02 / M-05 | — |
| 10 | Spend anomaly / spike alerting | CloudZero, Faros, Revenium | M-03 | — |
| 11 | Budget caps and enforcement | Portkey, Cloudflare, Revenium, **vendor-native** | M-08 | now table stakes |
| 12 | **NL analyst agent over your own data** | Faros, Weave (Wooly), Waydev (Agent) | none | **GAP** |
| 13 | **Board-ready narrative report, auto-generated** | Jellyfish, Waydev, Exceeds | M-07 has CSV/FOCUS only | **GAP** |
| 14 | Per-developer private view / coaching, opt-in | Onro, Exceeds | M-09 at **Pri 2** | **priority wrong** |
| 15 | Session drill-down: prompt → diff → outcome | Onro, Faros, Langfuse | M-02 / M-04 | — |
| 16 | Chargeback / showback by team and project | Faros, CloudZero, Jellyfish | M-07 | — |
| 17 | **SOC 2, SSO/SAML, RBAC, self-host option** | Faros, Weave, Onro, Credal | none | **GAP** |
| 18 | Trust posture stated up front: <15-min setup, no code leaves machine | Onro, Exceeds, Faros, CodeBurn | implied in M-01 | must state |
| 19 | Model comparison + cheaper-model recommendation | **Weave (routes it)**, Faros (keep/scope/cut) | M-10 at Pri 2 | **behind** |
| 20 | DORA / delivery metrics tied to AI usage | Jellyfish, Waydev, Weave, Faros, Swarmia | §7 | — |

### Six concrete additions this forces

1. **Line-level attribution with confidence** (new RQ). Session→PR windows aren't enough once a competitor shows a diff with `confidence 0.96` and the prompt beside it.
2. **Seat & license economics view** (extend M-02): active/idle seats, utilization, seat-vs-usage reconciliation.
3. **NL query agent** (new RQ). All four leading products have one; it's now the expected entry point, not a luxury.
4. **Auto-generated exec/board report** (extend M-07) alongside CSV/FOCUS export.
5. **M-09 → Priority 1.** It is table stakes *and* our trust story *and* the PLG wedge. Onro has already claimed "never a manager spy tool" as positioning.
6. **Enterprise trust RQ** (new). Our review doc says "security is out of scope" — that decision is fine for audit/compliance *features*, but SOC 2 + SSO + RBAC + self-host are procurement gates for exactly the buyer we're targeting. Separate the two ideas explicitly, or we'll fail security review at every enterprise deal.

---

## 4. Where we can actually be distinct

Ranked by defensibility given the July 2026 landscape. D1 and D2 are the ones no competitor's architecture can reach.

### D1. Progress-aware long-horizon run economics — *the strongest remaining position*

Every Tier 1/2 competitor is **PR-and-session shaped**. Onro's unit is a session→commit. Jellyfish's is a merged PR. Weave's is a prompt. None of them can represent the failure mode that is actually burning budgets: a goal/loop agent running for days, spawning thousands of sub-agents, producing one PR or none.

Vendor-native guardrails are blind counters — Claude Code caps sub-agents at 200 and web searches at 200 per session. A cap of 200 cannot distinguish a converging refactor from a thrashing loop; it just truncates both at the same arbitrary number.

Ship the **agent run** as a first-class economic unit with convergence semantics: burn rate, projected cost to completion, and thrash detection (tokens climbing while diff is static, repeated file re-reads, sub-agent fan-out without merge progress). Then the brake. This needs session interiors *plus* progress semantics *plus* actuation simultaneously — which is precisely why it's hard to copy, and it directly serves the loop/goal-agent behaviour already documented in our own user stories.

### D2. Seat-plane actuation — *structurally unavailable to Weave and every gateway*

A router only reaches traffic that passes through it. Weave Router proxies Anthropic/OpenAI/Google API calls; Portkey and Cloudflare cap virtual keys. **None of them touch Max/Team seat traffic — where most coding-agent spend sits today.** Faros, by choosing not to install on developer machines at all, structurally cannot see or steer seat sessions.

We already take the collector route. That earns a control path nobody else's architecture has: local hooks and agent configuration for seat traffic, gateway policy for API traffic, **one policy model across both planes**. Keep the escalation UX from our review doc (warn → throttle → approve, hard kill only at runaway thresholds) — Onro's own FAQ shows how loudly this buyer worries about surveillance and disruption.

### D3. Cost per *surviving* change — the metric nobody owns

Jellyfish sells cost per **merged** PR. Onro sells cost per **accepted** suggestion. Merged and accepted are both cheap to game and both silent about stability. Own **cost per change that merged and survived N days without revert or hotfix**, with remediation spend traced back to the originating session.

DORA's own research finds AI adoption can raise throughput while degrading delivery stability — which means every competitor's headline metric is structurally blind to the failure their buyers most fear. That's the wedge: same CFO-legible shape, opposite conclusion when the code is bad.

### D4. Seat-plan economics engine — genuinely unowned

Swarmia does seats vs. usage and idle seats. Zylo does forecast-vs-commitment. Nobody models **effective $/token under Max/Team seats, plan depletion forecasting, plan-vs-API break-even, or per-developer plan-tier recommendation.** Everyone prices API tokens because that's what the vendor APIs hand them. This is both an enterprise differentiator and the individual-developer PLG hook (M-09), and it's the cleanest unclaimed ground on the board.

### D5. Pre-flight / predictive economics — the whole field is retrospective

Every product listed answers "what did we spend?" Weave routes reactively, per prompt. Nobody answers **"what will this cost, and what's the rework risk?"** before the work starts — a cost band and quality-risk estimate for a task or plan, derived from the org's own task×model×outcome history.

This is the direct answer to problem #2 in our spec ("customers cannot predict or plan usage"), which currently has no requirement written against it, and it is the natural consumer of the M-04 + M-06 dataset. It also reframes M-10 usefully: Weave already routes, so shipping a routing evidence report is following. Shipping *forecasting and budget-at-plan-time* is not.

### D6. Incentive immunity — keep, demote

Still structurally true: no agent vendor will recommend a competitor's cheaper model. But Onro, Weave, Jellyfish and Faros all say the same thing. Use it to neutralize vendor-native dashboards; do not lead with it.

---

## 5. Recommended positioning

**Everyone else sells a measurement dashboard to a CTO. Sell the brake pedal.**

The measurement market is crowded, funded, and has incumbents with 100+ integrations and published benchmark datasets we cannot match on breadth. A dashboard-plus-waste-report entry is dead on arrival against Onro at $25/dev/month and Weave with Robinhood as a reference.

The lane that is open: **control of long-horizon agent runs on seat-plan traffic, with progress-aware discrimination** (D1 + D2), proved out by cost-per-surviving-change (D3), monetized upward through seat-plan economics (D4) and forward through pre-flight budgeting (D5).

Sequencing implication: table-stakes items in §3 are the price of entry to a demo, not the reason to buy — build them to parity, fast and unglamorous, and spend the differentiated engineering on D1/D2. The `awesome-ai-tokenomics` map shows how much of the token-reduction supply chain is already free OSS; assume anything a single CLI can do locally will be free within two quarters, and price accordingly.

Open strategic question for the team: Weave holds the two positions our review doc named as moats #1 and #2 (the brain, and outcome-grounded routing), with $17.7M and 500 orgs behind it. Either we accept a narrower, more technical wedge (D1/D2 — recommended) or we need a defensible reason we win a head-on measurement fight. The current spec assumes the latter without arguing it.

---

## References

Verified by direct read: [Faros Token Intelligence](https://www.faros.ai/platform/token-intelligence) · [Faros launch post](https://www.faros.ai/blog/token-intelligence-for-ai-engineering) · [Onro](https://www.onro.ai/) · [Weave](https://weaveos.com/) · [Jellyfish AI Impact](https://jellyfish.co/platform/jellyfish-ai-impact/) · [Jellyfish Claude Code dashboard](https://jellyfish.co/platform/claude-code-dashboard/) · [Waydev](https://waydev.co/) · [DX Claude Code connector](https://docs.getdx.com/connectors/claude-code/) · [Exceeds AI](https://blog.exceeds.ai/ai-code-analysis-ctos/) · [awesome-ai-tokenomics](https://github.com/QuesmaOrg/awesome-ai-tokenomics) · [CodeBurn](https://github.com/getagentseal/codeburn) · [Torii: 8 spend tools for Claude Code](https://www.toriihq.com/articles/spend-management-tools-claude-code) · [CloudZero](https://www.cloudzero.com/blog/claude-code-alternatives/)

Secondary coverage: [Weave $13.5M Series A](https://techstartups.com/2026/07/28/ai-startup-weave-raises-13-5m-to-help-companies-measure-ai-coding-roi-and-end-tokenmaxxing/) · [YC/tokenmaxxing market context](https://startupfortune.com/y-combinators-bet-on-killing-tokenmaxxing-just-landed-a-135m-seed-round-as-corporate-america-calls-time-on-ai-waste/) · [Claude Code subagent depth limits & budget caps](https://www.digitalapplied.com/blog/claude-code-subagent-depth-limits-budget-caps-2026) · [Swarmia AI coding tool integrations](https://help.swarmia.com/getting-started/integrations/ai-coding-tool-integrations) · [Jellyfish token monitoring benchmark](https://jellyfish.co/library/ai-token-usage-monitoring/)
