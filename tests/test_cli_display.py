from pathlib import Path

from jobpipe.cli import app
from jobpipe.db import insert_job


def test_list_and_shortlist_show_recommendation_columns(initialized_workspace, runner) -> None:
    fixture = Path(__file__).parent / "fixtures" / "helpdesk_clean.txt"
    assert runner.invoke(app, ["add-text", "--file", str(fixture)]).exit_code == 0
    assert runner.invoke(app, ["analyze", "1"]).exit_code == 0

    list_result = runner.invoke(app, ["list"])
    assert list_result.exit_code == 0
    assert "Jobs" in list_result.stdout
    assert "Due" in list_result.stdout

    show_result = runner.invoke(app, ["show", "1"])
    assert show_result.exit_code == 0
    assert "Job facts" in show_result.stdout
    assert "Profile facts" in show_result.stdout
    assert "Matched:" in show_result.stdout
    assert "Missing / Mismatched:" in show_result.stdout
    assert "Concerns:" in show_result.stdout

    shortlist_result = runner.invoke(app, ["shortlist", "--min-fit", "0", "--max-risk", "100"])
    assert shortlist_result.exit_code == 0
    assert "Shortlist" in shortlist_result.stdout

def test_feedback_rejects_unknown_action(initialized_workspace, runner) -> None:
    job_id = insert_job(
        {
            "provider": "greenhouse",
            "external_id": "feedback-unknown-action",
            "company": "Acme Co",
            "title": "Python Automation Engineer",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/apply",
            "description": "Python SQL ETL automation backend api",
        },
        review_status="new",
    )

    result = runner.invoke(app, ["feedback", str(job_id), "unknown"])
    assert result.exit_code != 0
    assert "Unsupported feedback action" in result.stdout or "Unsupported feedback action" in result.stderr

