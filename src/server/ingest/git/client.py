"""GitHubClient: the App-authenticated API reader (git-source.md §7.3/§7.4).

The auth chain, stated once so the implementation does not improvise:

    private key (RS256)                      the App's identity, shared across all customers
        │  sign a short-lived JWT (<=10 min, iss=client_id)
        ▼
    App JWT  ── Authorization: Bearer ─►  POST /app/installations/{id}/access_tokens
        │                                      (this is ALL the JWT can do -- it cannot read a repo)
        ▼
    installation token (ghs_…, 1 h)          scoped by GitHub to ONE installation's repos
        │  Authorization: Bearer
        ├─► REST     GET {api_base}/…
        └─► GraphQL  POST {graphql_url}

Customer isolation lives in the `installation_id` in the token-mint URL, not in the key: the
same key mints a differently-scoped token per installation, and GitHub enforces the boundary
server-side. Tokens are cached in-process to just before expiry and never written to disk or
DB (git-source.md §7.3).

Graduated (implementation plan A2) beyond the POC: a per-installation rate-limit throttle
(the 900-points/minute secondary limit and 100-concurrent cap, git-source.md §8.3, plus the
measured -- never estimated -- hourly primary budget from response headers/fields), ETag
conditional REST requests, and GraphQL query documents loaded from `.graphql` files.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import jwt  # PyJWT
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import GithubConfig

log = structlog.get_logger(module="ingest.git.client")

_JWT_TTL_S = 540           # 9 min; GitHub's hard cap is 10 min
_TOKEN_EARLY_S = 60        # re-mint this many seconds before the 1 h expiry

# The binding secondary limit for a tight backfill loop (git-source.md §8.3) -- easy to miss
# because it is not the number people quote. REST GET = 1 point, writes = 5 (we issue none);
# GraphQL's point cost is whatever the query's own `rateLimit.cost` measures.
_SECONDARY_POINTS_PER_MIN = 900.0
_MAX_CONCURRENT_PER_INSTALLATION = 100

_GRAPHQL_DIR = Path(__file__).parent / "graphql"


class GitHubApiError(RuntimeError):
    """An HTTP error from GitHub, carrying the status and response body so a failure is
    legible instead of an opaque `raise_for_status()` stack. The body is where GitHub explains
    itself -- a 404's "Not Found", a 403's rate-limit note, a 422's validation detail."""

    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} -> {status}: {body[:500]}")


class GitHubInstallationUnauthorized(GitHubApiError):
    """A 401: the installation's token was rejected -- typically a revoked or suspended
    install. Never retried (retrying a dead credential just loops), and raised as its own type
    so a caller can mark the `git_installations` row rather than treat this like any other API
    failure (git-source.md §10, §6.1)."""


class _Transient(RuntimeError):
    """A retryable failure: a network error or a 5xx. A 4xx (404, 401, 422) is NOT transient --
    retrying it four times just makes a wrong request slow, so those raise GitHubApiError (or
    GitHubInstallationUnauthorized) immediately and the retry decorator lets them through."""


class _SecondaryRateLimited(RuntimeError):
    """GitHub's secondary rate limit response (a 403/429 carrying `Retry-After`, §8.3): this is
    not "you're wrong," it's "slow down." Backed off for exactly the duration GitHub names,
    not the generic exponential schedule `_Transient` gets."""

    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(f"secondary rate limit; retry after {retry_after_s}s")


def _raise_for_github(method: str, url: str, r: httpx.Response) -> httpx.Response:
    if r.status_code < 400:
        return r
    if r.status_code == 401:
        raise GitHubInstallationUnauthorized(method, url, r.status_code, r.text)
    retry_after = r.headers.get("Retry-After")
    if r.status_code in (403, 429) and retry_after is not None:
        raise _SecondaryRateLimited(float(retry_after))
    if r.status_code == 429 or r.status_code >= 500:
        raise _Transient(f"{method} {url} -> {r.status_code}")
    raise GitHubApiError(method, url, r.status_code, r.text)


def _wait_secondary_or_backoff(retry_state):
    exc = retry_state.outcome.exception()
    if isinstance(exc, _SecondaryRateLimited):
        return exc.retry_after_s
    return wait_exponential(multiplier=1, max=30)(retry_state)


# Shared by every retrying call: a 5xx/network error backs off exponentially, a secondary
# rate limit backs off for exactly what GitHub asked (git-source.md §7.4, §8.3).
_retry_transient = retry(
    stop=stop_after_attempt(4),
    wait=_wait_secondary_or_backoff,
    retry=retry_if_exception_type((_Transient, _SecondaryRateLimited)),
)


def load_graphql(name: str) -> str:
    """Load a query document from `graphql/<name>.graphql`. Every document must request
    `rateLimit` (git-source.md §7.4): the throttle measures a GraphQL call's real cost from
    that field, so a document missing it would silently stop feeding the throttle real numbers
    -- caught here, at load time, rather than discovered later as a quietly-wrong budget."""
    path = _GRAPHQL_DIR / f"{name}.graphql"
    text = path.read_text(encoding="utf-8")
    if "rateLimit" not in text:
        raise ValueError(f"{path}: missing 'rateLimit {{ cost remaining nodeCount resetAt }}' (git-source.md §7.4)")
    return text


def _parse_reset_at(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


@dataclass
class _CachedToken:
    value: str
    expires_at: float      # epoch seconds


@dataclass
class _InstallationThrottle:
    """Per-installation rate-limit state (git-source.md §8.3). One instance per installation
    because rate limits, like tokens, are scoped to the installation -- not the process.

    Two budgets, tracked separately: the secondary points-per-minute budget (a token bucket,
    since it is the same 900 pts/min whether the point came from a REST GET or a GraphQL
    query), and the primary hourly budget, which is never computed here -- only *recorded*
    from what GitHub measured (REST's `X-RateLimit-*` headers, GraphQL's `rateLimit` field)."""

    installation_id: int
    concurrency: threading.BoundedSemaphore = field(
        default_factory=lambda: threading.BoundedSemaphore(_MAX_CONCURRENT_PER_INSTALLATION)
    )
    _points: float = field(default=_SECONDARY_POINTS_PER_MIN, init=False)
    _last_refill: float = field(default_factory=time.monotonic, init=False)
    hourly_remaining: int | None = field(default=None, init=False)
    hourly_reset_at: float | None = field(default=None, init=False)

    def _refill(self) -> None:
        now = time.monotonic()
        self._points = min(
            _SECONDARY_POINTS_PER_MIN,
            self._points + (now - self._last_refill) * (_SECONDARY_POINTS_PER_MIN / 60.0),
        )
        self._last_refill = now

    def spend(self, points: float) -> None:
        """Blocks until `points` are available, then spends them. Called before every REST/
        GraphQL request with a conservative estimate (REST: the real cost, always 1; GraphQL:
        a 1-point placeholder corrected by `adjust` once the response measures the real cost)."""
        while True:
            self._refill()
            if self._points >= points:
                self._points -= points
                return
            wait_s = (points - self._points) / (_SECONDARY_POINTS_PER_MIN / 60.0)
            log.info("rate_limit_wait", seconds=round(wait_s, 2),
                     remaining=round(self._points, 1), installation_id=self.installation_id)
            time.sleep(wait_s)

    def adjust(self, delta: float) -> None:
        """Correct the bucket once a response measures the real cost. Positive = refund
        (the placeholder overcharged); negative = spend more (it undercharged)."""
        self._refill()
        self._points = max(0.0, min(_SECONDARY_POINTS_PER_MIN, self._points + delta))

    def record_hourly(self, remaining: int, reset_at: float) -> None:
        """Record GitHub's own measurement of the primary hourly budget and, if it is
        exhausted, block until the reset rather than hammering a 0-remaining window."""
        self.hourly_remaining = remaining
        self.hourly_reset_at = reset_at
        if remaining <= 0:
            wait_s = max(0.0, reset_at - time.time())
            log.info("rate_limit_wait", seconds=round(wait_s, 1), remaining=remaining,
                     installation_id=self.installation_id)
            time.sleep(wait_s)


@dataclass
class GitHubClient:
    config: GithubConfig
    _private_key: str = field(default="", init=False, repr=False)
    _tokens: dict[int, _CachedToken] = field(default_factory=dict, init=False, repr=False)
    _throttles: dict[int, _InstallationThrottle] = field(default_factory=dict, init=False, repr=False)
    # Keyed on (installation_id, path, sorted params) -- the same GET on two installations (or
    # with different params) is a different resource and must not share a conditional cache.
    _etags: dict[tuple, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.config.private_key_path:
            self._private_key = Path(self.config.private_key_path).read_text(encoding="utf-8")

    def _throttle_for(self, installation_id: int) -> _InstallationThrottle:
        t = self._throttles.get(installation_id)
        if t is None:
            t = _InstallationThrottle(installation_id)
            self._throttles[installation_id] = t
        return t

    def budget_snapshot(self, installation_id: int) -> dict[str, Any]:
        """The client's own most-recently-measured rate-limit state for one installation --
        never estimated (git-source.md §7.4). Meant for a caller running a multi-request pass
        (a backfill, a diagnose run) to log one summary line rather than one per request."""
        t = self._throttle_for(installation_id)
        return {
            "installation_id": installation_id,
            "hourly_remaining": t.hourly_remaining,
            "hourly_reset_at": t.hourly_reset_at,
            "secondary_points_available": round(t._points, 1),
        }

    # --- app-level auth ---------------------------------------------------------------

    def _app_jwt(self) -> str:
        """A short-lived JWT signed with the App private key. iss=client_id (GitHub's current
        recommendation over app_id); iat backdated 60 s for clock drift."""
        if not self._private_key:
            raise RuntimeError("no private key loaded; set private_key_path")
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + _JWT_TTL_S,
            "iss": self.config.client_id or self.config.app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    def _app_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.api_version,
        }

    @_retry_transient
    def list_installations(self) -> list[dict[str, Any]]:
        """GET /app/installations -- which accounts installed the App. App-JWT auth."""
        url = f"{self.config.api_base}/app/installations"
        r = _raise_for_github("GET", url, httpx.get(url, headers=self._app_headers()))
        return r.json()

    @_retry_transient
    def list_hook_deliveries(self, per_page: int = 30) -> list[dict[str, Any]]:
        """GET /app/hook/deliveries -- GitHub's own log of what it tried to deliver to our
        webhook URL, and the HTTP status it got back. App-JWT auth. This is the single most
        useful call for 'the webhook isn't receiving anything': it distinguishes 'GitHub never
        sent' (empty / nothing recent) from 'GitHub sent and we 401'd it' (status_code=401)."""
        url = f"{self.config.api_base}/app/hook/deliveries"
        r = _raise_for_github("GET", url,
                              httpx.get(url, headers=self._app_headers(),
                                        params={"per_page": per_page}))
        return r.json()

    # --- installation-level auth ------------------------------------------------------

    def installation_token(self, installation_id: int) -> str:
        """A 1 h token scoped to one installation, cached in-process to just before expiry."""
        cached = self._tokens.get(installation_id)
        if cached and cached.expires_at - _TOKEN_EARLY_S > time.time():
            return cached.value
        url = f"{self.config.api_base}/app/installations/{installation_id}/access_tokens"
        r = _raise_for_github("POST", url, httpx.post(url, headers=self._app_headers()))
        body = r.json()
        # expires_at is ISO-8601; parse minimally without pulling a date lib.
        expires_at = time.time() + 3600 - 30   # conservative; the header says ~1 h
        self._tokens[installation_id] = _CachedToken(body["token"], expires_at)
        log.info("installation_token_minted", installation_id=installation_id)
        return body["token"]

    def _inst_headers(self, installation_id: int) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.installation_token(installation_id)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.api_version,
        }

    # --- the two data readers ---------------------------------------------------------

    @_retry_transient
    def rest_get(self, installation_id: int, path: str, params: dict | None = None) -> httpx.Response:
        """One REST GET. `path` is relative to api_base, e.g. '/repos/o/n/pulls'.

        Budget-checked (the 900 pts/min secondary limit and the 100-concurrent cap, §8.3) and
        ETag-conditional: a prior call's `ETag` response header is replayed as `If-None-Match`,
        so a repeat gap-fill of mostly-unchanged PRs answers `304` -- free against the primary
        limit (§8.2). The caller reads `r.status_code == 304` to know nothing changed."""
        throttle = self._throttle_for(installation_id)
        url = f"{self.config.api_base}{path}"
        cache_key = (installation_id, path, tuple(sorted((params or {}).items())))
        headers = self._inst_headers(installation_id)
        etag = self._etags.get(cache_key)
        if etag:
            headers["If-None-Match"] = etag
        with throttle.concurrency:
            throttle.spend(1)   # REST GET = 1 point (§8.3)
            r = httpx.get(url, headers=headers, params=params or {})
        r = _raise_for_github("GET", url, r)
        remaining = r.headers.get("X-RateLimit-Remaining")
        reset = r.headers.get("X-RateLimit-Reset")
        if remaining is not None and reset is not None:
            throttle.record_hourly(int(remaining), float(reset))
        new_etag = r.headers.get("ETag")
        if r.status_code != 304 and new_etag:
            self._etags[cache_key] = new_etag
        return r

    @_retry_transient
    def graphql(self, installation_id: int, query: str, variables: dict | None = None) -> dict[str, Any]:
        """One GraphQL POST. Returns the parsed `data`.

        Two failure modes, both surfaced rather than swallowed:
        - HTTP error (401/404 auth or permission): raised as GitHubApiError with the body. Note
          the GraphQL endpoint does NOT accept `X-GitHub-Api-Version`, so this uses app-style
          headers WITHOUT that header -- sending it is one documented cause of a GraphQL 404.
        - HTTP 200 with a partial `data` and an `errors[]` array (a timeout or permission gap;
          git-source.md §3.3): a caller that maps partial data silently corrupts metrics, so a
          non-empty `errors` is raised here.

        When the response carries `data.rateLimit` (every query document must request it,
        `load_graphql`), the throttle's 1-point placeholder is corrected to the measured cost
        and the primary hourly budget is recorded -- measured, never estimated (§7.4)."""
        throttle = self._throttle_for(installation_id)
        headers = {
            "Authorization": f"Bearer {self.installation_token(installation_id)}",
            "Accept": "application/vnd.github+json",
        }
        with throttle.concurrency:
            throttle.spend(1)   # placeholder; corrected below once the real cost is measured
            r = httpx.post(self.config.graphql_url, headers=headers,
                           json={"query": query, "variables": variables or {}})
        r = _raise_for_github("POST", self.config.graphql_url, r)
        body = r.json()
        if body.get("errors"):
            raise GitHubApiError("POST", self.config.graphql_url, 200, str(body["errors"]))
        data = body["data"]
        rl = data.get("rateLimit")
        if rl:
            throttle.adjust(1.0 - rl["cost"])
            throttle.record_hourly(rl["remaining"], _parse_reset_at(rl["resetAt"]))
        return data
