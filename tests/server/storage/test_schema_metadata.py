from server.storage.schema import Base


def test_all_tables_present():
    assert set(Base.metadata.tables) == {
        "producers", "sessions", "session_info", "users", "queue_jobs",
        "otel_series", "otel_metric_points", "otel_api_requests", "otel_watermarks",
        "org_tokens", "repositories", "feedback",
        "git_installations", "git_repos", "git_artifacts", "git_artifact_events", "git_deliveries",
        "git_pending_installs", "session_git_links", "pr_classification", "pr_issue_links",
        "session_graph", "session_tree",
        "identity_links", "teams", "team_members", "role_assignments",
        "budgets", "budget_cycle", "forecast_runs", "forecast_scope_series",
    }


def test_producers_has_generated_key_columns():
    cols = Base.metadata.tables["producers"].columns
    assert cols["key_hash"].computed is not None
    assert cols["key_prefix"].computed is not None


def test_sessions_has_subagent_linkage():
    cols = Base.metadata.tables["sessions"].columns
    # child->parent link, indexed for "list a session's subagents" (§3.3)
    assert cols["parent_session_id"].index is True
    assert cols["parent_session_id"].nullable is True
    # parent->child list of session_ids (JSONB)
    assert cols["subagents"].type.__class__.__name__ == "JSONB"


def test_no_foreign_keys():
    for table in Base.metadata.tables.values():
        assert not table.foreign_keys
