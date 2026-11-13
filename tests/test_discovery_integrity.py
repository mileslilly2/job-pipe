"""Transaction, evidence-boundary and real stdio discovery acceptance regressions."""
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest
import yaml

from jobpipe import db, discovery_service as discovery, services
from jobpipe.catalog import load_search_profile
from jobpipe.eligibility import ApplicantFacts, assess_eligibility
from jobpipe.mcp_server import CapabilityPolicy
from jobpipe.models import DiscoveredJob, JobSource, SearchProfile
from jobpipe.source_metrics import build_source_metrics
from jobpipe.utils import package_dir


def snapshot(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


def test_job_upsert_and_classification_share_caller_transaction(initialized_workspace):
    with closing(db.connect(mode='rw')) as conn:
        conn.execute('BEGIN IMMEDIATE')
        job_id = db.insert_job({'title': 'Junior Python Engineer', 'external_id': 'rollback'}, connection=conn)
        assert conn.execute('SELECT 1 FROM job_classifications WHERE job_id=?', (job_id,)).fetchone()
        conn.rollback()
    assert not db.list_jobs()
    assert not db.fetch_all('SELECT * FROM applications')
    assert not db.fetch_all('SELECT * FROM job_classifications')


def test_failed_source_transaction_retains_prior_source_only(initialized_workspace, monkeypatch):
    sources = [JobSource(provider='greenhouse', company=slug, board_token=slug) for slug in ('first', 'second')]
    import jobpipe.providers as providers
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, 'greenhouse', lambda source: [DiscoveredJob(
        provider='greenhouse', external_id=source.company, company=source.company,
        title='Junior Data Engineer', location='Remote US', remote=True,
        description='Python SQL ETL backend API automation.', apply_url='https://example.invalid/job')])
    real = discovery.record_source_run
    def fail(run_id, report, **kwargs):
        if report.source.company == 'second' and kwargs.get('connection') is not None:
            raise RuntimeError('measurement commit failure')
        return real(run_id, report, **kwargs)
    monkeypatch.setattr(discovery, 'record_source_run', fail)
    with pytest.raises(RuntimeError, match='measurement commit'):
        discovery.ingest_sync(sources, load_search_profile())
    assert [r['company'] for r in db.list_jobs()] == ['first']
    report = build_source_metrics(sources)
    assert report.runs[0]['status'] == 'aborted'
    assert sum(r['inserted'] for r in report.stats) == 1
    assert {r['source_id']: r['fetch_status'] for r in report.stats} == {
        'greenhouse:first': 'success', 'greenhouse:second': 'failed'}


def test_workspace_lock_excludes_a_second_process(initialized_workspace):
    fd = discovery.acquire_ingest_lock()
    try:
        result = subprocess.run([sys.executable, '-c',
            "from jobpipe.discovery_service import acquire_ingest_lock; acquire_ingest_lock()"],
            capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and 'already running' in result.stderr
    finally:
        os.close(fd)
    os.close(discovery.acquire_ingest_lock())


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('capability,flag', [('save', 'enable_local_writes'), ('prepare', 'enable_local_writes'),
    ('trial_sources', 'enable_source_trials'), ('ingest_sources', 'enable_ingest')])
def test_capabilities_are_independent(capability, flag, enabled):
    policy = CapabilityPolicy(**{flag: enabled})
    for other in ('save', 'prepare', 'trial_sources', 'ingest_sources'):
        authorized = enabled and (other == capability or {other, capability} == {'save', 'prepare'})
        if authorized:
            policy.require(other)
        else:
            with pytest.raises(services.ServiceError) as exc:
                policy.require(other)
            assert exc.value.code == 'permission_denied'


def test_absent_applicant_profile_does_not_break_nonpersistent_trial(workspace, monkeypatch):
    (workspace / 'data').mkdir()
    (workspace / 'data/job_sources.yaml').write_text('- provider: greenhouse\n  company: Fixture\n  board_token: fixture\n')
    import jobpipe.providers as providers
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, 'greenhouse', lambda _: [DiscoveredJob(
        provider='greenhouse', external_id='trial', company='Fixture', title='Junior Data Engineer',
        location='Remote US', description='Python SQL ETL backend API.', apply_url='https://example.invalid')])
    before = snapshot(workspace)
    result = discovery.trial_source_local('greenhouse:fixture')
    assert result['fetched'] == 1 and result['passed'] == 1
    assert result['eligibility_counts'] == {'eligible': 0, 'needs_review': 1, 'ineligible': 0}
    assert snapshot(workspace) == before


def test_unknown_urls_and_disabled_ingest_rejected_without_writes(initialized_workspace):
    path = Path('data/job_sources.yaml')
    path.write_text('- provider: greenhouse\n  company: Fixture\n  board_token: fixture\n  enabled: false\n')
    before = snapshot(Path.cwd())
    for fn, value in ((discovery.resolve_trial, 'https://localhost/secret'),
                      (discovery.selected_sources, ['greenhouse:fixture'])):
        with pytest.raises(services.ServiceError):
            fn(value)
    assert snapshot(Path.cwd()) == before


@pytest.mark.parametrize('text,expected', [('2–12+ years professional experience', 'ineligible'),
    ('2 years professional experience or equivalent training', 'needs_review'),
    ('Maximum 2 years of experience', 'eligible'),
    ('Preferred qualifications:\n5 years of experience\nRequirements:\nPython', 'eligible')])
def test_experience_bounds_do_not_invent_equivalences(text, expected):
    fact = lambda value: {'value': value, 'verified': True, 'evidence': 'Synthetic fact'}
    applicant = ApplicantFacts.model_validate({'residence_country': fact('US'),
        'professional_experience_years': fact(1.0),
        'work_authorizations': [{'country': 'US', 'authorized': True, 'verified': True,
                                 'sponsorship_required': False, 'evidence': 'Synthetic authorization'}]})
    result = assess_eligibility({'title': 'Junior Developer', 'location': 'Remote US', 'description': text},
                               applicant, SearchProfile(name='test'))
    assert result.status == expected


BOOTSTRAP = r"""
import io, json, os, time, urllib.request
class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
def mocked_open(request, *args, **kwargs):
    url = getattr(request, 'full_url', str(request))
    if 'boards-api.greenhouse.io/v1/boards/fixture/jobs' not in url:
        raise OSError('Fixture forbids live network: ' + url)
    time.sleep(float(os.environ.get('JOBPIPE_FIXTURE_DELAY', '0')))
    jobs = []
    for index in range(8):
        jobs.append({'id': index + 100, 'title': 'Junior Data Engineer',
            'location': {'name': 'Paris, France' if index == 5 else 'Remote' if index == 6 else 'Remote - United States'},
            'content': '<h3>Requirements</h3><p>Python SQL ETL backend API automation testing.</p>' +
                ('<p>Minimum 6 years of professional experience.</p>' if index == 7 else '<p>No experience required.</p>'),
            'absolute_url': 'https://example.invalid/fixture/' + str(index), 'updated_at': '2026-10-29T00:00:00Z'})
    return Response(json.dumps({'jobs': jobs}).encode())
urllib.request.urlopen = mocked_open
"""


@pytest.fixture()
def protocol_workspace(workspace):
    for folder in ('data', 'role_packs', 'templates'):
        shutil.copytree(package_dir() / folder, workspace / folder)
    shutil.copyfile(package_dir() / 'profile.md', workspace / 'profile.md')
    path = workspace / 'data/profiles/candidate.yaml'
    profile = yaml.safe_load(path.read_text())
    profile['eligibility'] = {
        'residence_country': {'value': 'US', 'verified': True, 'evidence': 'Synthetic fixture residence'},
        'professional_experience_years': {'value': 0.0, 'verified': True, 'evidence': 'Synthetic fixture employment history'},
        'work_authorizations': [{'country': 'US', 'authorized': True, 'sponsorship_required': False,
                                 'verified': True, 'evidence': 'Synthetic fixture authorization'}]}
    path.write_text(yaml.safe_dump(profile))
    (workspace / 'data/job_sources.yaml').write_text('- provider: greenhouse\n  company: Fixture\n  board_token: fixture\n  target_role_families: [data_engineer]\n')
    bootstrap = workspace.parent / (workspace.name + '-bootstrap')
    bootstrap.mkdir()
    (bootstrap / 'sitecustomize.py').write_text(BOOTSTRAP)
    db.init_database()
    return workspace, bootstrap


async def call(client, name, args=None, error=None):
    result = await client.call_tool(name, args or {})
    payload = result.structured_content
    assert payload['ok'] == (error is None), payload
    if error:
        assert payload['error']['code'] == error, payload
        return payload
    return payload['data']


def parameters(workspace, bootstrap, flags=(), delay='0'):
    return StdioServerParameters(command=sys.executable,
        args=['-m', 'jobpipe.mcp_server', '--workspace', str(workspace), *flags], cwd='/tmp',
        env={'PYTHONPATH': str(bootstrap), 'PYTHONDONTWRITEBYTECODE': '1', 'JOBPIPE_FIXTURE_DELAY': delay})


def test_discovery_to_five_packages_via_real_stdio(protocol_workspace):
    workspace, bootstrap = protocol_workspace
    transcript = []
    async def run():
        async with Client(parameters(workspace, bootstrap,
                ['--enable-ingest', '--enable-source-trials', '--enable-local-writes', '--enable-applicant-profile-read']), read_timeout_seconds=20) as client:
            assert len((await client.list_tools()).tools) == 20
            transcript.append(await call(client, 'get_active_goal'))
            transcript.append(await call(client, 'get_discovery_status'))
            before = snapshot(workspace)
            trial = await call(client, 'trial_source', {'source_id': 'greenhouse:fixture', 'diagnostics': True})
            assert trial['fetched'] == 8
            assert trial['eligibility_counts'] == {'eligible': 5, 'needs_review': 1, 'ineligible': 2}
            assert snapshot(workspace) == before
            started = await call(client, 'ingest_sources', {'source_ids': ['greenhouse:fixture']})
            for _ in range(100):
                status = await call(client, 'get_discovery_status', {'run_id': started['run_id']})
                if status['run']['status'] != 'running': break
                await asyncio.sleep(.05)
            assert status['run']['status'] == 'completed', status
            assert status['fetched'] == 8 and status['inserted'] == 8
            transcript.append(status)
            eligible = await call(client, 'search_eligible_jobs', {'remote_preference': 'remote_us'})
            assert eligible['total'] == 5
            all_jobs = await call(client, 'search_eligible_jobs', {'eligibility_status': 'all'})
            assert all_jobs['total'] == 8
            assert [item['eligibility']['status'] for item in all_jobs['items']] == ['eligible'] * 5 + ['needs_review'] + ['ineligible'] * 2
            for item in all_jobs['items']:
                job_id = item['job_id']
                assessment = await call(client, 'get_job_eligibility', {'job_id': job_id})
                fit = await call(client, 'get_fit_breakdown', {'job_id': job_id})
                assert assessment['technical_fit_score'] == item['fit_score'] == fit['displayed_score']
                await call(client, 'save_job', {'job_id': job_id})
                if assessment['eligibility']['status'] != 'eligible':
                    await call(client, 'prepare_application', {'job_id': job_id}, error='eligibility_unresolved')
                    continue
                package = await call(client, 'prepare_application', {'job_id': job_id})
                resume = Path(package['resume_path'])
                resume.write_text(resume.read_text() + '\nManual fixture edit\n')
                assert (await call(client, 'prepare_application', {'job_id': job_id}))['reused']
                assert 'Manual fixture edit' in resume.read_text()
                detail = await call(client, 'get_job', {'job_id': job_id})
                assert detail['application_status'] == 'saved'
                assert detail['application']['applied_at'] is None and detail['workflow_bucket'] == 'packaged'
                transcript.append({'job_id': job_id, 'package': package})
            repeated = await call(client, 'ingest_sources')
            for _ in range(100):
                status = await call(client, 'get_discovery_status', {'run_id': repeated['run_id']})
                if status['run']['status'] != 'running': break
                await asyncio.sleep(.05)
            assert status['inserted'] == 0 and status['updated'] == 8
            assert len((await call(client, 'list_applications'))['items']) == 8
            assert all(r['applied_at'] is None for r in (await call(client, 'list_applications'))['items'])
    asyncio.run(run())
    assert len(list((workspace / 'applications').glob('*/resume-tailored.md'))) == 5
    report_path = os.environ.get('JOBPIPE_V030_ACCEPTANCE_REPORT')
    if report_path:
        Path(report_path).write_text(json.dumps({'transport': 'stdio', 'prepared': 5,
            'external_submissions': 0, 'steps': transcript}, indent=2))


def test_stdio_readonly_denies_discovery_and_writes_without_changes(protocol_workspace):
    workspace, bootstrap = protocol_workspace
    before = snapshot(workspace)
    async def run():
        async with Client(parameters(workspace, bootstrap)) as client:
            for name, args in [('ingest_sources', {}), ('trial_source', {'source_id': 'greenhouse:fixture'}),
                               ('save_job', {'job_id': 1}), ('prepare_application', {'job_id': 1})]:
                await call(client, name, args, error='permission_denied')
    asyncio.run(run())
    assert snapshot(workspace) == before


def test_bounded_worker_timeout_releases_lock_and_aborts_run(protocol_workspace, monkeypatch):
    workspace, bootstrap = protocol_workspace
    monkeypatch.setenv('PYTHONPATH', str(bootstrap))
    monkeypatch.setenv('JOBPIPE_FIXTURE_DELAY', '5')
    monkeypatch.setattr(discovery, 'RUN_TIMEOUT', 1)
    started = discovery.ingest_sources()
    with pytest.raises(services.ServiceError) as exc:
        discovery.ingest_sources()
    assert exc.value.code == 'operation_in_progress'
    discovery.stop_workers()
    status = discovery.get_discovery_status(started['run_id'])
    assert status['run']['status'] == 'aborted'
    assert not db.list_jobs()
    os.close(discovery.acquire_ingest_lock())


@pytest.mark.parametrize("facts", [
    {"professional_experience_years": {"value": -1.0, "verified": True, "evidence": "Invalid negative"}},
    {"work_authorizations": [{"country": "US", "evidence": "A"}, {"country": "US", "evidence": "B"}]},
])
def test_conflicting_or_invalid_applicant_facts_fail_closed(facts):
    with pytest.raises(ValueError):
        ApplicantFacts.model_validate(facts)


@pytest.mark.parametrize("location,description,expected", [
    ("Paris, France", "This role is remote; work with us on Python APIs.", "ineligible"),
    ("Remote", "This role is remote and you will join us to build Python APIs.", "needs_review"),
    ("United States", "Build Python APIs.", "needs_review"),
    ("US", "This role is remote in Canada.", "needs_review"),
])
def test_remote_prose_and_unspecified_attendance_do_not_establish_eligibility(location, description, expected):
    applicant = ApplicantFacts.model_validate({
        "residence_country": {"value": "US", "verified": True, "evidence": "Synthetic US residence"},
        "work_authorizations": [{"country": "US", "authorized": True, "sponsorship_required": False,
                                 "verified": True, "evidence": "Synthetic US authorization"}]})
    profile = SearchProfile(name="test", preferred_locations=["United States", "Canada"],
                            preferred_work_styles=["remote"])
    result = assess_eligibility({"title": "Junior Developer", "location": location, "description": description}, applicant, profile)
    assert result.status == expected


def test_future_talent_pool_is_not_a_ready_current_opportunity():
    applicant = ApplicantFacts.model_validate({
        "residence_country": {"value": "US", "verified": True, "evidence": "Synthetic residence"},
        "work_authorizations": [{"country": "US", "authorized": True, "verified": True, "evidence": "Synthetic authorization"}]})
    result = assess_eligibility({"title": "QA Tester (Future Roles)", "location": "Remote US",
        "description": "Python API testing. No experience required."}, applicant, SearchProfile(name="test"))
    assert result.status == "needs_review"
    assert result.checks["posting_availability"].status == "needs_review"
    assert result.missing_employer_information
