# Synthetic Generator — Parent Review and Corrections

**Reviewed:** 2026-09-12, branch `forecast`. No commit created.

The initial subagent delivery was reviewed against the approved functional and detailed design specifications. It was not accepted solely on its five passing tests. The parent corrected the implementation and independently exercised the CLI workflow.

## Findings corrected

| Finding in the original delivery | Correction |
|---|---|
| The series runner accumulated every usage, coverage, and event record in memory. | Incremental JSONL streams now write each daily batch, with streaming hashes and row counts. A regression verifies that a day's records are written before the next batch is requested. |
| An entirely inactive series could fail while rendering its report because token-total keys were absent. | Zero counters and zero-consumption dimensions are initialized explicitly. Empty usage produces a complete dataset/report, coverage checks, and N/A allocation shares. |
| Some booleans/numeric strings were accepted as shares or spike settings; negative agent shares and normalized key collisions were insufficiently checked. | Validate before coercion; reject duplicate normalized IDs/keys, invalid shares, dates/timezones, unsupported billing modes, and incompatible settings. Normalize accepted mixtures deterministically. |
| Roster validation-only omitted identity and supplied/generated Team-ID collision checks. | Validation-only now exercises the same roster invariants as generation without writing artifacts. |
| Malformed input manifests could surface unexpected exceptions, and validation errors could echo raw record payloads. | Manifest shape/version/hash/count checks produce typed errors. Record failures expose field locations and line numbers without personal values. |
| Logs used inconsistent module/run context and inferred cleanup/stage status from whether a directory still existed. | Propagate one run ID and operation, bind the correct operational module, record actual stage and cleanup outcome, and report duration. |
| Reports omitted some required seeds/input references and did not explicitly verify coverage counts. | All stage reports identify saved configuration, input snapshots, seeds, counts, and report paths; series reports verify coverage and retain separate Team/Project/agent-model views. |
| Large token amounts could suffer float rounding errors near signed 64-bit boundaries. | Round exact rational amounts after sampling factors; preserve token conservation and reject overflow explicitly. |
| README direct Python commands lacked import-path setup, and config examples were difficult to inspect. | Added environment/import-path instructions, expanded event/behavior documentation, and commented readable YAML examples. |

## Validation completed

- Synthetic suite: **70 passed**, including strict input checks, numerical oracle, fixed profiles, event precedence, calendar behavior, coverage, report failures, streaming, corruption detection, and CLI logging.
- Existing shared logging tests: **10 passed**.
- Both direct pytest discovery and the root all-tests wrapper discover **2,335 tests**, including synthetic tests.
- Shell syntax/help, Python syntax, README links, source whitespace, and `git diff --check` passed.
- Isolated imports of shared contracts/artifact I/O and the synthetic package neither load Faker nor configure process-global logging.
- Independent CLI workflow from outside the repository, including paths containing spaces: manual Team ID preserved; hand-calculated token oracle satisfied; moved-directory replay succeeds with original input locations unavailable; 30-day prefixes match a 60-day run.
- Full sample workflow: 100 users, 3 Teams, 50 rostered users, 50 unassigned users, 60 dates, 8,888 usage rows, 6,000 complete coverage rows, and 41 random-spike events. Replay matches usage, coverage, profile, and event files byte-for-byte.

## Broader-suite limitation

A combined synthetic/common test run produced **861 passed and 29 failed**. The failures were existing messaging/socket tests that could not bind local listeners in the sandbox (`PermissionError: Operation not permitted`). These are outside the synthetic generator; a fully passing repository-wide test run is not claimed. No unrelated service code was changed to work around that environment restriction.

Synthetic correctness tests do not establish real forecasting accuracy. The forecasting module remains a separate specification/implementation task.

## Follow-up audit of generated token relationships

Audited all **8,888 usage rows** and **6,000 coverage rows** from the actual shell-generated sample. No violations were found for nonnegative integer counts, positive processed input, output/cache counts bounded by total processed input, configured output/cache ratios (allowing integer rounding), duplicate usage keys, or the one-Project-per-user-day rule. Token sums agree with the manifest and coverage is complete.

The comparison denominator is `input + cache_read`. Stored `input` excludes cache reads: all 8,888 sample rows correctly have cache reads greater than stored input under the default 80% cache-hit assumption. Output exceeds stored input in zero sample rows, but that is not a universal invariant for configurations with nearly all input cached.

Added explicit parameterized regression tests for token bounds, category ratios, and conservation with one-token and million-token baselines, cache ratios 0/0.8/1, and output ratios 0/0.03/0.99. The updated synthetic suite has **88 passing tests**.
