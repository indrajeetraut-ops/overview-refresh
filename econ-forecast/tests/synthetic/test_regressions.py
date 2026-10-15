"""Parent-review regression checks for gaps in the original implementation."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import yaml
from pydantic import ValidationError

from forecast.artifact_io import DatasetWriter, RecordStream, read_records
from forecast.synthetic import runner
from forecast.synthetic.config import (SeriesConfig, UsersConfig, RosterConfig,
    load_series_config, load_users_config, validate_series_inputs)
from forecast.synthetic.errors import ArtifactError, ConfigError, InputDataError
from forecast.synthetic.models import TeamRecord, UserRecord
from forecast.synthetic.roster import create_roster
from forecast.synthetic.series import generate_days
from forecast.synthetic.profiles import assign_profiles
from forecast.synthetic.users import create_users

ROOT = Path(__file__).parents[3]
WRAPPER = ROOT / 'econ-forecast/utils/synt_gen.sh'


def _processed(row):
    t = row.tokens
    return t.input + t.cache_write_5m + t.cache_write_1h + t.cache_read


@pytest.fixture
def raw_config():
    return {
        'schema_version': 3,
        'series': {'users_file': 'unused', 'roster_file': 'unused', 'start_date': '2026-07-01',
                   'days': 3, 'timezone': 'UTC', 'profile_seed': 100, 'usage_seed': 123},
        'defaults': {'daily_variation_pct': 0, 'output_ratio': .03, 'cache_hit_ratio': .8,
                     'cache_hit_variation_pp': 0, 'cache_write_ratio': 0.997, 'cache_write_1h_share': 0.97, 'mix_variation_pct': 0,
                     'daily_zero_probability': 0, 'weekend_reduction_probability': 0,
                     'weekend_reduction_factor': 1},
        'profiles': {'mid': {'daily_input_tokens': 1000000}},
        'team_profiles': {'team': {'profile_mix': {'mid': 1}, 'project_mix': {'Payments': 1}}},
        'projects': [{'name': 'Payments'}],
        'agents': {'claude': {'billing_mode': 'api', 'usage_share': .6, 'models': {'sonnet': .75, 'opus': .25}},
                   'codex': {'billing_mode': 'api', 'usage_share': .4, 'models': {'terra': .75, 'sol': .25}}},
        'events': {'spike_rules': [], 'zero_periods': []},
    }


@pytest.fixture
def prepared(tmp_path, raw_config):
    user_config = UsersConfig.model_validate({'schema_version': 1, 'users': {
        'count': 4, 'org_domain': 'talium.ai', 'locale': 'en_US', 'seed': 42}})
    runner.run_users(user_config, tmp_path / 'users')
    roster_config = RosterConfig.model_validate({'schema_version': 1, 'roster': {
        'users_file': tmp_path / 'users/users.jsonl', 'seed': 43,
        'teams': [{'team_name': 'team', 'count': 3}]}})
    runner.run_roster(roster_config, tmp_path / 'roster')
    raw_config['series']['users_file'] = str(tmp_path / 'users/users.jsonl')
    raw_config['series']['roster_file'] = str(tmp_path / 'roster/teams.jsonl')
    return raw_config


def mutate(data, path, value):
    container = data
    for part in path[:-1]:
        container = container[part]
    container[path[-1]] = value


@pytest.mark.parametrize('path', [
    ('defaults',),
    ('series', 'timezone'),
    ('defaults', 'daily_zero_probability'),
    ('agents', 'claude', 'billing_mode'),
    ('events', 'spike_rules'),
    ('events', 'zero_periods'),
    ('events',),
])
def test_missing_behavior_settings_are_rejected(raw_config, path):
    parent = raw_config
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate(raw_config)


def test_weekend_reduction_defaults_to_off(raw_config):
    """Omitting the weekend knobs generates no weekly pattern (default probability 0)."""
    del raw_config['defaults']['weekend_reduction_probability']
    del raw_config['defaults']['weekend_reduction_factor']
    config = SeriesConfig.model_validate(raw_config)
    assert config.defaults.weekend_reduction_probability == 0.0


@pytest.mark.parametrize('field', ['count', 'locale'])
def test_missing_user_settings_are_rejected(field):
    raw = {'schema_version': 1, 'users': {
        'count': 4, 'org_domain': 'example.test', 'locale': 'en_US', 'seed': 1,
    }}
    del raw['users'][field]
    with pytest.raises(ValidationError):
        UsersConfig.model_validate(raw)


def test_team_activity_requires_complete_values(raw_config):
    raw_config['team_profiles']['team']['activity'] = {
        'weekend_reduction_probability': .01,
    }
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate(raw_config)


def test_one_day_event_needs_explicit_end_date(raw_config):
    raw_config['events']['zero_periods'] = [{
        'id': 'pause', 'target_type': 'team', 'target': 'team',
        'start_date': '2026-07-01',
    }]
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate(raw_config)


@pytest.mark.parametrize('path,value', [
    (('schema_version',), True), (('schema_version',), 1.0),
    (('defaults','output_ratio'), 1), (('defaults','output_ratio'), True),
    (('defaults','cache_hit_ratio'), float('nan')), (('defaults','daily_variation_pct'), 100),
    (('defaults','mix_variation_pct'), float('inf')), (('defaults','output_ratio'), '.03'),
    (('defaults','weekend_reduction_probability'), 1.01),
    (('defaults','weekend_reduction_factor'), 0),
    (('series','days'), True), (('series','days'), 1.5), (('series','days'), 0),
    (('series','days'), 10**100), (('series','start_date'), 0),
    (('series','start_date'), '2026-07-01T00:00:00'), (('series','timezone'), '../zone'),
    (('agents','claude','models'), {'sonnet': True}),
    (('agents','claude','models'), {'sonnet': '1'}),
    (('agents','claude','models'), {' ': 1}),
    (('agents','claude','usage_share'), -.4),
    (('agents','claude','usage_share'), .600000005),
    (('agents','claude','billing_mode'), 'subscription'),
    (('team_profiles','team','profile_mix'), {'mid': True}),
    (('team_profiles','team','project_mix'), {12: .5, 'Payments': .5}),
    (('team_profiles','team','project_mix'), ['Payments']),
    (('team_profiles','team','project_mix'), {'unknown': 1}),
    (('team_profiles','team','activity'), {'daily_variation_pct': 2}),
    (('events',), {'spike_rules': [{'id': 'x', 'probability': True,
                                    'multiplier_min': 2, 'multiplier_max': 3,
                                    'targets': {'teams': [], 'agents': [], 'models': []}}],
                   'zero_periods': []}),
    (('events',), {'spike_rules': [{'id': 'x', 'probability': .2,
                                    'multiplier_min': 2, 'multiplier_max': '3',
                                    'targets': {'teams': [], 'agents': [], 'models': []}}],
                   'zero_periods': []}),
])
def test_invalid_config_is_rejected_before_coercion(raw_config, path, value):
    mutate(raw_config, path, value)
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate(raw_config)


def test_share_normalization_is_order_independent_and_zero_preserving(raw_config):
    raw_config['agents']['claude']['models'] = {'a': .5, 'b': .5000000005, 'zero': 0}
    first = SeriesConfig.model_validate(raw_config)
    raw_config['agents']['claude']['models'] = dict(reversed(list(raw_config['agents']['claude']['models'].items())))
    second = SeriesConfig.model_validate(raw_config)
    assert first == second
    assert sum(first.agents['claude'].models.values()) == pytest.approx(1)
    assert first.agents['claude'].models['zero'] == 0


def test_duplicate_projects_and_rule_ids_are_rejected_but_overlaps_are_allowed(raw_config):
    bad = copy.deepcopy(raw_config)
    bad['projects'].append({'name': 'payments'})
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate(bad)
    target={'teams':['team'],'agents':[],'models':[]}
    raw_config['events']['spike_rules'] = [
        {'id':'one','probability':1,'multiplier_min':2,'multiplier_max':2,
         'date_range':{'start_date':'2026-07-01','end_date':'2026-07-02'},'targets':target},
        {'id':'two','probability':1,'multiplier_min':3,'multiplier_max':3,
         'date_range':{'start_date':'2026-07-02','end_date':'2026-07-02'},'targets':target}]
    SeriesConfig.model_validate(raw_config)
    raw_config['events']['spike_rules'][1]['id']='one'
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate(raw_config)


@pytest.mark.parametrize('targets', [
    {'teams':['team','team'],'agents':[],'models':[]},
    {'teams':[],'agents':['codex','codex'],'models':[]},
    {'teams':[],'agents':[],'models':[{'agent':'codex','model':'sol'},
                                      {'agent':'codex','model':'sol'}]},
    {'teams':[],'agents':['codex'],'models':[{'agent':'codex','model':'sol'}]},
    {'teams':[],'agents':['unknown'],'models':[]},
    {'teams':[],'agents':[],'models':[{'agent':'codex','model':'unknown'}]},
])
def test_invalid_spike_targets_are_rejected(raw_config, targets):
    raw_config['events']['spike_rules']=[{
        'id':'bad','probability':.2,'multiplier_min':2,'multiplier_max':3,
        'targets':targets}]
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate(raw_config)


def test_unknown_spike_team_is_rejected_against_the_roster(prepared):
    prepared['events']['spike_rules']=[{
        'id':'bad-team','probability':.2,'multiplier_min':2,'multiplier_max':3,
        'targets':{'teams':['unknown'],'agents':[],'models':[]}}]
    config=SeriesConfig.model_validate(prepared)
    users=read_records(config.series.users_file,UserRecord)
    teams=read_records(config.series.roster_file,TeamRecord)
    with pytest.raises(InputDataError,match='Team target'):
        validate_series_inputs(config,users,teams)


def test_all_inactive_report_and_manifest_are_complete(prepared, tmp_path):
    prepared['defaults']['daily_zero_probability'] = 1
    config = SeriesConfig.model_validate(prepared)
    result = runner.run_series(config, tmp_path / 'inactive')
    assert result.summary_counts['usage_rows'] == 0
    assert result.summary_counts['coverage_rows'] == 3 * 3 * 2
    assert result.summary_counts['input_tokens'] == 0
    report = result.report_path.read_text()
    assert 'N/A' in report and 'Coverage check: PASS' in report and 'All user-days' in report
    assert 'Profile seed: 100' in report and 'Usage seed: 123' in report
    manifest = json.loads((result.output_dir / 'manifest.json').read_text())
    assert manifest['summary'] == result.summary_counts
    assert hashlib.sha256(result.report_path.read_bytes()).hexdigest() == manifest['report']['sha256']
    assert (result.output_dir / 'usage.jsonl').read_bytes() == b''


def test_weekend_reduction_preserves_usage_but_excludes_spikes(prepared, tmp_path):
    prepared['series'].update(start_date='2026-07-04', days=2)
    prepared['defaults']['weekend_reduction_probability'] = 1
    prepared['defaults']['weekend_reduction_factor'] = .25
    prepared['events']['spike_rules'] = [{
        'id':'weekdays-only','probability':1,'multiplier_min':3,'multiplier_max':3,
        'targets':{'teams':[],'agents':[],'models':[]}}]
    result = runner.run_series(SeriesConfig.model_validate(prepared), tmp_path / 'weekend')
    assert result.summary_counts['zero_volume_user_days'] == 0
    assert result.summary_counts['event_rows'] == 0
    assert result.summary_counts['active_user_days'] == 6


def test_streams_write_each_day_before_requesting_the_next(prepared, tmp_path, monkeypatch):
    real_generate = runner.generate_days
    real_write = RecordStream.write
    written = []
    def track_write(self, record):
        written.append(type(record).__name__)
        return real_write(self, record)
    def verify_batches(*args, **kwargs):
        for day in real_generate(*args, **kwargs):
            count = written.count('DailyUsageRecord')
            yield day
            assert written.count('DailyUsageRecord') > count, 'runner buffered a day instead of writing it'
    monkeypatch.setattr(RecordStream, 'write', track_write)
    monkeypatch.setattr(runner, 'generate_days', verify_batches)
    runner.run_series(SeriesConfig.model_validate(prepared), tmp_path / 'streamed')


@pytest.mark.parametrize('failure_stage', ['report', 'manifest', 'stream', 'snapshot'])
def test_failure_cleans_only_owned_output(prepared, tmp_path, monkeypatch, failure_stage):
    out = tmp_path / 'failed'
    def fail(*args, **kwargs):
        raise ArtifactError('injected failure')
    if failure_stage == 'report':
        monkeypatch.setattr(runner, 'render_report', fail)
    elif failure_stage == 'manifest':
        monkeypatch.setattr(DatasetWriter, 'manifest', fail)
    elif failure_stage == 'snapshot':
        monkeypatch.setattr(DatasetWriter, 'snapshot', fail)
    else:
        monkeypatch.setattr(RecordStream, 'write', fail)
    with pytest.raises(ArtifactError) as caught:
        runner.run_series(SeriesConfig.model_validate(prepared), out)
    assert caught.value.cleanup_outcome == 'removed-created-output'
    assert not out.exists()
    assert (tmp_path / 'users/manifest.json').is_file()
    assert (tmp_path / 'roster/manifest.json').is_file()


def test_existing_output_and_dangling_symlink_are_not_modified(prepared, tmp_path):
    existing = tmp_path / 'existing'
    existing.mkdir()
    sentinel = existing / 'keep'
    sentinel.write_text('keep')
    with pytest.raises(ArtifactError):
        runner.run_series(SeriesConfig.model_validate(prepared), existing)
    assert sentinel.read_text() == 'keep'
    link = tmp_path / 'dangling'
    link.symlink_to(tmp_path / 'not-there')
    with pytest.raises(ArtifactError):
        runner.run_series(SeriesConfig.model_validate(prepared), link)
    assert link.is_symlink() and not link.exists()


def test_input_corruption_and_record_error_dont_leak_payload(prepared, tmp_path):
    path = tmp_path / 'users/users.jsonl'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(InputDataError):
        read_records(path, UserRecord)
    marker = 'DO_NOT_LOG_THIS_PERSON'
    raw = {'user_id': 'u', 'org_id': 'talium.ai', 'name': marker, 'email': {'private': marker}}
    content = json.dumps(raw).encode() + b'\n'
    path.write_bytes(content)
    manifest_path = path.parent / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files'][path.name] = {'sha256': hashlib.sha256(content).hexdigest(), 'rows': 1}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(InputDataError) as caught:
        read_records(path, UserRecord)
    assert marker not in str(caught.value)
    assert 'line 1' in str(caught.value) and 'email' in str(caught.value)


@pytest.mark.parametrize('manifest', [[], {'schema_version': True, 'status': 'complete'},
    {'schema_version': 2, 'status': 'complete'}, {'schema_version': 1, 'status': 'incomplete'},
    {'schema_version': 1, 'status': 'complete', 'files': []}])
def test_malformed_manifests_raise_input_errors(tmp_path, manifest):
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(InputDataError):
        read_records(tmp_path / 'users.jsonl', UserRecord)


def test_roster_legacy_manual_id_rejected_during_validation(prepared, tmp_path):
    raw = {'schema_version': 1, 'roster': {'seed': 1, 'users_file': str(tmp_path / 'users/users.jsonl'),
        'teams': [{'team_name': 'auto', 'count': 1}, {'team_name': 'manual', 'count': 1, 'team_id': 'legacy'}]}}
    cfg = tmp_path / 'collision.yaml'
    cfg.write_text(yaml.safe_dump(raw))
    proc = subprocess.run([str(WRAPPER), '--op', 'roster-create', '--config', str(cfg), '--validate-only'],
                          cwd=tmp_path, text=True, capture_output=True)
    assert proc.returncode == 2 and not proc.stdout
    assert 'team_id is no longer an operator input' in proc.stderr


def test_logging_has_consistent_run_ids_modules_and_no_payload(prepared, tmp_path):
    cfg = tmp_path / 'series.yaml'
    cfg.write_text(yaml.safe_dump(prepared))
    proc = subprocess.run([str(WRAPPER), '--op', 'series-create', '--config', str(cfg), '--output', str(tmp_path / 'cli')],
                          cwd=tmp_path, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    logs = [json.loads(line) for line in proc.stderr.splitlines()]
    assert {log['run_id'] for log in logs} == {summary['run_id']}
    assert {log['operation'] for log in logs} == {'series'}
    assert {'forecast.synthetic.profiles', 'forecast.synthetic.series', 'forecast.artifact_io'} <= {log['module'] for log in logs}
    assert any('duration_ms' in log for log in logs)
    for row in read_records(tmp_path / 'users/users.jsonl', UserRecord):
        assert row.email not in proc.stderr and row.name not in proc.stderr


def test_large_token_boundary_and_overflow_are_explicit(prepared, tmp_path):
    prepared['agents'] = {'claude': {'billing_mode': 'api', 'usage_share': 1, 'models': {'sonnet': 1}}}
    prepared['profiles']['mid']['daily_input_tokens'] = 2**63 - 1
    prepared['defaults']['cache_hit_ratio'] = 1
    cfg = SeriesConfig.model_validate(prepared)
    result = runner.run_series(cfg, tmp_path / 'large')
    first = json.loads((result.output_dir / 'usage.jsonl').read_text().splitlines()[0])
    assert first['tokens']['input'] == 0 and first['tokens']['cache_read'] == 2**63 - 1
    prepared['events']['spike_rules']=[{
        'id':'overflow','probability':1,'multiplier_min':2,'multiplier_max':2,
        'targets':{'teams':[],'agents':[],'models':[]}}]
    with pytest.raises(InputDataError, match='64-bit'):
        runner.run_series(SeriesConfig.model_validate(prepared), tmp_path / 'spike-overflow')
    assert not (tmp_path / 'spike-overflow').exists()
    cfg.profiles['mid'].daily_input_tokens = 2**63
    with pytest.raises(InputDataError, match='64-bit'):
        runner.run_series(cfg, tmp_path / 'overflow')
    assert not (tmp_path / 'overflow').exists()


def test_fake_email_collisions_empty_name_fallback_and_length(monkeypatch):
    from forecast.synthetic import users as user_module
    class Fake:
        def __init__(self, locale):
            self.i = 0
        def seed_instance(self, seed):
            pass
        def name(self):
            return 'Example Person'
        def user_name(self):
            self.i += 1
            return '!' if self.i < 3 else 'a' * 100
    monkeypatch.setattr(user_module, 'Faker', Fake)
    cfg = UsersConfig.model_validate({'schema_version': 1, 'users': {'count': 4, 'org_domain': 'example.test', 'locale': 'en_US', 'seed': 1}})
    emails = {row.email for row in create_users(cfg)}
    assert 'user@example.test' in emails and 'user_2@example.test' in emails
    assert 'a' * 64 + '@example.test' in emails
    assert 'a' * 62 + '_2@example.test' in emails


def test_projects_random_mix_coverage_and_profile_independence(prepared, tmp_path):
    prepared['series']['days'] = 30
    prepared['projects'].append({'name': 'Auth'})
    prepared['team_profiles']['team']['project_mix'] = {'Payments': .7, 'Auth': .3}
    prepared['agents']['unused'] = {'billing_mode': 'api', 'usage_share': 0, 'models': {'none': 1}}
    config = SeriesConfig.model_validate(prepared)
    users = read_records(config.series.users_file, UserRecord)
    teams = read_records(config.series.roster_file, TeamRecord)
    profiles = assign_profiles(config, teams)
    baseline = list(generate_days(config, users, teams, profiles))
    config.defaults.mix_variation_pct = 80
    config.defaults.cache_hit_variation_pp = 20
    varied = list(generate_days(config, users, teams, profiles))
    for first, second in zip(baseline, varied):
        assert first.coverage == second.coverage and len(first.coverage) == 9
        assert {row.user_id: row.project_id for row in first.usage} == {row.user_id: row.project_id for row in second.usage}
        assert sum(_processed(row) for row in second.usage) == 3_000_000
        assert not any(row.agent_type == 'unused' for row in second.usage)
    assert {row.project_id for day in baseline for row in day.usage} == {
        str(uuid.uuid5(uuid.NAMESPACE_URL, f'forecast:project:talium.ai:{name}'))
        for name in ('payments', 'auth')}


def test_hash_seed_does_not_change_streams():
    code = 'from forecast.synthetic.randomness import derive_seed; print(derive_seed(42,"activity","u","2026-07-01"))'
    outputs = []
    for seed in ['1', '899']:
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'econ-forecast/src'), 'PYTHONHASHSEED': seed}
        outputs.append(subprocess.check_output([sys.executable, '-c', code], env=env))
    assert outputs[0] == outputs[1]


def test_project_probability_sanity():
    from forecast.synthetic.series import _choice
    from forecast.synthetic.randomness import rng_for
    rng = rng_for(123, 'test-project-choice')
    selections = [_choice({'p1': .7, 'p2': .3, 'zero': 0}, rng) for _ in range(10000)]
    assert .27 < selections.count('p2') / len(selections) < .33
    assert 'zero' not in selections


def test_shared_record_rejects_numeric_or_timestamp_date():
    from forecast.contracts import CoverageRecord
    for value in (0, True, '2026-07-01T00:00:00'):
        with pytest.raises(ValidationError):
            CoverageRecord(date=value, org_id='o', user_id='u', agent_type='a', status='complete')


def test_cli_config_errors_are_actionable_without_echoing_input(tmp_path):
    config = tmp_path / 'bad.yaml'
    marker = 'PRIVATE_IDENTITY_STRING'
    config.write_text(yaml.safe_dump({'schema_version': 1, 'users': {'org_domain': marker + '@x', 'seed': 2}}))
    proc = subprocess.run([str(WRAPPER), '--op', 'user-create', '--config', str(config), '--validate-only'],
                          text=True, capture_output=True)
    assert proc.returncode == 2 and not proc.stdout
    assert marker not in proc.stderr and 'users.org_domain' in proc.stderr
    record = json.loads(proc.stderr)
    assert record['stage'] == 'validation' and record['cleanup_outcome'] == 'not-created'


@pytest.mark.parametrize('baseline', [1, 1_000_000])
@pytest.mark.parametrize('cache_ratio', [0, .8, 1])
@pytest.mark.parametrize('output_ratio', [0, .03, .99])
def test_generated_token_bounds_include_tiny_and_fully_cached_usage(
        prepared, baseline, cache_ratio, output_ratio):
    """Compare cache/output with total processed input, not its uncached component."""
    prepared['profiles']['mid']['daily_input_tokens'] = baseline
    prepared['defaults']['cache_hit_ratio'] = cache_ratio
    prepared['defaults']['output_ratio'] = output_ratio
    config = SeriesConfig.model_validate(prepared)
    users = read_records(config.series.users_file, UserRecord)
    teams = read_records(config.series.roster_file, TeamRecord)
    profiles = assign_profiles(config, teams)
    for day in generate_days(config, users, teams, profiles):
        per_user = {}
        for row in day.usage:
            tokens = row.tokens
            processed = _processed(row)
            assert processed > 0
            assert all(type(value) is int and value >= 0 for value in tokens.model_dump().values())
            assert tokens.cache_read <= processed
            assert tokens.output <= processed
            assert abs(tokens.output - output_ratio * processed) <= .5 + 1e-9
            assert abs(tokens.cache_read - cache_ratio * processed) <= .5 + 1e-9
            per_user[row.user_id] = per_user.get(row.user_id, 0) + processed
            if cache_ratio == 1:
                assert processed == tokens.cache_read and tokens.input == 0
        assert set(per_user.values()) == {baseline}


def test_manifest_version_belongs_to_the_dataset_directory(prepared, tmp_path):
    from forecast.contracts import DailyUsageRecord
    result = runner.run_series(SeriesConfig.model_validate(prepared), tmp_path / 'series')
    out = result.output_dir
    versions = {name: json.loads((tmp_path / name / 'manifest.json').read_text())['schema_version']
                for name in ('users', 'roster')}
    assert versions == {'users': 1, 'roster': 1}
    assert json.loads((out / 'manifest.json').read_text())['schema_version'] == 2
    # users/teams directories keep reading at version 1 ...
    assert read_records(tmp_path / 'users/users.jsonl', UserRecord)
    # ... and the series directory (usage and user_profiles alike) only at version 2.
    assert read_records(out / 'usage.jsonl', DailyUsageRecord, schema_version=2)
    with pytest.raises(InputDataError, match='unsupported or incomplete manifest'):
        read_records(out / 'usage.jsonl', DailyUsageRecord)
    from forecast.synthetic.models import UserProfileRecord
    assert read_records(out / 'user_profiles.jsonl', UserProfileRecord, schema_version=2)
    with pytest.raises(InputDataError):
        read_records(tmp_path / 'users/users.jsonl', UserRecord, schema_version=2)


def test_forecast_dataset_rejects_a_version_1_series_manifest(prepared, tmp_path):
    from forecast.forecasting.dataset import load_dataset
    from forecast.forecasting.errors import InputError
    result = runner.run_series(SeriesConfig.model_validate(prepared), tmp_path / 'series')
    manifest_path = result.output_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['schema_version'] = 1
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(InputError) as caught:
        load_dataset(result.output_dir)
    assert str(caught.value) == 'dataset uses schema 1; regenerate with this version'


def test_series_config_requires_schema_version_3(prepared):
    prepared['schema_version'] = 2
    with pytest.raises(ValidationError):
        SeriesConfig.model_validate(prepared)
    prepared['schema_version'] = 3
    for key in ('cache_write_ratio', 'cache_write_1h_share'):
        broken = copy.deepcopy(prepared)
        del broken['defaults'][key]
        with pytest.raises(ValidationError):
            SeriesConfig.model_validate(broken)
        broken['defaults'][key] = 1.5
        with pytest.raises(ValidationError):
            SeriesConfig.model_validate(broken)
