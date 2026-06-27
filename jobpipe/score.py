from __future__ import annotations

import re
from typing import Any

from .models import CandidateProfile, ParsedJob, RolePack


RESUME_KEYWORDS = [
    "python",
    "data engineering",
    "automation",
    "sql",
    "etl",
    "backend",
    "ai evaluation",
    "react",
    "api",
]

ROLE_FAMILY_SCORES = {
    "data_engineer": 44,
    "python_automation": 40,
    "backend_api": 38,
    "software_engineer": 34,
    "junior_software_engineer": 36,
    "ai_evaluation": 36,
    "qa_automation": 36,
    "technical_operations": 26,
    "help_desk": -38,
    "customer_support": -38,
    "it_support": -36,
    "sales": -40,
    "clinical_admin": -34,
    "manager": -44,
    "unknown": 0,
}

NEGATIVE_ROLE_FAMILY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("help_desk", ("help desk", "helpdesk", "service desk", "desk support")),
    ("customer_support", ("customer support", "customer success", "client support", "customer care")),
    ("it_support", ("it support", "technical support", "support engineer", "desktop support", "systems support")),
    ("sales", ("business development", "account executive", "sales", "sdr", "bdr", "solutions consultant")),
    ("clinical_admin", ("clinical", "nurse", "patient", "medical assistant", "healthcare", "administrative")),
    ("manager", ("engineering manager", "product manager", "project manager", "program manager", "manager", "director", "head of")),
]

POSITIVE_ROLE_FAMILY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("data_engineer", ("data engineer", "analytics engineer", "data platform", "data pipeline", "etl", "airflow", "dbt", "warehouse")),
    ("ai_evaluation", ("ai evaluation", "model evaluation", "llm evaluation", "evals", "evaluation engineer")),
    ("qa_automation", ("qa automation", "test automation", "sdet", "quality engineer", "selenium", "playwright", "cypress", "pytest")),
    ("technical_operations", ("devops engineer", "site reliability engineer", "sre", "platform operations", "release engineer", "systems engineer", "technical operations", "ops engineer", "infrastructure engineer", "technical operations engineer")),
    ("python_automation", ("python automation", "automation engineer", "workflow automation", "process automation", "python developer", "scripting")),
    ("junior_software_engineer", ("junior software engineer", "software engineer i", "software engineer 1", "entry level software engineer", "associate software engineer", "junior developer", "new grad software engineer")),
    ("software_engineer", ("software engineer", "software developer", "full stack engineer", "application engineer", "application developer")),
    ("backend_api", ("backend engineer", "backend developer", "backend", "api", "microservices", "services", "platform engineer")),
]

TECHNICAL_OPS_TERMS = (
    "devops",
    "site reliability",
    "sre",
    "platform operations",
    "release engineer",
    "systems engineer",
    "technical operations",
    "ops engineer",
    "infrastructure engineer",
)

PROGRAMMING_TERMS = ("python", "automation", "scripting", "cli", "pipeline", "pipelines", "sql", "api", "testing", "terraform", "bash", "git")


def score_fit(job: ParsedJob, role_type: str, seniority: str, pack: RolePack | None, candidate: CandidateProfile, matched_keywords: list[str]) -> tuple[int, list[str]]:
    text = f"{job.title} {job.description}".lower()
    score = 20
    notes: list[str] = []
    score += min(len(matched_keywords) * 4, 24)

    if pack:
        required_hits = 0
        for name, terms in pack.required_skill_groups.items():
            if any(term.lower() in text for term in terms):
                required_hits += 1
                notes.append(f"matched skill group: {name}")
        if pack.required_skill_groups:
            score += int(required_hits / max(len(pack.required_skill_groups), 1) * 25)

        tool_hits = [tool for tool in pack.common_tools if tool.lower() in text]
        score += min(len(tool_hits) * 2, 8)

    profile_blob = " ".join(
        candidate.profile.get("target_roles", [])
        + [item for values in candidate.skills.values() for item in values]
        + [project.description for project in candidate.projects]
        + [bullet for project in candidate.projects for bullet in project.bullets]
    ).lower()
    relevance_terms = [term for term in text.split() if len(term) > 4 and term in profile_blob]
    score += min(len(set(relevance_terms)), 18)

    preferred_roles = " ".join(candidate.constraints.get("preferred_roles", [])).lower()
    if "remote" in preferred_roles and job.remote_type == "remote":
        score += 5
    if seniority in {"senior", "manager", "director"}:
        score -= 60
        notes.append("seniority mismatch")
    elif seniority in {"entry_level", "junior"}:
        score += 6
    if role_type == "unknown":
        score -= 15
    return max(0, min(score, 100)), notes


def score_risk(job: ParsedJob, pack: RolePack | None) -> tuple[int, list[str]]:
    text = f"{job.title} {job.description} {job.source_url or ''}".lower()
    flags: list[str] = []
    score = 5

    checks = {
        "Telegram interview": (["telegram"], 25),
        "WhatsApp interview": (["whatsapp"], 25),
        "deposit a check": (["deposit a check", "send a check"], 35),
        "buy equipment": (["buy equipment", "purchase equipment"], 35),
        "commission only": (["commission only", "commission-only"], 30),
        "door to door": (["door to door"], 20),
        "training fee": (["training fee"], 30),
        "chat only": (["chat only", "no phone calls"], 15),
        "flexible schedule": (["flexible schedule"], 5),
    }
    for label, (phrases, weight) in checks.items():
        if any(phrase in text for phrase in phrases):
            score += weight
            flags.append(label)

    if pack:
        for phrase in pack.red_flags:
            if phrase.lower() in text:
                score += 12
                flags.append(phrase)

    if re.search(r"\$\d{2,3},?\d{3}\+?.*no experience", text):
        score += 20
        flags.append("high pay plus no experience")
    if "no experience" in text and len(flags) >= 2:
        score += 8
    if not any(term in text for term in ["product", "platform", "software", "company", "team", "customers"]):
        score += 10
        flags.append("no clear product or service described")
    if "http" in text and any(bad in text for bad in [".xyz", ".click", ".top"]):
        score += 20
        flags.append("strange domain")
    return max(0, min(score, 100)), sorted(set(flags), key=str.lower)


def score_quality(job: ParsedJob, role_type: str, seniority: str) -> tuple[int, list[str]]:
    text = f"{job.title} {job.description}".lower()
    score = 20
    notes: list[str] = []

    if job.company:
        score += 12
    else:
        notes.append("missing company name")
    if job.salary_text:
        score += 8
    if any(term in text for term in ["responsibilities", "requirements", "about the role", "what you'll do"]):
        score += 20
    if any(term in text for term in ["benefits", "compensation", "salary"]):
        score += 10
    if role_type != "unknown":
        score += 12
    if seniority == "senior":
        score -= 8
    if len(job.description.split()) > 80:
        score += 10
    if any(term in text for term in ["team", "product", "customers", "platform"]):
        score += 10
    return max(0, min(score, 100)), notes


def score_job_fit_v1(job: Any) -> tuple[int, list[str]]:
    text = " ".join(
        str(value or "")
        for value in [
            _field(job, "title"),
            _field(job, "company"),
            _field(job, "location"),
            _field(job, "description"),
        ]
    ).lower()
    remote_flag = _remote_flag(job)
    score = 10
    notes: list[str] = ["base score: 10"]

    family, family_matches, family_reason = _classify_role_family(text)
    family_score = ROLE_FAMILY_SCORES.get(family, 0)
    score += family_score
    notes.append(f"role family: {family} ({family_score:+d})")
    if family_matches:
        notes.append("matched role signals: " + ", ".join(family_matches))
    if family_reason:
        notes.append(family_reason)

    keyword_hits: list[str] = []
    for keyword in RESUME_KEYWORDS:
        if _has_phrase(text, keyword):
            keyword_hits.append(keyword)
    if keyword_hits:
        score += min(len(keyword_hits) * 4, 20)
        for keyword in keyword_hits:
            notes.append(f"+4 keyword match: {keyword}")

    if _has_phrase(text, "junior"):
        score += 4
        notes.append("+4 boost: junior")
    if _has_phrase(text, "entry level"):
        score += 6
        notes.append("+6 boost: entry level")

    if remote_flag or _has_phrase(text, "remote"):
        score += 5
        notes.append("+5 boost: remote-friendly")
    elif "hybrid" in text:
        score += 2
        notes.append("+2 boost: hybrid-friendly")

    for phrase, penalty in {
        "senior": 10,
        "staff": 14,
        "principal": 14,
        "director": 12,
    }.items():
        if _has_phrase(text, phrase):
            score -= penalty
            notes.append(f"-{penalty} penalty: seniority signal '{phrase}'")

    if remote_flag and not _has_phrase(text, "remote"):
        score += 2
        notes.append("+2 remote flag from structured source")

    score = max(0, min(score, 100))
    notes.append(f"final score: {score}")
    return score, notes


def classify_role_family(job: Any) -> tuple[str, list[str], str | None]:
    text = " ".join(
        str(value or "")
        for value in [
            _field(job, "title"),
            _field(job, "company"),
            _field(job, "location"),
            _field(job, "description"),
        ]
    ).lower()
    return _classify_role_family(text)


def _field(job: Any, name: str) -> str:
    if isinstance(job, dict):
        return str(job.get(name) or "")
    return str(getattr(job, name, "") or "")


def _remote_flag(job: Any) -> bool:
    if isinstance(job, dict):
        return bool(job.get("remote"))
    return bool(getattr(job, "remote", False) or getattr(job, "remote_type", "") == "remote")


def _classify_role_family(text: str) -> tuple[str, list[str], str | None]:
    for family, phrases in NEGATIVE_ROLE_FAMILY_RULES:
        matched = [phrase for phrase in phrases if _has_phrase(text, phrase)]
        if matched:
            return family, matched, None

    for family, phrases in POSITIVE_ROLE_FAMILY_RULES:
        matched = [phrase for phrase in phrases if _has_phrase(text, phrase)]
        if not matched:
            continue
        if family == "technical_operations" and not _has_programming_signals(text):
            return "unknown", [], "technical operations signals ignored: not programming-heavy"
        return family, matched, None

    if any(_has_phrase(text, phrase) for phrase in TECHNICAL_OPS_TERMS):
        return "unknown", [], "technical operations signals ignored: not programming-heavy"

    return "unknown", [], None


def _has_programming_signals(text: str) -> bool:
    return any(_has_phrase(text, phrase) for phrase in PROGRAMMING_TERMS)


def _has_phrase(text: str, phrase: str) -> bool:
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return re.search(pattern, text) is not None
