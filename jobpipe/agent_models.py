"""Version 1 agent contracts, independent of the MCP SDK."""
from __future__ import annotations

from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .classification import CANONICAL_ROLE_FAMILIES
from .eligibility import Assessment, EligibilityStatus

T = TypeVar("T")
Status = Literal["new", "saved", "packaged", "assessment", "analyzed", "ready_to_apply", "applied", "rejected", "interview", "offer", "ignored", "archived", "skipped"]
ApplicationStatus = Literal["saved", "analyzed", "ready_to_apply", "applied", "rejected", "interview", "offer", "ignored", "archived"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyInput(Input):
    pass


class IngestInput(Input):
    source_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)
    profile: str | None = Field(default=None, max_length=80)
    diagnostics: bool = False


class TrialInput(Input):
    source_id: str = Field(min_length=1, max_length=500)
    diagnostics: bool = False


class DiscoveryStatusInput(Input):
    run_id: int | None = Field(default=None, gt=0)


class Pagination(Input):
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results, from 1 to 100.")
    offset: int = Field(default=0, ge=0, description="Zero-based result offset.")


class JobInput(Input):
    job_id: int = Field(gt=0, description="ID of an existing canonical Job-Pipe job.")


class FitInput(JobInput):
    goal: str | None = Field(default=None, description="Existing goal name; overrides this request only.")


class SearchInput(Pagination):
    query: str | None = Field(default=None, max_length=1000, description="Case-insensitive substring of title, company, location, or description.")
    role_family: Literal[*CANONICAL_ROLE_FAMILIES] | None = None
    company: str | None = Field(default=None, max_length=500)
    remote: bool | None = Field(default=None, description="True for remote, false for nonremote, null for either.")
    min_fit_score: int = Field(default=0, ge=0, le=100)
    status: Status | None = None
    goal: str | None = None
    active_only: bool = True


class EligibilityInput(JobInput):
    applicant_profile: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class EligibleSearchInput(Pagination):
    query: str | None = Field(default=None, max_length=1000)
    role_family: Literal[*CANONICAL_ROLE_FAMILIES] | None = None
    remote_preference: Literal["remote_us", "remote", "any"] | None = None
    minimum_fit_score: int = Field(default=0, ge=0, le=100)
    eligibility_status: Literal["eligible", "needs_review", "ineligible", "all"] = "eligible"


class ApplicationsInput(Pagination):
    status: ApplicationStatus | None = None
    company: str | None = Field(default=None, max_length=500)
    date_from: date | None = Field(default=None, description="Inclusive UTC creation date, YYYY-MM-DD.")
    date_to: date | None = Field(default=None, description="Inclusive UTC creation date, YYYY-MM-DD.")
    due_followup: bool | None = None

    @model_validator(mode="after")
    def ordered_dates(self) -> ApplicationsInput:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class FollowupsInput(Pagination):
    due_only: bool = False


class SourceYieldInput(Pagination):
    source_id: str | None = Field(default=None, max_length=500)


class AgentError(BaseModel):
    code: Literal["invalid_arguments", "unknown_job", "missing_database", "invalid_database", "configuration_error", "invalid_state_transition", "permission_denied", "application_preparation_failure", "internal_error", "eligibility_unresolved", "unknown_source", "unknown_run", "operation_in_progress", "operation_timeout"]
    message: str


class Result(BaseModel, Generic[T]):
    schema_version: Literal[1] = 1
    ok: bool
    data: T | None = None
    error: AgentError | None = None

    @model_validator(mode="after")
    def consistent_result(self) -> Result[T]:
        if self.ok != (self.data is not None and self.error is None):
            raise ValueError("A success requires data; a failure requires an error.")
        if not self.ok and (self.error is None or self.data is not None):
            raise ValueError("A failure requires only an error.")
        return self


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
    next_offset: int | None


class Source(BaseModel):
    source_id: str | None
    provider: str


class JobState(BaseModel):
    job_id: int
    status: str
    review_status: str | None
    application_status: str | None
    workflow_bucket: str


class JobSummary(JobState):
    title: str
    company: str
    location: str
    remote: bool
    primary_role_family: str | None
    seniority: str | None
    fit_score: int
    source: Source
    application_url: str


class SearchResult(Page[JobSummary]):
    goal: str | None


class EligibleJobSummary(JobSummary):
    eligibility: Assessment


class EligibleSearchResult(Page[EligibleJobSummary]):
    goal: str | None


class JobEligibility(BaseModel):
    job_id: int
    technical_fit_score: int
    applicant_profile: str
    eligibility: Assessment


class IngestStarted(BaseModel):
    run_id: int
    status: Literal["running"]
    selected_sources: list[str]
    timeout_seconds: int
    next_action: str


class TrialJob(BaseModel):
    external_id: str
    title: str
    company: str
    location: str
    application_url: str
    eligibility: Assessment


class TrialResult(BaseModel):
    source_id: str
    configuration_origin: str
    profile: str
    goal: str | None
    status: Literal["success", "failed"]
    error: str | None
    fetched: int | None
    passed: int | None
    rejected: int | None
    yield_rate: float | None
    distribution_denominators: dict[str, str]
    role_family_distribution: dict[str, int]
    distribution_denominators: dict[str, str]
    seniority_distribution: dict[str, int]
    rejection_reasons: dict[str, int]
    eligibility_denominator: int | None
    eligibility_counts: dict[EligibilityStatus, int | None]
    representative_jobs: list[TrialJob]


class DiscoveryRun(BaseModel):
    id: int
    started_at: str
    completed_at: str | None
    profile: str
    goal: str | None
    status: str
    error: str | None


class CurrentOperation(BaseModel):
    run_id: int
    started_at: str


class DiscoverySourceResult(BaseModel):
    source_id: str
    fetch_status: str
    error: str | None
    fetched: int | None
    passed: int | None
    rejected: int | None
    inserted: int
    updated: int


class EmployerShare(BaseModel):
    company: str
    active_jobs: int
    share: float | None


class Concentration(BaseModel):
    active_jobs: int
    employers: int
    sources: int
    largest_employer_share: float | None
    warning: bool
    top_employers: list[EmployerShare]


class DiscoveryStatus(BaseModel):
    latest_ingest_timestamp: str | None
    enabled_sources: list[str]
    current_operations: list[CurrentOperation]
    run: DiscoveryRun | None
    sources_checked: int
    successful_sources: int
    failed_sources: int
    fetched: int | None
    passed: int | None
    inserted: int | None
    updated: int | None
    yield_rate: float | None
    measurement_scope: str
    source_results: list[DiscoverySourceResult]
    current_corpus: Concentration
    stale_source_warnings: list[str]


class Classification(BaseModel):
    primary_role_family: str
    secondary_role_families: list[str]
    seniority: str
    provenance: str
    role_evidence: list[str]
    seniority_evidence: list[str]


class Requirements(BaseModel):
    required_skills: list[str]
    preferred_skills: list[str]
    responsibilities: list[str]
    seniority: str
    concerns: list[str]
    experience: list[dict] = Field(default_factory=list)


class Application(BaseModel):
    id: int
    job_id: int
    status: ApplicationStatus
    applied_at: str | None
    followup_at: str | None
    notes: str | None
    created_at: str
    updated_at: str


class ApplicationItem(Application):
    title: str | None
    company: str | None


class Followup(ApplicationItem):
    due: bool


class CanonicalJob(BaseModel):
    id: int
    provider: str
    external_id: str
    source_id: str | None
    source_url: str | None
    title: str | None
    company: str | None
    location: str | None
    remote: int
    apply_url: str | None
    posted_at: str | None
    discovered_at: str
    fit_score: int | None
    status: str
    remote_type: str | None
    employment_type: str | None
    salary_text: str | None
    description: str | None
    raw_text: str | None
    created_at: str
    updated_at: str


class JobDetail(JobSummary):
    canonical: CanonicalJob
    classification: Classification | None
    requirements: Requirements
    description: str
    application: Application | None
    package_paths: dict[str, str]


class Contribution(BaseModel):
    category: str
    label: str
    amount: int


class FitBreakdown(BaseModel):
    job_id: int
    goal: str | None
    primary_role_family: str
    secondary_role_families: list[str]
    seniority: str | None
    contributions: list[Contribution]
    fact_evidence: list[str]
    pre_cap_score: int
    location_cap: int
    displayed_score: int
    notes: list[str]
    positive_signals: list[Contribution]
    negative_signals: list[Contribution]
    eligibility_concerns: list[str]
    caps: dict[str, int]
    classification: Classification | None


class GoalDefinition(BaseModel):
    name: str
    label: str
    description: str
    role_family_modifiers: dict[str, int]


class ActiveGoal(BaseModel):
    profile: str
    active_goal: str | None
    configuration_origin: str
    available_goals: list[GoalDefinition]


class IngestMeasurement(BaseModel):
    run_id: int | None
    measured_at: str | None
    fetch_status: str
    error: str | None
    fetched: int | None
    passed: int | None
    rejected: int | None
    yield_rate: float | None


class Corpus(BaseModel):
    total: int
    active: int
    saved: int
    packaged: int
    applied: int


class SourceYield(BaseModel):
    source_id: str
    ingest_measurement: IngestMeasurement
    current_corpus: Corpus


class IngestRun(BaseModel):
    id: int
    started_at: str
    completed_at: str
    profile: str
    goal: str | None
    status: str


class SourceYieldResult(Page[SourceYield]):
    latest_completed_run: IngestRun | None
    inferred_jobs: int
    unattributed_active: int


class SaveResult(JobState):
    changed: bool


class PreparationResult(JobState):
    package_directory: str
    resume_path: str
    cover_note_path: str
    fit_analysis_path: str
    application_instruction_path: str
    validation_concerns: list[str]
    reused: bool
