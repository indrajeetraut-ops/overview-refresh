"""Vendor dispatch (spec: docs/modules/normalizer.md §11, source-adapter.md §2.1).

Two registries, keyed on the same vendor string: `local_loader.SOURCE_READERS` owns file
layout, `normalizer.registry` owns record mapping. Adding a vendor is one entry in each and no
change to any caller. This suite pins that seam before a second vendor exists, because the
failure it prevents -- a vendor that silently normalizes to nothing -- is invisible once the
scaffold is load-bearing.

Codex is deliberately a stub. Its record shape is understood ({timestamp,type,payload}
envelope, `call_id` linkage, explicit `turn_context`), but it has no `uuid`/`parentUuid` chain,
so what `record_id`/`parent_id` mean for it is a causal-graph decision, not a mapping one.
"""
import pytest

from collector import normalizer as normalizer_pkg
from collector.normalizer.local_loader import SOURCE_READERS, load_codex_session_source, resolve_vendor
from collector.normalizer.models import SessionSource
from fixtures.normalizer import recorded_sessions


# -- the two registries agree ---------------------------------------------------------------


def test_both_registries_cover_the_same_vendors():
    """A vendor with a reader but no normalizer (or vice versa) fails at runtime, in the middle
    of a batch, on whichever file happens to be first."""
    assert set(SOURCE_READERS) == set(normalizer_pkg.registry) | {"codex"}
    assert "claude_code" in SOURCE_READERS and "claude_code" in normalizer_pkg.registry


def test_aliases_resolve_to_registry_keys():
    assert resolve_vendor("claude") == "claude_code"
    assert resolve_vendor("claude_code") == "claude_code"
    assert resolve_vendor("codex") == "codex"


def test_unknown_vendor_names_the_known_ones():
    with pytest.raises(ValueError) as excinfo:
        resolve_vendor("cursor")
    assert "cursor" in str(excinfo.value)
    assert "claude" in str(excinfo.value) and "codex" in str(excinfo.value)


# -- claude path unchanged ------------------------------------------------------------------


def test_claude_dispatches_through_the_registry():
    src = recorded_sessions.load_recorded("session-regular")
    direct = list(normalizer_pkg.registry["claude_code"].normalize(src))
    viaregistry = list(normalizer_pkg.normalize(recorded_sessions.load_recorded("session-regular")))
    assert [e.event_type for e in direct[0].events] == [e.event_type for e in viaregistry[0].events]


def test_claude_reader_still_returns_a_session_source():
    src = SOURCE_READERS["claude_code"](
        recorded_sessions.RECORDED_DIR / "session-regular.jsonl", vendor="claude_code", source="batch"
    )
    assert isinstance(src, SessionSource)
    assert src.vendor == "claude_code"
    assert src.records


# -- codex fails loudly ---------------------------------------------------------------------


def test_codex_reader_raises_not_implemented():
    """Raising beats returning an empty SessionSource: an empty result is indistinguishable
    from a session that genuinely had no records."""
    with pytest.raises(NotImplementedError) as excinfo:
        load_codex_session_source("/tmp/rollout-does-not-matter.jsonl")
    message = str(excinfo.value)
    assert "codex source reader not implemented" in message
    assert "causal-graph" in message  # says *why*, not just *that*


def test_codex_is_registered_as_a_reader_but_not_a_normalizer():
    """The seam exists on both sides; only the reader side is stubbed. A codex run fails at
    the reader, before it can produce a misleading empty normalization."""
    assert "codex" in SOURCE_READERS
    assert "codex" not in normalizer_pkg.registry


def test_normalize_rejects_an_unregistered_vendor():
    src = recorded_sessions.load_recorded("session-regular")
    src.vendor = "codex"
    with pytest.raises(ValueError, match="no VendorNormalizer registered"):
        list(normalizer_pkg.normalize(src))


# -- CLI surface ----------------------------------------------------------------------------


def test_cli_reports_unimplemented_vendor_without_a_traceback(tmp_path, capsys):
    """The batch wrapper captures stderr per file. A traceback there buries the one line that
    says what to do."""
    from collector import cli_normalize

    session = recorded_sessions.RECORDED_DIR / "session-regular.jsonl"
    with pytest.raises(SystemExit) as excinfo:
        cli_normalize.main([str(session), "--out", str(tmp_path / "o.json"), "--vendor", "codex"])
    assert "codex source reader not implemented" in str(excinfo.value)
    assert "Traceback" not in capsys.readouterr().err


def test_cli_rejects_an_unknown_vendor(tmp_path, capsys):
    from collector import cli_normalize

    session = recorded_sessions.RECORDED_DIR / "session-regular.jsonl"
    with pytest.raises(SystemExit) as excinfo:
        cli_normalize.main([str(session), "--out", str(tmp_path / "o.json"), "--vendor", "cursor"])
    assert excinfo.value.code == 2  # argparse usage error
    assert "unknown vendor 'cursor'" in capsys.readouterr().err


def test_cli_defaults_to_claude(tmp_path):
    import json

    from collector import cli_normalize

    out = tmp_path / "o.json"
    cli_normalize.main([str(recorded_sessions.RECORDED_DIR / "session-regular.jsonl"), "--out", str(out)])
    payload = json.loads(out.read_text())
    sessions = payload["sessions"] if isinstance(payload, dict) else payload
    assert sessions[0]["vendor"] == "claude_code"
