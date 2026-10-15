# Forecast operator feedback

Issues raised during the user's operator-guide walkthrough. Implementation of OP-01 through OP-05 is now authorized and in progress; completion requires independent review and parent acceptance.

## Agreed naming contract

Team and project names are their operator-facing identifiers. Configuration must have no separate `id`, `team_id`, or `project_id` fields for defining or referencing these entities. Define each entity by name and use that same name in mixtures, forecast scopes, and plotting commands. Names are not display aliases that require operators to discover another identifier. Internal deterministic hashes are permitted as implementation details, but must never be required operator inputs. Existing stored datasets can retain their internal IDs and be resolved through saved name metadata; operators must not manage that mapping. This clarification supersedes any earlier suggestion to retain optional manual IDs in operator configuration.

## OP-01 — Default origin when omitted

- Reported: 2026-09-17.
- Status: pending; user requested recording this while reviewing other issues.
- Current behavior: `forecast.sh` requires `--origin`; omission causes a CLI argument error.
- Requested behavior: make `--origin` optional and automatically select the dataset's last date. Preserve explicit `--origin` for choosing an earlier historical cutoff.
- Implementation guidance: derive the default from the dataset's declared end date, including covered zero-consumption days, rather than the last usage record. Preserve coverage and eligibility checks; do not silently skip outages by moving to an earlier date. Record and display the resolved origin so operators can see the date used.
- Acceptance checks: omitted origin resolves to the declared end; a final covered zero day is retained; an explicit origin still overrides the default and excludes later observations; unknown/outage coverage still follows existing eligibility rules; help, operator guide, and relevant specifications explain the default.

## OP-02 — Use team names in operator inputs

- Reported: 2026-09-17, while using `/Users/sumandra/projects/talium/forecasting/forecast-trial`.
- Status: requested behavior agreed; implementation pending. The user's follow-up supersedes the earlier suggestion to improve ID-based error messages only.
- Observed: forecast config requested team `id: core`; the saved roster has `team_name: core` and `team_id: "32"`. Forecast selection currently matches IDs, so it rejects `('talium.ai', 'team', 'core')`.
- Verified workaround: change the forecast team scope to `id: "32"`. Parent tested a temporary config copy against the unchanged trial dataset with origin `2026-06-28`, month offset 2, periods 1, and `--validate-only`; validation passed with exit 0 for both requested scopes (organization and team). Actual fitting was not run. The user's trial config was not modified.
- Requested behavior: operators supply `team_name` to define/select teams; the name is the identifier. Remove separate team ID fields from operator configuration. Apply this consistently to generation configuration, forecast scope selection, and Plotly selection. Internal hashes/IDs, if used, stay hidden implementation details.
- Implementation guidance: use explicit name fields, not a name disguised as `id`. Existing datasets, including the trial's `core` / `"32"` pair, must remain usable through saved name metadata. Any migration from existing ID-based configs must produce name-only configuration rather than perpetuating optional ID inputs. Do not regenerate or rewrite saved artifacts just to enable name selection.
- Acceptance checks: selecting `core` resolves to its saved ID `"32"`; generated-ID teams also work by name; unknown names get actionable errors; identical names in different organizations resolve only within the selected organization; ambiguous names within one organization are rejected clearly. Config templates, CLI help, specifications, and operator examples must all agree.

## OP-03 — Use project names without requiring manual project IDs

- Reported: 2026-09-17.
- Status: requested behavior agreed; implementation pending.
- Requested behavior: operators define projects and project mixtures by name; the name is the identifier, and no separate project ID field appears in configuration. Forecast and Plotly selection also use project names. Internal deterministic hashes/IDs are permitted only as implementation details.
- Implementation guidance: resolve existing projects by their saved names so current datasets remain usable. Repeated generation from the same project definitions must retain stable identity. Keep names and internal IDs explicit rather than interpreting an `id` field as either one.
- Acceptance checks: name-only project definitions generate valid stable identities; team project mixtures resolve names correctly; forecast and Plotly select an existing project by name; unknown/ambiguous names are rejected clearly; organization scoping prevents cross-organization matches; templates, CLI help, specifications, and operator guide no longer require operators to manage project IDs.

## OP-04 — Use tables in the Markdown forecast report

- Reported: 2026-09-17.
- Status: requested behavior agreed; implementation pending while collecting operator feedback.
- Requested behavior: replace the long bullet entries in `report.md` with readable Markdown tables for both requested-window expected totals and models/evidence windows.
- Totals table: organization, scope type/name, model (or explicitly labeled scope aggregate), expected tokens, p10, p50, p90, and status. Use clear numeric formatting and token units. Group by scope if needed to avoid excessively wide tables.
- Models/evidence table: scope/model, status, history start/end, sampling start/end, shared sampling-day count, and last active date. Keep assumptions and failure reasons visible and associated with the applicable row; use adjacent notes if needed for readability.
- Preserve the distinction between model totals and scope aggregates, and between qualified, inactive, and unavailable results. Missing values must not appear as zero. Tables must use the same saved values as the JSON artifacts; this is a presentation change, not a forecasting change.
- Acceptance checks: verify table headers and rows against saved summaries/index/diagnostics; check numeric formatting, unavailable values, aggregate labels, inactive/failed cases, and Markdown escaping of names containing pipes or newlines. Render representative complete and partial reports to check readability. Update report documentation/examples to match.

## OP-05 — Make the operator guide concise and structured

- Reported: 2026-09-17.
- Status: requested style agreed; implementation pending with the other review items.
- Requested behavior: rewrite dense explanatory paragraphs in `docs/operations/README-forecast.md` as engineering reference material where simple structure is clearer. Keep brief prose for purpose, interpretation, and caveats; avoid burying parameters, units, equations, or file descriptions in prose.
- Show series defaults in a parameter/value/meaning table: daily variation 30%, cache-hit ratio 0.80 with ±0.05 fraction variation (±5 percentage points), output ratio 0.03 of processed input, weekday/weekend zero probabilities 0.10/0.60, and weekend multiplier 0.25. Make percent versus percentage-point units explicit.
- Use short equations for token accounting and mixtures: `processed_input = input + cache_read`; `total_tokens = input + cache_read + output`; output is approximately `0.03 × processed_input` subject to per-record rounding. Express share-sum constraints with their correct grouping (agents sum to 1; models within each agent sum to 1). Explain stored input/cache-write semantics briefly beside the equations.
- Show generated artifacts as an actual indented directory tree, with files under their owning directories. Follow it with a file/purpose table instead of prose descriptions or a flattened directory listing.
- Apply this style across the guide where useful, preserving copyable commands, readable JSON examples, default values/units, distinctions between current and pending behavior, and necessary interpretation limits. Do not turn nuanced semantics into misleading equations merely for brevity.
- Acceptance checks: compare revised parameter values and equations with maintained configs/implementation; verify artifact tree and file-purpose table against actual outputs; ensure copied commands remain correct and all links work. Inspect the rendered Markdown for readable tables, hierarchy, and code blocks.

Add subsequent operator issues below as they are reported.
