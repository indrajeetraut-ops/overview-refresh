CREATE TABLE budget_cycle (
	org_id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	recurrence VARCHAR NOT NULL, 
	sprint_weeks INTEGER, 
	anchor_date DATE, 
	anchor_number INTEGER, 
	custom_end DATE, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (org_id)
);

CREATE TABLE budgets (
	scope_type VARCHAR NOT NULL, 
	scope_id VARCHAR NOT NULL, 
	period VARCHAR NOT NULL, 
	amount_usd NUMERIC(12, 2) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (scope_type, scope_id, period)
);

CREATE TABLE feedback (
	id SERIAL NOT NULL, 
	session_id VARCHAR NOT NULL, 
	beat_ref VARCHAR NOT NULL, 
	verdict VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_feedback_session_id ON feedback (session_id);

CREATE TABLE forecast_runs (
	run_id VARCHAR NOT NULL, 
	origin DATE NOT NULL, 
	data_through DATE NOT NULL, 
	generated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	meta JSONB NOT NULL, 
	loaded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (run_id)
);

CREATE TABLE forecast_scope_series (
	run_id VARCHAR NOT NULL, 
	scope_type VARCHAR NOT NULL, 
	scope_id VARCHAR NOT NULL, 
	preset_id VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	payload JSONB NOT NULL, 
	PRIMARY KEY (run_id, scope_type, scope_id, preset_id)
);

CREATE TABLE git_artifact_events (
	event_id SERIAL NOT NULL, 
	artifact_id VARCHAR NOT NULL, 
	action VARCHAR NOT NULL, 
	to_state VARCHAR NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	source_key VARCHAR NOT NULL, 
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (event_id), 
	UNIQUE (source_key)
);

CREATE INDEX ix_git_artifact_events_artifact_id ON git_artifact_events (artifact_id);

CREATE TABLE git_artifacts (
	artifact_id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	repo_id VARCHAR NOT NULL, 
	org_id VARCHAR NOT NULL, 
	number INTEGER, 
	sha VARCHAR, 
	author_login VARCHAR, 
	author_email VARCHAR, 
	user_id VARCHAR, 
	is_bot BOOLEAN NOT NULL, 
	state VARCHAR NOT NULL, 
	title VARCHAR, 
	branch VARCHAR, 
	base_branch VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	closed_at TIMESTAMP WITH TIME ZONE, 
	merged_at TIMESTAMP WITH TIME ZONE, 
	stats JSONB NOT NULL, 
	files JSONB NOT NULL, 
	parent_artifact_id VARCHAR, 
	labels JSONB NOT NULL, 
	milestone VARCHAR, 
	linked_issue VARCHAR, 
	merge_commit_sha VARCHAR, 
	before_sha VARCHAR, 
	after_sha VARCHAR, 
	integration_method VARCHAR, 
	reverts_sha VARCHAR, 
	commit_shas JSONB NOT NULL, 
	info JSONB NOT NULL, 
	PRIMARY KEY (artifact_id)
);

CREATE INDEX ix_git_artifacts_author_login ON git_artifacts (author_login);

CREATE INDEX ix_git_artifacts_merge_commit_sha ON git_artifacts (merge_commit_sha);

CREATE INDEX ix_git_artifacts_merged_at ON git_artifacts (merged_at);

CREATE INDEX ix_git_artifacts_parent_artifact_id ON git_artifacts (parent_artifact_id);

CREATE INDEX ix_git_artifacts_repo_kind_updated ON git_artifacts (repo_id, kind, updated_at);

CREATE INDEX ix_git_artifacts_reverts_sha ON git_artifacts (reverts_sha);

CREATE INDEX ix_git_artifacts_sha ON git_artifacts (sha);

CREATE INDEX ix_git_artifacts_user_id ON git_artifacts (user_id);

CREATE TABLE git_deliveries (
	delivery_id VARCHAR NOT NULL, 
	event VARCHAR NOT NULL, 
	action VARCHAR, 
	installation_id BIGINT, 
	repo_num BIGINT, 
	payload JSONB NOT NULL, 
	status VARCHAR NOT NULL, 
	attempts INTEGER NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	mapped_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (delivery_id)
);

CREATE INDEX ix_git_deliveries_status_received ON git_deliveries (status, received_at);

CREATE TABLE git_installations (
	installation_id BIGINT NOT NULL, 
	org_id VARCHAR NOT NULL, 
	account_login VARCHAR NOT NULL, 
	account_type VARCHAR NOT NULL, 
	repo_selection VARCHAR NOT NULL, 
	state VARCHAR NOT NULL, 
	installed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	info JSONB NOT NULL, 
	PRIMARY KEY (installation_id)
);

CREATE INDEX ix_git_installations_org_id ON git_installations (org_id);

CREATE INDEX ix_git_installations_state ON git_installations (state);

CREATE TABLE git_pending_installs (
	installation_id BIGINT NOT NULL, 
	org_id VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (installation_id)
);

CREATE TABLE git_repos (
	repo_id VARCHAR NOT NULL, 
	repo_num BIGINT NOT NULL, 
	full_name VARCHAR NOT NULL, 
	owner VARCHAR NOT NULL, 
	default_branch VARCHAR NOT NULL, 
	private BOOLEAN NOT NULL, 
	installation_id BIGINT NOT NULL, 
	org_id VARCHAR NOT NULL, 
	backfill_cursor TIMESTAMP WITH TIME ZONE, 
	backfill_state VARCHAR NOT NULL, 
	first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	info JSONB NOT NULL, 
	PRIMARY KEY (repo_id)
);

CREATE INDEX ix_git_repos_full_name ON git_repos (full_name);

CREATE INDEX ix_git_repos_installation_id ON git_repos (installation_id);

CREATE INDEX ix_git_repos_org_id ON git_repos (org_id);

CREATE INDEX ix_git_repos_repo_num ON git_repos (repo_num);

CREATE TABLE identity_links (
	provider VARCHAR NOT NULL, 
	external_id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	PRIMARY KEY (provider, external_id)
);

CREATE INDEX ix_identity_links_user_id ON identity_links (user_id);

CREATE TABLE org_tokens (
	token_hash VARCHAR NOT NULL, 
	org_id VARCHAR NOT NULL, 
	descriptor JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	PRIMARY KEY (token_hash)
);

CREATE INDEX ix_org_tokens_org_id ON org_tokens (org_id);

CREATE TABLE otel_api_requests (
	vendor VARCHAR NOT NULL, 
	request_id VARCHAR NOT NULL, 
	session_id VARCHAR, 
	org_id VARCHAR NOT NULL, 
	user_key VARCHAR, 
	model VARCHAR, 
	input_tokens BIGINT, 
	output_tokens BIGINT, 
	cache_read_tokens BIGINT, 
	cache_creation_tokens BIGINT, 
	cost_usd_micros BIGINT, 
	duration_ms BIGINT, 
	query_source VARCHAR, 
	prompt_id VARCHAR, 
	event_ts TIMESTAMP WITH TIME ZONE, 
	attrs JSONB NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (vendor, request_id)
);

CREATE INDEX ix_otel_api_requests_received_at ON otel_api_requests (received_at);

CREATE INDEX ix_otel_api_requests_vendor_session ON otel_api_requests (vendor, session_id);

CREATE TABLE otel_metric_points (
	series_id VARCHAR NOT NULL, 
	time_unix_nano BIGINT NOT NULL, 
	start_unix_nano BIGINT NOT NULL, 
	value NUMERIC NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (series_id, time_unix_nano)
);

CREATE INDEX ix_otel_metric_points_received_at ON otel_metric_points (received_at);

CREATE TABLE otel_series (
	series_id VARCHAR NOT NULL, 
	vendor VARCHAR NOT NULL, 
	metric_name VARCHAR NOT NULL, 
	metric_kind VARCHAR NOT NULL, 
	temporality VARCHAR NOT NULL, 
	org_id VARCHAR NOT NULL, 
	session_id VARCHAR, 
	user_key VARCHAR, 
	attrs JSONB NOT NULL, 
	first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (series_id)
);

CREATE INDEX ix_otel_series_vendor_session ON otel_series (vendor, session_id);

CREATE TABLE otel_watermarks (
	vendor VARCHAR NOT NULL, 
	watermark TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (vendor)
);

CREATE TABLE pr_classification (
	artifact_id VARCHAR NOT NULL, 
	change_type VARCHAR NOT NULL, 
	change_confidence NUMERIC NOT NULL, 
	change_tier INTEGER NOT NULL, 
	artifact_class VARCHAR NOT NULL, 
	is_revert BOOLEAN NOT NULL, 
	is_recall BOOLEAN NOT NULL, 
	recall_reason VARCHAR, 
	recall_confidence NUMERIC, 
	module VARCHAR, 
	rework_ratio NUMERIC, 
	features_digest JSONB NOT NULL, 
	analyzer VARCHAR NOT NULL, 
	classified_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (artifact_id)
);

CREATE INDEX ix_pr_classification_artifact_class ON pr_classification (artifact_class);

CREATE INDEX ix_pr_classification_change_type ON pr_classification (change_type);

CREATE INDEX ix_pr_classification_module ON pr_classification (module);

CREATE TABLE pr_issue_links (
	pr_artifact_id VARCHAR NOT NULL, 
	tracker VARCHAR NOT NULL, 
	project_key VARCHAR NOT NULL, 
	number INTEGER NOT NULL, 
	issue_artifact_id VARCHAR, 
	method VARCHAR NOT NULL, 
	confidence NUMERIC NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (pr_artifact_id, tracker, project_key, number)
);

CREATE INDEX ix_pr_issue_links_issue ON pr_issue_links (tracker, project_key, number);

CREATE INDEX ix_pr_issue_links_issue_artifact_id ON pr_issue_links (issue_artifact_id);

CREATE TABLE producers (
	producer_id VARCHAR NOT NULL, 
	record JSONB NOT NULL, 
	key_hash VARCHAR GENERATED ALWAYS AS ((record ->> 'key_hash')) STORED NOT NULL, 
	key_prefix VARCHAR GENERATED ALWAYS AS ((record ->> 'key_prefix')) STORED NOT NULL, 
	PRIMARY KEY (producer_id)
);

CREATE INDEX ix_producers_key_prefix ON producers (key_prefix);

CREATE UNIQUE INDEX uq_producers_key_hash ON producers (key_hash);

CREATE TABLE queue_jobs (
	id BIGSERIAL NOT NULL, 
	topic VARCHAR NOT NULL, 
	dedup_key VARCHAR NOT NULL, 
	payload JSONB NOT NULL, 
	status VARCHAR DEFAULT 'pending' NOT NULL, 
	run_after TIMESTAMP WITH TIME ZONE NOT NULL, 
	attempts INTEGER DEFAULT 0 NOT NULL, 
	enqueued_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	claimed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_queue_jobs_claim ON queue_jobs (topic, status, run_after, id);

CREATE UNIQUE INDEX uq_queue_jobs_pending ON queue_jobs (topic, dedup_key) WHERE status = 'pending';

CREATE TABLE repositories (
	repo_id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	label VARCHAR NOT NULL, 
	first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	info JSONB NOT NULL, 
	PRIMARY KEY (repo_id)
);

CREATE TABLE role_assignments (
	user_id VARCHAR NOT NULL, 
	role VARCHAR NOT NULL, 
	scope_type VARCHAR NOT NULL, 
	scope_id VARCHAR NOT NULL, 
	PRIMARY KEY (user_id, role, scope_type, scope_id)
);

CREATE INDEX ix_role_assignments_scope ON role_assignments (scope_type, scope_id);

CREATE TABLE session_git_links (
	session_id VARCHAR NOT NULL, 
	artifact_id VARCHAR NOT NULL, 
	method VARCHAR NOT NULL, 
	confidence NUMERIC NOT NULL, 
	signals JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (session_id, artifact_id)
);

CREATE INDEX ix_session_git_links_artifact_id ON session_git_links (artifact_id);

CREATE TABLE session_graph (
	session_id VARCHAR NOT NULL, 
	parent_session_id VARCHAR, 
	graph_json JSONB NOT NULL, 
	schema_version VARCHAR NOT NULL, 
	built_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (session_id)
);

CREATE INDEX ix_session_graph_parent_session_id ON session_graph (parent_session_id);

CREATE TABLE session_info (
	session_id VARCHAR NOT NULL, 
	cost_usd NUMERIC(12, 4), 
	models JSONB NOT NULL, 
	tools JSONB NOT NULL, 
	files JSONB NOT NULL, 
	skills JSONB NOT NULL, 
	subagents JSONB NOT NULL, 
	pr_list JSONB NOT NULL, 
	turns JSONB NOT NULL, 
	turn_count INTEGER NOT NULL, 
	long_turn_count INTEGER NOT NULL, 
	tokens_total INTEGER NOT NULL, 
	compaction_count INTEGER NOT NULL, 
	subagent_count INTEGER NOT NULL, 
	error_count INTEGER NOT NULL, 
	info JSONB NOT NULL, 
	otel JSONB NOT NULL, 
	cost_effective_usd_micros BIGINT, 
	datasource VARCHAR, 
	jsonl_lag_s INTEGER, 
	repo_id VARCHAR, 
	repo_id_provenance VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	recoverable_resume_usd_micros BIGINT DEFAULT 0 NOT NULL, 
	recoverable_cache_bust_usd_micros BIGINT DEFAULT 0 NOT NULL, 
	recoverable_interrupted_usd_micros BIGINT DEFAULT 0 NOT NULL, 
	reasoning_tokens BIGINT, 
	has_files BOOLEAN DEFAULT false NOT NULL, 
	session_type VARCHAR, 
	otel_vendor_cost_usd_micros BIGINT, 
	PRIMARY KEY (session_id)
);

CREATE INDEX ix_session_info_compaction_count ON session_info (compaction_count);

CREATE INDEX ix_session_info_cost_effective_usd_micros ON session_info (cost_effective_usd_micros);

CREATE INDEX ix_session_info_datasource ON session_info (datasource);

CREATE INDEX ix_session_info_error_count ON session_info (error_count);

CREATE INDEX ix_session_info_repo_id ON session_info (repo_id);

CREATE INDEX ix_session_info_subagent_count ON session_info (subagent_count);

CREATE INDEX ix_session_info_tokens_total ON session_info (tokens_total);

CREATE TABLE session_tree (
	master_session_id VARCHAR NOT NULL, 
	tree_json JSONB NOT NULL, 
	member_count INTEGER NOT NULL, 
	built_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (master_session_id)
);

CREATE TABLE sessions (
	session_id VARCHAR NOT NULL, 
	producer_id VARCHAR NOT NULL, 
	org_id VARCHAR, 
	user_id VARCHAR, 
	agent VARCHAR NOT NULL, 
	agent_version VARCHAR, 
	status VARCHAR NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_event_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	signal_blob JSONB NOT NULL, 
	info_blob JSONB NOT NULL, 
	parent_session_id VARCHAR, 
	subagents JSONB NOT NULL, 
	PRIMARY KEY (session_id)
);

CREATE INDEX ix_sessions_parent_session_id ON sessions (parent_session_id);

CREATE INDEX ix_sessions_producer_id ON sessions (producer_id);

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE TABLE team_members (
	team_id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	role_in_team VARCHAR, 
	PRIMARY KEY (team_id, user_id)
);

CREATE TABLE teams (
	team_id VARCHAR NOT NULL, 
	org_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	parent_team_id VARCHAR, 
	source_provider VARCHAR NOT NULL, 
	PRIMARY KEY (team_id)
);

CREATE INDEX ix_teams_org_id ON teams (org_id);

CREATE INDEX ix_teams_parent_team_id ON teams (parent_team_id);

CREATE TABLE users (
	user_id VARCHAR NOT NULL, 
	email VARCHAR, 
	name VARCHAR, 
	github_user VARCHAR, 
	display_name VARCHAR, 
	status VARCHAR DEFAULT 'active' NOT NULL, 
	primary_team_id VARCHAR, 
	source_provider VARCHAR, 
	account_uuid VARCHAR, 
	timezone VARCHAR, 
	info JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (user_id)
);

CREATE INDEX ix_users_account_uuid ON users (account_uuid);

CREATE INDEX ix_users_github_user ON users (github_user);
