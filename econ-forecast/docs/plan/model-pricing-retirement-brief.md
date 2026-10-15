# Brief — step 2: official-source models and retirement

Same worktree/branch/rules as `model-pricing-report-brief.md` (do not commit, no network, no Claude/AI
references). Do this **after** the report change is done and green.

Design (updated, read carefully): `docs/modules/model-pricing.md` §3.4, **§3.5 (new)**, §5.2
(official-source models), §6.1 steps 2, 6, 7, §6.6, §7.

## Changes
1. **Official-source models in the registry** (§5.2): AWS/Azure records whose reduced model string does
   not normalize to a LiteLLM model are added: `model_id = canon(model string)`, vendor from name prefix
   (`claude`→anthropic, `gpt`/`o<digit>`→openai, `gemini`→gemini), prefixes configurable as
   `vendor_prefixes` in `model_pricing.yaml` (config.py, with those defaults). Unknown prefix → unmapped.
   Same `model_id` with different rates across such records → ambiguous. They get rows only on channels
   that publish them. Adapters currently parse against a finished registry; restructure minimally (e.g.
   adapters return unmatched candidates, `build.py` extends the registry and re-normalizes).
2. **Retirement** (§3.5): replace `no_longer_published` with `retired`. For each key in `previous` not
   already retired and not published by any source now → retirement row (last rates, `source="retired"`,
   `retired_on = built_on − 1`). Already-retired keys in `previous` still unpublished → carried unchanged.
   Re-published → normal row. Update BuildResult, reports (summary count "retired", JSON list), catalog
   lookup needs no change (latest-row `retired_on` rule) — verify with tests.
3. README: known-limitations / troubleshooting and prices-over-time sections for both changes.

## Tests
- AWS fixture: an older Claude model present in AWS but not in the LiteLLM fixture (e.g. Claude 3 Haiku
  or Sonnet 4 — extract from the raw AWS file with a short script) → registry entry vendor anthropic,
  priced on `aws_bedrock:us-east-1:regional`, unmatched on `anthropic` channel. Cohere still unmapped.
- Retirement: build v1 with a model, build v2 without it → retirement row, `retired` in result and
  summary count; lookup day before build date → last price, after → retired; v3 without it → carried,
  v3 is a no-op vs v2 (hash stable); v4 with it back → live again.
- Regenerate the junit + markdown test report as before.

Handshake: reply with concerns and a short plan first; wait for go-ahead.
