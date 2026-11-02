"""Real stdio discovery acceptance with transport mocks inherited by workers."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest
import yaml

from jobpipe import db, services
from jobpipe import discovery_service as discovery
from jobpipe.mcp_server import create_server
from jobpipe.models import DiscoveredJob, JobSource, SearchProfile
from jobpipe.ingest import evaluate_discovered_job


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def discovery_workspace(initialized_workspace, monkeypatch):
    root = initialized_workspace
    (root / "data/job_sources.yaml").write_text(yaml.safe_dump([
        {"provider": "greenhouse", "company": "Acme", "board_token": "fixture"},
        {"provider": "greenhouse", "company": "Broken", "board_token": "broken"},
        {"provider": "greenhouse", "company": "Disabled", "board_token": "disabled", "enabled": False},
    ]))
    (root / "data/search_profiles.yaml").write_text(yaml.safe_dump([{
        "name": "miles", "active_goal": "get_employed_fast", "preferred": ["backend_api"],
        "acceptable": ["technical_support", "help_desk"], "positive_keywords": ["python", "api"],
        "preferred_locations": ["remote us"], "preferred_work_styles": ["remote", "hybrid"],
        "excluded_levels": ["senior", "manager"],
    }]))
    candidate_path = root / "data/profiles/candidate.yaml"
    candidate = yaml.safe_load(candidate_path.read_text())
    candidate["eligibility"] = {
        "residence_country": {"value": "US", "verified": True, "evidence": "Synthetic US residence"},
        "work_authorizations": [{"country": "US", "authorized": True, "sponsorship_required": False,
                                 "verified": True, "evidence": "Synthetic authorization"}]}
    candidate_path.write_text(yaml.safe_dump(candidate))
    # A subprocess-only test import patches the HTTP transport, not Job-Pipe's
    # services/adapters. There is no production endpoint or fixture override flag.
    mock_path = root / "transport_mock"
    mock_path.mkdir()
    (mock_path / "sitecustomize.py").write_text('''
import socket
import time
import jobpipe.providers.greenhouse as gh
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected network request in offline stdio acceptance")
socket.socket.connect = forbidden
def fetch(url):
    if "/broken/" in url:
        raise ValueError("Synthetic upstream failure")
    if "/disabled/" in url:
        raise AssertionError("Disabled source was fetched")
    time.sleep(0.15)
    rows = []
    for i in range(8):
        location = "Remote - US" if i < 5 else "Paris, France" if i == 5 else "Remote" if i == 6 else "Madrid, Spain"
        title = "Backend Engineer Intern" if i == 7 else "Junior Backend Engineer"
        body = "Python SQL API automation testing. No professional employment experience required."
        if i == 7:
            body += " Must be currently enrolled at a Spanish university and graduate in 2027."
        rows.append({"id": 100+i, "title": title, "location": {"name": location},
                     "content": "<p>"+body+"</p>", "absolute_url": "https://example.invalid/jobs/"+str(i)})
    rows.append({"id": 999, "title": "Sales Representative", "location": {"name": "US"}, "content": "Sales"})
    return {"jobs": rows}
gh._fetch_json = fetch
''')
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(mock_path), str(repo)]))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    yield root
    discovery.stop_workers()


@asynccontextmanager
async def stdio(root, *flags):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "jobpipe.mcp_server", "--workspace", str(root), "--enable-applicant-profile-read", *flags], cwd="/tmp",
        env={"PYTHONPATH": os.environ["PYTHONPATH"], "PYTHONDONTWRITEBYTECODE": "1"})
    async with Client(params, read_timeout_seconds=40) as client:
        yield client


async def call(client, tool, args=None, error=None):
    response = await client.call_tool(tool, args or {})
    value = response.structured_content
    assert value == json.loads(response.content[0].text)
    assert response.is_error == bool(error), value
    if error:
        assert value["error"]["code"] == error
        return value
    assert value["ok"], value
    return value["data"]


async def completed(client, run_id):
    for _ in range(300):
        result = await call(client, "get_discovery_status", {"run_id": run_id})
        if result["run"]["status"] != "running":
            return result
        await asyncio.sleep(0.02)
    raise AssertionError("Bounded fixture run did not finish")


def test_stdio_discovery_to_five_prepared_applications(discovery_workspace):
    root = discovery_workspace
    configuration = snapshot(root / "data")
    async def run():
        async with stdio(root, "--enable-ingest", "--enable-source-trials", "--enable-local-writes") as client:
            tools = await client.list_tools()
            assert len(tools.tools) == 20
            annotations = {t.name: t.annotations for t in tools.tools}
            assert annotations["trial_source"].open_world_hint
            assert not annotations["ingest_sources"].idempotent_hint
            await call(client, "get_active_goal")
            before = await call(client, "get_discovery_status")
            assert before["fetched"] is None and before["stale_source_warnings"]
            initial = snapshot(root)
            trial = await call(client, "trial_source", {"source_id": "greenhouse:fixture"})
            assert (trial["fetched"], trial["passed"], trial["rejected"]) == (9, 8, 1)
            assert trial["eligibility_counts"] == {"eligible": 5, "needs_review": 1, "ineligible": 2}
            assert trial["rejection_reasons"] and len(trial["representative_jobs"]) == 5
            assert snapshot(root) == initial
            started = await call(client, "ingest_sources")
            await call(client, "ingest_sources", error="operation_in_progress")
            outcome = await completed(client, started["run_id"])
            assert outcome["run"]["status"] == "completed"
            assert (outcome["fetched"], outcome["passed"], outcome["inserted"], outcome["failed_sources"]) == (9, 8, 8, 1)
            assert outcome["yield_rate"] == 8/9
            jobs = await call(client, "search_eligible_jobs", {"remote_preference": "remote_us"})
            assert len(jobs["items"]) == 5
            assert jobs == await call(client, "search_eligible_jobs", {"remote_preference": "remote_us"})
            prepared_ids = []
            for job in jobs["items"]:
                job_id = job["job_id"]
                eligibility = await call(client, "get_job_eligibility", {"job_id": job_id})
                fit = await call(client, "get_fit_breakdown", {"job_id": job_id})
                assert eligibility["eligibility"]["status"] == "eligible"
                assert eligibility["technical_fit_score"] == job["fit_score"] == fit["displayed_score"]
                await call(client, "save_job", {"job_id": job_id})
                assert not (await call(client, "save_job", {"job_id": job_id}))["changed"]
                package = await call(client, "prepare_application", {"job_id": job_id})
                resume = Path(package["resume_path"])
                resume.write_text(resume.read_text() + "\nManual edit preserved.\n")
                assert (await call(client, "prepare_application", {"job_id": job_id}))["reused"]
                assert "Manual edit preserved" in resume.read_text()
                detail = await call(client, "get_job", {"job_id": job_id})
                assert detail["application_status"] == "saved"
                assert detail["application"]["applied_at"] is None
                assert detail["workflow_bucket"] == "packaged"
                prepared_ids.append(job_id)
            for status in ("ineligible", "needs_review"):
                other = await call(client, "search_eligible_jobs", {"eligibility_status": status})
                assert other["items"]
                job_id = other["items"][0]["job_id"]
                await call(client, "save_job", {"job_id": job_id})
                prior = snapshot(root / "applications")
                await call(client, "prepare_application", {"job_id": job_id}, error="eligibility_unresolved")
                assert snapshot(root / "applications") == prior
            # Updating sources must preserve saved/package/application state.
            await asyncio.sleep(0.05)
            again = await call(client, "ingest_sources", {"source_ids": ["greenhouse:fixture"]})
            outcome = await completed(client, again["run_id"])
            assert (outcome["inserted"], outcome["updated"]) == (0, 8)
            assert not (await call(client, "list_applications", {"status": "applied"}))["items"]
            assert len((await call(client, "list_applications"))["items"]) == 8
            assert len(db.fetch_all("SELECT * FROM feedback WHERE action='packaged'")) == 5
            assert len(db.fetch_all("SELECT * FROM feedback WHERE action='saved'")) == 7
            # A changed applicant fact must also gate reuse of an existing bundle.
            path = root / "data/profiles/candidate.yaml"
            candidate = yaml.safe_load(path.read_text())
            candidate["eligibility"]["residence_country"]["verified"] = False
            path.write_text(yaml.safe_dump(candidate))
            prior = snapshot(root / "applications")
            await call(client, "prepare_application", {"job_id": prepared_ids[0]}, error="eligibility_unresolved")
            assert snapshot(root / "applications") == prior
            candidate["eligibility"]["residence_country"]["verified"] = True
            path.write_text(yaml.safe_dump(candidate))
    asyncio.run(run())
    after = snapshot(root / "data")
    assert {k: v for k, v in after.items() if k != ".discovery.lock"} == configuration


@pytest.mark.parametrize("flags", [(), ("--enable-local-writes",), ("--enable-source-trials",), ("--enable-ingest",)])
def test_stdio_capability_isolation(discovery_workspace, flags):
    root = discovery_workspace
    before = snapshot(root)
    async def run():
        async with stdio(root, *flags) as client:
            if "--enable-source-trials" in flags:
                await call(client, "trial_source", {"source_id": "greenhouse:fixture"})
            else:
                await call(client, "trial_source", {"source_id": "greenhouse:fixture"}, error="permission_denied")
            if "--enable-ingest" not in flags:
                await call(client, "ingest_sources", error="permission_denied")
            else:
                await call(client, "save_job", {"job_id": 1}, error="permission_denied")
            await call(client, "get_discovery_status")
    asyncio.run(run())
    assert snapshot(root) == before


def test_ingest_timeout_and_workspace_lock(discovery_workspace, monkeypatch):
    monkeypatch.setattr(discovery, "RUN_TIMEOUT", 1)
    mock = discovery_workspace / "transport_mock/sitecustomize.py"
    mock.write_text(mock.read_text().replace("time.sleep(0.15)", "time.sleep(5)"))
    async def run():
        async with Client(create_server(enable_ingest=True)) as client:
            started = await call(client, "ingest_sources", {"source_ids": ["greenhouse:fixture"]})
            await call(client, "ingest_sources", error="operation_in_progress")
            outcome = await completed(client, started["run_id"])
            assert outcome["run"]["status"] == "aborted"
            assert outcome["inserted"] == 0 and not db.list_jobs()
            assert all(s["fetch_status"] == "failed" for s in outcome["source_results"])
    asyncio.run(run())
    discovery.stop_workers()
    fd = discovery.acquire_ingest_lock()
    try:
        with pytest.raises(services.ServiceError, match="already running"):
            discovery.ingest_sync(discovery.selected_sources(), services.selected_profile())
    finally:
        os.close(fd)


def test_trial_timeout_is_nonpersistent(discovery_workspace, monkeypatch):
    monkeypatch.setattr(discovery, "TRIAL_TIMEOUT", 0.05)
    before = snapshot(discovery_workspace)
    async def run():
        async with Client(create_server(enable_source_trials=True, enable_applicant_profile_read=True)) as client:
            await call(client, "trial_source", {"source_id": "greenhouse:fixture"}, error="operation_timeout")
    asyncio.run(run())
    assert snapshot(discovery_workspace) == before


@pytest.mark.parametrize("tool,args,code", [
    ("trial_source", {"source_id": "https://example.com"}, "unknown_source"),
    ("trial_source", {"source_id": "unknown"}, "unknown_source"),
    ("ingest_sources", {"source_ids": ["greenhouse:disabled"]}, "unknown_source"),
    ("ingest_sources", {"source_ids": []}, "invalid_arguments"),
    ("ingest_sources", {"source_ids": ["a"] * 21}, "invalid_arguments"),
    ("get_discovery_status", {"run_id": 99999}, "unknown_run"),
    ("get_job_eligibility", {"job_id": 1, "applicant_profile": "../candidate"}, "invalid_arguments"),
    ("search_eligible_jobs", {"eligibility_status": "probably"}, "invalid_arguments"),
])
def test_discovery_argument_boundaries(discovery_workspace, tool, args, code):
    async def run():
        async with Client(create_server(enable_source_trials=True, enable_ingest=True, enable_applicant_profile_read=True)) as client:
            await call(client, tool, args, error=code)
    asyncio.run(run())


def test_explicit_goal_support_roles_override_only_builtin_exclusions():
    profile = SearchProfile(name="support", preferred=["technical_support", "help_desk"], positive_keywords=["api"])
    source = JobSource(provider="greenhouse", board_token="fixture")
    job = DiscoveredJob(provider="greenhouse", external_id="1", company="Fixture", title="Technical Support Engineer", description="API troubleshooting")
    assert evaluate_discovered_job(job, source, profile).accepted
    source.exclude_keywords = ["technical support"]
    assert not evaluate_discovered_job(job, source, profile).accepted


def test_lever_production_payload_and_html_sections(monkeypatch):
    from jobpipe.providers import lever, greenhouse
    monkeypatch.setattr(lever, "_fetch_with_fallback", lambda _: [{"id": "1", "text": "Junior Backend Engineer",
        "descriptionPlain": "Python API automation.", "lists": [{"text": "Preferred qualifications", "content": "<li>5 years of experience</li>"}],
        "additionalPlain": "No sponsorship available.", "categories": {"location": "United States", "commitment": "Full-time"},
        "workplaceType": "remote", "hostedUrl": "https://example.invalid/job", "applyUrl": "https://example.invalid/apply"}])
    job = lever.fetch_lever_jobs(JobSource(provider="lever", account="fixture"))[0]
    assert job.remote_type == "remote" and job.employment_type == "Full-time"
    assert job.apply_url.endswith("/apply") and job.source_url.endswith("/job")
    from jobpipe.facts import extract_experience_min_years
    assert extract_experience_min_years(job.description) is None
    assert greenhouse._clean_text("<h2>Preferred qualifications</h2><li>5 years experience</li>") == "Preferred qualifications\n5 years experience"
