"""Fixed word lists for synthetic PR/session/issue text (plan §5 P2). No Faker: everything here
is a hardcoded tuple, chosen by the caller's own `rng_for` stream, never process randomness.
Every list has at least 30 entries so 1k PRs rarely collide on wording.
"""
from __future__ import annotations

CHANGE_TYPES = ("feature", "fix", "refactor", "test", "docs", "chore")

_PREFIX = {"feature": "feat", "fix": "fix", "refactor": "refactor", "test": "test",
           "docs": "docs", "chore": "chore"}

VERBS: dict[str, tuple[str, ...]] = {
    "feature": (
        "add", "introduce", "enable", "implement", "support", "expose", "extend", "build",
        "create", "ship", "wire up", "land", "bootstrap", "scaffold", "provide", "expand",
        "unlock", "surface", "roll out", "stand up", "launch", "activate", "integrate",
        "generate", "compute", "publish", "register", "attach", "mount", "onboard",
    ),
    "fix": (
        "fix", "resolve", "correct", "patch", "address", "handle", "repair", "guard against",
        "prevent", "avoid", "eliminate", "stop", "unbreak", "close", "work around",
        "account for", "clamp", "sanitize", "validate", "reject", "recover from",
        "deduplicate", "debounce", "retry", "harden", "stabilize", "silence", "suppress",
        "backfill", "reconcile",
    ),
    "refactor": (
        "refactor", "simplify", "restructure", "reorganize", "extract", "consolidate",
        "streamline", "clean up", "rework", "modularize", "decouple", "inline", "split",
        "merge", "rename", "flatten", "normalize", "isolate", "generalize", "tidy",
        "deduplicate", "unify", "collapse", "reshape", "trim", "prune", "relocate",
        "encapsulate", "abstract", "tighten",
    ),
    "test": (
        "add tests for", "cover", "test", "exercise", "verify", "harden tests for",
        "expand coverage of", "add regression test for", "add unit tests for",
        "add integration tests for", "stabilize tests for", "add fixtures for",
        "add golden tests for", "backfill tests for", "add property tests for",
        "add edge-case tests for", "assert on", "snapshot", "fuzz", "add smoke tests for",
        "parametrize tests for", "add contract tests for", "seed test data for",
        "add benchmark for", "retry flaky", "quarantine flaky", "speed up tests for",
        "isolate tests for", "add mocks for", "add load tests for",
    ),
    "docs": (
        "document", "clarify", "update docs for", "describe", "explain", "annotate",
        "write up", "expand docs for", "correct docs for", "add examples for",
        "add a guide for", "add a README for", "diagram", "cross-link docs for",
        "add a changelog entry for", "add comments to", "add a runbook for",
        "add a troubleshooting section for", "polish docs for", "reorganize docs for",
        "link docs for", "add docstrings to", "translate docs for", "fix typos in",
        "add a quickstart for", "add an FAQ for", "note caveats for",
        "add an architecture note for", "record a decision for", "summarize",
    ),
    "chore": (
        "bump", "upgrade", "pin", "tidy", "prune", "remove unused", "clean up",
        "update", "reorganize", "housekeep", "vendor", "regenerate", "rotate",
        "archive", "deprecate", "remove", "relock", "sync", "align", "standardize",
        "consolidate", "migrate", "rename", "tag", "format", "lint", "sort",
        "compress", "cache", "schedule",
    ),
}

OBJECTS: tuple[str, ...] = (
    "the retry budget", "the cache layer", "the auth flow", "the rate limiter",
    "the webhook handler", "the batch job", "the queue consumer", "the migration script",
    "the config loader", "the session store", "the token refresh", "pagination",
    "error handling", "structured logging", "the metrics export", "the health check",
    "the circuit breaker", "the connection pool", "schema validation",
    "the background worker", "the notification service", "the search index",
    "the audit trail", "the feature flag", "the rollback path", "the backoff policy",
    "the idempotency key", "the webhook signature check", "the rate-limit header",
    "timeout handling", "the export pipeline", "the ingestion path", "the review queue",
    "the onboarding wizard", "the billing sync", "the permission model",
    "the dependency graph", "the request validator", "the response serializer",
)

QUALIFIERS: tuple[str, ...] = (
    "under load", "on retry", "for large payloads", "during startup",
    "for the nightly job", "on cold start", "for multi-tenant orgs", "at scale",
    "for the edge case", "on reconnect", "for slow networks", "under contention",
    "for the common case", "on the hot path", "for backwards compatibility",
    "for the migration window", "on failover", "for concurrent writers",
    "for the free tier", "on the first request", "for stale caches",
    "for partial responses", "for the batch path", "on shutdown", "for replay",
    "for the dry-run mode", "on schema drift", "for the admin console",
    "for degraded mode", "for the sandbox environment",
)

FILE_STEMS: tuple[str, ...] = (
    "client", "handler", "service", "model", "schema", "utils", "config", "worker",
    "router", "validator", "adapter", "repository", "controller", "middleware", "parser",
    "formatter", "cache", "queue", "pipeline", "transform", "loader", "exporter",
    "importer", "scheduler", "monitor", "metrics", "logger", "auth", "session", "token",
    "policy", "registry", "resolver", "serializer", "dispatcher",
)

SUBAGENT_PURPOSES: tuple[str, ...] = (
    "investigate a flaky test", "survey call sites", "draft a migration plan",
    "research library options", "audit error handling", "check test coverage",
    "explore the config surface", "review recent commits", "summarize related PRs",
    "verify edge cases", "trace a root cause", "gather context", "map dependencies",
    "outline an approach", "inspect logs", "validate assumptions",
    "collect benchmarks", "review documentation", "assess blast radius",
    "draft a rollback plan", "compare implementation options", "profile a hot path",
    "check for regressions", "reproduce a bug", "search for prior art",
    "review the API surface", "check backwards compatibility", "draft release notes",
    "scope a follow-up", "validate the fix",
)

UNLINKED_TITLES: tuple[str, ...] = (
    "Explore the payments retry logic", "Survey usage of the cache layer",
    "Investigate slow queries in the pipeline", "Look into the flaky auth test",
    "Understand the webhook signature flow", "Trace a memory growth report",
    "Review the current rate-limit config", "Map out the migration dependencies",
    "Check how the queue consumer scales", "Read through the review-queue design",
    "Rotate expired API keys", "Clean up stale feature flags",
    "Prune old branches and tags", "Update dependency pins",
    "Reconcile drifted config values", "Archive unused dashboards",
    "Regenerate stale fixtures", "Tidy up the local dev setup",
    "Sync environment variables across services", "Refresh expired certificates",
    "Why does the queue backlog spike on Mondays?",
    "Why is the cache hit ratio dropping this week?",
    "Why did the nightly job take twice as long?",
    "What is causing the intermittent timeouts?",
    "Which service owns the retry budget?",
    "Why do some webhooks arrive out of order?",
    "What changed in the last deploy?",
    "Why are error rates higher for one region?",
    "How is the search index kept in sync?",
    "What is the current on-call runbook missing?",
)

FEATURE_NOUNS: tuple[str, ...] = (
    "onboarding flow", "billing sync", "search relevance", "permission model",
    "notification pipeline", "audit trail", "rate limiting", "session management",
    "cache invalidation", "webhook delivery", "review queue", "migration tooling",
    "export pipeline", "ingestion path", "retry policy", "backoff strategy",
    "config management", "feature flagging", "access control", "usage metering",
    "cost attribution", "alerting", "health checks", "dependency graph",
    "request validation", "response caching", "background jobs", "queue consumers",
    "data reconciliation", "schema evolution",
)

SUBAGENT_TYPES = ("Explore", "general-purpose", "Plan")


def pr_title(rng, change_type: str, module_name: str) -> str:
    prefix = _PREFIX[change_type]
    verb = rng.choice(VERBS[change_type])
    obj = rng.choice(OBJECTS)
    title = f"{prefix}({module_name}): {verb} {obj}"
    if rng.random() < 0.35:
        title = f"{title} {rng.choice(QUALIFIERS)}"
    return title


def session_title(rng, pr_title_: str) -> str:
    """An imperative sentence without the conventional-commit prefix."""
    _, _, rest = pr_title_.partition("): ")
    rest = rest or pr_title_
    return rest[:1].upper() + rest[1:]


def issue_title(rng, feature_noun: str, module_name: str) -> str:
    template = rng.choice((
        "Improve {noun} in {module}",
        "Rework {noun} for {module}",
        "{noun} is inconsistent in {module}",
        "Add {noun} support to {module}",
        "Revisit {noun} in {module}",
    ))
    return template.format(noun=feature_noun, module=module_name)


def branch(change_type: str, issue_number: int | None, slug: str) -> str:
    prefix = _PREFIX[change_type]
    if issue_number is not None:
        return f"{prefix}/{issue_number}-{slug}"
    return f"{prefix}/{slug}"
