"""Vendor-independent applicant contracts and the bounded fact vocabulary."""
from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Verification = Literal["verified", "self_reported", "imported_unverified", "inferred", "unknown"]
Section = Literal["identity", "contact", "residence", "work_authorization", "employment", "projects", "skills", "education", "languages", "preferences", "qualifications"]
SECTIONS = ("identity", "contact", "residence", "work_authorization", "employment", "projects", "skills", "education", "languages", "preferences", "qualifications")
DEFAULT_SECTIONS = [s for s in SECTIONS if s not in {"identity", "contact"}]

SCALARS: dict[str, type] = {
    "identity.name": str, "identity.summary": str,
    "contact.email": str, "contact.phone": str, "contact.portfolio_url": str,
    "contact.github_url": str, "contact.linkedin_url": str, "contact.other_urls": list,
    "residence.country": str, "residence.state": str, "residence.location": str,
    "residence.commute_locations": list, "qualifications.citizenship_countries": list,
    "qualifications.professional_experience_years": float, "qualifications.credentials": list,
    "education.enrolled": bool, "education.institution_country": str,
    "education.graduation_year": int, "education.has_bachelors": bool,
    "preferences.allowed_employment_types": list, "preferences.restricted_employers": list,
    "preferences.dealbreakers": list, "preferences.avoid_claiming": list,
    "preferences.relocation_willing": bool, "preferences.hours_per_week": float,
    "preferences.compensation": str,
}
RECORD_FIELDS: dict[str, dict[str, type]] = {
    "work_authorization": {"authorized": bool, "sponsorship_required": bool, "future_sponsorship_required": bool, "restrictions": list},
    "employment": {"employer": str, "title": str, "employment_type": str, "start_date": str, "end_date": str, "current": bool, "responsibilities": list, "technologies": list},
    "projects": {"name": str, "description": str, "responsibilities": list, "technologies": list},
    "skills": {"name": str, "category": str, "experience_basis": list, "proficiency": str},
    "education": {"institution": str, "credential": str, "credential_type": str, "field": str, "start_date": str, "end_date": str, "graduation_date": str, "enrollment_status": str, "coursework": list, "country": str},
    "languages": {"name": str, "proficiency": str},
    "qualifications": {"professional_experience_years": float},
}


def fact_type(key: str) -> type:
    if key in SCALARS:
        return SCALARS[key]
    parts = key.split(".")
    if len(parts) == 3 and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", parts[1]):
        if parts[0] == "work_authorization" and not re.fullmatch(r"[A-Z]{2}", parts[1]):
            raise ValueError("Authorization keys require an ISO two-letter country code")
        expected = RECORD_FIELDS.get(parts[0], {}).get(parts[2])
        if expected:
            return expected
    raise ValueError("Unsupported applicant fact key")


def validate_fact(key: str, value: JsonValue) -> None:
    expected = fact_type(key)
    if value is None:
        return
    if expected is float:
        valid = type(value) in {float, int} and math.isfinite(value) and value >= 0
        if isinstance(value, dict) and key.endswith("professional_experience_years"):
            bounds = [value.get("minimum"), value.get("maximum")]
            valid = (set(value) <= {"minimum", "maximum", "approximate"} and any(v is not None for v in bounds)
                     and all(v is None or type(v) in {float, int} and math.isfinite(v) and v >= 0 for v in bounds)
                     and type(value.get("approximate", True)) is bool
                     and (None in bounds or bounds[0] <= bounds[1]))
    else:
        valid = type(value) is expected
    if not valid:
        raise ValueError("Fact value has the wrong type or range")
    if isinstance(value, str) and (not value.strip() or len(value) > 4000):
        raise ValueError("Text facts must contain 1–4000 characters")
    if isinstance(value, list) and (len(value) > 100 or any(type(v) is not str or not v.strip() or len(v) > 2000 for v in value)):
        raise ValueError("List facts must contain at most 100 nonempty strings")
    if key.endswith((".start_date", ".end_date", ".graduation_date")) and isinstance(value, str):
        from datetime import date
        if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
            raise ValueError("Use a year, YYYY-MM or ISO date; do not invent missing precision")
        date.fromisoformat(value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else ""))
    if key.endswith(".employment_type") and value not in {"full_time", "part_time", "contract", "freelance", "internship", "other"}:
        raise ValueError("Unsupported employment type; independent projects belong in projects")
    if key.startswith("languages.") and key.endswith(".proficiency") and value not in {"unknown", "basic", "limited_working", "professional_working", "fluent", "native"}:
        raise ValueError("Unsupported language proficiency")
    if key.endswith(".enrollment_status") and value not in {"enrolled", "not_enrolled", "completed", "unknown"}:
        raise ValueError("Unsupported enrollment status")
    if key.endswith(".experience_basis") and isinstance(value, list) and not set(value) <= {"professional", "project", "education", "certification", "self_reported"}:
        raise ValueError("Unsupported qualification experience basis")
    if key in {"residence.country", "education.institution_country"} and not re.fullmatch(r"[A-Z]{2}", str(value)):
        raise ValueError("Use an ISO two-letter country code")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FactProposal(StrictModel):
    key: str = Field(min_length=1, max_length=180)
    value: JsonValue
    source_reference: str | None = Field(default=None, max_length=500)
    explanation: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_fact(self) -> FactProposal:
        validate_fact(self.key, self.value)
        return self


class ProfileInput(StrictModel):
    sections: list[Section] = Field(default_factory=lambda: list(DEFAULT_SECTIONS), min_length=1, max_length=12)


class ProposeInput(StrictModel):
    facts: list[FactProposal] = Field(min_length=1, max_length=50)
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def unique_keys(self) -> ProposeInput:
        if len({fact.key for fact in self.facts}) != len(self.facts):
            raise ValueError("Use one proposal per fact key in a batch")
        return self


class QuestionInput(StrictModel):
    job_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    query: str | None = Field(default=None, max_length=1000)
    job_limit: int = Field(default=100, ge=1, le=100)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def valid_selection(self) -> QuestionInput:
        if self.job_ids is not None:
            if self.query is not None or any(type(v) is not int or v <= 0 for v in self.job_ids):
                raise ValueError("Select positive job IDs or a search query")
            if len(set(self.job_ids)) != len(self.job_ids):
                raise ValueError("Job IDs must be unique")
        return self


class AnswerInput(StrictModel):
    job_id: int = Field(gt=0)
    question_type: str = Field(min_length=1, max_length=80)
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    experience_area: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class FactView(StrictModel):
    key: str
    value: JsonValue
    verification_status: Verification
    source_type: str
    source_reference: str | None = None
    evidence: str
    confirmed_at: str | None = None
    modified_at: str
    revision_id: int | None = None
    review_state: str = "accepted"
    conflicts_with_current: bool = False


class ApplicantProfile(StrictModel):
    profile_id: str = "candidate"
    revision: int
    storage_mode: Literal["legacy", "canonical"]
    sections: list[Section]
    facts: list[FactView]
    proposals: list[FactView]
    conflicting_keys: list[str]
    missing_fields: list[str]
    preferences: dict[str, JsonValue] = Field(default_factory=dict)


class ProposalResult(StrictModel):
    revision: int
    proposal_ids: list[int]
    review_state: str = "pending"
    changed: bool


class ReadinessQuestion(StrictModel):
    question_id: str
    fact_keys: list[str]
    question: str
    potentially_affected_job_ids: list[int]
    potentially_affected_jobs: int
    blocking: bool
    evidence_strength: str


class QuestionsResult(StrictModel):
    profile_revision: int
    questions: list[ReadinessQuestion]
    total_questions: int
    assessed_job_ids: list[int]
    selection_truncated: bool
    employer_questions: dict[str, list[str]]


class ApplicationReadiness(StrictModel):
    job_id: int
    profile_revision: int
    eligibility_status: Literal["eligible", "ineligible", "needs_review"]
    package_status: Literal["not_prepared", "prepared", "draft", "stale", "legacy", "edited", "incomplete"]
    submission_readiness: Literal["blocked", "ready_for_human_review"]
    blocking_facts: list[str]
    missing_facts: list[str]
    conflicting_facts: list[str]
    employer_uncertainties: list[str]
    next_action: str
    scope: str = "Stated posting requirements and local materials only; no submission or complete employer form validation."


class ApplicantReadiness(StrictModel):
    profile_revision: int
    status: Literal["needs_setup", "needs_review", "complete_for_assessed_requirements"]
    verified_fact_keys: list[str]
    missing_facts: list[str]
    conflicting_facts: list[str]
    eligibility_blockers: dict[str, list[str]]
    affected_jobs: list[int]
    questions: QuestionsResult


class ApplicationAnswer(StrictModel):
    job_id: int
    question_type: str
    question: str
    status: Literal["answerable", "unknown", "needs_review", "unsupported"]
    answer: JsonValue = None
    fact_keys: list[str]
    provenance: list[FactView]
    profile_revision: int
    source: str = "verified_applicant_profile"
