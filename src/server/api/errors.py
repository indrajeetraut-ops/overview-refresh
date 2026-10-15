"""Domain exceptions for the dashboard API. `app.py` catches these by type and translates
them to HTTP responses -- the same shape as `server/uploader/errors.py`.

Note what's *not* here: an endpoint that cannot compute a block in the caller's datasource
mode is not an error (DASH §8.1 rule 3) -- that is a 200 response whose body carries
`envelope.unavailable(...)`, never an exception. These classes are for requests that are
themselves invalid or unresolvable, not for capabilities a deployment structurally lacks.
"""


class ApiError(Exception):
    status_code: int = 500
    reason: str = "internal_error"

    def __init__(self, reason: str | None = None) -> None:
        if reason is not None:
            self.reason = reason
        super().__init__(self.reason)


class NotFoundError(ApiError):
    """404: no row for the given id (e.g. an unknown session_id)."""

    status_code = 404
    reason = "not_found"


class BadRequestError(ApiError):
    """400: a rejected query/path param -- e.g. an unknown `sort` value (A4). Rejected rather
    than silently defaulted, because a silently-ignored bad param looks like broken data."""

    status_code = 400
    reason = "bad_request"


class UnauthorizedError(ApiError):
    """401: no trusted viewer header, or it resolves to no user (F2). AuthN is a later SSO
    seam -- in MVP this is "the request carries no identity at all", not a credential check."""

    status_code = 401
    reason = "unauthorized"


class ForbiddenError(ApiError):
    """403: an authenticated viewer whose role/scope doesn't cover the request (F2) -- a dev
    outside their own team, a non-lead/admin write. Distinct from 401: the viewer is known,
    just not entitled to this scope."""

    status_code = 403
    reason = "forbidden"
