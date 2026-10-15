"""Config-driven redaction for the normalizer.

Span removal, never hashing. Applied to every string leaf of a record -- scoping to a
fixed set of keys risks missing an org's pattern in a field nobody anticipated, and
over-matching is the org's own pattern-authoring responsibility ("audited,
deliberate choice"). Redaction runs once per record, before both feature extraction and
the raw passthrough are built from it, so a secret can never survive in the passthrough
while being scrubbed from an extracted field.
"""
from __future__ import annotations

import re

_PLACEHOLDER = "[REDACTED]"


def compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p) for p in patterns]


def _redact_value(value, patterns: list[re.Pattern]):
    if isinstance(value, str):
        for pattern in patterns:
            value = pattern.sub(_PLACEHOLDER, value)
        return value
    if isinstance(value, dict):
        return {key: _redact_value(v, patterns) for key, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, patterns) for v in value]
    return value


def redact_record(record: dict, patterns: list[re.Pattern]) -> dict:
    """Returns a redacted deep copy of record, or record itself when patterns is empty."""
    if not patterns:
        return record
    return _redact_value(record, patterns)
