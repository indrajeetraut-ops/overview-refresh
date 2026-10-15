"""Provider precedence (docs/design/identity-ingestion.md §3, implementation plan G3).

When two providers assert the same person, precedence decides who owns a field:
`authoritative IdP > csv > telemetry`. The winner is stamped on `users.source_provider`,
so a later CSV re-import cannot clobber a field an IdP owns, and a lower-ranked provider
never overwrites a higher-ranked one.
"""
from __future__ import annotations

from collections.abc import Sequence

#: Ordered highest-precedence first. `config/directory.yaml` overrides it; adding `scim`/`ldap`
#: above `csv` when those providers land is a config line, not code (IDENT §8).
DEFAULT_CHAIN: tuple[str, ...] = ("scim", "ldap", "oidc", "csv", "telemetry")


class Precedence:
    def __init__(self, chain: Sequence[str] = DEFAULT_CHAIN) -> None:
        if not chain:
            raise ValueError("precedence chain must not be empty")
        self._chain = tuple(chain)
        # lower rank number == higher precedence
        self._rank = {name: i for i, name in enumerate(self._chain)}
        # a provider not in the chain sits below every named one (but still above "never set")
        self._unknown_rank = len(self._chain)

    @property
    def chain(self) -> tuple[str, ...]:
        return self._chain

    def _rank_of(self, provider: str | None) -> int:
        if provider is None:
            return self._unknown_rank + 1  # nothing has been asserted yet -- anyone outranks it
        return self._rank.get(provider, self._unknown_rank)

    def wins(self, incoming: str, existing: str | None) -> bool:
        """True when `incoming` may write a field currently owned by `existing`.

        Outranks **or equals** (plan G3): a same-provider re-import updates normally; a
        lower-ranked provider is refused; an unset field (`existing is None`) is always
        writable.
        """
        return self._rank_of(incoming) <= self._rank_of(existing)
