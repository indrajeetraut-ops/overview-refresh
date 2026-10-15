from server.storage.errors import QueryConfigError, SchemaVersionError


def test_errors_are_exceptions():
    assert issubclass(QueryConfigError, Exception)
    assert issubclass(SchemaVersionError, Exception)
