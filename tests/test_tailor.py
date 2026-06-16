from pathlib import Path

from jobpipe.classify import classify_job
from jobpipe.models import CandidateProfile, RolePack
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
    role_type, _seniority, _matched, _missing = classify_job(job, packs)
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
