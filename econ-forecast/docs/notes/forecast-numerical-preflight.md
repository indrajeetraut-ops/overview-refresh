# Forecast numerical preflight

> Historical ETS numerical preflight. The current weekday resampler has no ETS optimizer or clipping and is documented in [the current design](../design/forecasting-weekday-bootstrap.md).

Date: 2026-09-13. This is a macOS ARM64 compatibility check only; Linux/AWS remains unverified.

## Tested environment and dependencies

- macOS 15.7.4 ARM64, CPython 3.14.6, pip 26.1.2.
- `statsmodels==0.15.0`, `numpy==2.5.3`, `scipy==1.18.1`, `pandas==3.0.5`.
- `matplotlib==3.11.2`, `plotly==7.0.0`, `psutil==7.2.2`, `threadpoolctl==3.6.0`.
- Browser-test harness: `playwright==1.62.0`. The Python package is installed; browser binaries were not downloaded or exercised in this numerical preflight.
- `venv/bin/pip check` returned `No broken requirements found.`

The direct dependencies above are pinned in the root `requirements.txt`. Transitive packages remain dependency-resolver owned.

## Executable ETS result

The fixture was a 56-day positive series with a linear change and fixed seven-day additive pattern. The model call used:

```python
ETSModel(
    y,
    error="add",
    trend="add",
    damped_trend=True,
    seasonal="add",
    seasonal_periods=7,
    initialization_method="estimated",
).fit(maxiter=1000, disp=False)
```

Observed result: L-BFGS converged with `warnflag=0` in 117 iterations, all parameters, residuals, forecasts, and simulations were finite. `simulate(21, anchor="end", repetitions=64, random_errors=errors)` accepted an error array of shape `(21, 64)` and returned shape `(21, 64)`. Repeating the call with the same explicit error array was bitwise identical (`array_equal=True`, maximum absolute difference `0.0`). With an all-zero error array, every simulated path equaled `forecast(21)` exactly (maximum absolute difference `0.0`).

Affine normalization was tested on the regular weekly fixture, `1,000,000 +/- 1`, `1,000,000,000,000 +/- 1`, and `50 +/- 1e-10`. Seasonal/damped fits converged without warnings in 81, 95, 95, and 46 iterations respectively; all fallback configurations also converged. All produced finite parameters/residuals, and zero-error simulation agreed exactly with the transformed deterministic forecast. Convert fitted values and forecasts back with `mean + scale * value`; convert residuals back with `scale * residual` exactly once; supply token-unit simulation errors divided by the same scale. Centering as well as scaling is necessary: mean-absolute-only scaling failed the two most extreme near-constant fixtures at iteration zero.

## Constant and numerical edge cases

Unmodified statsmodels fits of exact constant data returned finite parameters and forecasts but `converged=False`, `warnflag=2`, zero optimizer iterations, and a `ConvergenceWarning` for every configured fallback. These returned values cannot be accepted under the explicit optimizer-convergence rule. Affine normalization supports every tested nonconstant near-constant case without treating real variation as constant.

For exactly identical observations only (`np.ptp(y) == 0.0` after conversion to float64), construct the exact fixed ETS level result without optimization:

```python
model = ETSModel(y, error="add", trend=None, damped_trend=False,
                 seasonal=None, initialization_method="known",
                 initial_level=float(y[0]))
result = model.smooth(np.array([0.1]))  # fixed admissible alpha
```

The tested result had one parameter (`smoothing_level=0.1`), zero residuals, constant deterministic forecast, and constant zero-error simulated trajectories, all exactly. Statsmodels emits divide-by-zero/invalid `RuntimeWarning`s while computing likelihood-derived diagnostics because zero residual variance is degenerate; catch only those expected warnings in this exact-constant branch, record the analytical fixed-result status, and validate the operational arrays directly. Do not claim optimizer convergence because no optimizer ran. The choice of admissible alpha does not affect a constant state with zero innovations. This is internal numerical handling of the same additive level ETS, not a new eligibility gate or model family, and it adds no noise. Do not apply an approximate-constant threshold: every representably nonconstant series follows the normalized fit/fallback sequence.

## Recommended production policy

- Normalize each nonconstant series with `location = mean(y)` and `scale = max(std(y), spacing(max(abs(location), 1.0)))`, using NumPy's population standard deviation (`ddof=0`); save both. Use `initialization_method="estimated"`, the statsmodels 0.15.0 default parameter bounds, `maxiter=1000`, and `disp=False`. The inspected defaults constrain smoothing parameters to `[0.0001, 0.9999]`, damping to `[0.8, 0.98]`, leave estimated states unbounded, and constrain the final seasonal state to zero for identification. Do not add untested custom bounds.
- Capture all warnings for each attempt. Reject the attempt on any `ConvergenceWarning`, `mle_retvals["converged"] is not True`, nonzero `warnflag`, or nonfinite parameter, residual, forecast, or simulation. Preserve other warnings in diagnostics; treat runtime/numerical warnings as rejection unless specifically proven benign. Continue only through the design's fixed fallback order.
- Use raw additive one-step residuals converted back to token units once. On the common valid-date window, subtract each model's arithmetic residual mean, then sample complete aligned date vectors. Apply no degrees-of-freedom or variance inflation: the empirical bootstrap already represents the observed innovation distribution, while a fitted-parameter correction would be an unvalidated calibration change.
- Derive a per-scope stream from the configured seed and a stable, versioned byte encoding of the canonical scope identity (for example SHA-256, converted to fixed 32-bit words and combined with the seed in `numpy.random.SeedSequence`). Use `numpy.random.default_rng` to draw the shared historical-date indices. Never use Python's process-randomized `hash()`. Per-scope derivation makes results invariant to scope order and worker scheduling. Because explicit error arrays are supplied to `simulate`, statsmodels consumes no random stream in this path.
- Persist the resolved dependency versions, canonical scope-to-seed derivation version, sampled historical row indices (or their reproducible digest), and error-array/path ordering in run metadata. Require exact replay for the same saved explicit errors when practical. For cross-platform acceptance of forecasts and simulated values use `numpy.testing.assert_allclose(rtol=1e-10, atol=1e-7)` in token units; this preflight observed exact equality but does not establish Linux equivalence.

## Commands and limitations

Commands executed from the repository root:

```text
venv/bin/pip install statsmodels==0.15.0 matplotlib plotly psutil threadpoolctl playwright
venv/bin/pip check
venv/bin/python /private/tmp/forecast_preflight.py
venv/bin/python /private/tmp/forecast_scaled_probe.py
venv/bin/python /private/tmp/forecast_affine_probe.py
```

The temporary fixture scripts were preflight instruments and are not production sources. No Linux x86-64 installation, AWS runtime, browser binary, browser rendering, capacity benchmark, or real usage dataset was tested here.

## Parent compatibility correction — 2026-09-14

The initial NumPy 2.5.3 / SciPy 1.18.1 pins require Python >=3.12, conflicting with documented Python 3.11 support. PyPI filtered version lookup confirmed NumPy 2.4.6 / SciPy 1.17.1 support 3.11. Parent installed these exact versions in the root Python 3.14 environment and updated requirements.txt. `pip check` remains clean. Parent reran `/private/tmp/forecast_affine_probe.py`, independently asserted convergence/finite values/zero-error identity in its JSON output for every nonconstant fit and the analytical constant result. All passed; evidence `/private/tmp/forecast_affine_parent_recheck.txt`. Other pins and all mathematical policies are unchanged. Python 3.11 wheel compatibility is verified by package metadata; actual Python 3.11 execution remains a separate platform check.
