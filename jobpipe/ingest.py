from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .models import DiscoveredJob, JobSource, ParsedJob
from .parse import parse_job_text
from .providers import discover_jobs as discover_provider_jobs
from .score import classify_role_family
from .utils import load_yaml, package_dir, slugify


DEFAULT_TARGET_ROLE_FAMILIES = [
    "junior_software_engineer",
    "software_engineer",
    "backend_api",
    "data_engineer",
    "python_automation",
    "qa_automation",
    "ai_evaluation",
    "technical_operations",
]

NEGATIVE_ROLE_FAMILIES = {
    "help_desk",
    "customer_support",
    "it_support",
    "sales",
    "clinical_admin",
    "manager",
}

DEFAULT_EXCLUDE_KEYWORDS = [
    "help desk",
    "helpdesk",
    "service desk",
    "customer support",
    "customer success",
    "technical support",
    "it support",
    "desktop support",
    "support engineer",
    "sales",
    "sales engineer",
    "account executive",
    "business development",
    "manager",
    "director",
    "principal",
    "staff",
    "lead",
    "clinical",
    "nurse",
    "medical",
    "healthcare",
    "admin",
]

TITLE_SIGNAL_KEYWORDS = [
    "engineer",
    "developer",
    "automation",
    "backend",
    "python",
    "software",
    "data",
    "qa",
    "quality",
    "sdet",
    "platform",
    "evaluation",
]


@dataclass(frozen=True)
class LoadedJobSources:
    sources: list[JobSource]
    origin: str
    path: Path | None = None


def ingest_text_file(path: Path) -> ParsedJob:
    text = path.read_text(encoding="utf-8")
    return parse_job_text(text)


def ingest_url(url: str) -> tuple[ParsedJob, str | None]:
    try:
        request = Request(url, headers={"User-Agent": "jobpipe/0.1"})
        with urlopen(request, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except (URLError, ValueError, TimeoutError):
        return ParsedJob(source_url=url, raw_text="", description=""), "Unable to extract job page. Save the URL, then use `jobpipe add-text --file PATH` with pasted job text."

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = _clean_html_text(title_match.group(1)) if title_match else ""
    body = _clean_html_text(re.sub(r"<(script|style).*?</\1>", " ", html, flags=re.I | re.S))
    body = re.sub(r"<[^>]+>", " ", body)
    body = " ".join(body.split())
    if len(body.split()) < 60:
        parsed = ParsedJob(source_url=url, title=title, raw_text=body, description=body)
        return parsed, "Extraction was too thin. The URL was saved, but you should paste the full job description with `jobpipe add-text --file PATH`."
    parsed = parse_job_text(f"{title}\n\n{body}", source_url=url)
    return parsed, None


def load_job_sources(path: Path) -> list[JobSource]:
    if not path.exists():
        return []
    raw = load_yaml(path)
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("job source config must be a YAML list")
    sources: list[JobSource] = []
    for item in raw:
        source = JobSource.model_validate(item)
        if not source.enabled:
            continue
        sources.append(_normalize_job_source(source))
    return sources


def resolve_job_sources(workspace_path: Path) -> LoadedJobSources:
    workspace_is_missing = not workspace_path.exists()
    workspace_is_blank = not workspace_is_missing and _path_is_blank_or_empty_list(workspace_path)

    if not workspace_is_missing and not workspace_is_blank:
        workspace_sources = load_job_sources(workspace_path)
        return LoadedJobSources(workspace_sources, "workspace", workspace_path)

    built_in_path = package_dir() / "data" / "job_sources.yaml"
    built_in_sources = load_job_sources(built_in_path)
    if built_in_sources:
        return LoadedJobSources(built_in_sources, "built_in", built_in_path)

    if workspace_is_missing or workspace_is_blank:
        return LoadedJobSources([], "none", workspace_path)
    return LoadedJobSources([], "none", None)


def discover_jobs_from_sources(sources: list[JobSource]) -> list[DiscoveredJob]:
    jobs: list[DiscoveredJob] = []
    for source in sources:
        jobs.extend(filter_discovered_jobs(discover_provider_jobs(source), source))
    return jobs


def filter_discovered_jobs(jobs: list[DiscoveredJob], source: JobSource) -> list[DiscoveredJob]:
    include_keywords = _explicit_keywords(source.include_keywords)
    exclude_keywords = _effective_keywords(source.exclude_keywords, DEFAULT_EXCLUDE_KEYWORDS)
    target_role_families = _effective_role_families(source.target_role_families)
    filtered: list[DiscoveredJob] = []
    for job in jobs:
        if _job_matches_source(job, target_role_families, include_keywords, exclude_keywords):
            filtered.append(job)
    return filtered


def _normalize_job_source(source: JobSource) -> JobSource:
    company_slug = source.company_slug or source.board_token or source.account or slugify(source.company or source.provider)
    target_role_families = _effective_role_families(source.target_role_families)
    return source.model_copy(update={"company_slug": company_slug, "target_role_families": target_role_families})


def _explicit_keywords(explicit: list[str]) -> list[str]:
    return [term.strip().lower() for term in explicit if term and term.strip()]


def _effective_keywords(explicit: list[str], defaults: list[str]) -> list[str]:
    values = _explicit_keywords(explicit)
    return values or defaults


def _effective_role_families(explicit: list[str]) -> list[str]:
    values = [value.strip().lower() for value in explicit if value and value.strip()]
    return values or list(DEFAULT_TARGET_ROLE_FAMILIES)


def _job_matches_source(
    job: DiscoveredJob,
    target_role_families: list[str],
    include_keywords: list[str],
    exclude_keywords: list[str],
) -> bool:
    family, _matched, _reason = classify_role_family(job)
    if family in NEGATIVE_ROLE_FAMILIES:
        return False
    if family not in target_role_families:
        return False
    if not _has_title_signal(job, family, include_keywords):
        return False
    return _job_matches_keywords(job, include_keywords, exclude_keywords)


def _job_matches_keywords(job: DiscoveredJob, include_keywords: list[str], exclude_keywords: list[str]) -> bool:
    text = " ".join([job.title, job.company, job.location, job.description]).lower()
    if include_keywords and not any(_phrase_in_text(text, term) for term in include_keywords):
        return False
    if any(_phrase_in_text(text, term) for term in exclude_keywords):
        return False
    return True


def _has_title_signal(job: DiscoveredJob, family: str, include_keywords: list[str]) -> bool:
    title_text = (job.title or "").lower()
    if not title_text:
        return False

    title_family, _matched, _reason = classify_role_family(
        {
            "title": job.title,
            "company": "",
            "location": "",
            "description": "",
        }
    )
    if title_family in NEGATIVE_ROLE_FAMILIES:
        return False
    if title_family == family:
        return True
    if any(_phrase_in_text(title_text, term) for term in include_keywords):
        return True
    return any(_phrase_in_text(title_text, term) for term in TITLE_SIGNAL_KEYWORDS)


def _phrase_in_text(text: str, phrase: str) -> bool:
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return re.search(pattern, text) is not None


def _path_is_blank_or_empty_list(path: Path) -> bool:
    contents = path.read_text(encoding="utf-8").strip()
    return contents in {"", "[]"}


def _clean_html_text(value: str) -> str:
    from html import unescape

    return unescape(value or "")
