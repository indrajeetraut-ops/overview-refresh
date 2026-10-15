from server.worker.pr_class.config import DEFAULT_BRANCH_SENTINEL, load_config


def test_load_config_reads_the_shipped_config():
    cfg = load_config("config/pr_class.yaml")
    assert cfg.eval_scope.base_branches == [DEFAULT_BRANCH_SENTINEL]
    assert "release/*" in cfg.eval_scope.exclude_head_branches
    assert cfg.pr_class.change_type.tier0_self_reported is True
    assert cfg.pr_class.change_type.tier2_diff is False
    assert cfg.pr_class.change_type.combine == "tier0_authoritative"
    assert cfg.pr_class.recall_reason is False
    assert cfg.artifact_class.documentation_min_fraction == 0.8
    assert cfg.integration.already_owned_ratio_threshold == 0.8
    assert cfg.modules == []


def test_per_repo_override_parses():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "pr_class.yaml"
        path.write_text(
            "eval_scope:\n"
            "  per_repo:\n"
            "    'github:1':\n"
            "      base_branches: ['develop']\n"
        )
        cfg = load_config(path)
    assert cfg.eval_scope.per_repo["github:1"].base_branches == ["develop"]
