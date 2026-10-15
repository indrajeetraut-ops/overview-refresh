"""Consumption forecasting public API.

The artifact reader stays lightweight: runner and scientific fitting modules are
loaded only when a caller asks for the execution API.
"""

from .artifacts import SavedRun, load_run
from .errors import ExecutionError, InputError
from .request import ForecastRequest, resolve_window

__all__ = [
    "ExecutionError",
    "ForecastRequest",
    "ForecastResult",
    "InputError",
    "SavedRun",
    "load_run",
    "resolve_window",
    "run_forecast",
]


def __getattr__(name):
    if name in {"ForecastResult", "run_forecast"}:
        from .runner import ForecastResult, run_forecast

        return {"ForecastResult": ForecastResult, "run_forecast": run_forecast}[name]
    raise AttributeError(name)
