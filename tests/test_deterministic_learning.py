import json
import yaml
from pathlib import Path
import pytest
from jobpipe.db import init_database, insert_job, upsert_analysis, insert_feedback, list_feedback_with_analysis
from jobpipe.learn import compute_learned_weights, sync_weights, load_learned_weights
from jobpipe.score import score_job_fit_v1
from jobpipe.utils import db_path, data_dir

@pytest.fixture
def test_db(tmp_path):
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

def test_feedback_recording(test_db):
    job_id = insert_job({"title": "Test Job", "company": "Test Co", "provider": "test", "external_id": "123"})
    insert_feedback(job_id, "saved", reason_codes="strong_python", note="Great match")
    
    feedback = list_feedback_with_analysis()
    assert len(feedback) == 1
    assert feedback[0]["action"] == "saved"
    assert feedback[0]["reason_codes"] == "strong_python"

def test_learned_weight_generation(test_db, tmp_path):
    import jobpipe.learn
    original_weights_file = jobpipe.learn.WEIGHTS_FILE
    jobpipe.learn.WEIGHTS_FILE = tmp_path / "learned_weights.yaml"
    
    # Use 3 DIFFERENT jobs to test accumulation
    for i in range(3):
        job_id = insert_job({"title": f"Python Developer {i}", "company": f"AI Corp {i}", "external_id": f"ext_{i}"})
        upsert_analysis(job_id, {
            "role_type": "python_automation",
            "seniority_level": "mid",
            "fit_score": 50,
            "risk_score": 10,
            "quality_score": 80,
            "recommendation": "apply",
            "skip_reason": None,
            "matched_keywords": ["python", "api"],
            "missing_keywords": [],
            "red_flags": [],
            "eligibility_flags": [],
            "notes": ""
        })
        insert_feedback(job_id, "saved")
    
    weights = compute_learned_weights()
    assert weights["role_families"]["python_automation"] == 6 # 3 * 2
    assert weights["keywords"]["python"] == 3 # 3 * 1
    
    # Skip a 4th job
    job_id_4 = insert_job({"title": "Python Developer 4", "company": "AI Corp 4", "external_id": "ext_4"})
    upsert_analysis(job_id_4, {
        "role_type": "python_automation",
        "seniority_level": "mid",
        "fit_score": 50,
        "risk_score": 10,
        "quality_score": 80,
        "recommendation": "apply",
        "skip_reason": None,
        "matched_keywords": ["python", "api"],
        "missing_keywords": [],
        "red_flags": [],
        "eligibility_flags": [],
        "notes": ""
    })
    insert_feedback(job_id_4, "skipped")
    
    weights = compute_learned_weights()
    assert weights["role_families"]["python_automation"] == 4 # 6 - 2
    assert weights["keywords"]["python"] == 2 # 3 - 1
    
    jobpipe.learn.WEIGHTS_FILE = original_weights_file

def test_scoring_with_learned_adjustments():
    # Use a description that clearly matches backend_api and NOT python_automation
    job = {"title": "API Developer", "description": "We need an expert for microservices and API work."}
    
    # Base score without weights
    base_score, notes = score_job_fit_v1(job)
    
    weights = {
        "role_families": {"backend_api": 10},
        "keywords": {"api": 5}
    }
    
    # Keyword 'api' should be in keyword_hits
    adjusted_score, adjusted_notes = score_job_fit_v1(job, learned_weights=weights)
    
    # Check that adjustments are present in notes
    assert any("learned adjustment: role family 'backend_api'" in n for n in adjusted_notes)
    assert any("learned adjustment: keyword 'api'" in n for n in adjusted_notes)
    assert adjusted_score > base_score

def test_weight_capping(test_db):
    # Skip 20 DIFFERENT jobs to reach capping
    for i in range(20):
        job_id = insert_job({"title": f"Clerk {i}", "company": f"Help Desk {i}", "external_id": f"desk_{i}"})
        upsert_analysis(job_id, {
            "role_type": "help_desk",
            "seniority_level": "mid",
            "fit_score": 10,
            "risk_score": 10,
            "quality_score": 10,
            "recommendation": "skip",
            "skip_reason": "bad_role",
            "matched_keywords": [],
            "missing_keywords": [],
            "red_flags": [],
            "eligibility_flags": [],
            "notes": ""
        })
        insert_feedback(job_id, "skipped")
        
    weights = compute_learned_weights()
    assert weights["role_families"]["help_desk"] == -20 # Capped at -20
