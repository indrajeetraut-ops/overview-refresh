import pytest

from collector.normalizer.claude_code import ClaudeCodeNormalizer


@pytest.fixture
def normalizer():
    return ClaudeCodeNormalizer()


@pytest.fixture
def normalize_one():
    """Runs a SessionSource with no subagents through normalize() and returns its single
    NormalizedSession -- most fixtures in this suite yield exactly one session."""

    def _run(normalizer, src):
        (session,) = list(normalizer.normalize(src))
        return session

    return _run
