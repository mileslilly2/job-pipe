from __future__ import annotations

from .models import ParsedJob, RolePack


ROLE_ALIASES = {
    "helpdesk": "helpdesk",
    "technical_support": "technical_support",
    "junior_dev": "junior_dev",
    "customer_support": "customer_support",
}


def classify_job(job: ParsedJob, role_packs: dict[str, RolePack]) -> tuple[str, str, list[str], list[str]]:
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
        for word in pack.negative_keywords:
            if word.lower() in text:
                score -= 4
        scores[key] = score
        matched[key] = sorted(set(pack_matches))

    if "sales" in text or "sdr" in text or "business development" in text:
        return "sales_sdr", detect_seniority(text), [], []
    if "operations" in text or "administrative" in text:
        return "admin_ops", detect_seniority(text), [], []

    best_role = max(scores, key=scores.get) if scores else "unknown"
    if scores.get(best_role, 0) <= 0:
        best_role = "unknown"
    pack = role_packs.get(best_role)
    missing = []
    if pack:
        for skill_terms in pack.required_skill_groups.values():
            if not any(term.lower() in text for term in skill_terms):
                missing.append(skill_terms[0])
    return best_role, detect_seniority(text), matched.get(best_role, []), missing


def detect_seniority(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ["senior", "lead", "principal", "manager", "director", "5+ years", "7+ years"]):
        return "senior"
    if any(term in lowered for term in ["junior", "entry level", "entry-level", "level 1", "tier 1", "associate"]):
        return "entry_level"
    return "mid"
