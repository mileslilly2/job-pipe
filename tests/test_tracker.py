from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobpipe.cli import app
from jobpipe.db import get_application, insert_job, set_followup_at
from jobpipe.tracker import update_status


def test_status_updates(initialized_workspace) -> None:
    job_id = insert_job({"title": "Sample Job", "raw_text": "Sample", "description": "Sample"})
    update_status(job_id, "ready_to_apply")
    row = get_application(job_id)
    assert row is not None
    assert row["status"] == "ready_to_apply"


def test_status_applied_schedules_followup(initialized_workspace) -> None:
    job_id = insert_job({"title": "Sample Job", "raw_text": "Sample", "description": "Sample"})
    update_status(job_id, "applied")
    row = get_application(job_id)
    assert row is not None
    assert row["status"] == "applied"
    assert row["applied_at"] is not None
    assert row["followup_at"] is not None
    applied_at = datetime.fromisoformat(row["applied_at"])
    followup_at = datetime.fromisoformat(row["followup_at"])
    assert followup_at - applied_at == timedelta(days=7)


def test_due_followups_lists_overdue_jobs(initialized_workspace, runner) -> None:
    job_id = insert_job({"title": "Follow Up Job", "raw_text": "Sample", "description": "Sample"})
    update_status(job_id, "applied")
    overdue = datetime.now(timezone.utc) - timedelta(days=1)
    set_followup_at(job_id, overdue.isoformat(timespec="seconds"))

    result = runner.invoke(app, ["due"])
    assert result.exit_code == 0
    assert "Due Follow-ups" in result.stdout
    assert "Follow Up Job" in result.stdout
