"""Synthetic eligibility/readiness integration; no ATS requests."""
import hashlib
import json

import pytest

from jobpipe import applicant_profile as ap, db, services
from jobpipe.applicant_models import ApplicationAnswer, ApplicationReadiness, ApplicantReadiness, QuestionsResult
from jobpipe.application_package import application_package_paths
from jobpipe.eligibility import load_applicant
from jobpipe.readiness import get_applicant_readiness, get_application_answer, get_application_readiness, get_readiness_questions
from test_applicant_profile import applicant_workspace, canonical_applicant, attest, synthetic_job


def eligible_facts():
    for key, value in [("residence.country", "US"), ("work_authorization.US.authorized", True),
                       ("work_authorization.US.sponsorship_required", False), ("identity.name", "Synthetic Applicant"),
                       ("contact.email", "synthetic@example.invalid")]:
        attest(key, value)


def test_verified_residence_and_authorization_reassess_without_score_changes(canonical_applicant):
    job = synthetic_job()
    score = services.job_score(db.get_job(job))[0]
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    attest("residence.country", "US")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    eligible_facts()
    assessment = services.get_job_eligibility(job)["eligibility"]
    assert assessment["status"] == "eligible" and assessment["profile_revision"] == ap.state()["revision"]
    assert services.job_score(db.get_job(job))[0] == score
    assert db.get_job(job)["status"] == "saved"


def test_country_authorization_is_independent_and_explicit_negative(canonical_applicant):
    eligible_facts()
    attest("residence.country", "CA")
    job = synthetic_job(location="Remote - Canada")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    assert get_application_answer(job, "work_authorization", country="CA")["status"] == "unknown"
    attest("work_authorization.CA.authorized", False)
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "ineligible"


def test_projects_do_not_count_as_professional_years(canonical_applicant):
    eligible_facts()
    for key, value in [("projects.synthetic.name", "Synthetic project"), ("projects.synthetic.responsibilities", ["Python project since 2010"]),
                       ("employment.synthetic.employer", "Synthetic Employer"), ("employment.synthetic.start_date", "2020")]:
        attest(key, value)
    job = synthetic_job(description="Minimum 2 years of professional Python experience.")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    assert get_application_answer(job, "professional_experience")["status"] == "unknown"
    attest("qualifications.professional_experience_years", 3.0)
    attest("qualifications.python.professional_experience_years", 2.0)
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "eligible"
    answer = ApplicationAnswer.model_validate(get_application_answer(job, "professional_experience", experience_area="python"))
    assert answer.answer["years"] == 2.0 and answer.answer["includes_independent_projects"] is False


def test_historical_education_does_not_establish_current_enrollment(canonical_applicant):
    eligible_facts()
    attest("education.old.enrollment_status", "completed")
    attest("education.old.credential", "Synthetic bachelor degree")
    job = synthetic_job(title="Software Engineer Intern", description="Must be currently enrolled in university.")
    assert load_applicant()[0].enrolled is None
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    attest("education.current.enrollment_status", "enrolled")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "eligible"
    attest("education.enrolled", False)
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    attest("education.current.enrollment_status", "not_enrolled")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "ineligible"


def test_language_name_and_limited_proficiency_do_not_establish_fluency(canonical_applicant):
    eligible_facts()
    job = synthetic_job(description="Fluent Spanish is required.")
    attest("languages.spanish.name", "Spanish")
    attest("languages.spanish.proficiency", "limited_working")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "ineligible"
    assert not get_readiness_questions([job])["questions"]
    attest("languages.spanish.proficiency", None)
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    questions = get_readiness_questions([job])
    assert len(questions["questions"]) == 1 and "spanish" in questions["questions"][0]["question"].lower()
    attest("languages.spanish.proficiency", "fluent")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "eligible"
    assert not get_readiness_questions([job])["questions"]


def test_conflicts_withhold_conclusions_and_rejection_restores_them(canonical_applicant):
    eligible_facts()
    job = synthetic_job()
    proposal = ap.propose_applicant_facts([{"key": "residence.country", "value": "CA"}], ap.state()["revision"])
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    assert get_application_readiness(job)["submission_readiness"] == "blocked"
    ap.review_facts([], proposal["proposal_ids"], proposal["revision"])
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "eligible"


def test_questions_deduplicate_prioritize_count_and_persist(canonical_applicant):
    eligible_facts()
    jobs = [synthetic_job(title=f"Junior Software Engineer {i}", location="Remote - NY, United States") for i in range(3)]
    other = synthetic_job(description="Minimum 2 years of professional Python experience.")
    result = QuestionsResult.model_validate(get_readiness_questions([*jobs, other], limit=1))
    assert result.questions[0].question_id == "residence.state"
    assert result.questions[0].potentially_affected_jobs == 3
    assert result.questions[0].potentially_affected_job_ids == jobs
    assert result.total_questions > 1
    assert result.model_dump() == get_readiness_questions([*jobs, other], limit=1)
    attest("residence.state", "NY")
    assert all(q["question_id"] != "residence.state" for q in get_readiness_questions([*jobs, other])["questions"])
    assert services.get_job_eligibility(jobs[0])["eligibility"]["status"] == "eligible"
    assert ApplicantReadiness.model_validate(get_applicant_readiness(jobs)).status == "complete_for_assessed_requirements"


def test_employer_uncertainties_are_separate_and_search_is_bounded(canonical_applicant):
    eligible_facts()
    ids = [synthetic_job(title=f"Junior Software Engineer {i}", location="Remote") for i in range(3)]
    result = get_readiness_questions(job_limit=1)
    assert len(result["assessed_job_ids"]) == 1 and result["selection_truncated"]
    result = get_readiness_questions(ids)
    assert result["employer_questions"] and not result["questions"]
    with pytest.raises(ValueError):
        get_readiness_questions([ids[0], ids[0]])
    with pytest.raises(ValueError):
        get_readiness_questions(ids, limit=21)


@pytest.mark.parametrize("kind", ["us_work_authorization", "visa_sponsorship", "future_sponsorship_required", "current_residence_state", "bachelors_degree", "professional_experience_years"])
def test_answers_never_invent_missing_information(canonical_applicant, kind):
    job = synthetic_job()
    result = ApplicationAnswer.model_validate(get_application_answer(job, kind))
    assert result.status == "unknown" and result.answer is None


def test_answers_use_verified_evidence_and_explicit_negative(canonical_applicant):
    eligible_facts()
    job = synthetic_job()
    assert get_application_answer(job, "us_work_authorization")["answer"] is True
    assert get_application_answer(job, "visa_sponsorship")["answer"] is False
    assert get_application_answer(job, "invented_question")["status"] == "unsupported"
    attest("education.has_bachelors", False)
    assert get_application_answer(job, "bachelors_degree")["answer"] is False
    proposal = ap.propose_applicant_facts([{"key": "education.has_bachelors", "value": True}], ap.state()["revision"])
    result = get_application_answer(job, "bachelors_degree")
    assert result["status"] == "needs_review" and result["answer"] is None


def test_verified_package_and_blocked_draft_have_distinct_states(canonical_applicant):
    eligible_facts()
    eligible, draft = synthetic_job(), synthetic_job(location="Remote - NY, United States", title="Junior Backend Engineer")
    prepared = services.prepare_application(eligible, require_eligible=True)
    assert prepared["readiness"]["submission_readiness"] == "ready_for_human_review"
    with pytest.raises(services.ServiceError, match="confirmed eligibility"):
        services.prepare_application(draft, require_eligible=True)
    prepared_draft = services.prepare_application(draft, mode="draft_preparation", require_eligible=True)
    assert prepared_draft["readiness"]["package_status"] == "draft"
    assert prepared_draft["readiness"]["submission_readiness"] == "blocked"
    text = application_package_paths(dict(db.get_job(draft))).resume_tailored_md.read_text()
    assert "DRAFT" in text and "SyntheticPython" not in text
    for job in (eligible, draft):
        record = db.get_application(job)
        assert record["status"] == "saved" and record["applied_at"] is None
    assert not db.fetch_all("SELECT * FROM applications WHERE status='applied'")


def test_ineligible_draft_is_rejected_by_mcp_preparation_gate(canonical_applicant):
    eligible_facts()
    job = synthetic_job(location="Remote - NY, United States")
    attest("residence.state", "TX")
    for mode in ("ready_for_submission", "draft_preparation"):
        with pytest.raises(services.ServiceError):
            services.prepare_application(job, mode=mode, require_eligible=True)
    # Explicit local manual drafting is preserved, with a warning banner.
    result = services.prepare_application(job)
    assert result["mode"] == "draft_preparation" and result["readiness"]["submission_readiness"] == "blocked"


def test_package_staleness_and_user_edits_are_preserved(canonical_applicant):
    eligible_facts()
    job = synthetic_job()
    result = services.prepare_application(job, require_eligible=True)
    path = application_package_paths(dict(db.get_job(job))).resume_tailored_md
    path.write_text(path.read_text() + "\nSynthetic manual edit.\n")
    edited = path.read_bytes()
    assert get_application_readiness(job)["package_status"] == "edited"
    services.prepare_application(job, require_eligible=True)
    assert path.read_bytes() == edited
    attest("contact.phone", "+1-555-0100")
    assert get_application_readiness(job)["package_status"] == "stale"
    services.prepare_application(job, require_eligible=True)
    assert path.read_bytes() == edited


def test_canonical_profile_ignores_later_legacy_changes(canonical_applicant):
    eligible_facts()
    job = synthetic_job()
    (canonical_applicant / "data/profiles/candidate.yaml").write_text("malformed: [yaml")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "eligible"
    assert ApplicationReadiness.model_validate(get_application_readiness(job)).eligibility_status == "eligible"


def test_authorization_restrictions_require_employer_review(canonical_applicant):
    eligible_facts()
    attest("work_authorization.US.restrictions", ["Synthetic employer-specific restriction"])
    job = synthetic_job()
    assessment = services.get_job_eligibility(job)["eligibility"]
    assert assessment["status"] == "needs_review"
    assert assessment["missing_employer_information"]


def test_semantic_conflicts_block_inconsistent_degree_answers(canonical_applicant):
    eligible_facts()
    for key, value in [("education.has_bachelors", False), ("education.one.credential_type", "bachelor"), ("education.one.enrollment_status", "completed")]:
        attest(key, value)
    job = synthetic_job()
    assert "education.has_bachelors" in ap.get_applicant_profile(["education"])["conflicting_keys"]
    assert get_application_answer(job, "has_bachelors")["status"] == "needs_review"
    assert get_application_readiness(job)["submission_readiness"] == "blocked"


def test_existing_legacy_packet_is_never_overwritten(canonical_applicant, runner):
    eligible_facts()
    job = synthetic_job()
    from jobpipe.cli import app
    assert runner.invoke(app, ["analyze", str(job)]).exit_code == 0
    assert runner.invoke(app, ["packet", str(job)]).exit_code == 0
    path = next((canonical_applicant / "output/packets").glob("*.md"))
    path.write_text("Synthetic edited packet")
    result = runner.invoke(app, ["tailor", str(job)])
    assert result.exit_code != 0 and path.read_text() == "Synthetic edited packet"


def test_approximate_experience_retains_bounds_without_exact_answers(canonical_applicant):
    eligible_facts()
    attest("qualifications.professional_experience_years", {"minimum": 1.0, "maximum": 3.0, "approximate": True})
    job = synthetic_job(description="Minimum 2 years of professional experience.")
    assert services.get_job_eligibility(job)["eligibility"]["status"] == "needs_review"
    answer = get_application_answer(job, "professional_experience")
    assert answer["status"] == "needs_review" and answer["answer"]["range"]["maximum"] == 3.0
    assert "years" not in answer["answer"]


def test_empty_job_selection_does_not_hide_missing_profile_information(canonical_applicant, monkeypatch):
    result = get_applicant_readiness()
    assert result["status"] == "needs_review"
    assert "residence.country" in result["missing_facts"]
    assert not result["questions"]["assessed_job_ids"]
    original = get_readiness_questions
    def questions_then_local_confirmation(*args, **kwargs):
        questions = original(*args, **kwargs)
        attest("contact.phone", "+1-555-0100")
        return questions
    monkeypatch.setattr("jobpipe.readiness.get_readiness_questions", questions_then_local_confirmation)
    with pytest.raises(services.ServiceError, match="changed during readiness"):
        get_applicant_readiness()
