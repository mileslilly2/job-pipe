from pathlib import Path

from jobpipe.classify import classify_job
from jobpipe.models import CandidateProfile, RolePack
from jobpipe.parse import parse_job_text
from jobpipe.score import score_fit, score_quality, score_risk
from jobpipe.utils import load_yaml


def _candidate() -> CandidateProfile:
    return CandidateProfile.model_validate(load_yaml(Path("jobpipe/data/profiles/candidate.yaml")))


def _packs() -> dict[str, RolePack]:
    base = Path("jobpipe/role_packs")
    return {path.stem: RolePack.model_validate(load_yaml(path)) for path in base.glob("*.yaml")}


def test_clean_helpdesk_scores_well() -> None:
    packs = _packs()
    job = parse_job_text(Path("tests/fixtures/helpdesk_clean.txt").read_text(encoding="utf-8"))
    role_type, seniority, matched, _missing = classify_job(job, packs)
    fit_score, _ = score_fit(job, role_type, seniority, packs[role_type], _candidate(), matched)
    risk_score, _ = score_risk(job, packs[role_type])
    quality_score, _ = score_quality(job, role_type, seniority)
    assert fit_score >= 70
    assert risk_score < 50
    assert quality_score >= 55


def test_scam_scores_high_risk() -> None:
    packs = _packs()
    job = parse_job_text(Path("tests/fixtures/chat_support_scam.txt").read_text(encoding="utf-8"))
    role_type, seniority, matched, _missing = classify_job(job, packs)
    risk_score, flags = score_risk(job, packs[role_type])
    assert risk_score >= 65
    assert any("telegram" in flag.lower() or "whatsapp" in flag.lower() for flag in flags)


def test_senior_role_penalized() -> None:
    packs = _packs()
    job = parse_job_text(Path("tests/fixtures/helpdesk_senior.txt").read_text(encoding="utf-8"))
    role_type, seniority, matched, _missing = classify_job(job, packs)
    fit_score, notes = score_fit(job, role_type, seniority, packs["helpdesk"], _candidate(), matched)
    assert seniority == "senior"
    assert fit_score < 70
    assert any("seniority mismatch" in note for note in notes)
