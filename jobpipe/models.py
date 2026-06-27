from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ApplicationStatus = Literal[
    "saved",
    "analyzed",
    "ready_to_apply",
    "applied",
    "rejected",
    "interview",
    "offer",
    "ignored",
    "archived",
]

RecommendationType = Literal["apply", "maybe", "skip"]
SeniorityLevel = Literal[
    "entry_level",
    "junior",
    "mid",
    "senior",
    "manager",
    "director",
    "unknown",
]


class ProjectItem(BaseModel):
    name: str
    type: str
    technologies: list[str] = Field(default_factory=list)
    description: str
    bullets: list[str] = Field(default_factory=list)
    support_angles: list[str] = Field(default_factory=list)
    technical_angles: list[str] = Field(default_factory=list)
    proof_points: list[str] = Field(default_factory=list)


class ExperienceItem(BaseModel):
    title: str
    organization: str
    dates: str
    bullets: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    school: str
    degree: str
    dates: str


class CandidateProfile(BaseModel):
    profile: dict[str, Any]
    skills: dict[str, list[str]]
    projects: list[ProjectItem] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    constraints: dict[str, list[str]] = Field(default_factory=dict)


class RolePack(BaseModel):
    role_name: str
    description: str
    positive_keywords: list[str] = Field(default_factory=list)
    strong_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    seniority_exclusion_keywords: list[str] = Field(default_factory=list)
    required_skill_groups: dict[str, list[str]] = Field(default_factory=dict)
    nice_to_have_skill_groups: dict[str, list[str]] = Field(default_factory=dict)
    resume_angles: list[str] = Field(default_factory=list)
    common_tools: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    bullet_templates: list[str] = Field(default_factory=list)
    answer_angles: dict[str, list[str]] = Field(default_factory=dict)


class ParsedJob(BaseModel):
    source_url: str | None = None
    title: str = ""
    company: str = ""
    location: str = ""
    remote_type: str = ""
    employment_type: str = ""
    salary_text: str = ""
    description: str = ""
    raw_text: str = ""


class JobSource(BaseModel):
    provider: str
    company: str = ""
    company_slug: str = ""
    board_token: str | None = None
    account: str | None = None
    target_role_families: list[str] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    enabled: bool = True


class DiscoveredJob(BaseModel):
    provider: str
    external_id: str
    company: str
    title: str
    location: str = ""
    remote: bool = False
    apply_url: str = ""
    description: str = ""
    posted_at: str | None = None
    source_url: str | None = None


class JobAnalysis(BaseModel):
    role_type: str
    seniority_level: str
    fit_score: int
    risk_score: int
    quality_score: int
    recommendation: str = "maybe"
    skip_reason: str | None = None
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    eligibility_flags: list[str] = Field(default_factory=list)
    notes: str = ""


class TailoredBullet(BaseModel):
    text: str
    angle: str
    provenance: str
    support_note: str


class TailorResult(BaseModel):
    resume_summary: str
    selected_skills: list[str]
    tailored_bullets: list[TailoredBullet]
    short_answers: dict[str, str]
    cover_note: str
    resume_angle: str


class PacketContext(BaseModel):
    recommendation: str
    reason: str | None = None
    explanation: str
    concerns: list[str] = Field(default_factory=list)
