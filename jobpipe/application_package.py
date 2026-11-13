from __future__ import annotations

from dataclasses import dataclass, field
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .catalog import load_search_profile
from .db import get_job
from .facts import build_fact_report
from .job_requirements import build_fit_summary, build_resume_tailoring
from .score import RESUME_KEYWORDS, score_job_fit_v1
from .utils import ensure_dir, normalize_text, root_dir, slugify


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
    employment: list[dict[str, Any]] = field(default_factory=list)
    verified: bool = False


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
    company = slugify(job.get("company") or "unknown-company")
    title = slugify(job.get("title") or "unknown-title")
    job_id = job.get("id")
    if job_id is not None and str(job_id).strip():
        suffix = f"-job-{job_id}"
    else:
        external_id = slugify(job.get("external_id") or "")
        suffix = f"-{external_id}" if external_id else ""
    return applications_dir() / f"{company}-{title}{suffix}"


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
    from .applicant_profile import candidate_projection, state
    if path is None and state()["active"]:
        candidate = candidate_projection()
        return BaseProfile(summary=candidate.profile.get("summary", ""), skills=[s for values in candidate.skills.values() for s in values],
                           projects=[ProfileProject(p.name, p.bullets) for p in candidate.projects],
                           education=[" | ".join(filter(None, [e.school, e.degree, e.dates])) for e in candidate.education],
                           employment=[e.model_dump() for e in candidate.experience], verified=True)
    actual = path or profile_path()
    text = actual.read_text(encoding="utf-8")
    return _parse_base_profile(text)


def existing_package(job: dict[str, Any]) -> ApplicationPackagePaths | None:
    """Return a complete, regular bundle; refuse ambiguous or unsafe destinations."""
    paths = application_package_paths(job)
    root = applications_dir()
    if root.is_symlink() or paths.directory.is_symlink():
        raise ValueError("Application package destination must not be a symlink.")
    if paths.directory.resolve().parent != root.resolve():
        raise ValueError("Application package destination escapes applications directory.")
    artifacts = (paths.job_md, paths.fit_md, paths.resume_tailored_md, paths.cover_note_md, paths.apply_md)
    if any(path.is_symlink() for path in artifacts):
        raise ValueError("Application package artifacts must not be symlinks.")
    if not paths.directory.exists():
        return None
    if not paths.directory.is_dir() or not all(path.is_file() for path in artifacts):
        raise ValueError("Incomplete application package; inspect and move it aside before preparing again. Existing files were preserved.")
    return paths


def build_application_package(job_id: int, profile_file: Path | None = None, *, preparation_mode: str | None = None) -> ApplicationPackagePaths:
    job_row = get_job(job_id)
    if not job_row:
        raise ValueError(f"Job {job_id} not found.")
    if str(job_row["status"] or "") not in PACKAGE_READY_STATUSES:
        raise ValueError("Run save before package.")

    job = dict(job_row)
    existing = existing_package(job)
    if existing is not None:
        return existing
    if preparation_mode is None:
        from .applicant_profile import state
        from .services import get_job_eligibility
        preparation_mode = "draft_preparation" if state()["active"] and get_job_eligibility(job_id)["eligibility"]["status"] != "eligible" else "ready_for_submission"
    base_profile = load_base_profile(profile_file)
    fit_score, fact_report = _fit_context(job)
    fit_summary = build_fit_summary(job, base_profile)
    tailoring = build_resume_tailoring(job, base_profile)

    paths = application_package_paths(job)
    content = {
        paths.job_md.name: _build_job_md(job),
        paths.fit_md.name: _build_fit_md(fit_score, fit_summary),
        paths.resume_tailored_md.name: _build_resume_tailored_md(tailoring),
        paths.cover_note_md.name: _build_cover_note_md(job, fit_score, fact_report, tailoring),
        paths.apply_md.name: _build_apply_md(job, job_id),
    }
    if base_profile.verified:
        employment_lines = []
        for record in base_profile.employment:
            employment_lines.extend([f"### {record['title']} — {record['organization']}", record["dates"], *[f"- {b}" for b in record["bullets"]], ""])
        if employment_lines:
            content[paths.resume_tailored_md.name] += "\n## Professional Employment\n\n" + "\n".join(employment_lines)
    if preparation_mode == "draft_preparation":
        from .services import get_job_eligibility
        assessment = get_job_eligibility(job_id)["eligibility"]
        banner = "> DRAFT — BLOCKED FROM SUBMISSION READINESS. Eligibility is not established.\n\n"
        content = {name: banner + value for name, value in content.items()}
        content[paths.apply_md.name] += "\n## Unresolved Information\n\n" + "\n".join(f"- {gap}" for gap in [*assessment["missing_applicant_information"], *assessment["missing_employer_information"], *assessment["concerns"]]) + "\n"
    ensure_dir(applications_dir())
    staging = Path(tempfile.mkdtemp(prefix=".jobpipe-package-", dir=applications_dir()))
    try:
        for name, text in content.items():
            (staging / name).write_text(text, encoding="utf-8")
        # A competing writer must never cause replacement of user materials.
        if paths.directory.exists() or paths.directory.is_symlink():
            raise ValueError("Application package appeared during preparation; retry after inspection.")
        staging.rename(paths.directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
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


def _fit_context(job: dict[str, Any]) -> tuple[int, Any]:
    from .services import job_score
    profile = load_search_profile()
    computed_score, _notes = job_score(job, profile=profile)
    fact_report = build_fact_report(job, profile)
    return computed_score, fact_report


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


def _build_fit_md(fit_score: int, fit_summary) -> str:
    lines = [
        "# Fit",
        "",
        f"- Fit score: {fit_score}/100",
        "",
        "## Matched Requirements",
        "",
    ]
    lines.extend(f"- {item}" for item in (fit_summary.matched_requirements or ["None"]))
    lines.extend(["", "## Missing Requirements", ""])
    lines.extend(f"- {item}" for item in (fit_summary.missing_requirements or ["None"]))
    lines.extend(["", "## Concerns", ""])
    lines.extend(f"- {item}" for item in (fit_summary.concerns or ["None recorded."]))
    lines.extend(["", "## Suggested Emphasis", ""])
    lines.extend(f"- {item}" for item in (fit_summary.suggested_emphasis or ["None recorded."]))
    return "\n".join(lines).rstrip() + "\n"


def _build_resume_tailored_md(tailoring) -> str:
    lines = [
        "# Tailored Resume",
        "",
        "## Summary",
        "",
        tailoring.summary or "[Summary requires applicant confirmation.]",
        "",
        "## Relevant Skills",
        "",
    ]
    lines.extend(f"- {skill}" for skill in (tailoring.selected_skills or ["None"]))
    lines.extend(["", "## Selected Projects", ""])
    for project in tailoring.selected_projects:
        lines.extend([f"### {project['title']}", ""])
        lines.extend(f"- {bullet}" for bullet in (project.get("bullets") or []))
        lines.append("")
    lines.extend(["## Suggested Emphasis", ""])
    lines.extend(f"- {item}" for item in (tailoring.emphasis or ["None recorded."]))
    lines.extend(["", "## Education", ""])
    lines.extend(f"- {line}" for line in (tailoring.education or ["No education section in base profile."]))
    return "\n".join(lines).rstrip() + "\n"


def _build_cover_note_md(job: dict[str, Any], fit_score: int, fact_report, tailoring) -> str:
    company = job.get("company") or "your team"
    title = job.get("title") or "role"
    from .applicant_profile import state
    if state()["active"]:
        lines = [f"I am interested in the {title} role at {company}."]
        if tailoring.selected_skills:
            lines.append("My confirmed skills include " + ", ".join(tailoring.selected_skills[:4]) + ".")
        for project in tailoring.selected_projects[:1]:
            lines.append(f"My independent project work includes {project['title']}.")
        return "\n\n".join(lines) + "\n"
    focus = ", ".join(tailoring.selected_skills[:4]) if tailoring.selected_skills else "practical problem solving"
    project_focus = tailoring.selected_projects[0]["title"] if tailoring.selected_projects else "repeatable workflows"
    lines = [
        f"I am writing to apply for the {title} role at {company}. The posting looks like a good fit for my background in {focus}.",
        "",
        f"I have built local automation pipelines, debugging scripts, and repeatable workflows around {project_focus}, and I am comfortable turning vague problems into clear steps.",
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
