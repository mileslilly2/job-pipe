from __future__ import annotations

import re
from typing import Any

from .models import FactReport, JobFacts, ProfileFacts, SearchProfile

NEGATIVE_ROLE_FAMILY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("legal_counsel", ("product counsel", "legal counsel", "counsel", "corporate counsel", "privacy counsel", "attorney", "lawyer")),
    ("security_only", ("security engineer", "security analyst", "security operations", "information security", "cybersecurity", "appsec", "secops", "security architect")),
    ("ml_research", ("ml research", "machine learning research", "research scientist", "applied scientist", "research engineer", "researcher")),
    ("senior_research", ("senior research", "senior research scientist", "senior research engineer")),
    ("data_products", ("data products",)),
    ("help_desk", ("help desk", "helpdesk", "service desk", "desk support")),
    ("customer_support", ("customer support", "customer success", "client support", "customer care")),
    ("technical_support", ("it support", "technical support", "support engineer", "desktop support", "systems support")),
    ("physician", ("physician", "medical doctor", "doctor", "surgeon")),
    ("truck_driver", ("truck driver", "cdl driver", "commercial driver")),
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
    ("frontend_engineer", ("frontend engineer", "front-end engineer", "frontend developer", "front-end developer", "ui engineer", "ui developer", "web engineer", "react", "next.js", "javascript", "typescript")),
    ("junior_software_engineer", ("junior software engineer", "software engineer i", "software engineer 1", "entry level software engineer", "associate software engineer", "junior developer", "new grad software engineer")),
    ("software_engineer", ("software engineer", "software developer", "full stack engineer", "application engineer", "application developer")),
    ("backend_api", ("backend engineer", "backend developer", "backend", "api", "microservices", "services", "platform engineer")),
]

JOB_SKILL_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("python", ("python",)),
    ("sql", ("sql",)),
    ("etl", ("etl",)),
    ("api", ("api", "apis")),
    ("react", ("react",)),
    ("typescript", ("typescript", "ts")),
    ("javascript", ("javascript", "js")),
    ("next.js", ("next.js", "nextjs")),
    ("node.js", ("node.js", "nodejs")),
    ("playwright", ("playwright",)),
    ("pytest", ("pytest",)),
    ("sqlite", ("sqlite",)),
    ("cli", ("cli", "command line", "command-line")),
    ("automation", ("automation",)),
    ("workflow automation", ("workflow automation",)),
    ("testing", ("testing",)),
    ("debugging", ("debugging",)),
    ("documentation", ("documentation",)),
    ("linux", ("linux",)),
    ("aws", ("aws",)),
    ("docker", ("docker",)),
    ("kubernetes", ("kubernetes",)),
    ("terraform", ("terraform",)),
    ("git", ("git",)),
    ("html", ("html",)),
    ("css", ("css",)),
    ("customer support", ("customer support",)),
    ("help desk", ("help desk", "helpdesk", "service desk")),
    ("servicenow", ("servicenow", "service now")),
    ("zendesk", ("zendesk",)),
    ("salesforce", ("salesforce",)),
    ("jira", ("jira",)),
    ("active directory", ("active directory",)),
    ("troubleshooting", ("troubleshooting",)),
    ("issue reproduction", ("issue reproduction",)),
    ("communication", ("communication",)),
    ("data engineering", ("data engineering",)),
    ("data pipeline", ("data pipeline", "data pipelines", "pipelines")),
    ("backend", ("backend",)),
]

PROFILE_LEVEL_EXPERIENCE = {
    "internship": 0,
    "apprenticeship": 0,
    "entry": 1,
    "junior": 2,
    "mid": 4,
    "senior": 6,
    "staff": 8,
    "principal": 10,
    "manager": 8,
}

EXPERIENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:at least|minimum(?: of)?|min(?:imum)?(?: of)?)\s*(\d{1,2})(?:\.\d+)?\s*\+?\s*years?\b", re.I),
    re.compile(r"\b(\d{1,2})(?:\.\d+)?\+\s*years?\b", re.I),
    re.compile(r"\b(\d{1,2})(?:\.\d+)?\s*(?:-|–|to)\s*(\d{1,2})(?:\.\d+)?\s*years?\b", re.I),
    re.compile(r"\b(\d{1,2})(?:\.\d+)?\s*years?(?:\s+of)?\s+experience\b", re.I),
    re.compile(r"\b(\d{1,2})(?:\.\d+)?\s+years?\s+experience\b", re.I),
]

INTERN_SIGNS = ("internship", "intern", "co-op", "coop", "student", "new grad")
APPRENTICE_SIGNS = ("apprentice", "apprenticeship", "trainee")
ENTRY_SIGNS = ("entry level", "entry-level", "level i", "level 1", "tier i", "tier 1", "analyst i", "specialist i", "associate")
JUNIOR_SIGNS = ("junior", "jr.", "jr ", "junior-", "junior ")
SENIOR_SIGNS = ("senior", "sr.", " sr ")
STAFF_SIGNS = ("staff engineer", "staff software engineer", "staff ")
PRINCIPAL_SIGNS = ("principal engineer", "principal ")
MANAGER_SIGNS = ("engineering manager", "product manager", "project manager", "program manager", "manager", "team lead", "head of", "director")
REQUIRED_SECTION_CUES = ("requirements", "required", "must have", "minimum qualifications", "minimum requirements", "what you'll need", "qualifications", "responsibilities")
PREFERRED_SECTION_CUES = ("preferred", "nice to have", "bonus", "pluses", "desired qualifications", "preferred qualifications")
SUPPORT_ROLE_FAMILIES = {"help_desk", "customer_support", "it_support"}
FRONTEND_ROLE_FAMILIES = {"frontend_engineer"}
UNCLEAR_LOCATION_TERMS = ("unknown", "tbd", "anywhere", "worldwide", "nationwide", "united states", "usa", "us")


def classify_role_family(job: Any) -> tuple[str, list[str], str | None]:
    text = _job_text(job).lower()
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

    if any(_has_phrase(text, phrase) for phrase in ("devops", "site reliability", "sre", "platform operations", "release engineer", "systems engineer", "technical operations", "ops engineer", "infrastructure engineer")):
        return "unknown", [], "technical operations signals ignored: not programming-heavy"

    return "unknown", [], None


def extract_experience_min_years(text: str) -> int | None:
    best: int | None = None
    for pattern in EXPERIENCE_PATTERNS:
        for match in pattern.finditer(text):
            value = int(float(match.group(1)))
            if best is None or value > best:
                best = value
    return best


def classify_seniority(job: Any, experience_min_years: int | None = None) -> tuple[str, list[str]]:
    title = (_field(job, "title") or "").lower()
    body = _job_text(job).lower()
    notes: list[str] = []
    years = experience_min_years if experience_min_years is not None else extract_experience_min_years(body)

    if _has_any(title, list(MANAGER_SIGNS)):
        return "manager", ["title indicates manager-level role"]
    if _has_any(title, list(PRINCIPAL_SIGNS)):
        return "principal", ["title indicates principal-level role"]
    if _has_any(title, list(STAFF_SIGNS)):
        return "staff", ["title indicates staff-level role"]
    if _has_any(title, list(SENIOR_SIGNS)):
        return "senior", ["title indicates senior-level role"]
    if _has_any(title, list(JUNIOR_SIGNS)):
        return "junior", ["title indicates junior-level role"]
    if _has_any(title, list(ENTRY_SIGNS)):
        return "entry", ["title indicates entry-level role"]
    if _has_any(title, list(APPRENTICE_SIGNS)):
        return "apprenticeship", ["title indicates apprenticeship-level role"]
    if _has_any(title, list(INTERN_SIGNS)):
        return "internship", ["title indicates internship-level role"]

    if _has_any(body, list(MANAGER_SIGNS)):
        return "manager", ["description indicates manager-level scope"]
    if _has_any(body, list(PRINCIPAL_SIGNS)):
        return "principal", ["description indicates principal-level scope"]
    if _has_any(body, list(STAFF_SIGNS)):
        return "staff", ["description indicates staff-level scope"]
    if _has_any(body, list(SENIOR_SIGNS)):
        return "senior", ["description indicates senior-level scope"]
    if _has_any(body, list(JUNIOR_SIGNS)):
        return "junior", ["description indicates junior-level scope"]
    if _has_any(body, list(ENTRY_SIGNS)):
        return "entry", ["description indicates entry-level scope"]
    if _has_any(body, list(APPRENTICE_SIGNS)):
        return "apprenticeship", ["description indicates apprenticeship-level scope"]
    if _has_any(body, list(INTERN_SIGNS)):
        return "internship", ["description indicates internship-level scope"]

    if years is not None:
        if years <= 0:
            notes.append("experience signal: no experience required")
            return "entry", notes
        if years <= 2:
            notes.append(f"experience signal: {years}+ years")
            return "junior", notes
        if years <= 4:
            notes.append(f"experience signal: {years}+ years")
            return "mid", notes
        if years <= 7:
            notes.append(f"experience signal: {years}+ years")
            return "senior", notes
        if years <= 9:
            notes.append(f"experience signal: {years}+ years")
            return "staff", notes
        notes.append(f"experience signal: {years}+ years")
        return "principal", notes

    if "no experience required" in body or "no experience" in body:
        return "entry", ["no experience requested"]

    return "mid", []


def extract_job_facts(job: Any) -> JobFacts:
    text = _job_text(job)
    role_family, _matches, _reason = classify_role_family(job)
    experience_min_years = extract_experience_min_years(text)
    persisted_seniority = str(job.get("canonical_seniority") or job.get("seniority_level") or "") if isinstance(job, dict) else ""
    seniority, _notes = (persisted_seniority, ["persisted canonical seniority"]) if persisted_seniority else classify_seniority(job, experience_min_years=experience_min_years)
    skills_required, skills_preferred = _extract_skills(text)
    remote = _remote_flag(job) or _has_any(text.lower(), ["remote", "work from home", "fully remote"])
    return JobFacts(
        role_family=role_family,
        skills_required=skills_required,
        skills_preferred=skills_preferred,
        experience_min_years=experience_min_years,
        seniority=seniority,
        remote=remote,
    )


def extract_profile_facts(profile: SearchProfile | None) -> ProfileFacts | None:
    if profile is None:
        return None
    preferred_role_families = _normalize_terms(profile.preferred)
    acceptable_role_families = _normalize_terms(profile.acceptable)
    avoid_role_families = _normalize_terms(profile.avoid)
    skills_preferred = _normalize_terms(profile.positive_keywords)
    skills_avoided = _normalize_terms(profile.negative_keywords)
    preferred_locations = _normalize_terms(profile.preferred_locations)
    allowed_locations = _normalize_terms(profile.allowed_locations)
    excluded_locations = _normalize_terms(profile.excluded_locations)
    preferred_work_styles = _normalize_terms(profile.preferred_work_styles)
    preferred_role_levels = _normalize_terms(profile.preferred_role_levels)
    excluded_levels = _normalize_terms(profile.excluded_levels)
    experience_max_years = _profile_experience_cap(preferred_role_levels)
    return ProfileFacts(
        preferred_role_families=preferred_role_families,
        acceptable_role_families=acceptable_role_families,
        avoid_role_families=avoid_role_families,
        skills_preferred=skills_preferred,
        skills_avoided=skills_avoided,
        preferred_locations=preferred_locations,
        allowed_locations=allowed_locations,
        excluded_locations=excluded_locations,
        preferred_work_styles=preferred_work_styles,
        preferred_role_levels=preferred_role_levels,
        excluded_levels=excluded_levels,
        experience_max_years=experience_max_years,
    )


def build_fact_report(job: Any, profile: SearchProfile | None = None) -> FactReport:
    job_facts = extract_job_facts(job)
    profile_facts = extract_profile_facts(profile)
    if profile_facts is None:
        return FactReport(job=job_facts)

    matched: list[str] = []
    missing_mismatched: list[str] = []
    concerns: list[str] = []
    score_adjustment = 0
    profile_preferred_role_families = set(profile_facts.preferred_role_families)
    profile_acceptable_role_families = set(profile_facts.acceptable_role_families)
    profile_avoid_role_families = set(profile_facts.avoid_role_families)
    profile_preferred_skills = set(profile_facts.skills_preferred)
    profile_avoided_skills = set(profile_facts.skills_avoided)
    profile_preferred_locations = set(profile_facts.preferred_locations)
    profile_allowed_locations = set(profile_facts.allowed_locations)
    profile_excluded_locations = set(profile_facts.excluded_locations)
    profile_work_styles = set(profile_facts.preferred_work_styles)
    profile_preferred_levels = {_normalize_level(level) for level in profile_facts.preferred_role_levels}
    profile_excluded_levels = {_normalize_level(level) for level in profile_facts.excluded_levels}

    if job_facts.role_family in profile_preferred_role_families:
        matched.append(f"role preference: preferred {job_facts.role_family} (+6)")
        score_adjustment += 6
    elif job_facts.role_family in profile_acceptable_role_families:
        matched.append(f"role preference: acceptable {job_facts.role_family} (+0)")
    if job_facts.role_family in profile_avoid_role_families:
        concerns.append(f"role preference: avoid {job_facts.role_family} (-20)")
        score_adjustment -= 20

    location_text = _field(job, 'location')
    preferred_location_hits = _matching_location_terms(location_text, list(profile_preferred_locations))
    allowed_location_hits = _matching_location_terms(location_text, list(profile_allowed_locations))
    excluded_location_hits = _matching_location_terms(location_text, list(profile_excluded_locations))
    if preferred_location_hits:
        matched.append(f"location alignment: {', '.join(preferred_location_hits)}")
        score_adjustment += min(len(preferred_location_hits) * 3, 8)
    elif allowed_location_hits:
        matched.append(f"location allowed: {', '.join(allowed_location_hits)}")
        score_adjustment += min(len(allowed_location_hits) * 2, 4)

    if excluded_location_hits:
        missing_mismatched.append(f"location mismatch: {', '.join(excluded_location_hits)}")
        score_adjustment -= 16
    elif location_text and not job_facts.remote:
        if _location_is_foreign_onsite(location_text):
            concerns.append(f"practical-fit concern: foreign onsite location '{location_text}'")
            score_adjustment -= 12
        elif _location_mentions_canada(location_text) and not allowed_location_hits:
            concerns.append(f"practical-fit concern: Canada-only location '{location_text}' not allowed")
            score_adjustment -= 10

    required_matches = [skill for skill in job_facts.skills_required if _matches_any(skill, list(profile_preferred_skills))]
    for skill in required_matches:
        matched.append(f"required skill match: {skill}")
    if required_matches:
        score_adjustment += min(len(required_matches) * 2, 8)

    missing_required = [skill for skill in job_facts.skills_required if not _matches_any(skill, list(profile_preferred_skills))]
    for skill in missing_required:
        missing_mismatched.append(f"required skill missing from profile: {skill}")
    if missing_required:
        score_adjustment -= min(len(missing_required) * 2, 8)

    for skill in job_facts.skills_preferred:
        if _matches_any(skill, list(profile_preferred_skills)):
            matched.append(f"preferred skill match: {skill}")
            score_adjustment += 1
    avoided_skill_hits = [skill for skill in job_facts.skills_required + job_facts.skills_preferred if _matches_any(skill, list(profile_avoided_skills))]
    if avoided_skill_hits:
        avoided = sorted(set(avoided_skill_hits))
        missing_mismatched.append(f"profile-avoided skills present: {', '.join(avoided)}")
        score_adjustment -= min(len(avoided) * 4, 16)

    if job_facts.remote and "remote" in profile_work_styles:
        matched.append("remote match: profile prefers remote work")
        score_adjustment += 3
    elif not job_facts.remote and "remote" in profile_work_styles:
        missing_mismatched.append("work-style mismatch: profile prefers remote work")
        score_adjustment -= 3

    normalized_job_level = _normalize_level(job_facts.seniority)
    if normalized_job_level in profile_preferred_levels:
        matched.append(f"seniority alignment: {job_facts.seniority}")
        score_adjustment += 3
    if normalized_job_level in profile_excluded_levels:
        missing_mismatched.append(f"seniority mismatch: {job_facts.seniority}")
        score_adjustment -= 10

    if job_facts.experience_min_years is not None and profile_facts.experience_max_years is not None:
        if job_facts.experience_min_years > profile_facts.experience_max_years:
            gap = job_facts.experience_min_years - profile_facts.experience_max_years
            concerns.append(
                f"experience mismatch: job asks for {job_facts.experience_min_years}+ years, profile targets up to {profile_facts.experience_max_years} years"
            )
            score_adjustment -= min(6 + gap * 2, 14)

    if job_facts.seniority in {"senior", "staff", "principal", "manager"}:
        concerns.append(f"seniority concern: {job_facts.seniority}")
        score_adjustment -= 4 if job_facts.seniority == "senior" else 6

    if _looks_like_infra_role(_job_text(job)) and _requires_deep_distributed_systems(_job_text(job)) and not _has_backend_api_signals(_job_text(job)):
        concerns.append("practical-fit concern: infra role requires deep distributed systems without strong backend/api signals")
        score_adjustment -= 10

    apply_url = _job_apply_url(job)
    if not apply_url:
        concerns.append("missing apply URL")
    if _location_is_unclear(job):
        concerns.append(f"unclear location: {(_field(job, 'location') or 'unknown').strip() or 'unknown'}")

    return FactReport(
        job=job_facts,
        profile=profile_facts,
        matched=matched,
        missing_mismatched=missing_mismatched,
        concerns=concerns,
        score_adjustment=score_adjustment,
    )


def _matching_location_terms(location: str, terms: list[str]) -> list[str]:
    matched: list[str] = []
    for term in terms:
        if _location_term_matches(location, term):
            matched.append(term)
    return matched


def _location_term_matches(location: str, term: str) -> bool:
    normalized_location = _normalize_location(location)
    normalized_term = _normalize_location(term)
    if not normalized_location or not normalized_term:
        return False
    if normalized_term in {"remote us", "remote united states", "remote usa"}:
        return _location_is_remote(location) and _location_mentions_us(location)
    if normalized_term in {"united states", "usa", "us"}:
        return _location_mentions_us(location)
    if normalized_term in {"us canada okay", "us canada", "us/canada okay", "north america"}:
        return _location_mentions_us(location) or _location_mentions_canada(location)
    if normalized_term in {"canada", "canada only"}:
        return _location_mentions_canada(location)
    if normalized_term in {"uk", "united kingdom", "uk only"}:
        return any(term in normalized_location for term in ("uk", "united kingdom", "london", "britain", "british"))
    if normalized_term in {"europe", "europe only"}:
        return any(term in normalized_location for term in ("europe", "european union", "eu", "paris", "dublin", "berlin", "amsterdam", "madrid", "frankfurt"))
    if normalized_term in {"paris", "london", "dublin", "sydney"}:
        return normalized_term in normalized_location
    return normalized_term in normalized_location


def _location_is_remote(location: str) -> bool:
    normalized = _normalize_location(location)
    return any(term in normalized for term in ("remote", "work from home", "wfh", "fully remote"))


def _location_mentions_us(location: str) -> bool:
    normalized = _normalize_location(location)
    return any(term in normalized for term in ("united states", "usa", "u s a", "us based", "us only", "remote us", "us remote")) or re.search(r"\bus\b", normalized) is not None


def _location_mentions_canada(location: str) -> bool:
    normalized = _normalize_location(location)
    return any(term in normalized for term in ("canada", "toronto", "vancouver", "montreal", "calgary", "ottawa", "waterloo"))


def _location_is_foreign_onsite(location: str) -> bool:
    if _location_is_remote(location):
        return False
    normalized = _normalize_location(location)
    if _location_mentions_us(location) or _location_mentions_canada(location):
        return False
    return any(term in normalized for term in ("paris", "london", "dublin", "sydney", "uk", "united kingdom", "europe", "france", "germany", "spain", "netherlands", "ireland", "australia"))


def _looks_like_infra_role(text: str) -> bool:
    normalized = _normalize_location(text)
    return any(term in normalized for term in ("devops", "site reliability", "sre", "platform operations", "release engineer", "systems engineer", "technical operations", "ops engineer", "infrastructure engineer"))


def _has_backend_api_signals(text: str) -> bool:
    return any(_has_phrase(text, phrase) for phrase in ("backend", "api", "microservices", "services", "platform engineer"))


def _requires_deep_distributed_systems(text: str) -> bool:
    return any(_has_phrase(text, phrase) for phrase in ("distributed systems", "distributed system", "high scale", "large scale", "infrastructure", "infra", "platform", "service mesh", "sharding", "replication"))


def _normalize_location(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else " " for char in value)
    return " ".join(cleaned.split())


def _extract_skills(text: str) -> tuple[list[str], list[str]]:
    required: list[str] = []
    preferred: list[str] = []
    raw_lines = text.splitlines()
    current_bucket = "required"
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if _is_heading(lower):
            current_bucket = "preferred" if any(cue in lower for cue in PREFERRED_SECTION_CUES) else "required"
            continue
        bucket = current_bucket
        if any(cue in lower for cue in PREFERRED_SECTION_CUES):
            bucket = "preferred"
        elif any(cue in lower for cue in REQUIRED_SECTION_CUES):
            bucket = "required"
        for canonical, aliases in JOB_SKILL_ALIASES:
            if any(_has_phrase(lower, alias) for alias in aliases):
                target = preferred if bucket == "preferred" else required
                if canonical not in target:
                    target.append(canonical)
    return required, preferred


def _is_heading(line: str) -> bool:
    if len(line) > 90:
        return False
    if line.endswith(":") and any(cue in line for cue in REQUIRED_SECTION_CUES + PREFERRED_SECTION_CUES):
        return True
    if any(cue in line for cue in REQUIRED_SECTION_CUES + PREFERRED_SECTION_CUES):
        return True
    return False


def _job_text(job: Any) -> str:
    parts = [
        _field(job, "title"),
        _field(job, "company"),
        _field(job, "location"),
        _field(job, "description"),
        _field(job, "raw_text"),
        _field(job, "employment_type"),
        _field(job, "remote_type"),
    ]
    return "\n".join(part for part in parts if part)


def _field(job: Any, name: str) -> str:
    if isinstance(job, dict):
        return str(job.get(name) or "")
    return str(getattr(job, name, "") or "")


def _remote_flag(job: Any) -> bool:
    if isinstance(job, dict):
        return bool(job.get("remote"))
    return bool(getattr(job, "remote", False) or getattr(job, "remote_type", "") == "remote")


def _job_apply_url(job: Any) -> str:
    if isinstance(job, dict):
        return str(job.get("apply_url") or job.get("source_url") or "")
    return str(getattr(job, "apply_url", "") or getattr(job, "source_url", "") or "")


def _location_is_unclear(job: Any) -> bool:
    location = _field(job, "location").strip().lower()
    if not location:
        return True
    if location in {"remote"} or location.startswith("remote ") or location.startswith("remote-"):
        return False
    if "," in location:
        return False
    if any(term in location for term in UNCLEAR_LOCATION_TERMS):
        return True
    return len(location.split()) <= 2


def _profile_experience_cap(levels: list[str]) -> int | None:
    years = [PROFILE_LEVEL_EXPERIENCE[level] for level in levels if level in PROFILE_LEVEL_EXPERIENCE]
    return max(years) if years else None


def _normalize_terms(values: list[str]) -> list[str]:
    return sorted({value.strip().lower() for value in values if value and value.strip()})


def _normalize_level(level: str) -> str:
    value = level.strip().lower()
    aliases = {
        "entry_level": "entry",
        "entry": "entry",
        "junior": "junior",
        "jr": "junior",
        "mid": "mid",
        "senior": "senior",
        "staff": "staff",
        "principal": "principal",
        "manager": "manager",
        "director": "manager",
        "head": "manager",
        "intern": "internship",
        "internship": "internship",
        "apprentice": "apprenticeship",
        "apprenticeship": "apprenticeship",
        "contractor": "contractor",
    }
    return aliases.get(value, value)


def _matches_any(term: str, candidates: list[str]) -> bool:
    return any(_phrase_matches(term, candidate) for candidate in candidates)


def _phrase_matches(term: str, candidate: str) -> bool:
    term_lower = term.lower().strip()
    candidate_lower = candidate.lower().strip()
    if not term_lower or not candidate_lower:
        return False
    return term_lower == candidate_lower or term_lower in candidate_lower or candidate_lower in term_lower


def _has_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _has_programming_signals(text: str) -> bool:
    return any(_has_phrase(text, phrase) for phrase in ("python", "automation", "scripting", "cli", "pipeline", "pipelines", "sql", "api", "testing", "terraform", "bash", "git"))


def _has_phrase(text: str, phrase: str) -> bool:
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return re.search(pattern, text) is not None
