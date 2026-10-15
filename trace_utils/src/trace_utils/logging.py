"""Structured JSON logging (project mandate §11).

One logger per module; UTC timestamps; content (prompts/responses/tool IO) is never
logged — only identifiers and counts. Import `get_logger(__name__)` per module.
"""
from __future__ import annotations

import logging
import sys

import structlog

_CONFIGURED = False


def configure(level: str = "INFO") -> None:
    """Configure structlog to emit one JSON object per line to stderr."""
    global _CONFIGURED
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(module: str):
    """Return a bound logger tagged with the module name."""
    if not _CONFIGURED:
        configure()
    return structlog.get_logger().bind(module=module)
