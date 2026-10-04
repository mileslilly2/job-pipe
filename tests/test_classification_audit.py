from pathlib import Path

from jobpipe.classification import classify_job
from jobpipe.classification_audit import audit_records, load_gold, load_review_sample
from jobpipe.facts import classify_role_family


def test_gold_review_sample_is_deterministically_parsed() -> None:
    records = load_review_sample(Path("diagnostics/false_negative_review.md"))
    assert len(records) == 100
    assert records == load_review_sample(Path("diagnostics/false_negative_review.md"))


def test_title_precedence_regressions() -> None:
    assert classify_role_family({"title": "Staff + Senior Software Engineer, Inference Infrastructure", "description": "counsel"})[0] == "software_engineer"
    assert classify_role_family({"title": "Senior Backend Engineer (Python), Agent Developer: Flow Components", "description": "frontend"})[0] == "backend_api"
    assert classify_role_family({"title": "Growth Recruiter, High Volume - Contract", "description": "backend APIs"})[0] == "unknown"
    assert classify_role_family({"title": "Technical Solutions Engineer", "description": "research and machine learning"})[0] == "technical_support"
    assert classify_job({"title": "Software Engineer Intern", "description": "mid-level team"}).seniority == "intern"
    assert classify_job({"title": "Salesforce Platform Engineer", "description": "software engineering"}).primary_role_family != "sales"


def test_audit_reports_high_confidence_metrics() -> None:
    records = [{"company": "Acme", "title": "Software Engineer Intern", "source": "x", "recorded_role": "frontend_engineer", "recorded_seniority": "mid", "rejection_reasons": ["role_family_not_targeted"], "url": ""}]
    report = audit_records(records, {"Software Engineer Intern": {"expected_role": "software_engineering", "expected_seniority": "intern"}})
    assert report["labeled_roles"] == 1
    assert report["correct_roles"] == 1
    assert report["correct_seniority"] == 1

