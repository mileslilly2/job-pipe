from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from .facts import classify_seniority, extract_job_facts
from .score import score_risk
from .utils import normalize_text
from .experience import experience_dicts


@dataclass(frozen=True)
class JobRequirements:
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    seniority: str = 'unknown'
    concerns: list[str] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ResumeTailoring:
    summary: str
    selected_skills: list[str]
    selected_projects: list[dict[str, Any]]
    education: list[str]
    emphasis: list[str]


@dataclass(frozen=True)
class FitSummary:
    matched_requirements: list[str]
    missing_requirements: list[str]
    concerns: list[str]
    suggested_emphasis: list[str]


def extract_job_requirements(job: Any) -> JobRequirements:
    text = _job_text(job)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    required_skills = _dedupe(_merge_lists(_find_section_items(lines, 'required') + _skill_hits(text, preferred=False)))
    preferred_skills = _dedupe(_merge_lists(_find_section_items(lines, 'preferred') + _skill_hits(text, preferred=True)))
    responsibilities = _dedupe(_extract_responsibilities(lines, text))
    facts = extract_job_facts(job)
    seniority, seniority_notes = classify_seniority(job, experience_min_years=facts.experience_min_years)
    if isinstance(job, dict):
        canonical = job.get('canonical_seniority')
        if canonical and canonical != 'unknown':
            seniority, seniority_notes = canonical, ['persisted canonical seniority']
    concerns = _build_concerns(job, text, responsibilities, seniority, seniority_notes)
    return JobRequirements(
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        responsibilities=responsibilities,
        seniority=seniority,
        concerns=concerns,
        experience=experience_dicts(text),
    )


def build_resume_tailoring(job: Any, profile: Any) -> ResumeTailoring:
    requirements = extract_job_requirements(job)
    job_text = _job_text(job)
    summary = normalize_text(getattr(profile, 'summary', '') or '')
    emphasis_terms = _emphasis_terms(requirements)
    if emphasis_terms:
        summary = normalize_text(f"{summary} For this role, emphasize {_join_phrases(emphasis_terms[:3])}.")

    selected_skills = _select_skills(getattr(profile, 'skills', []), job_text, requirements)
    selected_projects = _select_projects(getattr(profile, 'projects', []), job_text, requirements)
    education = list(getattr(profile, 'education', []))
    emphasis = _build_emphasis(selected_skills, selected_projects, requirements)
    return ResumeTailoring(
        summary=summary or 'Project-focused technical problem solver.',
        selected_skills=selected_skills,
        selected_projects=selected_projects,
        education=education,
        emphasis=emphasis,
    )


def build_fit_summary(job: Any, profile: Any) -> FitSummary:
    requirements = extract_job_requirements(job)
    job_text = _job_text(job)
    profile_blob = _profile_blob(profile)
    matched: list[str] = []
    missing: list[str] = []

    for skill in requirements.required_skills:
        if _matches(profile_blob, skill):
            matched.append(f'Required skill matched: {skill}')
        else:
            missing.append(f'Required skill missing: {skill}')

    for skill in requirements.preferred_skills:
        if _matches(profile_blob, skill):
            matched.append(f'Preferred skill matched: {skill}')

    for responsibility in requirements.responsibilities[:6]:
        if _matches(profile_blob, responsibility):
            matched.append(f'Responsibility aligned: {responsibility}')

    emphasis = _build_emphasis([], [], requirements)
    if not emphasis:
        emphasis = _suggest_emphasis_from_profile(profile, job_text)
    concerns = list(requirements.concerns)
    if not requirements.required_skills and not requirements.preferred_skills and not requirements.responsibilities:
        concerns.append('Few explicit requirements detected.')
    return FitSummary(
        matched_requirements=matched,
        missing_requirements=missing,
        concerns=_dedupe(concerns),
        suggested_emphasis=_dedupe(emphasis),
    )


def _build_emphasis(selected_skills: list[str], selected_projects: list[dict[str, Any]], requirements: JobRequirements) -> list[str]:
    emphasis: list[str] = []
    if selected_skills:
        emphasis.append('Selected skills: ' + ', '.join(selected_skills[:5]))
    if selected_projects:
        emphasis.append('Selected projects: ' + ', '.join(project['title'] for project in selected_projects[:3]))
    if requirements.required_skills:
        emphasis.append('Requirements to mirror: ' + ', '.join(requirements.required_skills[:5]))
    if requirements.responsibilities:
        emphasis.append('Responsibilities to echo: ' + ', '.join(requirements.responsibilities[:3]))
    return emphasis


def _suggest_emphasis_from_profile(profile: Any, job_text: str) -> list[str]:
    skills = _select_skills(getattr(profile, 'skills', []), job_text, JobRequirements())
    projects = _select_projects(getattr(profile, 'projects', []), job_text, JobRequirements())
    emphasis: list[str] = []
    if skills:
        emphasis.append('Selected skills: ' + ', '.join(skills[:5]))
    if projects:
        emphasis.append('Selected projects: ' + ', '.join(project['title'] for project in projects[:3]))
    return emphasis


def _select_skills(skill_groups: Any, job_text: str, requirements: JobRequirements) -> list[str]:
    skills: list[str] = []
    if isinstance(skill_groups, dict):
        for values in skill_groups.values():
            for item in values:
                if item not in skills:
                    skills.append(item)
    else:
        for item in skill_groups:
            if item not in skills:
                skills.append(item)
    scored: list[tuple[int, int, str]] = []
    for index, skill in enumerate(skills):
        score = 0
        lower = skill.lower()
        if lower in job_text:
            score += 10
        for req in requirements.required_skills + requirements.preferred_skills + requirements.responsibilities:
            if lower and lower in req.lower():
                score += 4
        scored.append((-score, index, skill))
    scored.sort()
    selected = [skill for neg_score, _, skill in scored if neg_score < 0]
    if len(selected) < 6:
        for _, _, skill in scored:
            if skill not in selected:
                selected.append(skill)
            if len(selected) >= 8:
                break
    return selected[:8]


def _select_projects(projects: Any, job_text: str, requirements: JobRequirements) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, project in enumerate(projects):
        data = _project_dict(project)
        blob = ' '.join([data['title'], data['description'], ' '.join(data['bullets']), ' '.join(data['technologies'])]).lower()
        score = 0
        for term in requirements.required_skills + requirements.preferred_skills + requirements.responsibilities:
            if term.lower() in blob and term.lower() in job_text:
                score += 3
        for term in requirements.required_skills + requirements.preferred_skills:
            if term.lower() in blob:
                score += 2
        if any(keyword in blob for keyword in ['python', 'automation', 'testing', 'debugging', 'sql', 'api']):
            score += 1
        scored.append((-score, index, data))
    scored.sort()
    selected = [project for neg_score, _, project in scored if neg_score < 0]
    if not selected:
        selected = [project for _, _, project in scored[:3]]
    return [_truncate_project(project, requirements) for project in selected[:3]]


def _truncate_project(project: dict[str, Any], requirements: JobRequirements) -> dict[str, Any]:
    bullets = list(project['bullets'])
    if len(bullets) > 2:
        selected_bullets = [
            bullet for bullet in bullets
            if any(term.lower() in bullet.lower() for term in requirements.required_skills + requirements.preferred_skills + requirements.responsibilities)
        ]
        if not selected_bullets:
            selected_bullets = bullets[:2]
        bullets = selected_bullets[:2]
    return {**project, 'bullets': bullets}


def _build_concerns(job: Any, text: str, responsibilities: list[str], seniority: str, seniority_notes: list[str]) -> list[str]:
    concerns: list[str] = []
    if seniority in {'senior', 'staff', 'principal', 'manager', 'director'}:
        concerns.append(f'Seniority concern: {seniority}')
    if not responsibilities:
        concerns.append('Responsibilities are not explicit.')
    if not getattr(job, 'company', ''):
        concerns.append('Company name is missing.')
    risk_score, flags = score_risk(job if not isinstance(job, dict) else SimpleNamespace(**{"source_url": None, **job}), None)
    if flags:
        concerns.append('Risk signals: ' + ', '.join(flags[:4]))
    if risk_score >= 25 and not flags:
        concerns.append('Posting has quality concerns.')
    concerns.extend(seniority_notes[:1])
    return _dedupe(concerns)


def _find_section_items(lines: list[str], section: str) -> list[str]:
    collected: list[str] = []
    current = ''
    for line in lines:
        marker = _section_marker(line)
        if marker:
            current = marker
            continue
        if current != section:
            continue
        if line.startswith(('-', '*', '•')):
            collected.append(line.lstrip('-*• ').strip())
        elif ':' not in line or len(line.split()) <= 12:
            collected.append(line.strip())
    return _dedupe(collected)


def _extract_responsibilities(lines: list[str], text: str) -> list[str]:
    section_lines = _find_section_items(lines, 'responsibilities')
    if section_lines:
        return section_lines
    candidates: list[str] = []
    for sentence in _sentences(text):
        lower = sentence.lower()
        if any(keyword in lower for keyword in ['build', 'design', 'maintain', 'support', 'partner', 'debug', 'document', 'analyze', 'own', 'implement']):
            candidates.append(normalize_text(sentence))
    return _dedupe(candidates[:6])


def _skill_hits(text: str, *, preferred: bool) -> list[str]:
    from .facts import JOB_SKILL_ALIASES

    hits: list[str] = []
    for label, aliases in JOB_SKILL_ALIASES:
        if preferred and label in {'help desk', 'customer support', 'servicenow', 'zendesk', 'salesforce'}:
            continue
        if not preferred and label in {'help desk', 'customer support', 'servicenow', 'zendesk', 'salesforce'}:
            continue
        if any(alias in text.lower() for alias in aliases):
            hits.append(label)
    return hits


def _section_marker(line: str) -> str:
    lower = line.lower().strip(':#')
    cues = {
        'required': ['requirements', 'required', 'must have', "what you'll need", 'minimum qualifications', 'qualifications'],
        'preferred': ['preferred', 'nice to have', 'bonus', 'desired qualifications'],
        'responsibilities': ['responsibilities', "what you'll do", 'day to day', 'about the role'],
    }
    for marker, phrases in cues.items():
        if any(phrase in lower for phrase in phrases) and len(lower) < 80:
            return marker
    return ''


def _sentences(text: str) -> list[str]:
    import re

    parts = re.split(r'(?<=[.!?])\s+', text)
    return [part.strip() for part in parts if part.strip()]


def _profile_blob(profile: Any) -> str:
    chunks: list[str] = []
    summary = getattr(profile, 'summary', '') or ''
    chunks.append(summary)
    skills = getattr(profile, 'skills', [])
    if isinstance(skills, dict):
        for values in skills.values():
            chunks.extend(values)
    else:
        chunks.extend(skills)
    for project in getattr(profile, 'projects', []):
        data = _project_dict(project)
        chunks.extend([data['title'], data['description'], *data['bullets'], *data['technologies']])
    for item in getattr(profile, 'education', []):
        if isinstance(item, str):
            chunks.append(item)
        else:
            chunks.extend([getattr(item, 'school', ''), getattr(item, 'degree', ''), getattr(item, 'dates', '')])
    return ' '.join(chunk for chunk in chunks if chunk).lower()


def _matches(blob: str, term: str) -> bool:
    return term.lower() in blob


def _project_dict(project: Any) -> dict[str, Any]:
    if isinstance(project, dict):
        return {
            'title': project.get('title') or project.get('name') or 'Project',
            'description': project.get('description') or '',
            'bullets': list(project.get('bullets') or []),
            'technologies': list(project.get('technologies') or []),
        }
    return {
        'title': getattr(project, 'title', None) or getattr(project, 'name', None) or 'Project',
        'description': getattr(project, 'description', '') or '',
        'bullets': list(getattr(project, 'bullets', []) or []),
        'technologies': list(getattr(project, 'technologies', []) or []),
    }


def _emphasis_terms(requirements: JobRequirements) -> list[str]:
    return _dedupe(requirements.required_skills[:3] + requirements.preferred_skills[:2] + requirements.responsibilities[:2])


def _join_phrases(values: list[str]) -> str:
    if not values:
        return ''
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f'{values[0]} and {values[1]}'
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = normalize_text(str(item).strip())
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _merge_lists(items: list[str]) -> list[str]:
    return [item for item in items if item]


def _job_text(job: Any) -> str:
    values = []
    for attr in ('title', 'company', 'location', 'description', 'raw_text'):
        value = getattr(job, attr, None)
        if value is None and isinstance(job, dict):
            value = job.get(attr)
        values.append(str(value or ''))
    return '\n'.join(values)
