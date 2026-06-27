from pathlib import Path

from jobpipe.cli import app
from jobpipe.db import get_job


def test_edit_updates_job_fields(initialized_workspace, runner) -> None:
    fixture = Path(__file__).parent / "fixtures" / "helpdesk_clean.txt"
    assert runner.invoke(app, ["add-text", "--file", str(fixture)]).exit_code == 0

    result = runner.invoke(app, ["edit", "1", "--title", "Edited Title", "--company", "Edited Co"])
    assert result.exit_code == 0

    row = get_job(1)
    assert row is not None
    assert row["title"] == "Edited Title"
    assert row["company"] == "Edited Co"
