"""Exceptions for the ingest service. `reason` is the wire-visible NACK code (ingest.md §3.2)."""


class IngestError(Exception):
    pass


class MalformedBatchError(IngestError):
    """400s: gzip/parse/validation failure on any line -> whole-batch NACK (ingest.md §3.3).

    reason values: not_gzip | invalid_envelope | missing_session_id | body_too_large.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
