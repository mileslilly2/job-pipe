from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CANONICAL_ROLE_FAMILIES = ("backend_api", "python_automation", "data_engineering", "software_engineering", "full_stack", "frontend", "qa_automation", "help_desk", "technical_support", "ai_evaluation", "machine_learning", "ai_research", "security", "infrastructure", "data_analytics", "product", "legal", "sales", "other")
ROLE_ALIASES = {"backend_api": ("backend engineer", "backend developer", "api engineer", "server side engineer", "backend api", "backend api engineer"), "python_automation": ("python automation", "automation engineer"), "data_engineering": ("data engineer", "data engineering", "data pipeline"), "software_engineering": ("software engineer", "software developer"), "full_stack": ("full stack", "fullstack"), "frontend": ("frontend", "front end", "ui engineer"), "qa_automation": ("qa automation", "test automation", "sdet"), "help_desk": ("helpdesk", "help desk", "service desk", "it help desk"), "technical_support": ("technical support", "support engineer", "it support"), "ai_evaluation": ("ai evaluation", "model evaluation", "evals"), "machine_learning": ("machine learning engineer", "ml engineer"), "ai_research": ("ai research", "research engineer", "research scientist"), "security": ("security engineer", "security analyst", "cybersecurity", "security"), "infrastructure": ("infrastructure engineer", "site reliability", "sre", "devops"), "data_analytics": ("product analytics", "data analytics", "data analyst"), "product": ("product manager", "product owner"), "legal": ("product counsel", "legal counsel", "counsel"), "sales": ("sales", "sdr", "bdr")}
LEGACY_ROLE_ALIASES = {"helpdesk": "help_desk", "data_engineer": "data_engineering", "software_engineer": "software_engineering", "frontend_engineer": "frontend", "technical_operations": "infrastructure", "security_only": "security", "legal_counsel": "legal", "ml_research": "ai_research"}

@dataclass(frozen=True)
class Classification:
    primary_role_family: str
    secondary_role_families: tuple[str, ...]
    seniority: str
    role_evidence: tuple[str, ...]
    seniority_evidence: tuple[str, ...]

def normalize_role_family(value: str | None) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return LEGACY_ROLE_ALIASES.get(value, value if value in CANONICAL_ROLE_FAMILIES else "other")

def classify_job(job: Any) -> Classification:
    title, body = _text(job, "title"), _text(job, "description")
    scores = []
    for family, aliases in ROLE_ALIASES.items():
        evidence, title_score, description_score, title_specificity = [], 0, 0, 0
        for alias in aliases:
            phrase = _normal(alias)
            if _has(title, phrase):
                title_score += 100; title_specificity = max(title_specificity, len(phrase.split())); evidence.append(f"title matched {alias!r}")
            elif _has(body, phrase):
                description_score += 15; evidence.append(f"description matched {alias!r}")
        if title_score or description_score:
            scores.append((title_score, description_score, title_specificity, family, evidence))
    if scores:
        # A title names the profession; description-only matches add context.
        scores.sort(key=lambda item: (-bool(item[0]), -item[0], -item[2], item[3]))
        _, _, _, primary, evidence = scores[0]
        secondary = tuple(family for title_score, description_score, _title_specificity, family, _ in scores[1:] if title_score or description_score)
        evidence = tuple(evidence + [f"secondary concept: {item}" for item in secondary])
    else: primary, secondary, evidence = "other", (), ("no canonical role alias found",)
    seniority, seniority_evidence = classify_seniority(_text(job, "title"))
    return Classification(primary, secondary, seniority, evidence, seniority_evidence)

def classify_seniority(title: str) -> tuple[str, tuple[str, ...]]:
    text = _normal(title)
    for level, signals in (("executive", ("vp", "vice president", "chief")), ("director", ("director",)), ("manager", ("manager",)), ("lead", ("lead",)), ("principal", ("principal",)), ("staff", ("staff",)), ("senior", ("senior", "sr")), ("junior", ("junior", "jr")), ("apprentice", ("apprentice",)), ("intern", ("intern",)), ("entry_level", ("entry level", "associate", "analyst i", "engineer i")), ("senior", ("engineer iii",)), ("mid", ("engineer ii",))):
        if any(_has(text, item) for item in signals): return level, (f"title matched seniority token {next(item for item in signals if _has(text, item))!r}",)
    return "unknown", ("no explicit seniority token found",)

def _text(job: Any, name: str) -> str:
    return _normal(str(job.get(name, "") if isinstance(job, dict) else getattr(job, name, "") or ""))
def _normal(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())
def _has(text: str, phrase: str) -> bool:
    return bool(re.search(r"(?<!\\w)" + re.escape(phrase) + r"(?!\\w)", text))
