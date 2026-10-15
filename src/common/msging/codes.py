"""One enum everywhere: send() return codes, wire body `code`, NACK codes over
SSE (design §8). Two vocabularies for the same conditions is not allowed."""
from enum import StrEnum


class Code(StrEnum):
    OK = "ok"
    ALREADY_PROCESSED = "already_processed"   # reserved for §9.1; v1 never emits
    STORED_TIMEOUT = "stored_timeout"
    STORED_UNREACHABLE = "stored_unreachable"
    STORED_THROTTLED = "stored_throttled"
    SERVER_BUSY = "server_busy"
    SLOT_FULL = "slot_full"
    TOO_LARGE = "too_large"
    UNAUTHORIZED = "unauthorized"
    REJECTED = "rejected"
    ACK = "ack"
    NACK = "nack"


class Action(StrEnum):
    PURGE = "purge"
    RETAIN = "retain"


#: durably stored, not yet shipped — informational, the promise still holds
IS_STORED = frozenset({Code.STORED_TIMEOUT, Code.STORED_UNREACHABLE,
                       Code.STORED_THROTTLED, Code.SERVER_BUSY})

#: the only outright rejections — the caller must handle these
IS_REJECTION = frozenset({Code.SLOT_FULL, Code.TOO_LARGE})
