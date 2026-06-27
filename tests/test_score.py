from pathlib import Path

from jobpipe.classify import classify_job, detect_seniority
from jobpipe.models import CandidateProfile, DiscoveredJob, RolePack
from jobpipe.parse import parse_job_text
from jobpipe.recommend import make_recommendation
from jobpipe.score import score_fit, score_job_fit_v1, score_quality, score_risk
from jobpipe.utils import load_yaml


def _candidate() -> CandidateProfile:
    return CandidateProfile.model_validate(load_yaml(Path("jobpipe/data/profiles/candidate.yaml")))


def _packs() -> dict[str, RolePack]:
    base = Path("jobpipe/role_packs")
    return {path.stem: RolePack.model_validate(load_yaml(path)) for path in base.glob("*.yaml")}


def _analyze(path: str):
    packs = _packs()
    job = parse_job_text(Path(path).read_text(encoding="utf-8"))
    role_type, matched, _missing = classify_job(job, packs)
    pack = packs.get(role_type)
    risk_score, red_flags = score_risk(job, pack)
    seniority, eligibility_flags = detect_seniority(job, risk_score=risk_score, risk_flags=red_flags)
    fit_score, _fit_notes = score_fit(job, role_type, seniority, pack, _candidate(), matched)
    quality_score, _quality_notes = score_quality(job, role_type, seniority)
    recommendation, skip_reason = make_recommendation(fit_score, risk_score, quality_score, role_type, seniority, red_flags)
    return {
        "job": job,
        "role_type": role_type,
        "seniority": seniority,
        "fit_score": fit_score,
        "risk_score": risk_score,
        "quality_score": quality_score,
        "recommendation": recommendation,
        "skip_reason": skip_reason,
        "red_flags": red_flags,
        "eligibility_flags": eligibility_flags,
    }


def test_clean_helpdesk_apply() -> None:
    result = _analyze("tests/fixtures/helpdesk_clean.txt")
    assert result["role_type"] == "helpdesk"
    assert result["seniority"] == "entry_level"
    assert result["fit_score"] >= 70
    assert result["risk_score"] < 50
    assert result["recommendation"] == "apply"
    assert result["skip_reason"] is None


def test_scammy_chat_skips_for_risk() -> None:
    result = _analyze("tests/fixtures/chat_support_scam.txt")
    assert result["risk_score"] >= 75
    assert result["recommendation"] == "skip"
    assert result["skip_reason"] == "high scam/quality risk"


def test_senior_helpdesk_skips_for_seniority() -> None:
    result = _analyze("tests/fixtures/helpdesk_senior.txt")
    assert result["role_type"] == "helpdesk"
    assert result["seniority"] in {"senior", "manager"}
    assert result["fit_score"] <= 20
    assert result["risk_score"] <= 30
    assert result["recommendation"] == "skip"
    assert result["skip_reason"] == "seniority mismatch"


def test_senior_technical_support_skips_for_seniority() -> None:
    result = _analyze("tests/fixtures/senior_technical_support.txt")
    assert result["role_type"] == "technical_support"
    assert result["seniority"] == "senior"
    assert result["recommendation"] == "skip"
    assert result["skip_reason"] == "seniority mismatch"


def test_unknown_low_fit_skips() -> None:
    result = _analyze("tests/fixtures/unknown_low_fit.txt")
    assert result["recommendation"] == "skip"
    assert result["skip_reason"] in {"poor fit", "unsupported role"}


def test_fit_scoring_v1_rewards_keywords_and_boosts() -> None:
    job = DiscoveredJob(
        provider="greenhouse",
        external_id="score-1",
        company="Acme",
        title="Junior Data Engineer",
        location="Remote",
        remote=True,
        apply_url="https://example.com/score-1",
        description="Python SQL ETL automation backend api react ai evaluation",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    score, notes = score_job_fit_v1(job)

    assert score >= 60
    assert any("python" in note for note in notes)
    assert any("remote" in note for note in notes)
    assert any("junior" in note for note in notes)


def test_fit_scoring_v1_penalizes_seniority() -> None:
    junior = DiscoveredJob(
        provider="greenhouse",
        external_id="score-2",
        company="Acme",
        title="Junior Data Engineer",
        location="Remote",
        remote=True,
        apply_url="https://example.com/score-2",
        description="Python SQL ETL",
        posted_at="2026-06-22T00:00:00+00:00",
    )
    senior = DiscoveredJob(
        provider="greenhouse",
        external_id="score-3",
        company="Acme",
        title="Senior Engineering Manager",
        location="Remote",
        remote=True,
        apply_url="https://example.com/score-3",
        description="Python SQL ETL",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    junior_score, _ = score_job_fit_v1(junior)
    senior_score, notes = score_job_fit_v1(senior)

    assert senior_score < junior_score
    assert any("manager" in note for note in notes)
