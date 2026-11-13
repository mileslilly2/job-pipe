import pytest
from jobpipe.db import init_database, insert_job, list_feedback_with_analysis
from jobpipe.cli import _apply_flow_session, _batch_review_job
from unittest.mock import patch, MagicMock

@pytest.fixture
def test_db(tmp_path, monkeypatch):
    # Isolate profile reads and generated files as well as SQLite. A renderer
    # regression must never reach the real workspace through cwd-bound paths.
    monkeypatch.chdir(tmp_path)
    db_file = tmp_path / "test_jobpipe.db"
    import jobpipe.utils
    import jobpipe.db
    original_utils_db_path = jobpipe.utils.db_path
    original_db_db_path = jobpipe.db.db_path
    
    jobpipe.utils.db_path = lambda: db_file
    jobpipe.db.db_path = lambda: db_file
    
    init_database(db_file)
    yield db_file
    
    jobpipe.utils.db_path = original_utils_db_path
    jobpipe.db.db_path = original_db_db_path

def test_apply_flow_double_counting(test_db):
    job_id = insert_job({"title": "Test Job", "company": "Test Co", "provider": "test", "external_id": "123", "status": "saved", "source_url": "http://example.com"})
    
    # Mock inputs: "y" for mark applied
    with patch("builtins.input", side_effect=["y"]):
        with patch("jobpipe.cli.open_url", return_value=True):
            # We need to mock application_package_paths and build_application_package or ensure they work
            with patch("jobpipe.cli.build_application_package") as mock_build:
                mock_build.return_value = MagicMock(resume_tailored_md=MagicMock(exists=lambda: True),
                                                   cover_note_md=MagicMock(exists=lambda: True),
                                                   apply_md=MagicMock(exists=lambda: True))
                _apply_flow_session(job_id)
    
    feedback = list_feedback_with_analysis()
    # If run standalone, it should have 1: 'applied'
    assert len(feedback) == 1
    assert feedback[0]["action"] == "applied"

def test_feedback_prompts_handle_empty_input(test_db, initialized_workspace, runner):
    job_id = insert_job({"title": "Test Job", "company": "Test Co", "provider": "test", "external_id": "999", "status": "new"})
    job_row = {"id": job_id, "title": "Test Job", "company": "Test Co", "location": "Remote", "apply_url": "http://example.com", "status": "new"}
    
    # Mock inputs: "s" (save), then Enter for reason, then Enter for note
    with patch("builtins.input", side_effect=["s", "", ""]):
        from jobpipe.cli import _batch_review_job
        _batch_review_job(job_row)
    
    from jobpipe.db import list_feedback_with_analysis
    feedback = list_feedback_with_analysis()
    assert len(feedback) == 1
    assert feedback[0]["action"] == "saved"
    assert feedback[0]["reason_codes"] is None
    assert feedback[0]["note"] is None

def test_feedback_prompts_capture_input(test_db, initialized_workspace, runner):
    job_id = insert_job({"title": "Test Job", "company": "Test Co", "provider": "test", "external_id": "888", "status": "new"})
    job_row = {"id": job_id, "title": "Test Job", "company": "Test Co", "location": "Remote", "apply_url": "http://example.com", "status": "new"}
    
    # Mock inputs: "s", "strong_python", "Looks great"
    with patch("builtins.input", side_effect=["s", "strong_python", "Looks great"]):
        from jobpipe.cli import _batch_review_job
        _batch_review_job(job_row)
    
    from jobpipe.db import list_feedback_with_analysis
    feedback = list_feedback_with_analysis()
    assert len(feedback) == 1
    assert feedback[0]["action"] == "saved"
    assert feedback[0]["reason_codes"] == "strong_python"
    assert feedback[0]["note"] == "Looks great"
