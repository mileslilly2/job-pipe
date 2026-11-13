from pathlib import Path

from jobpipe.cli import app
from jobpipe.ingest import evaluate_discovered_job
from jobpipe.db import get_job, get_job_by_provider_external_id, insert_job, list_review_jobs, set_review_status
from jobpipe.models import DiscoveredJob, JobSource, SearchProfile


def test_ingest_loads_greenhouse_and_lever_sources(initialized_workspace, runner, monkeypatch) -> None:
    Path("data/job_sources.yaml").write_text(
        """- provider: greenhouse
  company: Acme
  board_token: acme
- provider: lever
  company: Beta
  account: beta
""",
        encoding="utf-8",
    )

    import jobpipe.providers as providers

    monkeypatch.setitem(
        providers.PROVIDER_FETCHERS,
        "greenhouse",
        lambda source: [
            DiscoveredJob(
                provider="greenhouse",
                external_id="gh-1",
                company=source.company,
                title="Junior Data Engineer",
                location="Remote - US",
                remote=True,
                apply_url="https://example.com/gh-1",
                description="Python SQL ETL automation backend API",
                posted_at="2026-06-22T00:00:00+00:00",
                source_url="https://example.com/gh-1",
            )
        ],
    )
    monkeypatch.setitem(
        providers.PROVIDER_FETCHERS,
        "lever",
        lambda source: [
            DiscoveredJob(
                provider="lever",
                external_id="lv-1",
                company=source.company,
                title="Automation Engineer",
                location="Hybrid",
                remote=False,
                apply_url="https://example.com/lv-1",
                description="Automation python api sql",
                posted_at="2026-06-21T00:00:00+00:00",
                source_url="https://example.com/lv-1",
            )
        ],
    )

    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0, result.stdout

    greenhouse = get_job_by_provider_external_id("greenhouse", "gh-1")
    lever = get_job_by_provider_external_id("lever", "lv-1")
    assert greenhouse is not None
    assert lever is not None
    assert greenhouse["status"] == "new"
    assert greenhouse["fit_score"] is not None
    assert lever["fit_score"] is not None


def test_ingest_deduplicates_provider_jobs(initialized_workspace, runner, monkeypatch) -> None:
    Path("data/job_sources.yaml").write_text(
        """- provider: greenhouse
  company: Acme
  board_token: acme
""",
        encoding="utf-8",
    )

    import jobpipe.providers as providers

    monkeypatch.setitem(
        providers.PROVIDER_FETCHERS,
        "greenhouse",
        lambda source: [
            DiscoveredJob(
                provider="greenhouse",
                external_id="gh-dup",
                company=source.company,
                title="Data Engineer",
                location="Remote",
                remote=True,
                apply_url="https://example.com/gh-dup",
                description="Python SQL ETL",
                posted_at="2026-06-22T00:00:00+00:00",
                source_url="https://example.com/gh-dup",
            )
        ],
    )

    assert runner.invoke(app, ["ingest"]).exit_code == 0
    assert runner.invoke(app, ["ingest"]).exit_code == 0
    rows = [row for row in list_review_jobs(limit=100) if row["provider"] == "greenhouse"]
    assert len(rows) == 1


def test_review_queue_orders_by_fit_score(initialized_workspace) -> None:
    low_id = insert_job(
        {
            "provider": "greenhouse",
            "external_id": "low",
            "company": "Acme",
            "title": "Backend Engineer",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/low",
            "description": "python",
            "fit_score": 45,
        },
        review_status="new",
    )
    high_id = insert_job(
        {
            "provider": "lever",
            "external_id": "high",
            "company": "Beta",
            "title": "Junior Data Engineer",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/high",
            "description": "python sql etl automation backend api",
            "fit_score": 90,
        },
        review_status="new",
    )
    saved_id = insert_job(
        {
            "provider": "lever",
            "external_id": "saved",
            "company": "Gamma",
            "title": "Support Engineer",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/saved",
            "description": "python",
            "fit_score": 80,
        },
        review_status="saved",
    )

    rows = list_review_jobs(limit=10)
    ids = [row["id"] for row in rows]
    assert high_id in ids
    assert low_id in ids
    assert all(row["status"] == "new" for row in rows)
    assert saved_id not in ids


def test_review_commands_update_status(initialized_workspace, runner) -> None:
    job_id = insert_job(
        {
            "provider": "greenhouse",
            "external_id": "review-1",
            "company": "Acme",
            "title": "Junior Data Engineer",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/review-1",
            "description": "python sql etl",
            "fit_score": 88,
        },
        review_status="new",
    )

    assert runner.invoke(app, ["show", str(job_id)]).exit_code == 0
    assert runner.invoke(app, ["save", str(job_id)]).exit_code == 0
    assert get_job(job_id)["status"] == "saved"
    assert runner.invoke(app, ["skip", str(job_id)]).exit_code == 0
    assert get_job(job_id)["status"] == "skipped"


def test_review_session_can_show_details_and_save(initialized_workspace, runner, monkeypatch) -> None:
    first_id = insert_job(
        {
            "provider": "greenhouse",
            "external_id": "queue-1",
            "company": "Acme",
            "title": "Junior Data Engineer",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/queue-1",
            "description": "python sql etl automation",
            "fit_score": 92,
        },
        review_status="new",
    )
    insert_job(
        {
            "provider": "lever",
            "external_id": "queue-2",
            "company": "Beta",
            "title": "Automation Engineer",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/queue-2",
            "description": "python api automation",
            "fit_score": 84,
        },
        review_status="new",
    )

    opened = []
    monkeypatch.setattr("jobpipe.cli.open_url", lambda url: opened.append(url) or True)

    result = runner.invoke(app, ["review"], input="d\ny\n\n\nq\n")
    assert result.exit_code == 0, result.stdout
    assert "Fit breakdown" in result.stdout
    assert get_job(first_id)["status"] == "saved"


def test_filter_diagnostic_reports_all_deterministic_reasons() -> None:
    source = JobSource(provider="greenhouse", company="Acme", target_role_families=["backend_api"], exclude_keywords=["contract"])
    profile = SearchProfile(name="test", preferred=[], acceptable=[], avoid=["legal"], negative_keywords=["blocked"], excluded_levels=["senior"])
    job = DiscoveredJob(provider="greenhouse", external_id="bad", company="Acme", title="Senior Product Counsel", location="Remote", description="blocked contract", remote=True)
    decision = evaluate_discovered_job(job, source, profile)
    assert decision.accepted is False
    assert decision.reason_codes == ("role_family_not_targeted", "seniority_excluded", "profile_negative_keyword", "keyword_excluded")
    assert decision.role_family == "legal_counsel"
