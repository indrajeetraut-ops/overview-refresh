"""The backend-owned response contract (MVP plan 2026-09-09 §1 + F0).

**Source of truth.** These Pydantic models are the contract. FastAPI emits an OpenAPI schema
from them; the front-end's TypeScript types are generated from that schema in CI
(`utils/gen_api_types.sh` -> `src/webui/src/data/api-types.ts`) and drift is a build failure,
not a convention two files must remember. `mock.ts` is demoted to a fixture that *conforms* to
the generated types.

**The envelope wraps every manager view model** (DASH §8.1, plan §1):

* every response carries a mode block -- `SessionMode` (session-scoped) or `AggregateMode`
  (a timeframe spanning >1 datasource mode);
* every dollar carries its completeness basis -- `Money` (one cost source, ground rule 3;
  the API never re-derives dollars from tokens);
* a block that cannot be computed in the caller's datasource mode is `data: null` +
  `unavailable: {block, requires}` -- never `0`, never `[]` (those are measurements).

See `README.md` in this package for the "prototype-isms to strip" rules that govern what may
and may not enter a view model (no colours, no pre-formatted strings, no routing hints).
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")
V = TypeVar("V")


class Sourced(BaseModel, Generic[V]):
    """A precedence-resolved figure plus the source chain that produced it (envelope rule 2 --
    a figure whose provenance the UI cannot name is one it cannot caption honestly). `source`
    is a comma-joined set for an aggregate (`resolve_aggregate` returns the set that actually
    contributed, not a single scalar)."""

    model_config = ConfigDict(extra="forbid")

    value: V | None
    source: str | None = None


class Money(BaseModel):
    """A dollar figure with its completeness basis (DASH §8.1: every dollar carries
    `cost_basis`/`cost_complete`). `usd_micros` is the single cost source (ground rule 3);
    nothing downstream re-derives it from tokens."""

    model_config = ConfigDict(extra="forbid")

    usd_micros: int | None
    #: the precedence sources that contributed, comma-joined -- e.g. "otel_vendor",
    #: "otel_vendor,estimated". `None` only when `usd_micros` is `None`.
    cost_basis: str | None = None
    #: False when any contributing session's cost was estimated or partial (the UI captions
    #: an incomplete figure differently); `None` when `usd_micros` is `None`.
    cost_complete: bool | None = None


class UnavailableBlock(BaseModel):
    """`block` names the capability that cannot be computed (e.g. "prs", "provenance");
    `requires` names what would make it available (e.g. "jsonl", "otel", "github") -- MODES
    §5 rule 1: name the source, not the user."""

    model_config = ConfigDict(extra="forbid")

    block: str
    requires: str


class Unavailable(BaseModel):
    """Rule 3's shape for a whole sub-block that is absent (not empty). Embed this in a view
    model where one section can be dark while the rest of the payload is fine."""

    model_config = ConfigDict(extra="forbid")

    unavailable: UnavailableBlock


class SessionMode(BaseModel):
    """Mode block for a session-scoped response (a single `session_info` row)."""

    model_config = ConfigDict(extra="forbid")

    datasource: str | None = None
    vendor: str | None = None


class AggregateMode(BaseModel):
    """Mode block for an aggregate response -- a timeframe can span sessions in more than one
    datasource mode at once, so there is no single `datasource`; the UI needs the full set to
    choose between `unavailable` and cold-start per block."""

    model_config = ConfigDict(extra="forbid")

    modes_present: list[str]
    sessions_by_mode: dict[str, int]


class Envelope(BaseModel, Generic[T]):
    """The one wrapper every manager view-model response carries. Declared once here so it
    lands in the OpenAPI schema identically for every endpoint that returns
    `Envelope[SomeViewModel]`.

    `data` is `None` when the entire view model is uncomputable in the caller's datasource
    mode; `unavailable` then names why. A partially-dark payload keeps `data` non-null and
    carries `Unavailable` on the specific sub-block instead.
    """

    model_config = ConfigDict(extra="forbid")

    data: T | None = None
    mode: SessionMode | AggregateMode
    unavailable: UnavailableBlock | None = None
