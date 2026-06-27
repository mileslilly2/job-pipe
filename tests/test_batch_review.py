from __future__ import annotations

from pathlib import Path

from jobpipe.cli import app
from jobpipe.db import get_application, get_job, insert_job
from jobpipe.utils import slugify


def _insert_job(external_id: str, company: str, title: str, fit_score: int, apply_url: str) -> int:
    return insert_job(
        {
            "provider": "greenhouse",
            "external_id": external_id,
            "company": company,
            "title": title,
            "location": "Remote - United States",
            "remote": True,
            "apply_url": apply_url,
            "description": "Python SQL ETL automation backend api react ai evaluation",
            "fit_score": fit_score,
        },
        review_status="new",
    )


def test_batch_review_processes_save_package_apply_and_skip_paths(initialized_workspace, runner, monkeypatch) -> None:
    save_id = _insert_job("batch-save", "Alpha Co", "Junior Data Engineer", 95, "https://example.com/save")
    package_id = _insert_job("batch-package", "Beta Labs", "Automation Engineer", 90, "https://example.com/package")
    apply_id = _insert_job("batch-apply", "Gamma Systems", "Support Engineer", 85, "https://example.com/apply")
    skip_id = _insert_job("batch-skip", "Delta Works", "Senior Manager", 80, "https://example.com/skip")

    opened_urls: list[str] = []
    monkeypatch.setattr("jobpipe.cli.open_url", lambda url: opened_urls.append(url) or True)

    result = runner.invoke(app, ["batch-review"], input="s\np\na\ny\nk\n")
    assert result.exit_code == 0, result.stdout

    assert opened_urls == ["https://example.com/apply"]
    assert "Saved job" in result.stdout
    assert "Generated application package" in result.stdout
    assert "Marked job" in result.stdout
    assert "Skipped job" in result.stdout

    save_job = get_job(save_id)
    package_job = get_job(package_id)
    apply_job = get_job(apply_id)
    skip_job = get_job(skip_id)
    assert save_job is not None
    assert package_job is not None
    assert apply_job is not None
    assert skip_job is not None
    assert save_job["status"] == "saved"
    assert package_job["status"] == "saved"
    assert apply_job["status"] == "applied"
    assert skip_job["status"] == "skipped"

    save_application = get_application(save_id)
    package_application = get_application(package_id)
    apply_application = get_application(apply_id)
    skip_application = get_application(skip_id)
    assert save_application is not None
    assert package_application is not None
    assert apply_application is not None
    assert skip_application is not None
    assert save_application["status"] == "saved"
    assert package_application["status"] == "saved"
    assert apply_application["status"] == "applied"
    assert apply_application["followup_at"] is not None
    assert skip_application["status"] == "saved"

    package_dir = Path(f"applications/{slugify('Beta Labs')}-{slugify('Automation Engineer')}")
    apply_dir = Path(f"applications/{slugify('Gamma Systems')}-{slugify('Support Engineer')}")
    for directory in [package_dir, apply_dir]:
        assert (directory / "job.md").exists()
        assert (directory / "fit.md").exists()
        assert (directory / "resume-tailored.md").exists()
        assert (directory / "cover-note.md").exists()
        assert (directory / "apply.md").exists()
