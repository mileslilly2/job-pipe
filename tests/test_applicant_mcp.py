"""Real stdio sessions and a separate synthetic local CLI confirmation process."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

import jsonschema
from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest

from jobpipe import applicant_profile as ap, db, services
from jobpipe.mcp_server import create_server
from test_applicant_profile import applicant_workspace, canonical_applicant, synthetic_job

APPLICANT_TOOLS = {"get_applicant_profile", "get_applicant_readiness", "get_application_readiness", "get_readiness_questions", "propose_applicant_facts", "get_application_answer"}


@asynccontextmanager
async def stdio(workspace, flags=()):
    args = ["-m", "jobpipe.mcp_server", "--workspace", str(workspace), *flags]
    params = StdioServerParameters(command=sys.executable, args=args, cwd="/tmp", env={"PYTHONDONTWRITEBYTECODE": "1"})
    async with Client(params, read_timeout_seconds=30) as client:
        yield client


async def call(client, name, arguments=None, error=None):
    result = await client.call_tool(name, arguments or {})
    payload = result.structured_content
    assert payload == json.loads(result.content[0].text)
    assert payload["ok"] == (error is None), payload
    assert result.is_error == (error is not None)
    if error:
        assert payload["error"]["code"] == error, payload
        return payload
    return payload["data"]


@pytest.mark.parametrize("flags", [{}, {"enable_local_writes": True}, {"enable_applicant_fact_proposals": True}])
def test_profile_permission_is_independent(canonical_applicant, flags):
    job = synthetic_job()
    async def run():
        async with Client(create_server(**flags)) as client:
            tools = (await client.list_tools()).tools
            assert APPLICANT_TOOLS <= {t.name for t in tools}
            await call(client, "search_jobs")
            for tool, args in [("get_applicant_profile", {}), ("get_applicant_readiness", {}),
                ("get_application_readiness", {"job_id": job}), ("get_readiness_questions", {}),
                ("get_application_answer", {"job_id": job, "question_type": "us_work_authorization"}),
                ("get_job_eligibility", {"job_id": job}), ("search_eligible_jobs", {})]:
                result = await call(client, tool, args, error="permission_denied")
                assert "synthetic@example.invalid" not in json.dumps(result)
            await call(client, "propose_applicant_facts", {"expected_revision": ap.state()["revision"], "facts": [{"key": "residence.country", "value": "US"}]}, error="permission_denied")
    asyncio.run(run())


def test_proposal_permission_and_confirmation_cannot_be_forged(canonical_applicant):
    async def run():
        async with Client(create_server(enable_applicant_profile_read=True)) as client:
            await call(client, "propose_applicant_facts", {"expected_revision": ap.state()["revision"], "facts": [{"key": "residence.country", "value": "US"}]}, error="permission_denied")
        async with Client(create_server(enable_applicant_profile_read=True, enable_applicant_fact_proposals=True)) as client:
            for extra in ({"confirmed": True}, {"verification_status": "verified"}, {"source_type": "applicant_attestation"}):
                await call(client, "propose_applicant_facts", {"expected_revision": ap.state()["revision"], "facts": [{"key": "residence.country", "value": "US", **extra}]}, error="invalid_arguments")
            await call(client, "confirm_applicant_facts", {}, error="invalid_arguments")
            await call(client, "get_applicant_profile", {"sections": ["skills"]})
            for args in ({"job_ids": [True]}, {"job_ids": [1, 1]}, {"limit": 21}):
                await call(client, "get_readiness_questions", args, error="invalid_arguments")
    asyncio.run(run())


def test_profile_errors_do_not_log_values(canonical_applicant, monkeypatch, caplog):
    def fail(**kwargs):
        raise ValueError("Synthetic private applicant detail")
    monkeypatch.setattr(services, "get_applicant_profile", fail)
    async def run():
        async with Client(create_server(enable_applicant_profile_read=True)) as client:
            response = await call(client, "get_applicant_profile", error="configuration_error")
            assert "Synthetic private" not in json.dumps(response)
    asyncio.run(run())
    assert "Synthetic private" not in caplog.text


def test_real_two_session_applicant_acceptance(canonical_applicant, monkeypatch):
    root = canonical_applicant
    eligible = synthetic_job(title="Junior Python Engineer")
    draft = synthetic_job(title="Junior Backend Engineer", location="Remote - NY, United States")
    scores = {i: services.job_score(db.get_job(i))[0] for i in (eligible, draft)}
    configuration = (root / "data/profiles/candidate.yaml").read_bytes()
    # Any accidental outbound request fails, including in independent child processes.
    guard = root / "network_guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text("import socket\ndef deny(*args, **kwargs):\n    raise AssertionError('External network forbidden in applicant acceptance')\nsocket.create_connection = deny\n")
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(guard), str(repo)]))
    flags = ("--enable-applicant-profile-read", "--enable-applicant-fact-proposals", "--enable-local-writes")
    transcript = {"transport": "stdio", "synthetic_only": True, "sessions": {}, "permission_checks": [], "no_external_submission": False}
    facts = [{"key": key, "value": value, "explanation": "Synthetic applicant acceptance fixture"} for key, value in [
        ("identity.name", "Synthetic Applicant"), ("contact.email", "synthetic@example.invalid"),
        ("residence.country", "US"), ("work_authorization.US.authorized", True),
        ("work_authorization.US.sponsorship_required", False), ("skills.python.name", "Python")]]

    async def session_a():
        async with stdio(root, flags) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 20 and APPLICANT_TOOLS <= {t.name for t in tools}
            for tool in tools:
                jsonschema.Draft202012Validator.check_schema(tool.input_schema)
                jsonschema.Draft202012Validator.check_schema(tool.output_schema)
            profile = await call(client, "get_applicant_profile", {"sections": ["identity", "contact", "residence", "work_authorization", "skills"]})
            before = await call(client, "get_application_readiness", {"job_id": eligible})
            assert before["eligibility_status"] == "needs_review"
            questions = await call(client, "get_readiness_questions", {"job_ids": [eligible, draft]})
            proposed = await call(client, "propose_applicant_facts", {"expected_revision": profile["revision"], "facts": facts})
            after = await call(client, "get_applicant_profile", {"sections": ["identity", "contact", "residence", "work_authorization", "skills"]})
            assert all(f["verification_status"] != "verified" for f in after["proposals"])
            unchanged = await call(client, "get_job_eligibility", {"job_id": eligible})
            assert unchanged["eligibility"]["status"] == "needs_review"
            await call(client, "propose_applicant_facts", {"expected_revision": after["revision"], "facts": [{"key": "residence.state", "value": "NY", "confirmed": True}]}, error="invalid_arguments")
            transcript["sessions"]["A"] = {"tools_invoked": ["tools/list", "get_applicant_profile", "get_application_readiness", "get_readiness_questions", "propose_applicant_facts", "get_applicant_profile", "get_job_eligibility", "propose_applicant_facts"], "initial_revision": profile["revision"], "proposed_revision": proposed["revision"], "facts_proposed": facts, "proposal_ids": proposed["proposal_ids"], "eligibility_before_and_after_proposals": [before["eligibility_status"], unchanged["eligibility"]["status"]], "questions_before": questions}
            transcript["permission_checks"].append("MCP confirmed=true rejected")
            return proposed

    proposed = asyncio.run(session_a())
    # This is a separate real CLI process, supplied synthetic applicant input.
    process = subprocess.run([sys.executable, "-m", "jobpipe.cli", "applicant", "review", "--confirm", ",".join(map(str, proposed["proposal_ids"])), "--reject", ""],
                             cwd=root, input="y\n", text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    transcript["confirmation"] = {"interface": "separate local CLI process", "input": "synthetic test responses; not real human review", "confirmed_proposal_ids": proposed["proposal_ids"], "profile_revision": ap.state()["revision"]}
    assert all(ap.verified_values()[f["key"]]["value"] == f["value"] for f in facts)

    async def session_b():
        async with stdio(root, flags) as client:
            profile = await call(client, "get_applicant_profile", {"sections": ["identity", "contact", "residence", "work_authorization", "skills"]})
            assert not profile["proposals"] and profile["revision"] > proposed["revision"]
            assessment = await call(client, "get_job_eligibility", {"job_id": eligible})
            assert assessment["eligibility"]["status"] == "eligible"
            questions = await call(client, "get_readiness_questions", {"job_ids": [eligible, draft]})
            assert [q["question_id"] for q in questions["questions"]] == ["residence.state"]
            await call(client, "get_applicant_readiness", {"job_ids": [eligible, draft]})
            answer = await call(client, "get_application_answer", {"job_id": eligible, "question_type": "us_work_authorization"})
            assert answer["answer"] is True and answer["status"] == "answerable"
            await call(client, "save_job", {"job_id": eligible})
            package = await call(client, "prepare_application", {"job_id": eligible})
            blocked = await call(client, "prepare_application", {"job_id": draft, "mode": "draft_preparation"})
            assert package["readiness"]["submission_readiness"] == "ready_for_human_review"
            assert blocked["readiness"]["submission_readiness"] == "blocked"
            assert "DRAFT" in Path(blocked["resume_path"]).read_text()
            assert not (await call(client, "list_applications", {"status": "applied"}))["items"]
            transcript["sessions"]["B"] = {"tools_invoked": ["get_applicant_profile", "get_job_eligibility", "get_readiness_questions", "get_applicant_readiness", "get_application_answer", "save_job", "prepare_application", "prepare_application", "list_applications"], "confirmed_revision": profile["revision"], "eligibility": assessment["eligibility"]["status"], "questions_after": questions, "package_outcomes": {"eligible": package["readiness"], "draft": blocked["readiness"]}}
        async with stdio(root, ("--enable-local-writes",)) as restricted:
            await call(restricted, "get_applicant_profile", error="permission_denied")
            await call(restricted, "propose_applicant_facts", {"expected_revision": 0, "facts": facts}, error="permission_denied")
            await call(restricted, "get_job_eligibility", {"job_id": eligible}, error="permission_denied")
            transcript["permission_checks"].extend(["local writes do not grant profile reads", "local writes do not grant proposals", "job-only clients cannot read applicant eligibility evidence"])

    asyncio.run(session_b())
    assert configuration == (root / "data/profiles/candidate.yaml").read_bytes()
    assert {i: services.job_score(db.get_job(i))[0] for i in (eligible, draft)} == scores
    assert all(db.get_application(i)["applied_at"] is None for i in (eligible, draft))
    transcript["no_external_submission"] = True
    transcript["network_guard_enabled"] = True
    transcript["passed"] = True
    before_keys = {q["question_id"] for q in transcript["sessions"]["A"]["questions_before"]["questions"]}
    after_keys = {q["question_id"] for q in transcript["sessions"]["B"]["questions_after"]["questions"]}
    transcript["questions_eliminated"] = sorted(before_keys - after_keys)
    output = os.environ.get("JOBPIPE_APPLICANT_VALIDATION_REPORT")
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "stdio-two-session.json").write_text(json.dumps(transcript, indent=2) + "\n")
