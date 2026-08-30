from pathlib import Path

from jobpipe.cli import app
from jobpipe.db import insert_job


def test_package_creates_deterministic_bundle(initialized_workspace, runner) -> None:
    job_id = insert_job(
        {
            "provider": "greenhouse",
            "external_id": "package-1",
            "company": "Acme Co",
            "title": "Python Automation Engineer",
            "location": "Remote - United States",
            "remote": True,
            "apply_url": "https://example.com/apply",
            "description": "Build Python automation pipelines with API integrations, SQL, SQLite, ETL, CLI tools, and testing.",
        },
        review_status="new",
    )

    assert runner.invoke(app, ["save", str(job_id)]).exit_code == 0
    result = runner.invoke(app, ["package", str(job_id)])
    assert result.exit_code == 0, result.stdout

    package_dir = Path(f"applications/acme-co-python-automation-engineer-job-{job_id}")
    job_md = package_dir / "job.md"
    fit_md = package_dir / "fit.md"
    resume_md = package_dir / "resume-tailored.md"
    cover_md = package_dir / "cover-note.md"
    apply_md = package_dir / "apply.md"

    for path in [job_md, fit_md, resume_md, cover_md, apply_md]:
        assert path.exists()

    assert "Company: Acme Co" in job_md.read_text(encoding="utf-8")
    assert "Provider: greenhouse" in job_md.read_text(encoding="utf-8")
    fit_text = fit_md.read_text(encoding="utf-8")
    assert "Fit score:" in fit_text
    assert "## Matched Requirements" in fit_text
    assert "## Missing Requirements" in fit_text
    assert "## Concerns" in fit_text
    assert "## Suggested Emphasis" in fit_text
    assert "Python" in resume_md.read_text(encoding="utf-8")
    assert "SQLite" in resume_md.read_text(encoding="utf-8")
    cover_text = cover_md.read_text(encoding="utf-8")
    paragraphs = [chunk for chunk in cover_text.split("\n\n") if chunk.strip()]
    assert 2 <= len(paragraphs) <= 4
    assert f"jobpipe status {job_id} applied" in apply_md.read_text(encoding="utf-8")

    snapshot = {path.name: path.read_text(encoding="utf-8") for path in [job_md, fit_md, resume_md, cover_md, apply_md]}
    assert runner.invoke(app, ["package", str(job_id)]).exit_code == 0
    for path in [job_md, fit_md, resume_md, cover_md, apply_md]:
        assert path.read_text(encoding="utf-8") == snapshot[path.name]


def test_package_requires_saved_job(initialized_workspace, runner) -> None:
    job_id = insert_job(
        {
            "provider": "lever",
            "external_id": "package-2",
            "company": "Beta Labs",
            "title": "Data Engineer",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/apply",
            "description": "Python SQL ETL",
        },
        review_status="new",
    )

    result = runner.invoke(app, ["package", str(job_id)])
    assert result.exit_code != 0
    assert "Run save before package." in result.stdout or "Run save before package." in result.stderr

def test_package_directory_includes_job_id_to_avoid_collisions(initialized_workspace, runner) -> None:
    shared_payload = {
        "provider": "greenhouse",
        "company": "Same Co",
        "title": "Python Automation Engineer",
        "location": "Remote - United States",
        "remote": True,
        "apply_url": "https://example.com/apply",
        "description": "Build Python automation pipelines with API integrations, SQL, SQLite, ETL, CLI tools, and testing.",
    }
    first_id = insert_job({**shared_payload, "external_id": "package-collision-1"}, review_status="new")
    second_id = insert_job({**shared_payload, "external_id": "package-collision-2"}, review_status="new")

    assert runner.invoke(app, ["save", str(first_id)]).exit_code == 0
    assert runner.invoke(app, ["save", str(second_id)]).exit_code == 0
    assert runner.invoke(app, ["package", str(first_id)]).exit_code == 0
    assert runner.invoke(app, ["package", str(second_id)]).exit_code == 0

    first_dir = Path(f"applications/same-co-python-automation-engineer-job-{first_id}")
    second_dir = Path(f"applications/same-co-python-automation-engineer-job-{second_id}")

    assert first_dir != second_dir
    assert (first_dir / "job.md").exists()
    assert (second_dir / "job.md").exists()
    assert f"Run `jobpipe status {first_id} applied`" in (first_dir / "apply.md").read_text(encoding="utf-8")
    assert f"Run `jobpipe status {second_id} applied`" in (second_dir / "apply.md").read_text(encoding="utf-8")

