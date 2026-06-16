from jobpipe.db import get_application, insert_job
from jobpipe.tracker import update_status


def test_status_updates(initialized_workspace) -> None:
    job_id = insert_job({"title": "Sample Job", "raw_text": "Sample", "description": "Sample"})
    update_status(job_id, "ready_to_apply")
    row = get_application(job_id)
    assert row is not None
    assert row["status"] == "ready_to_apply"
