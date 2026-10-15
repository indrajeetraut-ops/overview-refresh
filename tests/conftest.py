"""Suite-wide fixtures for the mainline tests."""
from __future__ import annotations

import pytest
import structlog


@pytest.fixture(autouse=True)
def _restore_structlog_config():
    """structlog's configuration is process-wide, and the CLIs set it from
    `main()` -- `PrintLoggerFactory(file=sys.stderr)` binds whatever
    `sys.stderr` is at call time. Under `capsys` that is a per-test capture
    stream pytest closes at teardown, so a CLI test left every later test in
    the session logging into a closed file (`ValueError: I/O operation on
    closed file.`), in whichever directory happened to run next. Save and
    restore it around every test."""
    saved = structlog.get_config()
    was_configured = structlog.is_configured()
    yield
    if was_configured:
        structlog.configure(**saved)
    else:
        structlog.reset_defaults()
