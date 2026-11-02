from __future__ import annotations

from .models import ParsedJob, RolePack


ROLE_ALIASES = {
    "helpdesk": "helpdesk",
    "technical_support": "technical_support",
    "junior_dev": "junior_dev",
    "customer_support": "customer_support",
}


def classify_job(job: ParsedJob, role_packs: dict[str, RolePack]) -> tuple[str, list[str], list[str]]:
    text = f"{job.title} {job.description}".lower()
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}

    for key, pack in role_packs.items():
        pack_matches: list[str] = []
        score = 0
        for word in pack.strong_keywords:
            if word.lower() in text:
                score += 8
                pack_matches.append(word)
        for word in pack.positive_keywords:
            if word.lower() in text:
                score += 3
                pack_matches.append(word)
        scores[key] = score
        matched[key] = sorted(set(pack_matches))

    if "sales" in text or "sdr" in text or "business development" in text:
        return "sales_sdr", [], []
    if "operations" in text or "administrative" in text:
        return "admin_ops", [], []

    best_role = max(scores, key=scores.get) if scores else "unknown"
    if scores.get(best_role, 0) <= 0:
        best_role = "unknown"

    pack = role_packs.get(best_role)
    missing: list[str] = []
    if pack:
        for skill_terms in pack.required_skill_groups.values():
            if not any(term.lower() in text for term in skill_terms):
                missing.append(skill_terms[0])
    return best_role, matched.get(best_role, []), missing


def detect_seniority(job: ParsedJob, risk_score: int = 0, risk_flags: list[str] | None = None) -> tuple[str, list[str]]:
    title = (job.title or "").lower()
    body = f"{job.title} {job.description} {getattr(job, 'employment_type', '')}".lower()
    flags: list[str] = []

    if _has_any(title, ["director", "head of"]):
        return "director", ["title indicates director-level role"]
    if _has_any(title, ["manager", "team lead", "supervise"]):
        return "manager", ["title indicates manager-level role"]
    if _has_any(title, ["senior", "sr."]):
        return "senior", ["title indicates senior-level role"]
    if _has_any(title, ["contract", "contractor", "c2h", "freelance", "consultant"]):
        return "contractor", ["title indicates contractor-level role"]

    if _has_any(title, ["junior", "jr."]):
        return "junior", ["title indicates junior-level role"]
    if _has_any(title, ["entry level", "entry-level"]):
        return "entry_level", ["title indicates entry-level role"]
    if _has_any(title, ["apprentice", "apprenticeship", "apprentices", "trainee"]):
        return "apprenticeship", ["title indicates apprenticeship-level role"]
    if any(pattern in title for pattern in [" analyst i", " specialist i", " technician i", " level 1", " tier 1"]):
        return "entry_level", ["title indicates entry-level role"]
    import re
    if re.search(r"\b(?:intern|internship|co-op)\b", title):
        return "internship", ["title indicates internship-level role"]

    if _has_any(body, ["director", "head of"]):
        flags.append("description indicates director-level scope")
        return "director", flags
    if _has_any(body, ["manager", "team lead", "supervise"]):
        flags.append("description indicates manager-level scope")
        return "manager", flags
    if _has_any(body, ["senior", "sr.", "3+ years", "5+ years", "7+ years", "enterprise experience", "administrator", "architect"]):
        flags.append("description indicates senior-level scope")
        return "senior", flags
    if _has_any(body, ["contract", "contractor", "c2h", "freelance", "consultant"]):
        flags.append("description indicates contractor-level scope")
        return "contractor", flags

    if _has_any(body, ["junior", "entry level", "entry-level", "level 1", "tier 1", "technician i", "analyst i", "specialist i"]):
        flags.append("description indicates entry-level scope")
        return "entry_level", flags

    if _has_any(body, ["apprentice", "apprenticeship", "apprentices", "trainee"]):
        flags.append("description indicates apprenticeship-level scope")
        return "apprenticeship", flags

    no_experience = "no experience required" in body or "no experience" in body
    risky_no_experience = no_experience and risk_score >= 75
    if no_experience and not risky_no_experience:
        flags.append("no experience requested")
        return "entry_level", flags
    if no_experience and risky_no_experience:
        flags.append("no experience requested but scam risk is high")
        return "unknown", flags

    return "mid", flags


def _has_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)
