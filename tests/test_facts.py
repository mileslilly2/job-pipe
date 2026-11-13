from jobpipe.catalog import load_search_profile
from jobpipe.facts import build_fact_report, classify_seniority, extract_experience_min_years
from jobpipe.models import ParsedJob


def test_experience_extraction_parses_common_phrases() -> None:
    cases = {
        "3+ years of experience": 3,
        "at least 2 years": 2,
        "5 years of experience": 5,
        "2-4 years in a similar role": 2,
    }

    for text, expected in cases.items():
        assert extract_experience_min_years(text) == expected


def test_seniority_classifier_uses_title_and_experience_signals() -> None:
    job = ParsedJob(
        title="Senior Staff Engineer",
        company="Acme",
        location="Remote",
        description="Responsible for leading architecture with 8+ years of experience.",
        raw_text="Senior Staff Engineer with 8+ years of experience.",
    )

    seniority, notes = classify_seniority(job)
    assert seniority == "staff"
    assert notes


def test_fact_report_flags_experience_mismatch_against_profile() -> None:
    profile = load_search_profile()
    job = ParsedJob(
        title="Senior Data Engineer",
        company="Acme",
        location="Remote",
        description="Python SQL ETL, 8+ years of experience, and team leadership.",
        raw_text="Senior Data Engineer role requiring 8+ years of experience.",
    )

    report = build_fact_report(job, profile)
    assert report.job.experience_min_years == 8
    assert report.profile is not None
    assert report.profile.experience_max_years == 4
    assert report.matched
    assert report.missing_mismatched
    assert report.concerns
    assert any("experience mismatch" in note for note in report.concerns)
    assert any("seniority concern" in note for note in report.concerns)
    assert report.score_adjustment < 0
