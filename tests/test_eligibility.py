from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from jobpipe.eligibility import ApplicantFacts, assess_eligibility, load_applicant
from jobpipe.experience import extract_experience_requirements
from jobpipe.facts import extract_experience_min_years
from jobpipe.job_requirements import extract_job_requirements
from jobpipe.models import SearchProfile


def fact(value, verified=True):
    return {"value": value, "verified": verified, "evidence": "Synthetic verified test fact"}


def applicant(**updates):
    return ApplicantFacts.model_validate({
        "residence_country": fact("US"), "residence_state": fact("NY"),
        "professional_experience_years": fact(2.0),
        "work_authorizations": [{"country": "US", "authorized": True, "sponsorship_required": False,
                                 "verified": True, "evidence": "Synthetic authorization"}], **updates})


def assess(location="Remote - US", description="Python APIs and automated testing.", facts=None, title="Junior Software Engineer", **fields):
    return assess_eligibility({"title": title, "company": "Fixture", "location": location,
                               "description": description, **fields}, facts or applicant(),
                              SearchProfile(name="fixture", preferred_locations=["remote us"],
                                            allowed_locations=["canada"], preferred_work_styles=["remote", "hybrid"]))


@pytest.mark.parametrize("expression,minimum,maximum,preferred,upper_open", [
    ("2–12+ years", 2, 12, False, True), ("2-5 years", 2, 5, False, False),
    ("3+ years", 3, None, False, True), ("At least 2 years", 2, None, False, False),
    ("Minimum 3 years", 3, None, False, False), ("5 years preferred", 5, None, True, False),
    ("1-3 years of experience", 1, 3, False, False),
    ("Up to 2 years of experience", None, 2, False, False),
])
def test_experience_expressions(expression, minimum, maximum, preferred, upper_open):
    requirements = extract_experience_requirements(expression)
    assert len(requirements) == 1
    r = requirements[0]
    assert (r.minimum, r.maximum, r.preferred, r.upper_open) == (minimum, maximum, preferred, upper_open)
    assert r.evidence == expression
    if expression == "2–12+ years":
        assert r.ambiguous and extract_experience_min_years(expression) == 2
    if preferred:
        assert extract_experience_min_years(expression) is None


def test_preferred_sections_and_alternatives_remain_explicit():
    text = "Requirements:\nAt least 2 years of experience.\nPreferred qualifications:\n5 years of experience."
    assert extract_experience_min_years(text) == 2
    assert extract_experience_requirements(text)[1].preferred
    assert extract_experience_requirements("2 years experience or equivalent training")[0].ambiguous


def test_new_graduate_role_is_not_an_internship():
    from jobpipe.classification import classify_seniority
    assert classify_seniority("New Grad Software Engineer")[0] == "entry_level"
    assert classify_seniority("Senior Engineer, Internship Platform")[0] == "senior"
    assert extract_job_requirements({"title": "New Grad Software Engineer", "description": "Python API"}).seniority == "entry"


@pytest.mark.parametrize("title,seniority", [("Senior Software Engineer", "senior"),
    ("Junior Software Engineer", "junior"), ("Software Engineering Intern", "internship"),
    ("Software Engineer", "senior")])
def test_requirement_seniority_does_not_leak_from_internship_program(title, seniority):
    job = {"title": title, "description": "Company benefits include an internship program. Minimum 6 years of professional experience."}
    result = extract_job_requirements(job)
    assert result.seniority == seniority
    assert result.experience


@pytest.mark.parametrize("location,description,status,scope", [
    ("Remote - United States", "Python APIs.", "eligible", "remote_us"),
    ("Remote anywhere worldwide", "Python APIs.", "eligible", "remote_anywhere"),
    ("Remote - Canada", "Python APIs.", "ineligible", "remote_selected_countries"),
    ("Remote - NY, CA, United States", "Python APIs.", "eligible", "remote_us_states"),
    ("Remote - TX, United States", "Python APIs.", "ineligible", "remote_us_states"),
    ("New York City - Hybrid", "Python APIs.", "needs_review", "hybrid"),
    ("Paris, France", "We provide a remote-work equipment stipend.", "ineligible", "onsite"),
    ("Toronto, Canada - Hybrid", "Python APIs.", "needs_review", "hybrid"),
    ("São Paulo, Brazil", "Python APIs.", "ineligible", "onsite"),
    ("Office Based - Taipei, Taiwan", "Python APIs.", "ineligible", "onsite"),
    ("Office Based - Beijing, China", "Python APIs.", "ineligible", "onsite"),
    ("Rome", "Python APIs.", "ineligible", "onsite"),
    ("Remote", "Python APIs.", "needs_review", "ambiguous"),
    ("", "Join us and enjoy remote benefits.", "needs_review", "ambiguous"),
])
def test_location_cases(location, description, status, scope):
    result = assess(location, description)
    assert result.status == status
    assert result.location_scope == scope
    assert all(check.evidence or check.concerns for check in result.checks.values())


def test_state_exclusions_and_remote_conflicts_require_review():
    result = assess("Remote - United States excluding CA")
    assert result.checks["location"].status == "needs_review"
    assert assess("Hybrid - US", "This role is fully remote.").status == "needs_review"


def test_ambiguous_country_state_abbreviation_is_not_us_eligibility():
    facts = applicant(residence_state=fact("CA"))
    assert assess("Remote - CA", facts=facts).status == "needs_review"
    assert assess("Remote - Canada (CA)", facts=facts).status == "ineligible"


def test_residence_authorization_and_commute_are_independent():
    assert assess(facts=applicant(residence_country=fact("US", False))).status == "needs_review"
    assert assess(facts=applicant(work_authorizations=[])).checks["work_authorization"].status == "needs_review"
    result = assess("New York City - Hybrid", facts=applicant(commute_locations=fact(["New York City"])))
    assert result.status == "eligible"
    assert assess("Remote - Canada", facts=applicant(residence_country=fact("CA"))).checks["work_authorization"].status == "needs_review"


def test_authorization_restrictions():
    facts = applicant(work_authorizations=[{"country": "US", "authorized": True, "sponsorship_required": True,
        "verified": True, "evidence": "Synthetic sponsored authorization"}])
    assert assess(description="We cannot provide visa sponsorship.", facts=facts).status == "ineligible"
    assert assess(description="Must have work authorization in Canada.").status == "needs_review"
    assert assess(description="US citizens only.").status == "needs_review"
    assert assess(description="US citizens only.", facts=applicant(citizenship_countries=fact(["US"]))).status == "eligible"


@pytest.mark.parametrize("requirement,years,status", [
    ("Minimum 3 years professional experience", 2.0, "ineligible"),
    ("At least 2 years professional experience", 2.0, "eligible"),
    ("5 years preferred", 2.0, "eligible"),
    ("1-3 years of experience", 2.0, "eligible"),
    ("2–12+ years", 2.0, "needs_review"),
    ("Minimum 2 years professional experience", None, "needs_review"),
])
def test_experience_eligibility(requirement, years, status):
    facts = applicant(professional_experience_years=fact(years) if years is not None else None)
    assert assess(description=requirement, facts=facts).status == status


def test_projects_cannot_establish_employment_or_domain_experience():
    facts = applicant(professional_experience_years=None)
    assert assess(description="Minimum 2 years experience", facts=facts).status == "needs_review"
    assert assess(description="Minimum 2 years Python experience").status == "needs_review"
    facts = applicant(experience_by_area={"python": fact(2.0)})
    assert assess(description="Minimum 2 years Python experience", facts=facts).status == "eligible"


def test_internship_enrollment_country_and_graduation():
    description = "Must be currently enrolled at a Spanish university and graduate in 2027."
    result = assess("Madrid, Spain", description, title="Software Engineering Intern")
    assert result.status == "ineligible" and result.checks["internship"].status == "needs_review"
    facts = applicant(enrolled=fact(False))
    assert assess(description=description, title="Intern", facts=facts).checks["internship"].status == "ineligible"
    facts = applicant(enrolled=fact(True), institution_country=fact("ES"), graduation_year=fact(2027))
    assert assess(description=description, title="Intern", facts=facts).checks["internship"].status == "eligible"


def test_employment_type_and_missing_description():
    result = assess_eligibility({"title": "Unpaid intern", "location": "Remote US", "description": "Python."},
        applicant(), SearchProfile(name="test"), {"constraints": {"dealbreakers": ["unpaid"]}})
    assert result.checks["employment_type"].status == "ineligible"
    assert assess(description="").status == "needs_review"


def test_future_pool_is_not_a_current_ready_opportunity():
    result = assess(title="QA Tester (Future Roles)")
    assert result.status == "needs_review"
    assert result.checks["posting_availability"].status == "needs_review"


def test_title_country_and_language_requirements_preserve_unknowns():
    result = assess("Remote", title="AI Evaluation Specialist (USA)", remote_type="remote")
    assert result.status == "eligible" and result.location_scope == "remote_us"
    assert assess("Remote", title="AI Evaluation Specialist (Italy)", remote_type="remote").status == "ineligible"
    result = assess("Remote", description="Native-level fluency in Chinese Simplified.", title="AI Evaluation Specialist (USA)", remote_type="remote")
    assert result.status == "needs_review" and result.checks["language_proficiency"].concerns
    native = applicant(native_languages=fact(["Chinese Simplified"]))
    assert assess(description="Native-level fluency in Chinese Simplified.", facts=native).status == "eligible"


def test_unlisted_language_or_credential_is_unknown_not_negative():
    assert assess(description="Must be fluent in Spanish.", facts=applicant(languages=fact(["English"]))).status == "needs_review"
    assert assess(description="A master degree is required.", facts=applicant(credentials=fact(["Bachelor degree"]))).status == "needs_review"


def test_legacy_profile_unknown_and_named_profile_boundary(initialized_workspace):
    facts, _ = load_applicant()
    assert not facts.work_authorizations
    assert assess(facts=facts).status == "needs_review"
    with pytest.raises(ValueError):
        load_applicant("../candidate")
    path = Path("data/profiles/candidate.yaml")
    raw = yaml.safe_load(path.read_text())
    raw["eligibility"] = {"residence_country": {"value": "US", "verified": True}}
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_applicant()
