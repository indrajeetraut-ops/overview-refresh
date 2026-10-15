"""structlog logger for msging, plus the one obvious way to redact a
key before it touches a log line (design §10.4).

Normative, not stylistic -- these logs ship in support bundles:
  - never log an api_key. Only `redact_key(api_key)` (a 12-char prefix).
  - never log a payload. Only its `size_bytes`.

Importing this module configures nothing. structlog's global configuration
is process-wide state, and two modules calling `structlog.configure()` at
import time would fight over it. A process that wants structured JSON output
(CLAUDE.md rule 1, design §10.4) must call `configure_logging()` itself,
exactly once, at startup -- typically from its entry point, before any
logger is used in anger.

TODO: `src/server/storage/log.py` has the same missing-configuration gap
(it also wraps `structlog.get_logger()` without configuring a renderer).
It should adopt this same `configure_logging()` entry point rather than
grow its own.
"""
import structlog

_KEY_PREFIX_LEN = 12
_REDACTED_MARKER = "<redacted>"


def get_logger(module: str) -> structlog.BoundLogger:
    return structlog.get_logger(module=module)


def configure_logging(json: bool = True, logger_factory=None) -> None:
    """Configure structlog's global processor chain for this process.

    Call this once, at process startup, before any `get_logger()` output is
    expected to matter. Not called on import -- see module docstring.

    json=True (default) renders each log line as a single JSON object via
    `structlog.processors.JSONRenderer()`, satisfying CLAUDE.md rule 1 and
    design §10.4. json=False uses structlog's default human-readable
    `ConsoleRenderer`, useful for local interactive debugging.

    logger_factory defaults to `structlog.PrintLoggerFactory()` (writes to
    stdout). Tests that need to capture output pass their own factory, e.g.
    `structlog.PrintLoggerFactory(file=some_stringio)`.
    """
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            renderer,
        ],
        logger_factory=logger_factory or structlog.PrintLoggerFactory(),
    )


def redact_key(api_key) -> str:
    """Return a value that is safe to log in place of an api_key.

    Never returns the input, for any input:
      - keys longer than 12 characters: the first 12 characters (a prefix
        that identifies the key without disclosing it).
      - keys 12 characters or shorter: the input's first 12 characters WOULD
        be the entire key, so a prefix carries no redaction at all. Return
        the fixed opaque marker `"<redacted>"` instead.
      - empty string, `None`, or any non-str input: also `"<redacted>"`.

    This is the record's `key_prefix` (design §10.4/§8) -- the only form of
    the key that is ever safe to log.
    """
    if not isinstance(api_key, str) or len(api_key) <= _KEY_PREFIX_LEN:
        return _REDACTED_MARKER
    return api_key[:_KEY_PREFIX_LEN]
