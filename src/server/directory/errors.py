"""Directory module errors (docs/design/identity-ingestion.md)."""


class DirectoryError(Exception):
    """Base for every error this module raises."""


class DirectoryValidationError(DirectoryError):
    """A record or team carries a value outside the contract (bad enum, no external keys).
    Unknown *keys* are ignored, not an error (IDENT §3.2 evolution rules) -- this is for
    values that are present and wrong."""


class UnresolvableRecordError(DirectoryError):
    """A record whose `external_keys` resolve to nothing and which carries no email or
    display_name -- there is no identity to attach it to and none to create. Never guessed
    (IDENT §3.2)."""
