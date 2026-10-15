"""Price source adapters: each module splits `fetch()` (network) from a pure `parse()`
so tests never touch the network (design §6.2, §10.1)."""
