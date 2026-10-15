# Cache-write contract — brainstorm scratchpad (#67)

Context brief: [issue-67-cache-write-contract-context.md](issue-67-cache-write-contract-context.md).
Branch: `fix/issue-67-cache-write-contract` (off `main` dee721c). Started 2026-10-06.
Target doc: `docs/design/cache-write-contract-design.md` (cross-module change doc) plus edits to the four existing designs listed in the brief §7.

## Grounding facts (read from code, 2026-10-06)

| # | Fact | Where |
|---|---|---|
| G1 | `TokenCounts(input, cache_read, output)`, all `StrictInt \| None`; docstring says input includes writes. Contract is "version-one". | `forecast/contracts.py` |
| G2 | Dataset manifest `schema_version` is the int `1`; `read_records` rejects any other value. Record files have no own schema id. | `forecast/artifact_io.py:36-38` |
| G3 | Layer 1 splits per row/day: `cached = half_up(total × cache_ratio)`, `input = total − cached`; `output = half_up(total × output_ratio)`. `total` = processed input (input + cache_read). | `synthetic/series.py:240-246` |
| G4 | Layer 1 config: `SeriesConfig.schema_version: Literal[2]`, `BehaviorDefaults.cache_hit_ratio`, `cache_hit_variation_pp`. Unknown keys are rejected (`Strict`). | `synthetic/config.py:204,370` |
| G5 | Forecast reads/aggregates the three types in `dataset.py:374-395`, `allocation.py`, `runner.py:542`. Forecast artifacts carry int `schema_version: 1`. Each daily row has `tokens{input,cache_read,output}` and `total_tokens` = their sum. | `forecasting/*` |
| G6 | Cost: `TOKEN_TYPES` is the single tuple used by mix, rates, kernel, summaries, scenario, presentation. Artifact ids `cost-mix/1.0`, `cost-daily-expected/1.0`, `rate-sheet/1.0` etc. Major is the compatibility boundary. | `cost/schemas.py:19`, `cost/artifacts.py:223-245` |
| G7 | Rate sheet catalog already carries all five rates. `price_tokens` handles `cache_write_5m`, `cache_write_1h` directly. | `common/pricing.py` |
| G8 | **Dashboard prices a bare `cache_write` at the 1-hour rate** (`split_cache_write` case 3, deliberately conservative). Only `cache_write_detail` (per request) or explicit `cache_write_5m`/`cache_write_1h` keys get the 5m rate. | `common/pricing.py:135-163` |
| G9 | Layer 2 `session_info.models` holds one `cache_write` per model, no TTL split; `Pricer._price_channel` / `channel_micros` read the single `cache_write` key. Layer 2 requests carry one `cache_write` per request; `ttl_s: 300`. | `synthetic/org/pricing.py`, `requests.py`, `config.py:278` |
| G10 | Layer 2 invents the write split itself: `cw_total_m = min(input_combined − R_m, round(cr_total × cw_shape / cr_shape))`, i.e. writes are derived from the cache-read curve, not from a share. Reserves 1 input token per request. | `synthetic/org/requests.py:226-243` |
| G11 | Real pipeline: `graph_fold` assumes `_ASSUMED_CACHE_TTL_S = 3600` ("CC v1 writes 1h caches"). | `src/server/worker/graph_fold.py:777` |
| G12 | Budget design §4.2 `cache_write.fraction_of_input` / `share_5m` / `configured_cache_write_ratio` exist only in a superseded design; no code. | `docs/design/budget-module-design.md:119-128,293,468,584` |

## Decisions (proposed — none agreed yet)

| D# | Decision | Notes |
|---|---|---|
| D1 | Contract: add `cache_write_5m`, `cache_write_1h` (nullable ints, like the others). `input` = uncached input only. Processed input = `input + cache_write_5m + cache_write_1h + cache_read`. | Meaning of `input` changes, so this is a breaking change even though two fields are additive. |
| D2 | Bump dataset manifest `schema_version` 1→2. Reader accepts only 2 and fails with "regenerate the dataset". No dual-read. | Silent misread of a v1 `input` (includes writes) as v2 would misprice by ~1.2×; no value in keeping v1 readable. Committed example runs under `examples/` are regenerated. |
| D3 | Forecast target stays `total = sum of all token types`; token paths and fits unchanged. Layer 1 keeps `total` = processed input (input + writes + reads) so generated history is bit-identical in total. | Brief §6.3. |
| D4 | Layer 1 config gains two fields in `defaults`: write share of non-cache-read input and 1h share of writes, next to `cache_hit_ratio`. `SeriesConfig.schema_version` 2→3, fields required (no hidden defaults). | Q1 (values). |
| D5 | Forecast artifacts: `schema_version` 1→2 (allocation shares, `daily_expected.tokens`). | `input` meaning changed. |
| D6 | Cost: `TOKEN_TYPES` becomes five; `cost-mix`, `cost-daily-expected`, `cost-period-summary` `/1.0`→`/2.0`; cost reader rejects forecast artifacts at v1. `rate-sheet/1.0` unchanged (already five rates). | Cost kernel prices writes at the catalog rate; a vendor with no separate write price already falls back to the input rate (`model_pricing/build.py:487-489`). |
| D7 | Remove budget design §4.2 and its labels; edit only (no code). | G12. |

## Open questions

| Q# | Question | Status |
|---|---|---|
| ~~Q1~~ | **DECIDED 2026-10-06 (user): 1 hour** — standard for Claude Code sessions (chat has 5 min, out of scope). Layer 1 default 1h share = 1.0. Consequence: Layer 2 `ttl_s: 300` must become 3600 (bust gaps change) → new Q6. Original: What TTL does the synthetic world write?** G8/G9/G11 collide: dashboard prices bare `cache_write` at 1h; Layer 2 simulates 300 s (5m); real Claude Code writes 1h. Acceptance (brief §7) needs forecast $ == dashboard $ per model. | **Ask user.** Position: Layer 1 default 1h share = 1.0 (matches G11 and what the dashboard already assumes); Layer 2 emits the split anyway (Q3) so a non-default share still reconciles. |
| ~~Q2~~ | Layer 1 write share. **Measured 2026-10-06 on real Claude Code transcripts (`~/.claude/projects`, 124 files, 19,157 deduped requests, 9 models):** input 0.35M, cache_write 108.5M, cache_read 6,262M, output 17.1M. Write share of (input+write) = **99.7%** (every model 98.3–100%); 1h share of writes = 96.8%; writes are 1.7% of processed tokens, reads 98.3%. The earlier 0.47 was Layer 2's own invention, not real data — withdrawn. Position: `cache_write_ratio` default 0.997 (share of non-cache-read input that is write); `cache_write_1h_share` 0.97 or 1.0. Separate finding: synthetic `cache_hit_ratio` 0.80 vs real ~0.98 — out of scope, flag only. | **DECIDED 2026-10-06 (user): `cache_write_ratio` 0.997, `cache_write_1h_share` 0.97** |
| Q3 | Layer 2: request/session carry `cache_write_5m` + `cache_write_1h` separately, or one `cache_write` + a day-level split? Position: separate on `Request`, `session_info.models` gains the two keys beside the legacy `cache_write` (= sum) — needs a check that dashboard code in `src/` tolerates it. | Open — depends on Q1 |
| | Q3 revised 2026-10-06: with 1h share 0.97 (not 1.0) 3% of writes are 5m, and the dashboard prices a bare `cache_write` at 1h, so Layer 2 MUST carry the 5m/1h split to reconcile (acceptance 1.00). Earlier 'keep single cache_write' withdrawn. | |
| Q4 | Layer 2 `cw_total_m` is currently derived from the cache-read curve; under the new contract the total is given by Layer 1. Position: distribute the given write total over requests with the existing `cw_shape` weights; guarantee `input_m ≥ R_m` by a rule (to design). | Open |
| Q6 | Layer 2 `ttl_s` 300→3600 changes when cache busts happen (gap > ttl) and so the shape of every synthetic session. Accept and regenerate, or keep 300 as the bust threshold and only label the writes 1h? | Open |
| Q5 | Invariant I1 + loader post-load rollup (`synthetic_load.py:384`, `synthetic.yaml daily_rollup`) per category. | Open — follows Q3 |

## External-module items to decide together

| Module | Change | Decided |
|---|---|---|
| `src/server/storage/synthetic_load.py`, `config/queries/synthetic.yaml` | per-category rollup check | no |
| `src/common/pricing.py` / `graph_fold` | none expected; confirm `session_info.models` extra keys are tolerated | no |
| `examples/` committed runs | regenerate | no |
| `budget-module-design.md` §4.2 | remove | no |

## Drafts

_(none yet)_

## Correction 2026-10-06 (found while grounding Q3, before writing the doc)

- `session_info.models` is read by `src/server/api/precedence.py:71`, `api/manager/aggregate.py:328` (`_MODEL_TOKEN_FIELDS`), `worker/graph_fold.py:147,429,597`, `segments/beats.py:409`. All use the single `cache_write` key. `channel_cost_of` / `_price_channel(models, "cache_write")` price it via `split_cache_write` case 3 → **1h rate**.
- So adding `cache_write_5m`/`cache_write_1h` to `session_info.models` is ignored by the dashboard; and keeping `cache_write` beside them double-counts in `price_tokens` (combined is added to 1h on top of the explicit keys). The earlier Q3 position ("dashboard already prices the two keys; drop combined `cache_write`") is **withdrawn** — dropping `cache_write` would zero the writes in every dashboard view.
- Real data has the same property: dashboard prices real 5m writes at 1h. The mismatch is a dashboard convention, not a #67 defect.
- New Q3 position: Layer 2 `session_info.models` keeps `cache_write` (= 5m + 1h). The 5m/1h split lives per request as `cache_write_detail` (same shape as real payloads). Acceptance prices at request level with the split; the dashboard-vs-forecast residual (≈ 5m writes × (1h − 5m rate)) is reported separately like #68's Sonnet effect. Fixing the dashboard convention = separate issue.
