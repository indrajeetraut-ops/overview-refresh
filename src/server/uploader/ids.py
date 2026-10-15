"""Synthetic producer identity (RFC §5.2): uuid5 of declared org/user, so a user's
repeat uploads map to one stable producer without a registration round-trip. stdlib
uuid — no bespoke hashing (arch rule 6)."""
import uuid

TALIUM_PRODUCER_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "producers.talium.ai")


def make_producer_id(org_id: str, user_id: str) -> str:
    return str(uuid.uuid5(TALIUM_PRODUCER_NAMESPACE, f"{org_id}/{user_id}"))
