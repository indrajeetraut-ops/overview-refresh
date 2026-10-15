"""Stable forecast-library error categories."""


class ForecastError(Exception):
    """Base error with optional execution-stage context."""

    def __init__(self, message: str, *, stage: str | None = None):
        super().__init__(message)
        self.stage = stage


class InputError(ForecastError):
    """Invalid request, configuration, or prepared input (CLI exit 2)."""


class ExecutionError(ForecastError):
    """Operational I/O or numerical execution failure (CLI exit 1)."""
