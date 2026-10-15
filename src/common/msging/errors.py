"""Runtime conditions return a Code; programming errors raise (design §8.3)."""


class MsgingError(Exception):
    pass


class ConfigError(MsgingError):
    """Unknown, unset, or disabled server alias; malformed config."""


class RegistrationError(MsgingError):
    """Server rejected registration. Terminal — no retry loop."""


class CapacityError(MsgingError):
    """Server side: a resource cap refuses the work (§10.1 `max_producers`).

    Distinct from `AuthError` on purpose -- the caller presented nothing wrong,
    the server has no room. The HTTP layer maps it to §8.1's `503 server_busy`
    + `Retry-After`, which is the one code that already means exactly that.
    """


class AuthError(MsgingError):
    """Server side: verification failed. code: missing | unknown | revoked."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NotRegisteredError(MsgingError):
    """send() before register()."""


class NoCallbackError(MsgingError):
    """SSE connect attempted without on_notify() (design §4.1)."""


class InvalidEnvelopeError(MsgingError):
    """dedup=True with no event_id."""


class ProducerClosingError(MsgingError):
    """send() after close() — the producer is draining."""
