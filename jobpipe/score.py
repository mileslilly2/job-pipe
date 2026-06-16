from __future__ import annotations

import re

from .models import CandidateProfile, ParsedJob, RolePack


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
    if seniority == "senior":
        score -= 25
        notes.append("seniority mismatch")
    elif seniority == "entry_level":
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
