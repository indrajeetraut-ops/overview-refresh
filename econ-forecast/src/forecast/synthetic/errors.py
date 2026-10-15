"""Expected operation errors translated to documented CLI exit codes."""


class SyntheticError(Exception):
    """Base for expected generator failures."""


class ConfigError(SyntheticError):
    """Configuration cannot be parsed or validated."""


class InputDataError(SyntheticError):
    """Saved inputs or generation constraints are invalid."""


class ArtifactError(SyntheticError):
    """An output artifact cannot be created or completed."""
