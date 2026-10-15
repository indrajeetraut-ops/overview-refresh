"""Suite-wide fixtures for the econ-forecast tests."""
from __future__ import annotations

import pytest
import structlog


@pytest.fixture(autouse=True)
def _restore_structlog_config():
    """structlog's configuration is process-wide, and every forecast CLI sets
    it from `main()` -- `PrintLoggerFactory(file=sys.stderr)` binds whatever
    `sys.stderr` is at call time. Under `capsys` that is a per-test capture
    stream pytest closes at teardown, so a CLI test would leave every later
    test in the session logging into a closed file. Save and restore it around
    every test (same fixture as `tests/conftest.py`; the two suites share one
    pytest session)."""
    saved = structlog.get_config()
    was_configured = structlog.is_configured()
    yield
    if was_configured:
        structlog.configure(**saved)
    else:
        structlog.reset_defaults()
