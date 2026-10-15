"""The canonical corpus the published contract is derived from.

One definition, two consumers: `test_shape_contract.py` (the drift guard) and
`common.events.cli_publish_payload` (regeneration). If they built the corpus separately they
could disagree, and the committed artifacts would fail a guard that was measuring something
else.

Recorded real sessions first -- the primary subject -- then the synthetic builders covering
event types the recordings do not contain (api_error, compaction, denials, attachments, remote
MCP artifacts). Ordered so anything derived from it is deterministic.
"""
from __future__ import annotations

from collector.normalizer.claude_code import ClaudeCodeNormalizer

from . import recorded_sessions, sessions

SYNTHETIC_BUILDERS = [
    sessions.simple_session,
    sessions.tool_pairing_session,
    sessions.denied_tool_session,
    sessions.attachment_session,
    sessions.mcp_remote_artifact_session,
    sessions.mixed_model_subagent_session,
    sessions.api_error_and_compaction_session,
    sessions.passthrough_session,
    sessions.pr_link_session,
]


def all_sources():
    """Every SessionSource in the corpus, recorded first."""
    yield from recorded_sessions.all_recorded()
    for build in SYNTHETIC_BUILDERS:
        yield build()


def all_sessions(normalizer=None):
    """Every NormalizedSession the corpus produces, including subagent streams."""
    normalizer = normalizer or ClaudeCodeNormalizer()
    for src in all_sources():
        yield from normalizer.normalize(src)
