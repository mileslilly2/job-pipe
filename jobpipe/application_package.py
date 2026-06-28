from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Any

from .catalog import load_search_profile
from .db import get_analysis, get_job
from .score import RESUME_KEYWORDS, score_job_fit_v1
from .utils import ensure_dir, load_json, normalize_text, root_dir, slugify


PACKAGE_FOCUS_TERMS = [
    "python",
    "automation",
    "api",
    "data engineering",
    "sql",
    "etl",
    "backend",
    "cli tools",
    "sqlite",
    "testing",
    "project pipeline",
    "project pipelines",
    "workflow automation",
    "debugging",
    "documentation",
]

PACKAGE_SKILL_PRIORITY = [
    "Python",
    "automation",
    "API testing",
    "CLI tools",
    "SQLite",
    "testing",
    "debugging",
    "workflow automation",
    "documentation",
    "SQL",
    "Git",
    "Playwright",
    "Jinja2",
]

PACKAGE_READY_STATUSES = {"saved", "packaged"}


@dataclass(frozen=True)
class ProfileProject:
    title: str
    bullets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BaseProfile:
    summary: str
    skills: list[str]
    projects: list[ProfileProject]
    education: list[str]


@dataclass(frozen=True)
class ApplicationPackagePaths:
    directory: Path
    job_md: Path
    fit_md: Path
    resume_tailored_md: Path
    cover_note_md: Path
    apply_md: Path


def profile_path() -> Path:
    return root_dir() / "profile.md"


def applications_dir() -> Path:
    return root_dir() / "applications"


def package_directory_for_job(job: dict[str, Any]) -> Path:
    return applications_dir() / f"{slugify(job.get('company') or 'unknown-company')}-{slugify(job.get('title') or 'unknown-title')}"


def application_package_paths(job: dict[str, Any]) -> ApplicationPackagePaths:
    directory = package_directory_for_job(job)
    return ApplicationPackagePaths(
        directory=directory,
        job_md=directory / "job.md",
        fit_md=directory / "fit.md",
        resume_tailored_md=directory / "resume-tailored.md",
        cover_note_md=directory / "cover-note.md",
        apply_md=directory / "apply.md",
    )


def load_base_profile(path: Path | None = None) -> BaseProfile:
    actual = path or profile_path()
    text = actual.read_text(encoding="utf-8")
    return _parse_base_profile(text)


def build_application_package(job_id: int, profile_file: Path | None = None) -> ApplicationPackagePaths:
    job_row = get_job(job_id)
    if not job_row:
        raise ValueError(f"Job {job_id} not found.")
    if str(job_row["status"] or "") not in PACKAGE_READY_STATUSES:
        raise ValueError("Run save before package.")

    analysis_row = get_analysis(job_id)
    job = dict(job_row)
    base_profile = load_base_profile(profile_file)
    fit_score, matched_keywords, missing_keywords, concerns = _fit_context(job, analysis_row)

    directory = package_directory_for_job(job)
    ensure_dir(directory)
    paths = application_package_paths(job)

    paths.job_md.write_text(_build_job_md(job), encoding="utf-8")
    paths.fit_md.write_text(_build_fit_md(fit_score, matched_keywords, missing_keywords, concerns), encoding="utf-8")
    paths.resume_tailored_md.write_text(_build_resume_tailored_md(job, base_profile), encoding="utf-8")
    paths.cover_note_md.write_text(_build_cover_note_md(job, fit_score, matched_keywords), encoding="utf-8")
    paths.apply_md.write_text(_build_apply_md(job, job_id), encoding="utf-8")
    return paths


def _parse_base_profile(text: str) -> BaseProfile:
    summary_lines: list[str] = []
    skills: list[str] = []
    projects: list[ProfileProject] = []
    education: list[str] = []

    section = ""
    current_project: ProfileProject | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            if current_project is not None:
                projects.append(current_project)
                current_project = None
            section = line[3:].strip().lower()
            continue
        if line.startswith("### ") and section == "projects":
            if current_project is not None:
                projects.append(current_project)
            current_project = ProfileProject(title=line[4:].strip())
            continue

        if section == "summary":
            summary_lines.append(line)
        elif section in {"core skills", "skills"} and line.startswith("- "):
            skills.append(line[2:].strip())
        elif section == "projects":
            if current_project is None:
                current_project = ProfileProject(title="Project highlights")
            if line.startswith("- "):
                current_project.bullets.append(line[2:].strip())
        elif section == "education" and line.startswith("- "):
            education.append(line[2:].strip())

    if current_project is not None:
        projects.append(current_project)

    summary = normalize_text(" ".join(summary_lines))
    return BaseProfile(summary=summary, skills=skills, projects=projects, education=education)


def _fit_context(job: dict[str, Any], analysis_row) -> tuple[int, list[str], list[str], list[str]]:
    text = " ".join(
        str(value or "")
        for value in (
            job.get("title"),
            job.get("company"),
            job.get("location"),
            job.get("description"),
            job.get("raw_text"),
        )
    ).lower()
    computed_score, notes = score_job_fit_v1(job, profile=load_search_profile())
    fit_score = computed_score
    matched_keywords = [keyword for keyword in RESUME_KEYWORDS if _phrase_in_text(text, keyword)]
    missing_keywords = [keyword for keyword in RESUME_KEYWORDS if keyword not in matched_keywords]
    concerns: list[str] = []

    if analysis_row is not None:
        red_flags = load_json(analysis_row["red_flags_json"])
        if isinstance(red_flags, list):
            concerns.extend(str(flag) for flag in red_flags)
        skip_reason = analysis_row["skip_reason"]
        if skip_reason:
            concerns.append(str(skip_reason))
        analysis_notes = analysis_row["notes"] or ""
        if analysis_notes:
            concerns.extend(note.strip() for note in analysis_notes.split("; ") if note.strip())
    else:
        concerns.extend(
            note
            for note in notes
            if note.startswith("-") or "ignored" in note or (note.startswith("role family:") and "(-" in note)
        )

    concerns = _dedupe_preserve_order(concerns)
    return fit_score, matched_keywords, missing_keywords, concerns


def _build_job_md(job: dict[str, Any]) -> str:
    lines = [
        "# Job",
        "",
        f"- Company: {job.get('company') or 'Unknown company'}",
        f"- Title: {job.get('title') or 'Unknown title'}",
        f"- Location: {job.get('location') or 'Unknown'}",
        f"- Apply URL: {job.get('apply_url') or job.get('source_url') or ''}",
        f"- Provider: {job.get('provider') or 'manual'}",
        "",
        "## Description",
        "",
        (job.get("description") or job.get("raw_text") or "").strip() or "No description stored.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _build_fit_md(fit_score: int, matched_keywords: list[str], missing_keywords: list[str], concerns: list[str]) -> str:
    lines = [
        "# Fit",
        "",
        f"- Fit score: {fit_score}/100",
        "",
        "## Matched keywords",
        "",
    ]
    lines.extend(f"- {keyword}" for keyword in (matched_keywords or ["None"]))
    lines.extend(["", "## Missing keywords", ""])
    lines.extend(f"- {keyword}" for keyword in (missing_keywords or ["None"]))
    lines.extend(["", "## Concerns / penalties", ""])
    lines.extend(f"- {concern}" for concern in (concerns or ["None recorded."]))
    return "\n".join(lines).rstrip() + "\n"


def _build_resume_tailored_md(job: dict[str, Any], base_profile: BaseProfile) -> str:
    job_text = " ".join(
        str(value or "")
        for value in (
            job.get("title"),
            job.get("company"),
            job.get("location"),
            job.get("description"),
            job.get("raw_text"),
        )
    ).lower()
    focus_terms = _select_focus_terms(job_text)
    summary = base_profile.summary
    if focus_terms:
        summary = f"{summary} For this role, I would foreground { _join_phrases(focus_terms[:3]) }."

    skills = _select_skills(base_profile.skills, job_text)
    projects = _select_projects(base_profile.projects, job_text)

    lines = [
        "# Tailored Resume",
        "",
        "## Summary",
        "",
        summary or "Project-focused technical problem solver.",
        "",
        "## Relevant Skills",
        "",
    ]
    lines.extend(f"- {skill}" for skill in skills)
    lines.extend(["", "## Selected Projects", ""])
    for project in projects:
        lines.extend([f"### {project.title}", ""])
        lines.extend(f"- {bullet}" for bullet in project.bullets)
        lines.append("")
    lines.extend(["## Education", ""])
    lines.extend(f"- {line}" for line in (base_profile.education or ["No education section in base profile."]))
    return "\n".join(lines).rstrip() + "\n"


def _build_cover_note_md(job: dict[str, Any], fit_score: int, matched_keywords: list[str]) -> str:
    company = job.get("company") or "your team"
    title = job.get("title") or "role"
    focus = ", ".join(matched_keywords[:3]) if matched_keywords else "practical problem solving"
    lines = [
        f"I am writing to apply for the {title} role at {company}. The posting looks like a good fit for my background in {focus}.",
        "",
        "I have built local automation pipelines, debugging scripts, and repeatable workflows with Python, SQLite, CLI tooling, and tests, and I am comfortable turning vague problems into clear steps.",
        "",
        f"This package reflects a measured fit score of {fit_score}/100, and I would be glad to discuss how I could contribute in a practical, reliable way.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _build_apply_md(job: dict[str, Any], job_id: int) -> str:
    company = job.get("company") or "Unknown company"
    title = job.get("title") or "Unknown title"
    apply_url = job.get("apply_url") or job.get("source_url") or ""
    lines = [
        f"# Apply Checklist: {company} - {title}",
        "",
        "- [ ] Review resume",
        "- [ ] Review cover note",
        f"- [ ] Open apply URL: {apply_url}",
        "- [ ] Submit application",
        f"- [ ] Run `jobpipe status {job_id} applied`",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _select_focus_terms(job_text: str) -> list[str]:
    terms = [
        term
        for term in PACKAGE_FOCUS_TERMS
        if _phrase_in_text(job_text, term)
    ]
    if terms:
        return _dedupe_preserve_order(terms)
    return []


def _select_skills(skills: list[str], job_text: str) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    for index, skill in enumerate(skills):
        score = 0
        lower = skill.lower()
        if _phrase_in_text(job_text, lower):
            score += 10
        for priority_index, priority in enumerate(PACKAGE_SKILL_PRIORITY):
            if lower == priority.lower():
                score += 20 - priority_index
        scored.append((-score, index, skill))
    scored.sort()
    selected = [skill for _, _, skill in scored if _score_for_skill(skill, job_text) > 0]
    if len(selected) < 6:
        for _, _, skill in scored:
            if skill not in selected:
                selected.append(skill)
            if len(selected) >= 10:
                break
    return selected[:10]


def _score_for_skill(skill: str, job_text: str) -> int:
    score = 0
    lower = skill.lower()
    if _phrase_in_text(job_text, lower):
        score += 10
    if lower in {"python", "automation", "sql", "sqlite", "testing", "cli tools", "api testing"}:
        score += 5
    return score


def _select_projects(projects: list[ProfileProject], job_text: str) -> list[ProfileProject]:
    scored: list[tuple[int, int, ProfileProject]] = []
    for index, project in enumerate(projects):
        blob = f"{project.title} {' '.join(project.bullets)}".lower()
        score = 0
        for term in PACKAGE_FOCUS_TERMS + RESUME_KEYWORDS:
            if _phrase_in_text(job_text, term) and _phrase_in_text(blob, term):
                score += 2
        if any(term in blob for term in ["python", "automation", "cli", "sqlite", "testing"]):
            score += 1
        scored.append((-score, index, project))
    scored.sort()
    selected = [project for score, _, project in scored if score < 0]
    if not selected:
        selected = [project for _, _, project in scored[:3]]
    return [_truncate_project(project, job_text) for project in selected[:3]]


def _truncate_project(project: ProfileProject, job_text: str) -> ProfileProject:
    if len(project.bullets) <= 2:
        return project
    selected_bullets = [
        bullet
        for bullet in project.bullets
        if any(_phrase_in_text(bullet.lower(), term) for term in PACKAGE_FOCUS_TERMS + RESUME_KEYWORDS)
        and _phrase_in_text(job_text, _best_term_for_bullet(bullet))
    ]
    if not selected_bullets:
        selected_bullets = project.bullets[:2]
    return ProfileProject(title=project.title, bullets=selected_bullets[:2])


def _best_term_for_bullet(bullet: str) -> str:
    lower = bullet.lower()
    for term in PACKAGE_FOCUS_TERMS + RESUME_KEYWORDS:
        if term in lower:
            return term
    return ""


def _join_phrases(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _phrase_in_text(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return re.search(pattern, text.lower()) is not None


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
