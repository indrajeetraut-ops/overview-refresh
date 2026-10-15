"""Numerically conditioned additive ETS fitting (design doc §3)."""

from dataclasses import dataclass
import warnings
import threading
import numpy as np
from statsmodels.tools.sm_exceptions import ConvergenceWarning
from statsmodels.tsa.exponential_smoothing.ets import ETSModel

_WARNING_LOCK = threading.RLock()


@dataclass
class Fit:
    result: object
    location: float
    scale: float
    residuals: np.ndarray
    forecast: np.ndarray
    attempt: dict
    analytic: bool = False

    def simulate(self, horizon: int, errors: np.ndarray):
        with _WARNING_LOCK:
            values = np.asarray(
                self.result.simulate(
                    horizon,
                    anchor="end",
                    repetitions=errors.shape[1],
                    random_errors=errors / self.scale,
                ),
                dtype=np.float64,
            )
        if values.ndim == 1 and errors.shape[1] == 1:
            values = values.reshape(horizon, 1)
        if values.shape != (horizon, errors.shape[1]):
            raise ValueError("ETS simulation returned an unexpected path shape")
        return self.location + self.scale * values


def _attempt(y, trend, seasonal, horizon):
    location = float(np.mean(y))
    scale = max(float(np.std(y, ddof=0)), float(np.spacing(max(abs(location), 1.0))))
    z = (y - location) / scale
    try:
        with _WARNING_LOCK:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = ETSModel(
                    z,
                    error="add",
                    trend=trend,
                    damped_trend=trend is not None,
                    seasonal=seasonal,
                    seasonal_periods=7 if seasonal else None,
                    initialization_method="estimated",
                ).fit(maxiter=1000, disp=False)
    except (ValueError, FloatingPointError, ArithmeticError) as exc:
        attempt = {
            "trend": trend,
            "seasonal": seasonal,
            "damped_trend": trend is not None,
            "converged": False,
            "iterations": None,
            "warnings": [],
            "reason": f"{type(exc).__name__}: {exc}",
        }
        return None, False, attempt
    messages = [f"{type(w.message).__name__}: {w.message}" for w in caught]
    ret = result.mle_retvals or {}
    forecast = location + scale * np.asarray(result.forecast(horizon))
    residuals = scale * np.asarray(result.resid)
    accepted = (
        ret.get("converged") is True
        and ret.get("warnflag") in (None, 0)
        and not any(
            issubclass(w.category, (ConvergenceWarning, RuntimeWarning)) for w in caught
        )
        and np.isfinite(result.params).all()
        and np.isfinite(residuals).all()
        and np.isfinite(forecast).all()
    )
    attempt = {
        "trend": trend,
        "seasonal": seasonal,
        "damped_trend": trend is not None,
        "converged": bool(ret.get("converged")),
        "iterations": ret.get("iterations"),
        "warnings": messages,
        "reason": None if accepted else "numerical acceptance failed",
    }
    return Fit(result, location, scale, residuals, forecast, attempt), accepted, attempt


def _configs(n: int, seasonal_mode: str, trend_mode: str) -> list[tuple[str | None, str | None]]:
    """Ordered (trend, seasonal) attempts for the tiered fallback (design doc §3).

    The length-based candidate list is unchanged from the original fixed table; `auto`
    lets a component be learned, `none` forces it off by dropping every candidate that
    would have used it. `(None, None)` (level-only) is never filtered, so it remains the
    guaranteed last resort under every seasonal/trend mode combination.
    """
    base = (
        [("add", "add"), (None, "add"), (None, None)] if n >= 28 else [(None, None)]
    )
    out = []
    for trend, seasonal in base:
        if trend is not None and trend_mode == "none":
            continue
        if seasonal is not None and seasonal_mode == "none":
            continue
        out.append((trend, seasonal))
    if not out:
        out.append((None, None))
    return out


def fit_ets(y: np.ndarray, horizon: int, *, seasonal: str = "auto", trend: str = "auto"):
    if np.ptp(y) == 0.0:
        with _WARNING_LOCK:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                model = ETSModel(
                    y,
                    error="add",
                    trend=None,
                    damped_trend=False,
                    seasonal=None,
                    initialization_method="known",
                    initial_level=float(y[0]),
                )
                result = model.smooth(np.array([0.1]))
        forecast = np.asarray(result.forecast(horizon))
        residuals = np.asarray(result.resid)
        attempt = {
            "trend": None,
            "seasonal": None,
            "damped_trend": False,
            "converged": None,
            "iterations": 0,
            "warnings": [f"{type(w.message).__name__}: {w.message}" for w in caught],
            "reason": None,
            "analytic": True,
        }
        if not (
            np.isfinite(result.params).all()
            and np.isfinite(residuals).all()
            and np.isfinite(forecast).all()
        ):
            attempt["reason"] = "nonfinite analytic ETS operational array"
            return None, [attempt]
        return Fit(result, 0.0, 1.0, residuals, forecast, attempt, True), [attempt]
    attempts = []
    for candidate_trend, candidate_seasonal in _configs(len(y), seasonal, trend):
        fit, ok, attempt = _attempt(y, candidate_trend, candidate_seasonal, horizon)
        attempts.append(attempt)
        if ok:
            return fit, attempts
    return None, attempts
