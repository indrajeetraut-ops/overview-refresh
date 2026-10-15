# Operator guide execution evidence — 2026-09-17

Branch `forecast`, macOS 15.7.4 arm64, repository venv. The exact guide sequence was executed in a fresh workspace: `/tmp/forecast-guide-exact.xAkplf` (`WORK` below). Checked-in data and existing outputs were untouched. All listed CLI commands exited **0**. No commit or push was made.

```bash
WORK=$(mktemp -d /tmp/forecast-guide-exact.XXXXXX)
mkdir -p "$WORK/config/synthetic" "$WORK/config/forecasting" "$WORK/artifacts"
cp econ-forecast/config/synthetic/*.yaml "$WORK/config/synthetic/"
cp econ-forecast/config/forecasting/forecast.yaml "$WORK/config/forecasting/"
./econ-forecast/utils/synt_gen.sh --op user-create --config "$WORK/config/synthetic/users.yaml" --output "$WORK/artifacts/users"
./econ-forecast/utils/synt_gen.sh --op roster-create --config "$WORK/config/synthetic/roster.yaml" --output "$WORK/artifacts/roster"
./econ-forecast/utils/synt_gen.sh --op series-create --config "$WORK/config/synthetic/series.yaml" --output "$WORK/artifacts/series-60d"
venv/bin/python - "$WORK/config/forecasting/forecast.yaml" <<'PY'
import pathlib, sys, yaml
path = pathlib.Path(sys.argv[1])
config = yaml.safe_load(path.read_text())
config["scopes"].append({"type": "project", "id": "12"})
path.write_text(yaml.safe_dump(config, sort_keys=False))
PY
./econ-forecast/utils/forecast.sh --config "$WORK/config/forecasting/forecast.yaml" --origin 2026-08-29 --unit month --offset 1 --periods 1 --validate-only
./econ-forecast/utils/forecast.sh --config "$WORK/config/forecasting/forecast.yaml" --origin 2026-08-29 --unit month --offset 1 --periods 1 --output "$WORK/artifacts/september-forecast"
./econ-forecast/utils/plot_forecast.sh --run "$WORK/artifacts/september-forecast" --org-id talium.ai --scope-type organization --sample-paths 0 --output "$WORK/artifacts/organization.html"
./econ-forecast/utils/plot_forecast.sh --run "$WORK/artifacts/september-forecast" --org-id talium.ai --scope-type team --scope-id payments-team --model claude-sonnet-5 --sample-paths 10 --output "$WORK/artifacts/payments-claude.html"
./econ-forecast/utils/plot_forecast.sh --run "$WORK/artifacts/september-forecast" --org-id talium.ai --scope-type project --scope-id 12 --model claude-sonnet-5 --model gpt-5.6-sol --sample-paths 1 --output "$WORK/artifacts/project-two-models.html"
```

Stdout JSON and stderr structured logs were captured for each stage as `$WORK/{users,roster,series,validate,forecast,org,team,project}.{stdout,stderr}`. Results: users `complete`/100; roster `complete`/3 Teams and 50 assigned; series `complete`/60 dates, 6,000 coverage rows and 8,888 usage rows; forecast validation `valid`; forecast `complete`/3 scopes; plots `complete`, selecting 4/1/2 models and displaying 0/10/1 sample paths respectively. The three HTML files are in `$WORK/artifacts/` and each exceeds 4 MB. Forecast diagnostics reported 12 series, 30 requested days, 1,000 paths, 1.35 seconds wall time, 243,007,488 bytes sampled peak RSS, and 3,948,383 bytes of manifest-listed output excluding the manifest. These measurements apply only to this run.

Independent artifact inspection found `status: complete` and every manifest-listed artifact present in all four stage directories. The dataset has 100 unique generated users, Team sizes 10/15/25, explicit Team ID `payments-team`, coverage on each of 60 dates from 2026-07-01 to 2026-08-29 with every row `complete`, nonnegative integer token categories, and at most one Project per active user-day. The 12 forecast index entries cover four models each for Organization, Team, and Project; all are `qualified`. Every saved NumPy array was finite/nonnegative and 30 × 1,000. Requested dates are 2026-09-01 through 2026-09-30. Recomputing the `payments-team`/`gpt-5.6-sol` mean and p90 from column sums matched `period_summary.json`: 26,053,131.356144723 and 28,387,890.778596934 tokens. Reports, resolved config, diagnostics, allocation and summary JSON, static PNGs, path index, and arrays were present.

Browser visual inspection was **blocked in this subagent**: the CUA in-app browser rejected visible tabs in a subagent thread, and CUA Chrome access was not approved. Thus hover, zoom, legend readability, and offline rendering were not independently verified in this documentation run. The self-contained export mechanism is described in the Plotly module README and its prior acceptance evidence; HTML file existence and CLI output alone do not establish browser behavior. The parent should open `/tmp/forecast-guide-exact.xAkplf/artifacts/payments-claude.html` in a visible browser and record that separate check before final acceptance. No production defect was observed. Historical test counts were not treated as this run's verification.

## Parent review and acceptance — 2026-09-17

The parent reviewed the guide against the maintained configs, module READMEs, shell environment helper, actual artifacts, and execution evidence. Two documentation findings were corrected: JSON examples now use readable expanded objects, and `schema_version` is correctly described as belonging to the period-summary envelope rather than individual entries. No production code changed.

The following independent checks passed using the repository venv:

```bash
venv/bin/python /private/tmp/forecast-guide-parent-artifacts.py
PLAYWRIGHT_BROWSERS_PATH=/private/tmp/forecast-playwright \
  venv/bin/python /private/tmp/forecast-guide-parent-browser.py
git diff --check
```

The temporary artifact inspection script verified all 38 manifest-listed file hashes (and declared byte counts where provided), 8,888 usage records with nonnegative integer categories and output bounded by processed input, and 6,000 unique complete coverage records. It checked all 12 saved arrays for float64 type, 30 × 1,000 shape, finite nonnegative values, and recomputed each series' requested-period mean and p90 from column sums. Documentation links and all JSON example blocks parsed successfully.

The browser check used the already installed Playwright Chromium in headless mode against the actual `payments-claude.html` produced by the guide. After a sandbox launch restriction, it ran with approved execution outside the sandbox. A preliminary inspection-script error reading Plotly's encoded array representation was corrected to use rendered plot values; it was not a product failure. The final check passed with external HTTP(S) requests blocked, zero external requests, zero page errors, a real mouse hover showing `Sep 6, 2026`, `claude-sonnet-5`, and `712,717.698 total tokens`, changed axis ranges after drag zoom, restored autorange after reset, and changed legend opacity after toggling a trace.

The parent visually inspected `/tmp/forecast-guide-exact.xAkplf/parent-chart-review.png`: the title, dates, token axis, mean, uncertainty band, and sample legend were readable without clipping. This resolves the subagent's browser-check limitation. Temporary scripts, logs, HTML, and screenshots are local review artifacts and are not committed. No full implementation test suite was rerun for these documentation-only changes. Parent acceptance is complete; no review findings remain open.
