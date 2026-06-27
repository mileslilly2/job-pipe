from __future__ import annotations

from pathlib import Path

from jobpipe.cli import app
from jobpipe.db import get_application, get_job, insert_job


def _make_job() -> int:
    return insert_job(
        {
            "provider": "greenhouse",
            "external_id": "apply-flow-1",
            "company": "Acme Co",
            "title": "Python Automation Engineer",
            "location": "Remote - United States",
            "remote": True,
            "apply_url": "https://example.com/apply",
            "description": "Build Python automation pipelines with API integrations, SQL, SQLite, ETL, CLI tools, and testing.",
        },
        review_status="new",
    )


def test_apply_flow_auto_generates_package_and_marks_applied(initialized_workspace, runner, monkeypatch) -> None:
    job_id = _make_job()
    assert runner.invoke(app, ["save", str(job_id)]).exit_code == 0

    opened_urls: list[str] = []

    def fake_open_url(url: str) -> bool:
        opened_urls.append(url)
        return True

    monkeypatch.setattr("jobpipe.cli.open_url", fake_open_url)

    result = runner.invoke(app, ["apply-flow", str(job_id)], input="y\n")
    assert result.exit_code == 0, result.stdout

    package_dir = Path("applications/acme-co-python-automation-engineer")
    assert (package_dir / "job.md").exists()
    assert (package_dir / "fit.md").exists()
    assert (package_dir / "resume-tailored.md").exists()
    assert (package_dir / "cover-note.md").exists()
    assert (package_dir / "apply.md").exists()
    assert "Resume tailored:" in result.stdout
    assert "Cover note:" in result.stdout
    assert "Apply checklist:" in result.stdout
    assert opened_urls == ["https://example.com/apply"]

    job_row = get_job(job_id)
    application_row = get_application(job_id)
    assert job_row is not None
    assert application_row is not None
    assert job_row["status"] == "applied"
    assert application_row["status"] == "applied"
    assert application_row["followup_at"]


def test_apply_flow_leaves_status_unchanged_when_declined(initialized_workspace, runner, monkeypatch) -> None:
    job_id = _make_job()
    assert runner.invoke(app, ["save", str(job_id)]).exit_code == 0
    assert runner.invoke(app, ["package", str(job_id)]).exit_code == 0

    opened_urls: list[str] = []

    def fake_open_url(url: str) -> bool:
        opened_urls.append(url)
        return True

    monkeypatch.setattr("jobpipe.cli.open_url", fake_open_url)

    result = runner.invoke(app, ["apply-flow", str(job_id)], input="n\n")
    assert result.exit_code == 0, result.stdout
    assert opened_urls == ["https://example.com/apply"]
    assert "Left job unchanged." in result.stdout

    job_row = get_job(job_id)
    application_row = get_application(job_id)
    assert job_row is not None
    assert application_row is not None
    assert job_row["status"] == "saved"
    assert application_row["status"] == "saved"
    assert application_row["followup_at"] is None
