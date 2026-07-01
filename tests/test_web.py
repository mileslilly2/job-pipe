from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpipe.cli import _ensure_workspace
from jobpipe.db import get_application, get_job, insert_job, set_followup_at, upsert_analysis
from jobpipe.tracker import update_status
from jobpipe import web as web_module


@pytest.fixture()
def web_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    import jobpipe.db
    import jobpipe.learn
    import jobpipe.utils

    db_file = tmp_path / "jobpipe.db"
    weights_file = tmp_path / "data" / "learned_weights.yaml"
    monkeypatch.setattr(jobpipe.utils, "db_path", lambda: db_file)
    monkeypatch.setattr(jobpipe.db, "db_path", lambda: db_file)
    monkeypatch.setattr(jobpipe.learn, "WEIGHTS_FILE", weights_file)
    _ensure_workspace()
    return tmp_path


@pytest.fixture()
def client(web_workspace: Path) -> TestClient:
    return TestClient(web_module.create_app())


def _job_payload(company: str, title: str, *, provider: str = "greenhouse", external_id: str, remote: bool = True, url: str = "https://example.com/apply", description: str = "Build Python automation pipelines with API integrations, SQL, SQLite, ETL, CLI tools, and testing.") -> dict[str, object]:
    return {
        "provider": provider,
        "external_id": external_id,
        "company": company,
        "title": title,
        "location": "Remote - United States" if remote else "New York, NY",
        "remote": remote,
        "apply_url": url,
        "description": description,
    }


def test_dashboard_jobs_filters_and_actions(client: TestClient) -> None:
    job_new = insert_job(_job_payload("Alpha Co", "Backend Engineer", external_id="job-new"), review_status="new")
    job_saved = insert_job(_job_payload("Beta Co", "Python Automation Engineer", external_id="job-saved"), review_status="new")
    job_skipped = insert_job(_job_payload("Gamma Co", "Support Engineer", external_id="job-skipped", remote=False, description="Help desk and customer support"), review_status="new")
    job_applied = insert_job(_job_payload("Delta Co", "Data Engineer", external_id="job-applied"), review_status="new")

    resp = client.post(f"/jobs/{job_saved}/save?next=/jobs", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/jobs"

    resp = client.post(f"/jobs/{job_saved}/package?next=/jobs", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/jobs"

    resp = client.post(f"/jobs/{job_skipped}/skip?next=/jobs", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/jobs"

    update_status(job_applied, "applied")
    overdue = datetime.now(timezone.utc) - timedelta(days=1)
    set_followup_at(job_applied, overdue.isoformat(timespec="seconds"))

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Total jobs" in dashboard.text
    assert "Packaged jobs" in dashboard.text
    assert 'Total jobs</div><div class="stat">4</div>' in dashboard.text
    assert 'Saved jobs</div><div class="stat">1</div>' in dashboard.text
    assert 'Packaged jobs</div><div class="stat">1</div>' in dashboard.text
    assert 'Applied jobs</div><div class="stat">1</div>' in dashboard.text
    assert 'Skipped jobs</div><div class="stat">1</div>' in dashboard.text
    assert 'Due follow-ups</div><div class="stat">1</div>' in dashboard.text

    jobs_page = client.get(
        "/jobs",
        params={
            "status": "packaged",
            "provider": "greenhouse",
            "company": "Beta",
            "min_score": 0,
            "remote_only": "true",
            "q": "automation",
        },
    )
    assert jobs_page.status_code == 200
    assert "Python Automation Engineer" in jobs_page.text
    assert "Alpha Co" not in jobs_page.text
    assert "Gamma Co" not in jobs_page.text
    assert "Open apply URL" in jobs_page.text
    assert "Package" in jobs_page.text

    assert get_job(job_new)["status"] == "new"
    assert get_job(job_saved)["status"] == "saved"
    assert get_job(job_skipped)["status"] == "skipped"
    assert get_application(job_applied)["status"] == "applied"


def test_kanban_route_renders_and_move_updates_status(client: TestClient) -> None:
    job_id = insert_job(_job_payload("Kanban Co", "Platform Engineer", external_id="job-kanban"), review_status="new")

    kanban = client.get("/kanban")
    assert kanban.status_code == 200
    assert "Kanban" in kanban.text
    assert "New" in kanban.text
    assert "Mark applied" in kanban.text
    assert "Assessment" in kanban.text

    move = client.post(
        f"/jobs/{job_id}/status/interview?next=/kanban",
        follow_redirects=False,
    )
    assert move.status_code == 303
    assert move.headers["location"] == "/kanban"

    application_row = get_application(job_id)
    assert application_row is not None
    assert application_row["status"] == "interview"


def test_views_page_lists_saved_views(client: TestClient) -> None:
    resp = client.get("/views")
    assert resp.status_code == 200
    for name in [
        "High Fit New Jobs",
        "Saved Not Packaged",
        "Packaged Not Applied",
        "Applied Awaiting Response",
        "Follow-Up Due",
        "Interview Pipeline",
        "Rejected Archive",
    ]:
        assert name in resp.text
    assert "status=new" in resp.text
    assert "min_score=70" in resp.text
    assert "status=rejected" in resp.text


def test_jobs_hide_skipped_and_rejected_until_explicit(client: TestClient) -> None:
    job_active = insert_job(_job_payload("Active Co", "Backend Engineer", external_id="job-active"), review_status="new")
    job_skipped = insert_job(_job_payload("Skipped Co", "Support Engineer", external_id="job-skipped", remote=False, description="Help desk and customer support"), review_status="new")
    job_rejected = insert_job(_job_payload("Rejected Co", "QA Engineer", external_id="job-rejected"), review_status="new")

    skip_resp = client.post(f"/jobs/{job_skipped}/skip?next=/jobs", follow_redirects=False)
    assert skip_resp.status_code == 303

    reject_resp = client.post(
        f"/jobs/{job_rejected}/status/rejected?next=/jobs",
        follow_redirects=False,
    )
    assert reject_resp.status_code == 303

    default_page = client.get("/jobs")
    assert default_page.status_code == 200
    assert "Active Co" in default_page.text
    assert "Skipped Co" not in default_page.text
    assert "Rejected Co" not in default_page.text

    skipped_page = client.get("/jobs", params={"status": "skipped"})
    assert skipped_page.status_code == 200
    assert "Skipped Co" in skipped_page.text
    assert "Rejected Co" not in skipped_page.text

    rejected_page = client.get("/jobs", params={"status": "rejected"})
    assert rejected_page.status_code == 200
    assert "Rejected Co" in rejected_page.text
    assert "Skipped Co" not in rejected_page.text


def test_job_detail_shows_breakdown_package_paths_and_open_apply(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = insert_job(_job_payload("Acme Co", "Python Automation Engineer", external_id="job-detail"), review_status="new")
    upsert_analysis(
        job_id,
        {
            "role_type": "backend_api",
            "seniority_level": "junior",
            "fit_score": 68,
            "risk_score": 12,
            "quality_score": 78,
            "recommendation": "maybe",
            "skip_reason": None,
            "matched_keywords": ["python", "api"],
            "missing_keywords": ["testing"],
            "red_flags": [],
            "eligibility_flags": [],
            "notes": "manual analysis note",
        },
    )
    resp = client.post(f"/jobs/{job_id}/save?next=/jobs/{job_id}", follow_redirects=False)
    assert resp.status_code == 303
    resp = client.post(f"/jobs/{job_id}/package?next=/jobs/{job_id}", follow_redirects=False)
    assert resp.status_code == 303

    detail = client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert "Acme Co - Python Automation Engineer" in detail.text
    assert "Fit score" in detail.text
    assert "Learned adjustments" in detail.text
    assert "Job facts" in detail.text
    assert "Profile facts" in detail.text
    assert "Matched" in detail.text
    assert "Missing / Mismatched" in detail.text
    assert "Concerns" in detail.text
    assert "job.md" in detail.text
    assert "fit.md" in detail.text
    assert "resume-tailored.md" in detail.text
    assert "cover-note.md" in detail.text
    assert "apply.md" in detail.text
    assert "manual analysis note" in detail.text

    opened_urls: list[str] = []

    def fake_open_url(url: str) -> bool:
        opened_urls.append(url)
        return True

    monkeypatch.setattr(web_module, "open_url", fake_open_url)
    apply_resp = client.post(f"/jobs/{job_id}/apply-flow?next=/jobs/{job_id}", follow_redirects=False)
    assert apply_resp.status_code == 303
    assert opened_urls == ["https://example.com/apply"]
    assert get_job(job_id)["status"] == "saved"


def test_followups_page_lists_due_jobs(client: TestClient) -> None:
    job_id = insert_job(_job_payload("Follow Up Co", "Data Engineer", external_id="job-followup"), review_status="new")
    update_status(job_id, "applied")
    set_followup_at(job_id, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"))

    resp = client.get("/followups")
    assert resp.status_code == 200
    assert "Follow Up Co" in resp.text
    assert "Data Engineer" in resp.text
    assert "applied" in resp.text


def test_analytics_page_and_browser_actions(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = insert_job(_job_payload("Browser Co", "Python Automation Engineer", external_id="job-browser"), review_status="new")
    upsert_analysis(
        job_id,
        {
            "role_type": "python_automation",
            "seniority_level": "junior",
            "fit_score": 88,
            "risk_score": 8,
            "quality_score": 74,
            "recommendation": "apply",
            "skip_reason": None,
            "matched_keywords": ["python", "sql"],
            "missing_keywords": ["aws"],
            "red_flags": [],
            "eligibility_flags": [],
            "notes": "web analytics test note",
        },
    )
    assert client.post(f"/jobs/{job_id}/save?next=/jobs/{job_id}", follow_redirects=False).status_code == 303
    assert client.post(f"/jobs/{job_id}/package?next=/jobs/{job_id}", follow_redirects=False).status_code == 303

    opened_paths: list[str] = []
    copied: list[str] = []
    monkeypatch.setattr(web_module, "open_path", lambda path: opened_paths.append(str(path)) or True)
    monkeypatch.setattr(web_module, "copy_file", lambda path: copied.append(Path(path).name) or True)

    analytics = client.get("/analytics")
    assert analytics.status_code == 200
    assert "Applications today" in analytics.text
    assert "Top 10 Jobs to Apply Today" in analytics.text

    open_package = client.post(f"/jobs/{job_id}/open-package?next=/jobs/{job_id}", follow_redirects=False)
    assert open_package.status_code == 303
    assert opened_paths and opened_paths[0].endswith("applications/browser-co-python-automation-engineer")

    copy_cover = client.post(f"/jobs/{job_id}/copy-cover-note?next=/jobs/{job_id}", follow_redirects=False)
    assert copy_cover.status_code == 303
    copy_resume = client.post(f"/jobs/{job_id}/copy-resume-text?next=/jobs/{job_id}", follow_redirects=False)
    assert copy_resume.status_code == 303
    assert copied == ["cover-note.md", "resume-tailored.md"]
