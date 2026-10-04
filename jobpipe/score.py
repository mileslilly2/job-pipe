from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .facts import build_fact_report, classify_role_family as _classify_role_family, extract_job_facts
from .knowledge import build_knowledge_report, require_goal
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
    if isinstance(job, dict) and job.get("primary_role_family"):
        family = str(job["primary_role_family"]); family_matches = ["persisted classification"]; family_reason = "persisted primary role family"
    notes.append(f"role family: {family}")
    if family_matches:
        notes.append("matched role signals: " + ", ".join(family_matches))
    if family_reason:
        notes.append(family_reason)

    primary_role = str(job.get("primary_role_family") or "") if isinstance(job, dict) else ""
    secondary_roles = tuple(
        str(value) for value in job.get("secondary_role_families", ())
        if str(value) != primary_role
    ) if isinstance(job, dict) else ()
    if profile is not None and profile.active_goal and primary_role:
        modifiers = require_goal(profile.active_goal).role_family_modifiers
        primary_modifier = int(modifiers.get(primary_role, 0))
        score += primary_modifier
        notes.append(f"{primary_modifier:+d} primary role goal modifier: {primary_role}")
        secondary_values = [(role, int(modifiers.get(role, 0) / 4)) for role in dict.fromkeys(secondary_roles)]
        secondary_cap = 3 if primary_modifier == 0 else abs(primary_modifier) // 2
        secondary_modifier = max(-secondary_cap, min(sum(value for _, value in secondary_values), secondary_cap))
        for role, value in secondary_values:
            notes.append(f"{value:+d} secondary role goal modifier: {role}")
        if secondary_modifier:
            score += secondary_modifier
        if secondary_modifier != sum(value for _, value in secondary_values):
            notes.append(f"secondary role goal cap: {secondary_modifier:+d}")


    knowledge_report = build_knowledge_report(job, profile=profile)
    if knowledge_report.goal_notes:
        notes.append(f"goal: {knowledge_report.goal_label}")
        notes.extend(knowledge_report.goal_notes)
        score += knowledge_report.goal_modifier
    if knowledge_report.matches or knowledge_report.profile_boosts or knowledge_report.conflict_penalties:
        score += knowledge_report.score_adjustment
        notes.extend(knowledge_report.notes)

    keyword_hits: list[str] = []
    for keyword in RESUME_KEYWORDS:
        if _has_phrase(text, keyword):
            keyword_hits.append(keyword)

    job_facts = extract_job_facts(job)
    if job_facts.seniority in {"junior", "entry", "entry_level", "internship", "apprenticeship"}:
        bonus = 6 if job_facts.seniority in {"entry", "entry_level", "internship"} else 4
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

    fact_report = build_fact_report(job, profile) if profile is not None else None
    if fact_report is not None:
        score += fact_report.score_adjustment
        if fact_report.notes:
            notes.extend(fact_report.notes)

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

    location_cap, location_reason = _location_score_cap(_field(job, "location"), profile)
    if score > location_cap:
        score = location_cap
        if location_reason:
            notes.append(f"hard location ceiling: {location_reason} ({location_cap})")

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


def _location_score_cap(location: str, profile: SearchProfile | None) -> tuple[int, str | None]:
    normalized = _normalize_location(location)
    if not normalized:
        return 100, None
    if _location_is_foreign_onsite(location):
        return 92, "foreign onsite location"
    if _location_mentions_canada(location):
        allowed = profile is not None and any(_location_term_matches(location, term) for term in profile.allowed_locations)
        if not allowed:
            return 88, "Canada-only location not allowed"
    return 100, None


def _normalize_location(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else " " for char in value)
    return " ".join(cleaned.split())


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
@dataclass(frozen=True)
class ScoreContribution:
    category: str
    label: str
    amount: int


@dataclass(frozen=True)
class ScoreBreakdown:
    primary_role_family: str
    secondary_role_families: tuple[str, ...]
    contributions: tuple[ScoreContribution, ...]
    fact_evidence: tuple[str, ...]
    pre_cap_score: int
    location_cap: int
    displayed_score: int
    notes: tuple[str, ...]

    @property
    def goal_specific_total(self) -> int:
        return sum(item.amount for item in self.contributions if item.category in {"primary_role_goal", "secondary_role_goal", "secondary_role_goal_cap", "goal"})


def score_job_fit_breakdown(job: Any, learned_weights: dict[str, Any] | None = None, profile: SearchProfile | None = None) -> ScoreBreakdown:
    """Decompose the existing fit score and assert the arithmetic agrees."""
    displayed, notes = score_job_fit_v1(job, learned_weights=learned_weights, profile=profile)
    primary = _field(job, "primary_role_family")
    secondary = tuple(job.get("secondary_role_families", ()) if isinstance(job, dict) else ())
    family, _, _ = _classify_role_family(job)
    if primary:
        family = primary
    items = [ScoreContribution("base", "base score", 10)]
    if primary:
        items.append(ScoreContribution("persisted_primary", primary, 0))
    items.extend(ScoreContribution("secondary_context", str(item), 0) for item in secondary)
    if profile is not None and profile.active_goal and primary:
        modifiers = require_goal(profile.active_goal).role_family_modifiers
        primary_modifier = int(modifiers.get(primary, 0))
        items.append(ScoreContribution("primary_role_goal", primary, primary_modifier))
        values = [(str(role), int(modifiers.get(str(role), 0) / 4)) for role in dict.fromkeys(secondary)]
        cap = 3 if primary_modifier == 0 else abs(primary_modifier) // 2
        total = max(-cap, min(sum(value for _, value in values), cap))
        for role, value in values: items.append(ScoreContribution("secondary_role_goal", role, value))
        if total != sum(value for _, value in values): items.append(ScoreContribution("secondary_role_goal_cap", "combined secondary cap", total - sum(value for _, value in values)))
    knowledge = build_knowledge_report(job, profile=profile)
    items.extend(ScoreContribution(f"kr_{match.match_type}", match.concept, match.score) for match in knowledge.matches)
    items.extend(ScoreContribution("kr_profile", note, _amount(note)) for note in knowledge.profile_boosts + knowledge.conflict_penalties)
    items.extend(ScoreContribution("goal", note, _amount(note)) for note in knowledge.goal_notes)
    text = " ".join(_field(job, name) for name in ("title", "company", "location", "description")).lower()
    facts = extract_job_facts(job)
    if facts.seniority in {"junior", "entry", "entry_level", "internship", "apprenticeship"}:
        items.append(ScoreContribution("seniority", facts.seniority, 6 if facts.seniority in {"entry", "entry_level", "internship"} else 4))
    elif facts.seniority in {"senior", "staff", "principal", "manager"}:
        items.append(ScoreContribution("seniority", facts.seniority, -10 if facts.seniority == "senior" else -14))
    if _remote_flag(job) or _has_phrase(text, "remote"):
        items.append(ScoreContribution("remote", "remote-friendly", 5))
    elif "hybrid" in text:
        items.append(ScoreContribution("remote", "hybrid-friendly", 2))
    evidence: tuple[str, ...] = ()
    if profile is not None:
        report = build_fact_report(job, profile)
        evidence = tuple(report.notes)
        items.append(ScoreContribution("profile_practical_fit", "fact report", report.score_adjustment))
        styles = {value.strip().lower() for value in profile.preferred_work_styles if value and value.strip()}
        items.append(ScoreContribution("profile_remote", "remote work style", 4 if facts.remote and "remote" in styles else -3 if not facts.remote and "remote" in styles else 0))
        preferred = {value.strip().lower() for value in profile.preferred_role_levels if value and value.strip()}
        excluded = {value.strip().lower() for value in profile.excluded_levels if value and value.strip()}
        if facts.seniority in preferred: items.append(ScoreContribution("profile_seniority", facts.seniority, 6))
        if facts.seniority in excluded: items.append(ScoreContribution("profile_seniority", facts.seniority, -30))
    if learned_weights:
        role = learned_weights.get("role_families", {})
        if family in role: items.append(ScoreContribution("learned_role", family, int(role[family])))
        keywords = learned_weights.get("keywords", {})
        for keyword in RESUME_KEYWORDS:
            if keyword in keywords and _has_phrase(text, keyword): items.append(ScoreContribution("learned_keyword", keyword, int(keywords[keyword])))
    pre_cap = sum(item.amount for item in items)
    cap, _ = _location_score_cap(_field(job, "location"), profile)
    if max(0, min(pre_cap, cap, 100)) != displayed:
        raise AssertionError("score breakdown drift")
    return ScoreBreakdown(primary, secondary, tuple(items), evidence, pre_cap, cap, displayed, tuple(notes))


def _amount(note: str) -> int:
    match = re.search(r"\(([+-]\d+)\)|^([+-]\d+)", note)
    return int(next(value for value in match.groups() if value is not None)) if match else 0

