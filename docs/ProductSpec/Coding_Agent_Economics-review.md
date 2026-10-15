# Agent Economics Platform — Consolidated Review

*Covers: "Coding Agent Economics," "Workflow\_Economics," "Claude Code Cost and Governance," plus review discussion. Original documents untouched. Final section ("MVP Feature Requests") is written in product-spec format for direct lift into the spec.*

---

## 1\. Revised Assessment

The full picture across the three documents is stronger than any single one: a vendor-neutral, cloud/on-prem-independent **economic control plane for AI agents**, entered through the coding-agent wedge (the most deployed, most measurable agent type today), generalizing to all agent workflows via outcome economics.

The defensible architecture is **sensor → brain → actuator**:

- **Sensors**: session/workflow telemetry from local collectors, vendor analytics APIs, and OTel — largely commodity.  
- **Actuators**: LLM gateways (budgets, throttles, kills) — commodity, and explicitly leveraged rather than rebuilt.  
- **Brain**: the missing layer and the product. Session-context intelligence — task classification, progress detection, waste/rework analysis — that decides *which* policy to trigger *when*. Gateways are actuators without a brain; observability tools are sensors without actuators. Nobody in the competitive landscape (documented in "Claude Code Cost and Governance") owns the brain.

**Why vendors won't close this gap** — state it as an incentive argument, not an access argument: vendors already ship reporting and basic spend controls ([Claude Code analytics](https://code.claude.com/docs/en/analytics), [Analytics API](https://platform.claude.com/docs/en/build-with-claude/claude-code-analytics-api), Cursor admin). What no vendor will ever build is the tool that optimizes against its own revenue — "route DEBUG tasks to a competitor's cheaper model." That version of the uniqueness claim is beyond debate; the access version is not.

**Moats** (in order of achievability): (1) customer usage data \+ customer git \+ platform intelligence for forecasting and waste detection; (2) cross-vendor normalized schema and single control center; (3) unit economics — cost per successful outcome — hardest to get right, most valuable when right.

---

## 2\. The Real Differentiator

Ranked. Everything else in the spec is necessary plumbing, not the reason to buy.

1. **The policy brain — context-aware control.** The single genuinely unowned layer. Gateways (LiteLLM, Portkey, Bifrost, Unity) can cap and kill but can't tell a productive long-horizon loop from a wasteful one; observability tools (Helicone, LangSmith, Datadog) can see everything but control nothing. The intelligence that reads session context — task type, progress signals, rework patterns — and triggers the *right* actuator at the *right* moment is the product. It is also the hardest part to copy, because it requires the session-level telemetry, the task semantics, and the actuator integrations simultaneously.  
2. **Task-level unit economics.** "What does a bug fix cost us, on which model, at what rework rate?" No vendor dashboard, gateway, or FinOps tool answers this. The cost/quality-by-model-×-task-type dataset (M-04 \+ M-06 \+ M-10) compounds into the moat that makes Phase-2 routing credible — routing grounded in outcome evidence rather than heuristics.  
3. **Incentive immunity.** Structural, not technical: no agent vendor will build the tool that recommends a competitor's cheaper model, and no vertically integrated agent vendor will hand economic controls to the customer. Cross-vendor consolidation — one view, one control center, cloud/on-prem independent — is therefore permanently available only to a neutral third party.  
4. **Cost-per-successful-change on the DORA axis.** DORA platforms have quality without cost; cost tools have spend without quality. Owning the metric that joins them (§7) is a differentiator with a CFO-legible headline number.  
5. **Seat-plan economics.** Everyone prices API tokens; nobody correctly models effective cost under Max/Team seats, plan depletion, or plan-vs-API break-even — yet that's where most coding-agent spend sits today.

What is *not* a differentiator, by the spec's own admission and this review: raw token/latency/cache metrics, generic dashboards, first-order caps and rate limits, and playbook-to-deterministic-rules conversion. Ship them as table stakes; don't lead with them.

---

## 3\. Competitive Landscape

Consolidated from the source docs' research plus this review's. No one occupies the brain layer; every category below owns a piece of sensor or actuator.

| Category | Players | What they do | What they lack |
| :---- | :---- | :---- | :---- |
| Vendor-native analytics | [Anthropic Claude Code analytics \+ API](https://code.claude.com/docs/en/analytics), Cursor admin, GitHub Copilot metrics API | Free usage/cost/productivity reporting, seat controls, leaderboards | Single-vendor only; will never optimize against own revenue; reporting, not context-aware control |
| LLM gateways | LiteLLM, [Portkey, Bifrost, OpenRouter, Cloudflare AI Gateway](https://www.getmaxim.ai/articles/top-ai-gateways-for-tracking-coding-agent-spend-in-2026/), [Databricks Unity AI Gateway](https://www.databricks.com/blog/ai-governance-data-ai-summit-2026-whats-new-unity-ai-gateway) | Budgets, rate limits, routing, hard kills — the actuators | No session context; see symptoms not progress; miss seat-plan traffic entirely |
| LLM observability | Helicone, LangSmith, Braintrust, Datadog LLM Obs, Grafana Cloud, Honeycomb, [Confident AI et al.](https://www.confident-ai.com/knowledge-base/compare/best-ai-agent-observability-tools-2026) | Traces, token/cost/latency analytics — the sensors | Consumption metrics, not outcome economics; no enforcement; developer-debug orientation, not C-suite |
| FinOps platforms | CloudZero, Vantage, Finout | Cloud cost allocation, forecasting, adding LLM-spend modules | No session/task semantics, no git correlation, no agent control; likely partners or acquirers |
| Engineering intelligence | [DX](https://docs.getdx.com/connectors/claude-code/), Jellyfish, LinearB, [Worklytics](https://www.worklytics.co/blog/tracking-claude-code-usage) | DORA/productivity metrics, now ingesting Claude Code data | Quality without cost axis; no token telemetry, no control plane; same buyer — partner or collide |
| Direct/emerging | [CanyonCode](https://canyoncode.ai/blog/beyond-per-token), [claudecosts.com](https://www.claudecosts.com/), TensorOps CodeMesh, [Maxim AI](https://www.getmaxim.ai/articles/top-ai-gateways-for-tracking-coding-agent-spend-in-2026/) | Coding-agent cost management/governance — the same wedge | Early; none shown to combine task-level economics \+ git quality \+ dual-plane enforcement |
| OSS / dev-local | ccusage, Claude Code Usage Monitor, [claude-cost-optimizer](https://github.com/Sagargupta16/claude-cost-optimizer), GrepAI | Personal cost visibility and burn reduction | Individual scope, no org view — validates demand and sets the free baseline M-09 must beat |

Two strategic reads: the **direct/emerging row is the one to watch** — the wedge is already contested, so speed on M-03/M-04/M-05 matters more than breadth; and **FinOps \+ eng-intelligence rows are channels first, competitors later** — export to their formats (FOCUS, DX connectors) before they build inward.

---

## 4\. Pushbacks Raised During Review

Recorded so the reasoning survives into future drafts. Status: ✔ resolved in discussion, ◐ partially resolved, ✗ open.

1. ✔ **"A runaway loop can't be detected at gateway, period" — overstated.** Gateways detect symptoms (token velocity, spikes, near-duplicate calls) and already do budget kills. What they lack is progress/context discrimination. Resolved by reframing: sensors/actuators are commodity, the brain is the product (§2.1). Keep the corrected form — the "period" version fails with technical buyers.  
2. ✔ **Uniqueness claim: access vs. incentive.** Vendors *do* expose reporting and basic controls, so "they won't give visibility" is false. Resolved: the beyond-debate version is the incentive argument — no vendor optimizes against its own revenue.  
3. ◐ **Net Value's most important terms are unmeasurable.** Customers can't supply "Business Value of Successful Outcomes" or risk cost. Resolved for Release 1 (ship cost-per-successful-outcome; keep Net Value as narrative), open for the ROI story long-term.  
4. ◐ **TAM ≠ generalizability.** "All agents will need this" is true of the market, but coding is measurable because git/CI provide free ground truth; other domains don't. Accepted as forward-looking; every post-coding vertical is gated on an outcome-truth adapter (§6.6).  
5. ✗ **Two products, two economic units.** Laptop session/task vs. server-side workflow execution — different data planes, buyers, integrations. Proposed fix (agent-run abstraction with pluggable outcome adapters) not yet adopted in the docs.  
6. ✗ **Seat-plan enforcement gap.** "We leverage gateways for control" doesn't cover seat traffic, which is most coding-agent spend. Mitigated in M-08 (dual enforcement planes) but unaddressed in the source docs.  
7. ✔ **Friction moves from data to enforcement.** Collection is accepted; silent mid-session kills are not. Resolved: warn → throttle → approve escalation, hard kill only at runaway thresholds.  
8. ✗ **Prompt-gist privacy conflict.** Compaction stores user-prompt gists, contradicting a metadata-only trust posture. Proposed default-off/opt-in not yet reflected in the docs.  
9. ✔ **LLM classification can't do runtime control.** Too slow and too costly in the hot path. Resolved: streaming heuristics for control (M-03), LLM classification post-hoc for analytics (M-04).  
10. ◐ **MVP overlap with free vendor dashboards.** Generic session metrics duplicate what Anthropic ships free. Partially resolved by prioritization: alerting, task economics, and waste prescriptions lead; the dashboard is table stakes.

---

## 5\. Decisions Locked In (from review discussion)

1. **Wedge**: coding agents first; other agent types follow. The generalization docs are forward-looking, not release-1 scope.  
2. **Buyer**: C-suite / VP Eng. Collection from dev laptops is accepted practice; devs are neutral as long as it's frictionless.  
3. **Friction moves from data to enforcement**: devs tolerate telemetry but will revolt at silent mid-session kills that destroy in-flight agent work. Governance UX defaults to warn → throttle → approve; hard kill reserved for runaway thresholds.  
4. **Security is out of scope.** No audit/compliance/exfiltration features.  
5. **Gateway claim, corrected form**: gateways detect *symptoms* (token velocity, spikes) but cannot distinguish a productive long-horizon loop from a wasteful one — they lack session context and progress signals. The platform supplies that context and triggers gateway policy. (Avoid "can't be detected at gateway, period" — technically false and a credibility risk with technical buyers.)  
6. **DORA at team level only**, per [DORA's own guidance](https://dora.dev/guides/dora-metrics-four-keys/) — which also aligns with the dev-trust posture (no per-dev leaderboards).

---

## 6\. Open Risks Carried Forward

These remain unresolved in the docs and should be answered before a skeptical technical buyer sees the spec:

1. **Seat-plan enforcement gap.** Subscription traffic (Max/Team plans) never touches a customer gateway. Active control for that traffic requires a local enforcement path — e.g., [Claude Code hooks](https://code.claude.com/docs/en/hooks) and agent config — alongside gateway policy for API traffic. Two enforcement planes, one policy model.  
2. **Two economic units across the docs.** Coding docs: session/task on a laptop. Workflow\_Economics: server-side workflow execution. Different data planes, different buyers. Fix with one abstraction — an **agent run** with pluggable telemetry sources and pluggable outcome adapters (git adapter for coding; business-event adapters later).  
3. **Net Value's unmeasurable terms.** Execution, HITL, and failure costs are measurable; "Business Value of Successful Outcomes" and risk cost are not — customers don't know these numbers themselves. Ship **cost per successful outcome**; keep Net Value as the framing narrative.  
4. **Prompt-gist privacy conflict.** The compaction step stores gists of user prompts, which conflicts with a metadata-only trust posture. Resolution: prompt-content collection off by default, org-level opt-in, disclosed to devs.  
5. **Realtime vs. LLM classification.** LLM-based task classification is itself token spend and too slow to kill a runaway loop mid-flight. Split: cheap streaming heuristics (token velocity, repeated file reads, no-diff progress, sub-agent fan-out) for runtime control; LLM classification as post-hoc batch for analytics. The docs' System Cost (SC) term covers the spend but not the latency split.  
6. **Vertical outcome adapters.** Coding is measurable because git/CI provide free ground truth; other domains don't. The expansion thesis needs a per-domain outcome-truth strategy (deterministic checks, business-event confirmation) — acknowledged as forward-looking, but it's the gating item for every vertical after coding.

---

## 7\. DORA Mapping for Quality Measurement

The core move: **DORA supplies the quality/velocity denominators; platform telemetry supplies the cost numerator.** No one else can put both on the same axis.

| DORA key | Platform signal | Economic metric |
| :---- | :---- | :---- |
| Deployment frequency | Agent-assisted PRs merged/deployed (git \+ CI/CD events) | Cost per deployment |
| Lead time for changes | Session-start → merge time, per task type | Cost per unit of lead time; agent vs. non-agent delta |
| Change failure rate (CFR) | Rework signals: reverts, hotfixes, fix-commits within N days, traced to originating session/task | Cost-weighted CFR — remediation spend as % of total spend (the C\_f term of the Net Value model, made concrete) |
| Failed-deployment recovery time | Time \+ token cost of the fix session traced to the failure | Remediation cost per incident |

Design principles for the section:

1. **Headline metric: cost per successful change** — a change that merged and survived N days without revert or hotfix. This is the Net Value model instantiated for coding, in one number a CFO understands.  
2. **DORA as calibration, not realtime.** DORA metrics lag by weeks. They are the slow ground truth that validates fast session-level proxies (rework rate, auto-accept rate). Proxies that fail to correlate with CFR over time get dropped — this two-tier structure is also the defense against gamed metrics. (Relevant context: [DORA's State of DevOps research](https://dora.dev/research/) has found AI adoption can raise throughput while degrading delivery stability — exactly the failure mode cost-weighted CFR would surface.)  
3. **Team-level only.** Never individualized, per DORA guidance and the dev-trust posture.

New integration requirement this creates (not in any current doc): **deploy and incident events** — CI/CD webhooks, GitHub deployment events, revert detection — beyond plain git activity.

---

## 8\. MVP Feature Requests (Release 1 — proposed spec section)

*Scope: coding agents. Claude Code first (richest telemetry: local JSONL, [OTel metrics](https://code.claude.com/docs/en/monitoring-usage), [hooks](https://code.claude.com/docs/en/hooks), [Analytics API](https://platform.claude.com/docs/en/build-with-claude/claude-code-analytics-api)); Cursor/Codex collectors fast-follow. Deliverable: visibility \+ intelligence \+ first-order control. Second-order control (auto model selection, policy engine) deferred to Phase 2\.*

| RQ | Pri | Desc |
| :---- | :---- | :---- |
| **M-01** Unified Collection Layer | 1, MVP | Normalized **agent-run schema** across sources: local collector (Claude Code JSONL/OTel), vendor admin APIs ([Anthropic usage/cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)), optional gateway logs. Zero-config install; metadata-only by default (tokens, models, tools, files touched — no prompt content). Prompt-gist collection is org-level opt-in, disclosed to devs. This schema is the foundation for every other RQ and the later multi-agent expansion. |
| **M-02** Session Metrics & True-Cost Accounting | 1, MVP | Per session: cost, model mix, length, sub-agents, tools/MCPs, cache efficiency, context overrun, files changed. Must model **seat-plan economics**: plan depletion, effective per-token cost under Max/Team seats, plan-vs-API break-even. Vendor dashboards price API traffic; nobody prices seats correctly — differentiator. |
| **M-03** Anomaly & Runaway-Loop Alerting | 1, MVP | Realtime **streaming heuristics** (no LLM in the hot path): token-velocity spikes, sub-agent fan-out, repeated file re-reads, no-progress loops (tokens burning, diff unchanged). Alerts to Slack/email with session context and estimated burn rate. This is the "brain" signal gateways lack, and the feature the 8-dev startup in the user stories actually needs — an alarm, not a dashboard. |
| **M-04** Task Classification & Cost-per-Task | 1, MVP | Post-hoc batch pipeline per the design doc: boundary clustering (time gaps, sub-agent launches, tool-call patterns) → compaction → LLM classification into BUG\_FIX / IMPLEMENT / PLAN / RESEARCH / DEBUG / GIT\_OPS / MISC. Output: **cost, length, model mix, and outcome per task type** — the unit-economics layer no vendor dashboard has. Classification spend is tracked as System Cost. |
| **M-05** Waste Detection & Savings Recommendations | 1, MVP | Prescriptions, not reports: low cache-hit sessions and why; context-bloat sources (oversized CLAUDE.md, chatty MCPs); model-overkill findings ("RESEARCH tasks on frontier model, projected −N% on cheaper tier"); loop waste quantified in $. Each finding carries estimated recoverable spend. **Release success metric: identify ≥15% recoverable waste within 30 days of install.** |
| **M-06** Productivity & Quality via Git \+ DORA | 1, MVP | Session→PR attribution (branch names, commit trailers, time windows). PR opened/merged/refused; **rework within N days** (reverts, hotfixes, fix-commits) as the quality proxy. Team-level DORA mapping per §7, requiring CI/CD deploy \+ incident event ingestion. Headline: **cost per successful change**. Every volume metric paired with a quality counter-metric to prevent rewarding tokenmaxing. |
| **M-07** Enterprise Dashboard, Chargeback & Forecast | 1, MVP | Single pane across users/teams/projects/models/agents. Showback/chargeback by team and project; CSV \+ [FOCUS](https://focus.finops.org/)\-format export into the FinOps stack. Budget-vs-actual and trend-based spend forecast (customer usage \+ customer git \+ platform intelligence). Answers "can't predict or plan usage" — currently problem \#2 with no requirement against it. |
| **M-08** First-Order Governance | 1, MVP | Token caps and rate limits per user/team/project/time. **Two enforcement planes, one policy model**: gateway integration (LiteLLM/Portkey-class) for API traffic; local hooks for seat-plan traffic. Escalation UX: warn → throttle → require approval; hard kill only at runaway thresholds (protects in-flight dev work — the actual adoption friction point). |
| **M-09** Dev-Local Personal View | 2, MVP-if-possible | Private, laptop-local: personal plan optimizer ("Pro saves you $N/mo"), session token breakdown, prompt-efficiency tips. Never leaves the machine unless the dev shares it. Cheap to build on M-01's collector; converts the monitored into users, answers the spec's open question on dev value, and is the PLG hook. |
| **M-10** Model-Usage Evidence Report | 2 | Not auto-routing (Phase 2). Release 1 ships the **evidence**: cost/quality/rework by model × task type from M-04 \+ M-06. This dataset is what makes Phase-2 routing credible and is itself a moat — task-aware, outcome-grounded routing data nobody else has. |

**Explicitly deferred from Release 1**: auto model selection and routing enforcement (Ph2, built on M-10 evidence); full policy engine (Ph2); non-coding agent adapters (forward-looking docs); Business Rules Guard / playbook-to-deterministic-rules (generic, low differentiation — Ph2+); all security/audit features (out of scope per decision).

---

## References

1. [Claude Code team analytics dashboard](https://code.claude.com/docs/en/analytics) — Anthropic  
2. [Claude Code Analytics API](https://platform.claude.com/docs/en/build-with-claude/claude-code-analytics-api) — Anthropic  
3. [Claude usage & cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) — Anthropic  
4. [Claude Code monitoring / OpenTelemetry](https://code.claude.com/docs/en/monitoring-usage) — Anthropic  
5. [Claude Code hooks](https://code.claude.com/docs/en/hooks) — Anthropic  
6. [DORA four key metrics](https://dora.dev/guides/dora-metrics-four-keys/) and [DORA research](https://dora.dev/research/) — DORA/Google Cloud  
7. [FOCUS — FinOps Open Cost & Usage Specification](https://focus.finops.org/) — FinOps Foundation  
8. [The token bill comes due](https://techcrunch.com/2026/06/05/the-token-bill-comes-due-inside-the-industry-scramble-to-manage-ais-runaway-costs/) — TechCrunch, June 2026  
9. [Top AI gateways for tracking coding-agent spend](https://www.getmaxim.ai/articles/top-ai-gateways-for-tracking-coding-agent-spend-in-2026/) — Maxim AI  
10. [Unity AI Gateway governance](https://www.databricks.com/blog/ai-governance-data-ai-summit-2026-whats-new-unity-ai-gateway) — Databricks  
11. [DX Claude Code connector](https://docs.getdx.com/connectors/claude-code/) — DX  
12. [Loops — long-horizon agent execution](https://x.com/mvanhorn/article/2063865685558903149) (cited in source docs)  
13. [CanyonCode: Beyond per-token](https://canyoncode.ai/blog/beyond-per-token) and [claudecosts.com](https://www.claudecosts.com/) — competitors listed in source docs  
14. [claude-cost-optimizer](https://github.com/Sagargupta16/claude-cost-optimizer) — OSS, from source-doc landscape  
15. [Managing AI coding token costs in 2026](https://blog.exceeds.ai/ai-coding-token-costs-2026/) — exceeds.ai

