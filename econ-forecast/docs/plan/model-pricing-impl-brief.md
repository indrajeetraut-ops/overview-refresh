# Implementation brief — `common.model_pricing`

## Where
- Worktree: `/Users/sumandra/project/talium/forecast/code-econ-model-pricing` (branch `feat/model-pricing`).
  Work **only** here. Do not touch the main checkout.
- Python: `/Users/sumandra/project/talium/forecast/code-econ/venv/bin/python` (the worktree has no venv).
  Run commands from the worktree root; `pytest.ini` puts `src` on the path.

## What
- **Authoritative design:** `docs/modules/model-pricing.md` (cited §N). The design wins over this brief
  and the plan if they disagree — raise it.
- **Plan:** `econ-forecast/docs/plan/model-pricing-plan.md`. Implement tasks **T0–T10**. **Not T11**
  (no live `update`, no commits of a catalog).
- Files in scope: `src/common/model_pricing/**`, `config/model_pricing/**` (templates only, no catalog
  versions), `utils/model_pricing.sh`, `tests/common/model_pricing/**`.
- Out of scope / do not modify: `src/common/pricing.py`, `config/pricing.yaml`, anything under
  `econ-forecast/src`, budget docs.

## Fixtures
Real captured payloads (read-only) are in
`/private/tmp/claude-502/-Users-sumandra-project-talium-forecast-code-econ/553f4fef-0309-4a0a-b151-dc576c7cce34/scratchpad/raw/`:
- `litellm.json` — LiteLLM price file at commit `d3462c65b585eb7e51594330a5785669f6b7db51` (2.9 MB)
- `aws-AmazonBedrockFoundationModels.json` — AWS Price List, us-east-1, publication 2026-09-22
- `azure-eastus2-gpt5-pages.json` — list of Azure Retail Prices API pages (eastus2, GPT5 products)

Build **small** fixture subsets (each ≲100 KB) under `tests/common/model_pricing/fixtures/` from these,
keeping the real structure. Include the §10.1 edge cases (both AWS naming schemes, mixed-case Azure
meters, `LongCo`/`Batch`/`PP`, the `gpt-4o` dated-variant conflict, a model without cache-write fields,
a `deprecation_date`, a non-vendor AWS product). For Azure paging, split a subset across two pages
linked by `NextPageLink`. Tests must not touch the network; adapters split `fetch()` (network, httpx)
from pure `parse()`; `update` accepts injected fetchers.

## Repo rules (root `CLAUDE.md`)
- Python 3.11. Deps already in `requirements.txt` (`httpx`, `pydantic`, `PyYAML`, `structlog`); add none
  without asking.
- Structured JSON logging via `common.msging.log` (`get_logger("model_pricing")`, `configure_logging()`
  in CLIs).
- CLI entry points `cli_<function>.py`; wrapper `utils/model_pricing.sh` like `utils/ingest.sh`.
- `TODO(<ref>)` comment for anything deliberately deferred (e.g. GCP SKU mapping).
- No references to Claude/AI tooling anywhere in code, comments, or docs.
- **Do not commit.** The coordinator reviews and commits.
- Match the style of existing `src/common` code (docstrings explain why; see `src/common/pricing.py`).

## Tests and report
- Tests in `tests/common/model_pricing/` per the plan's test table; every design example (§2.4, §3.2,
  §3.4 lookup table, §5.1 table) is a test.
- Run and persist:
  ```bash
  venv=/Users/sumandra/project/talium/forecast/code-econ/venv/bin/python
  $venv -m pytest tests/common/model_pricing -q --junitxml=docs/test/reports/model-pricing-junit.xml
  $venv utils/gen_test_report.py --title "Model pricing (common.model_pricing)" \
      --junit docs/test/reports/model-pricing-junit.xml \
      --out docs/test/reports/model-pricing-test-report.md --tests tests/common/model_pricing/
  ```
- Also run `tests/common/test_pricing.py` to confirm nothing existing broke.

## Done when
- T0–T10 acceptance criteria in the plan are met; all tests green; report files written.
- Final message: files created, test counts, any deviations from design/plan and why, open questions.

## First step: handshake (no code)
Before writing any code, read the design, the plan, and this brief, look at the fixtures and the
existing code you will reuse, and reply with: (1) whether the instructions make sense, (2) concerns,
ambiguities, or contradictions with proposed resolutions, (3) your intended internal design in a few
bullets (data model for rows/versions, how lookup across versions works, how canonical decimal strings
and `hash8` are computed). Then **stop and wait** for the go-ahead.
