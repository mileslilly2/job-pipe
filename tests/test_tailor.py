from pathlib import Path

from jobpipe.application_package import load_base_profile
from jobpipe.classify import classify_job
from jobpipe.job_requirements import build_resume_tailoring, extract_job_requirements
from jobpipe.models import CandidateProfile, ParsedJob, RolePack
from jobpipe.parse import parse_job_text
from jobpipe.tailor import tailor_job
from jobpipe.utils import load_yaml


def _candidate() -> CandidateProfile:
    return CandidateProfile.model_validate(load_yaml(Path("jobpipe/data/profiles/candidate.yaml")))


def _packs() -> dict[str, RolePack]:
    base = Path("jobpipe/role_packs")
    return {path.stem: RolePack.model_validate(load_yaml(path)) for path in base.glob("*.yaml")}


def test_tailoring_avoids_forbidden_claims() -> None:
    packs = _packs()
    job = parse_job_text(Path("tests/fixtures/helpdesk_clean.txt").read_text(encoding="utf-8"))
    role_type, _matched, _missing = classify_job(job, packs)
    tailored = tailor_job(job, role_type, packs[role_type], _candidate())
    text = "\n".join([bullet.text for bullet in tailored.tailored_bullets])
    forbidden = [
        "active directory admin experience",
        "servicenow experience",
        "corporate help desk experience",
        "security clearance",
    ]
    for item in forbidden:
        assert item not in text.lower()
    assert all(bullet.provenance for bullet in tailored.tailored_bullets)


def test_requirement_parser_and_resume_tailoring_are_deterministic() -> None:
    job = ParsedJob(
        title="Python Automation Engineer",
        company="Acme Co",
        location="Remote",
        description="""
        Requirements:
        - Python
        - SQL

        Preferred:
        - Playwright

        Responsibilities:
        - Build automation workflows and document results.
        - Support debugging and testing.
        """,
        raw_text="Python SQL Playwright automation",
    )
    requirements = extract_job_requirements(job)
    assert "Python" in requirements.required_skills
    assert "SQL" in requirements.required_skills
    assert "Playwright" in requirements.preferred_skills
    assert requirements.responsibilities

    profile = load_base_profile(Path("profile.md"))
    first = build_resume_tailoring(job, profile)
    second = build_resume_tailoring(job, profile)
    assert first == second
    assert "8+ years" not in first.summary.lower()
    assert first.selected_skills
    assert first.selected_projects
