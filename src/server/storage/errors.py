"""Storage module errors (storage.md §4)."""


class QueryConfigError(Exception):
    """A named-query YAML file is malformed (raised at catalog load)."""


class SchemaVersionError(Exception):
    """The database migration revision does not match the expected head."""
