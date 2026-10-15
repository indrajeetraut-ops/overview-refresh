"""Stable cost-library error categories."""


class CostError(Exception):
    """Base error with optional execution-stage context."""

    def __init__(self, message: str, *, stage: str | None = None):
        super().__init__(message)
        self.stage = stage


class InputError(CostError):
    """Invalid request, configuration, or prepared input (CLI exit 2)."""


class ExecutionError(CostError):
    """Operational I/O or numerical execution failure (CLI exit 1)."""
