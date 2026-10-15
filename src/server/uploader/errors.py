"""Exceptions for the batch uploader. MalformedArchiveError.reason is the wire-visible code."""


class UploaderError(Exception):
    pass


class MalformedArchiveError(UploaderError):
    """400s: not-gzip, not-tar, truncated, empty, or an unsafe tar entry.

    reason values: not_gzip | not_tar | truncated_tar | empty_archive |
    absolute_path | path_traversal | symlink_entry | unsupported_entry_type.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class UploadTooLargeError(UploaderError):
    """413: cumulative uncompressed bytes exceeded the configured cap."""


class UploadTimeoutError(UploaderError):
    """499: upload did not complete within the configured deadline; nothing staged."""
