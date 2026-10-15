"""Stable feature-specific streams; never depend on process hash randomization."""
import hashlib
import json
import random


def derive_seed(seed: int, domain: str, *keys: str) -> int:
    data = json.dumps([seed, domain, *keys], ensure_ascii=True, separators=(",", ":")).encode()
    return int.from_bytes(hashlib.sha256(data).digest()[:16], "big")


def rng_for(seed: int, domain: str, *keys: str) -> random.Random:
    return random.Random(derive_seed(seed, domain, *keys))
