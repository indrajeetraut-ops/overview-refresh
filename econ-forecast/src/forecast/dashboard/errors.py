"""Stable dashboard-batch error categories (mirrors `forecast.cost.errors`)."""


class DashboardError(Exception):
    """Base error with optional execution-stage context (`"forecast"` or `"cost"`
    when the failure came from that stage of a run)."""

    def __init__(self, message: str, *, stage: str | None = None):
        super().__init__(message)
        self.stage = stage


class InputError(DashboardError):
    """Invalid presets, series dataset, forecast template, or output location (CLI exit 2)."""


class ExecutionError(DashboardError):
    """A forecast or cost stage did not complete, or a file of the run could not be written
    (CLI exit 1)."""
