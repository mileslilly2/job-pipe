from __future__ import annotations

from jobpipe.catalog import load_search_profile
from jobpipe.cli import app
from jobpipe.db import get_analysis, get_job, insert_job, list_review_jobs
from jobpipe.score import score_job_fit_v1


def _insert_stale_job(*, external_id: str, company: str, title: str, description: str, stored_fit_score: int, remote: bool) -> int:
    return insert_job(
        {
            "provider": "greenhouse",
            "external_id": external_id,
            "company": company,
            "title": title,
            "location": "Remote - United States" if remote else "Onsite - United States",
            "remote": remote,
            "apply_url": f"https://example.com/{external_id}",
            "description": description,
            "fit_score": stored_fit_score,
        },
        review_status="new",
    )


def test_fit_score_is_consistent_across_queue_list_details_and_analyze(initialized_workspace, runner) -> None:
    low_priority_id = _insert_stale_job(
        external_id="stale-low",
        company="Beta",
        title="Senior Manager",
        description="Lead a team and manage projects.",
        stored_fit_score=88,
        remote=False,
    )
    high_priority_id = _insert_stale_job(
        external_id="stale-high",
        company="Acme",
        title="Junior Data Engineer",
        description="Python SQL ETL automation backend api",
        stored_fit_score=12,
        remote=True,
    )

    high_job = get_job(high_priority_id)
    low_job = get_job(low_priority_id)
    assert high_job is not None
    assert low_job is not None

    profile = load_search_profile()
    expected_high_score, expected_high_notes = score_job_fit_v1(dict(high_job), profile=profile)
    expected_low_score, _ = score_job_fit_v1(dict(low_job), profile=profile)
    assert expected_high_score > expected_low_score
    assert high_job["fit_score"] == 12
    assert low_job["fit_score"] == 88

    queued_ids = [row["id"] for row in list_review_jobs(limit=10)]
    assert queued_ids == [high_priority_id, low_priority_id]

    list_result = runner.invoke(app, ["list"])
    assert list_result.exit_code == 0, list_result.stdout
    assert str(expected_high_score) in list_result.stdout
    assert str(expected_low_score) in list_result.stdout
    assert "12" not in list_result.stdout
    assert "88" not in list_result.stdout

    review_result = runner.invoke(app, ["review"], input="q\n")
    assert review_result.exit_code == 0, review_result.stdout
    assert "Acme - Junior Data Engineer" in review_result.stdout
    assert f"Fit score: {expected_high_score}" in review_result.stdout
    assert "Beta - Senior Manager" not in review_result.stdout

    batch_result = runner.invoke(app, ["batch-review"], input="q\n")
    assert batch_result.exit_code == 0, batch_result.stdout
    assert "Acme - Junior Data Engineer" in batch_result.stdout
    assert f"Fit score: {expected_high_score}" in batch_result.stdout
    assert "Beta - Senior Manager" not in batch_result.stdout

    show_result = runner.invoke(app, ["show", str(high_priority_id)])
    assert show_result.exit_code == 0, show_result.stdout
    assert "Stored Fit Score" not in show_result.stdout
    assert "Computed Fit Score" not in show_result.stdout
    assert "Fit Score" in show_result.stdout
    for note in expected_high_notes:
        assert note in show_result.stdout

    analyze_result = runner.invoke(app, ["analyze", str(high_priority_id)])
    assert analyze_result.exit_code == 0, analyze_result.stdout
    assert f"fit {expected_high_score}" in analyze_result.stdout
    analysis_row = get_analysis(high_priority_id)
    assert analysis_row is not None
    assert analysis_row["fit_score"] == expected_high_score
