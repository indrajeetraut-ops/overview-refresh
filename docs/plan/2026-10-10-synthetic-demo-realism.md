# Synthetic demo realism: spikes, power users, low users, a team with margin

| | |
|---|---|
| **Status** | **Done 2026-10-10** (S1 `0446a3b`..`113ec59`, S2 `75df357`..`a7bd09c`); results in §7. The targets in §3.3 were proposals the user did not review; §7 lists where they were not met. |
| **Branch** | `feat/forecast-dashboard-integration-2` (follow-up to #85) |
| **Requested by** | The user, 2026-10-10 (list in §1). |
| **Touches** | `econ-forecast/config/synthetic/org/` (example config, curves); `econ-forecast/src/forecast/synthetic/` (Layer 1: one small, optional feature, §3.1); the demo plan's measured figures (F5, T8 results). No dashboard, API or storage code. |

## 1. What was asked

1. More spikes, so the forecast's daily chart does not look calm.
2. Some PRs that cost $50 or more.
3. At least two Development users who spike often and consume a large number of tokens consistently.
4. A few low users.
5. One team that has a decent margin to spare, consistently.

## 2. What the example org looks like today (measured 2026-10-10, series ending 2026-10-09)

| Ask | Today | Cause |
|---|---|---|
| Spikes | None. The org's weekday usage varies about ±10% (coefficient of variation 0.096 over 90 days); no weekday is above 1.25 times the mean. Weekends sit at a fixed 0.04–0.05 billion tokens. | T2 set `spike_rules: []` (a v1 per-user spike has no v2 equivalent, see §3.1). Only the Development and Platform curves (about ±11% and ±19%) move the level. |
| $50+ PRs | Already there: the Economics screen shows p95 $53 and max $147 over 231 merged PRs, so about 5% (roughly a dozen) are $50 or more. | Session-level cost variation. It has to survive the changes below. |
| Heavy Development users | 16 of 20 Development users are `heavy_profile` and nearly identical: 0.31–0.42 billion tokens each over 28 days. No outlier and none spikes. | `profile_mix` gives every user of a profile the same baseline. |
| Low users | Development has 4 `light_profile` users (about 0.06 billion over 28 days each), Platform 4, QA 1. All the same level, 5–6 times lower than the heavy users. | Same. |
| Team with margin | None. October baseline against budget: Development $6.5k vs $4,000 (163%), QA $1.2k vs $1,000, Platform $1.8k vs $2,000 (2% chance of running out, so close to the line), org $9.6k vs $7,000. | Budgets are $200 per developer (D12); consumption was never tuned per team. |

## 3. Design

### 3.1 Layer 1: optional per-profile personal spikes (the one generator change)

`spike_rules` can only target teams, agents and models; one rule hits every targeted user on a date, in proportion to their baseline. There is no way to say "this user spikes often". Add two optional fields to a profile:

```yaml
profiles:
  power_profile:
    daily_input_tokens: 45000000
    spike_probability: 0.15       # per active weekday of that user; independent per user
    spike_multiplier_min: 1.5
    spike_multiplier_max: 2.5
```

- Both fields absent (every existing profile) gives byte-identical output, as for the curve binding (`econ-forecast/docs/design/series-curve-binding-design.md` §2). No `schema_version` change.
- The draw uses the existing `spikes` random stream, keyed so that a user's draws do not move when other config changes (knob independence, I2b).
- Composes multiplicatively with weekend reduction, daily variation and the curve, like `spike_rules`.
- Needs: a short addendum to `econ-forecast/docs/design/synthetic-data-generation-design.md` (the spike section), tests in `econ-forecast/tests/synthetic`, the series report counting personal spikes.
- Alternative without a Layer 1 change: team `spike_rules` only. It gives crunch days where the heaviest users take the largest share, but no user spikes "often" on their own. Not recommended.

### 3.2 Example config

| Item | Change |
|---|---|
| Profiles | Add `power_profile` (about 2.5 times `heavy_profile`, personal spikes above) and `idle_profile` (about 0.4M tokens a day, 7 times below `light_profile`). Existing profiles unchanged. |
| Development (20 users) | `profile_mix`: power 0.10 (exactly 2 users, via `allocate_integer`), heavy, mid, light and idle for the rest. The 2 power users are fixed for the whole series, so they spike often and stay large consistently. |
| Low users | Idle: 1 in Development, 2 in Platform, 1 in QA. Light users stay. |
| Team with margin | **Platform** (10 developers, $2,000): shift its mix toward mid, light and idle so the baseline lands near 60–70% of the budget. |
| More spikes | (a) Curve phases for Development, Platform and QA, with a few crunch weeks up to about 2 times a regular week at different times (curves are stretched over the series, so the shape is the same for any end date); (b) one org-wide `spike_rules` entry for occasional busy days (about 1.3–1.6 times, a few percent of weekdays), if its position does not move the forecast band with the end date (F7 found it did for dated rules; to be measured, not assumed). |
| Budgets | Unchanged: $200 per developer for every team (D12). |

### 3.3 Targets (what "done" means; to be confirmed)

Measured on a scratch dataset ending 2026-10-09, baseline October, 120 days of history.

| Check | Target |
|---|---|
| Daily org spikes | At least 3 weekday org totals at 1.4 times the 90-day weekday mean or more; at least one week whose weekday mean is 1.7 times a regular week or more; none above 2.2 times. |
| Forecast chart | The p10–p90 band is visibly wider than today; the expected line is still smooth (it is a mean), but the daily series shows irregular history. |
| Power users | Exactly 2 Development users; each at 2 times a heavy user's 28-day tokens or more; each with personal spikes on at least 10% of their active weekdays. |
| Low users | At least 4 users with 28-day tokens at 15% or less of the org's median user. |
| $50+ PRs | At least 8 merged PRs at $50 or more, maximum above $100. |
| Team with margin | Platform: October and November baseline landing at 70% of budget or less; p90 landing at 85% or less. |
| Other teams | Development over budget but not absurdly (landing 110–140% of $4,000); QA near its line; org landing 95–115% of $7,000, so the budget probability reads between 5% and 95% rather than 0% or 100%. |
| Untouched | Existing org invariants (I1–I7) and the 112 org tests green with no assertion weakened; Layer 1's full test suite green; `utils/synth_demo_refresh.sh` completes. |

## 4. Tasks

| # | Task | Notes |
|---|---|---|
| S1 | Layer 1 personal spikes (§3.1) with tests and a design note | Verifier after. |
| S2 | Example config and curves (§3.2); iterate with the 15-second loop (`synt_gen.sh --op series-create`, then forecast and cost), then one full `synth_org.sh` run | Verifier recomputes §3.3. |
| S3 | Regenerate the demo on a scratch database with `utils/synth_demo_refresh.sh`; refresh the plan's F5 figures and the T8 results; update org README | Your databases are not touched; reload `econ_synth` only when you say so. |

## 5. Consequences

- Every demo number changes again (landing figures, probabilities, run-out dates, planner gaps, PR costs). The forecast plan's F5 and the T4–T8 result paragraphs quote the current numbers and will be marked as superseded.
- ETS cannot predict future spikes. With spikes in the history it will fit a noisier level and wider bands; the expected line stays smooth.
- Regenerating takes about 10–12 minutes; the single-seed series and forecast loop takes about 15 seconds.
- The weekend band weakness (too wide, additive noise) is a forecaster property and is not changed here.

## 6. Decisions needed

1. Approve the Layer 1 change (§3.1) or pick the team-rules-only alternative.
2. Confirm or change the targets in §3.3, especially which team has the margin (Platform proposed) and how far over budget Development and the org should be.
3. Should the example keep ending "today", so the spikes are always in the recent past (yes, curves follow the end date)?

## 7. Results (2026-10-10)

**S1 (Layer 1 personal spikes)**: `Profile` gains optional `spike_probability`, `spike_multiplier_min`, `spike_multiplier_max`; absent fields leave every artifact byte-identical (golden hashes pinned in the tests, checked by an independent verifier on six configs and the org expander); a new `personal_spike` event; 378 tests in `econ-forecast/tests/synthetic`, about 72 mutants of the new code run by the verifier, no Major.

**S2 (example config)**: new `power_profile` (45M a day, 30% chance a weekday is 1.5-2.5x) and `idle_profile` (0.4M a day); Development 2 power, 1 heavy, 3 mid, 13 light, 1 idle; Platform 1 heavy, 2 mid, 5 light, 2 idle; QA 1 heavy, 2 mid, 1 light, 1 idle; curves on all three teams (Platform calm, Development crunch about 1.5x around days 62-76, QA about 1.4x around days 79-86, a larger opening week outside the dashboard windows); an untargeted busy-days rule (10% chance per weekday, 1.3-1.6x); `usage_seed` 123 changed to 41; Development and QA daily absence lowered (4%, 3%). Budgets unchanged ($200 per developer). 399 tests in `econ-forecast/tests/synthetic`.

Measured on the full refresh ending 2026-10-10 (API, baseline, October): org lands $6,717 against $7,000 (p10 $6,191, p90 $7,310, 76% likely within budget, `at_risk`); Development $4,940 against $4,000 (runs out around Oct 27); Platform $1,126 against $2,000 (`lasts`, 100% within); QA $918 against $1,000 (71% within, `at_risk`). Over a sweep of 25 end dates from 2026-09-28 to 2026-11-30 (both months): Platform 53-61% of budget with p90 at most 64%; Development 113-140%; QA 83-114%; org 92-112%. PRs on the 2026-10-09 dataset (dashboard definition): 1,469 priced merged PRs, 57 at $50 or more, 7 at $100 or more, maximum $278, median $9.62. Power users: 2.8x and 3.3x a heavy user over 28 days, spiking on 29-33% of active weekdays over the full series.

**Not met or weaker than the targets (found by the independent verifier, not hidden):**
- **The forecast line is still regular.** The daily chart's expected line is the mean of 1,000 simulated paths, so it repeats a weekly profile whatever the history looks like. The history is now livelier (weekdays $223-$476 in the last 28 days against a $328 mean, one day at 1.45x), the band is wider, but no history change makes the mean irregular. Showing a sample path is the way to do that (not built).
- **The 28-day actual chart shows modest spikes:** one weekday at 1.3x or more, none at 1.5x. Power-user spikes barely register at org level because other days are low.
- **Org weekly peak:** 1.28x (last 90 days) at 2026-10-09, not 1.4x; the 1.4-1.75x weeks are outside the dashboard windows. Larger Development crunches widened the bands to 100-200% of landing.
- **Band width depends on the end date** (the forecaster re-selects its model per fit): org October p10-p90 width is 18% at 2026-10-09, 3-8% for late-month ends, 43-62% for early-month ends; Development and QA next-month p90 reach 180-250% of budget on some dates.
- **`usage_seed` was tuned** (123 to 41, picked from 7 seeds by Development landing; the verifier confirmed that with 123 Development lands at 146% and the org at 115%). The slow regression test fails if the seed is reverted.
- **Late-November ends:** org current-month landing is 83-88% at 2026-11-14/24 (targets were defined at 2026-10-09 only).
- The landing and margin tests are marked `slow`, so a `-m "not slow"` run does not execute them.

**Superseded:** the figures in F5 and in the T3-T8 result paragraphs of the forecast integration plan describe the earlier example data and no longer match the demo.
