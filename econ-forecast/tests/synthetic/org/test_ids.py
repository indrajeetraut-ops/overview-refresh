"""P2: deterministic id/sha functions and the random-stream domain registry (plan §2/§4)."""
import ast
import re
from pathlib import Path

from forecast.synthetic.org import ids

ORG_SRC = Path(__file__).resolve().parents[3] / "src" / "forecast" / "synthetic" / "org"


def test_repo_num_stable_and_in_range():
    # Golden value: repo_num("acme/payments-api").
    assert ids.repo_num("acme/payments-api") == 900_000_000 + int(
        __import__("hashlib").sha256(b"synthetic-org:repo:acme/payments-api").hexdigest()[:8], 16) % 90_000_000
    for _ in range(50):
        value = ids.repo_num("acme/payments-api")
        assert 900_000_000 <= value < 990_000_000


def test_repo_num_deterministic_and_distinguishing():
    assert ids.repo_num("acme/a") == ids.repo_num("acme/a")
    assert ids.repo_num("acme/a") != ids.repo_num("acme/b")


def test_installation_id_range_and_deterministic():
    for owner in ("acme", "other-org"):
        value = ids.installation_id(owner)
        assert 800_000_000 <= value < 890_000_000
        assert ids.installation_id(owner) == value


def test_session_id_is_uuid5_and_deterministic():
    sid = ids.session_id("u1", "2026-01-05", "payments_api", "claude_code", "s00")
    assert re.fullmatch(r"[0-9a-f-]{36}", sid)
    assert sid == ids.session_id("u1", "2026-01-05", "payments_api", "claude_code", "s00")
    assert sid != ids.session_id("u1", "2026-01-05", "payments_api", "claude_code", "s01")


def test_child_session_id_matches_agent_hex_shape():
    child = ids.child_session_id(ids.session_id("u1", "2026-01-05", "p", "claude_code", "s00"), 1)
    assert re.fullmatch(r"agent-[0-9a-f]{17}", child)
    assert child == ids.child_session_id(ids.session_id("u1", "2026-01-05", "p", "claude_code", "s00"), 1)


def test_sha_is_hex_sha1_and_deterministic():
    value = ids.sha("repo", "pr:1", "0")
    assert re.fullmatch(r"[0-9a-f]{40}", value)
    assert value == ids.sha("repo", "pr:1", "0")
    assert value != ids.sha("repo", "pr:1", "1")


def test_review_id_deterministic_and_distinct():
    a = ids.review_id("repo:u1:1", 0)
    b = ids.review_id("repo:u1:1", 1)
    assert isinstance(a, int) and a == ids.review_id("repo:u1:1", 0)
    assert a != b


def test_team_slug_matches_d7():
    assert ids.team_slug("Engineering") == "engineering"
    assert ids.team_slug("Platform & Infra") == "platform-infra"
    assert ids.team_slug("  data_pipeline  ") == "data-pipeline"
    assert ids.team_slug("***") == "team"


def test_artifact_id_shape():
    assert ids.artifact_id(901234567, "pr", "42") == "github:901234567:pr:42"
    assert ids.artifact_id(901234567, "commit", "abc123") == "github:901234567:commit:abc123"


def test_choice_deterministic_and_weighted():
    import random

    weights = {"a": 1.0, "b": 3.0, "c": 0.0}
    counts = {"a": 0, "b": 0, "c": 0}
    rng = random.Random(7)
    for _ in range(4000):
        counts[ids.choice(weights, rng)] += 1
    assert counts["c"] == 0
    # b:a should be roughly 3:1.
    ratio = counts["b"] / counts["a"]
    assert 2.4 < ratio < 3.8, counts
    assert ids.choice({"x": 1.0}, random.Random(1)) == "x"
    # Un-normalized weights (e.g. raw token totals) work the same as normalized ones.
    assert ids.choice({"a": 10, "b": 30}, random.Random(3)) == ids.choice({"a": 1, "b": 3}, random.Random(3))


def test_domain_registry_complete():
    """Every `rng_for(`/`rng(`-style domain string literal used anywhere under org/ must be
    listed in `ids.DOMAINS` (plan §4)."""
    used: set[str] = set()
    for path in sorted(ORG_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if called_name not in ("rng_for", "rng"):
                continue
            args = node.args
            # `rng_for(seed, domain, *keys)` or a local `rng(domain, *keys)` closure.
            domain_arg = args[1] if called_name == "rng_for" and len(args) > 1 else (
                args[0] if called_name == "rng" and args else None)
            if isinstance(domain_arg, ast.Constant) and isinstance(domain_arg.value, str):
                used.add(domain_arg.value)
    # No rng_for/rng calls exist yet at P2 (sessions.py etc. land in P4+); this test keeps
    # working as those calls are added, re-scanning the whole org/ package each run.
    unknown = used - set(ids.DOMAINS)
    assert not unknown, f"domains used but not in ids.DOMAINS: {sorted(unknown)}"
