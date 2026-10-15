"""Expected operation errors translated to documented CLI exit codes.

Curve-local error hierarchy, independent of `forecast.synthetic.errors`.
`forecast.artifact_io` raises `forecast.synthetic.errors.{ArtifactError,InputDataError}`
internally; the runner boundary (`curve/runner.py`) catches those and re-raises the
curve-local equivalents below so `cli_curve.py`'s exit-code dispatch only ever has to
handle one error hierarchy.
"""


class CurveError(Exception):
    """Base for expected curve-tool failures."""


class ConfigError(CurveError):
    """Configuration cannot be parsed or validated."""


class InputDataError(CurveError):
    """Curve input or a derived value violates a validation constraint."""


class ArtifactError(CurveError):
    """An output artifact cannot be created or completed."""
