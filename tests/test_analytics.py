from datetime import datetime, timedelta, timezone

from jobpipe.analytics import build_analytics_report
from jobpipe.cli import app
from jobpipe.db import insert_job, set_followup_at, upsert_analysis
from jobpipe.tracker import update_status


def _job_payload(company: str, title: str, *, external_id: str) -> dict[str, object]:
    return {
        "provider": "greenhouse",
        "external_id": external_id,
        "company": company,
        "title": title,
        "location": "Remote - United States",
        "remote": True,
        "apply_url": "https://example.com/apply",
        "description": "Build Python automation pipelines with API integrations, SQL, SQLite, ETL, CLI tools, and testing.",
    }


def _analysis(role_type: str, recommendation: str = "apply") -> dict[str, object]:
    return {
        "role_type": role_type,
        "seniority_level": "junior",
        "fit_score": 88,
        "risk_score": 8,
        "quality_score": 74,
        "recommendation": recommendation,
        "skip_reason": None if recommendation != "skip" else "poor fit",
        "matched_keywords": ["python", "sql"],
        "missing_keywords": ["aws"],
        "red_flags": [],
        "eligibility_flags": [],
        "notes": "analytics test note",
    }


def test_analytics_aggregates_cli_and_web_metrics(initialized_workspace, runner) -> None:
    saved_id = insert_job(_job_payload("Saved Co", "Python Automation Engineer", external_id="analytics-saved"), review_status="new")
    packaged_id = insert_job(_job_payload("Packaged Co", "Data Engineer", external_id="analytics-packaged"), review_status="new")
    applied_id = insert_job(_job_payload("Applied Co", "Backend Engineer", external_id="analytics-applied"), review_status="new")

    upsert_analysis(saved_id, _analysis("python_automation", "maybe"))
    upsert_analysis(packaged_id, _analysis("data_engineer", "maybe"))
    upsert_analysis(applied_id, _analysis("backend_api", "apply"))

    runner.invoke(app, ["save", str(saved_id)])
    runner.invoke(app, ["save", str(packaged_id)])
    runner.invoke(app, ["package", str(packaged_id)])

    update_status(applied_id, "applied")
    set_followup_at(applied_id, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"))

    report = build_analytics_report()
    assert report.summary["applications_today"] == 1
    assert report.summary["applications_this_week"] >= 1
    assert report.summary["saved_not_packaged"] == 1
    assert report.summary["packaged_not_applied"] >= 1
    assert report.summary["followups_due"] == 1
    assert report.top_apply_today
    assert report.top_role_families_by_score
    assert report.top_sources_by_high_fit_jobs
    assert report.skip_reasons == []
    assert report.role_family_rates

    result = runner.invoke(app, ["analytics"])
    assert result.exit_code == 0
    assert "Analytics Summary" in result.stdout
    assert "Top 10 Jobs to Apply Today" in result.stdout
    assert "Applications today" in result.stdout
