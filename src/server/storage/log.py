"""structlog JSON logger for the storage module (storage.md §8)."""
import structlog


def get_logger(name: str = "storage") -> structlog.BoundLogger:
    return structlog.get_logger(module=name)
