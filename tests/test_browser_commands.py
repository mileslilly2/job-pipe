from pathlib import Path

from jobpipe.cli import app
from jobpipe.db import insert_job


def _job_payload(external_id: str) -> dict[str, object]:
    return {
        "provider": "greenhouse",
        "external_id": external_id,
        "company": "Acme Co",
        "title": "Python Automation Engineer",
        "location": "Remote - United States",
        "remote": True,
        "apply_url": "https://example.com/apply",
        "description": "Build Python automation pipelines with API integrations, SQL, SQLite, ETL, CLI tools, and testing.",
    }


def test_browser_open_opens_folder_url_and_copies_requested_files(initialized_workspace, runner, monkeypatch) -> None:
    job_id = insert_job(_job_payload("browser-open"), review_status="new")
    assert runner.invoke(app, ["save", str(job_id)]).exit_code == 0
    assert runner.invoke(app, ["package", str(job_id)]).exit_code == 0

    opened_urls: list[str] = []
    opened_paths: list[str] = []
    copied: list[str] = []

    monkeypatch.setattr("jobpipe.cli.open_url", lambda url: opened_urls.append(url) or True)
    monkeypatch.setattr("jobpipe.cli.open_path", lambda path: opened_paths.append(str(path)) or True)
    monkeypatch.setattr("jobpipe.cli.copy_file", lambda path: copied.append(Path(path).name) or True)

    result = runner.invoke(app, ["browser-open", str(job_id), "--copy", "resume", "--copy", "cover-note"])
    assert result.exit_code == 0, result.stdout
    assert opened_urls == ["https://example.com/apply"]
    assert opened_paths and opened_paths[0].endswith(f"applications/acme-co-python-automation-engineer-job-{job_id}")
    assert copied == ["resume-tailored.md", "cover-note.md"]
    assert "Copied to clipboard" in result.stdout
