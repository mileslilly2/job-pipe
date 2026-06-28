from __future__ import annotations

import re
from typing import Any

from .facts import ROLE_FAMILY_SCORES, build_fact_report, classify_role_family as _classify_role_family, extract_job_facts
from .models import CandidateProfile, ParsedJob, RolePack, SearchProfile


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

classify_role_family = _classify_role_family


def score_fit(
    job: ParsedJob,
    role_type: str,
    seniority: str,
    pack: RolePack | None,
    candidate: CandidateProfile,
    matched_keywords: list[str],
    profile: SearchProfile | None = None,
) -> tuple[int, list[str]]:
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

    fact_report = build_fact_report(job, profile) if profile is not None else None
    if fact_report is not None:
        score += fact_report.score_adjustment
        notes.extend(fact_report.notes)

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


def score_job_fit_v1(job: Any, learned_weights: dict[str, Any] | None = None, profile: SearchProfile | None = None) -> tuple[int, list[str]]:
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

    family, family_matches, family_reason = _classify_role_family(job)
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

    job_facts = extract_job_facts(job)
    if job_facts.seniority in {"junior", "entry", "internship", "apprenticeship"}:
        bonus = 6 if job_facts.seniority in {"entry", "internship"} else 4
        score += bonus
        notes.append(f"+{bonus} boost: {job_facts.seniority}")
    if job_facts.seniority in {"senior", "staff", "principal", "manager"}:
        penalty = 10 if job_facts.seniority == "senior" else 14
        score -= penalty
        notes.append(f"-{penalty} penalty: seniority signal '{job_facts.seniority}'")

    if remote_flag or _has_phrase(text, "remote"):
        score += 5
        notes.append("+5 boost: remote-friendly")
    elif "hybrid" in text:
        score += 2
        notes.append("+2 boost: hybrid-friendly")

    if profile:
        profile_target_families = {value.strip().lower() for value in profile.target_role_families if value and value.strip()}
        if family in profile_target_families:
            score += 8
            notes.append("+8 profile boost: target role family")

        profile_positive_matches = [keyword for keyword in profile.positive_keywords if _has_phrase(text, keyword)]
        if profile_positive_matches:
            score += min(len(profile_positive_matches) * 3, 18)
            for keyword in profile_positive_matches:
                notes.append(f"+3 profile keyword match: {keyword}")

        profile_negative_matches = [keyword for keyword in profile.negative_keywords if _has_phrase(text, keyword)]
        if profile_negative_matches:
            penalty = min(len(profile_negative_matches) * 6, 24)
            score -= penalty
            for keyword in profile_negative_matches:
                notes.append(f"-{min(6, penalty)} profile negative keyword: {keyword}")

        report = build_fact_report(job, profile)
        if report.notes:
            notes.extend(report.notes)
        score += report.score_adjustment

        if job_facts.remote and "remote" in {value.strip().lower() for value in profile.preferred_work_styles if value and value.strip()}:
            score += 4
            notes.append("+4 profile boost: remote work style")
        elif not job_facts.remote and "remote" in {value.strip().lower() for value in profile.preferred_work_styles if value and value.strip()}:
            score -= 3
            notes.append("-3 profile penalty: remote work style")

        preferred_levels = {value.strip().lower() for value in profile.preferred_role_levels if value and value.strip()}
        excluded_levels = {value.strip().lower() for value in profile.excluded_levels if value and value.strip()}
        if job_facts.seniority in preferred_levels:
            score += 6
            notes.append(f"+6 profile boost: preferred role level {job_facts.seniority}")
        if job_facts.seniority in excluded_levels:
            score -= 30
            notes.append(f"-30 profile penalty: excluded role level {job_facts.seniority}")

    # Learned adjustments
    if learned_weights:
        role_adjustments = learned_weights.get("role_families", {})
        if family in role_adjustments:
            adj = role_adjustments[family]
            score += adj
            notes.append(f"{adj:+d} learned adjustment: role family '{family}'")

        keyword_adjustments = learned_weights.get("keywords", {})
        for kw in keyword_hits:
            if kw in keyword_adjustments:
                adj = keyword_adjustments[kw]
                score += adj
                notes.append(f"{adj:+d} learned adjustment: keyword '{kw}'")

    score = max(0, min(score, 100))
    notes.append(f"final score: {score}")
    return score, notes


def _field(job: Any, name: str) -> str:
    if isinstance(job, dict):
        return str(job.get(name) or "")
    return str(getattr(job, name, "") or "")


def _remote_flag(job: Any) -> bool:
    if isinstance(job, dict):
        return bool(job.get("remote"))
    return bool(getattr(job, "remote", False) or getattr(job, "remote_type", "") == "remote")


def _has_programming_signals(text: str) -> bool:
    return any(_has_phrase(text, phrase) for phrase in PROGRAMMING_TERMS)


def _has_phrase(text: str, phrase: str) -> bool:
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return re.search(pattern, text) is not None
