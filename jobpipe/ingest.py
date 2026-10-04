from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .catalog import load_search_profile, normalize_job_source
from .classify import detect_seniority
from .models import DiscoveredJob, JobSource, ParsedJob, SearchProfile
from .catalog import source_identity_label
from .parse import parse_job_text
from .providers import discover_jobs as discover_provider_jobs
from .score import classify_role_family
from .utils import load_yaml, package_dir, root_dir, slugify


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
    "legal_counsel",
    "security_only",
    "ml_research",
    "senior_research",
    "data_products",
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
class FilterDecision:
    """Deterministic outcome of the existing source/profile eligibility rules."""

    accepted: bool
    reason_codes: tuple[str, ...]
    role_family: str
    seniority: str
    matched_positive_keywords: tuple[str, ...] = ()
    matched_negative_keywords: tuple[str, ...] = ()
    missing_required_signals: tuple[str, ...] = ()


@dataclass
class SourceDiscoveryReport:
    source: JobSource
    status: str
    fetched_count: int = 0
    passed_count: int = 0
    rejected_count: int = 0
    reason_counts: Counter[str] = field(default_factory=Counter)
    role_family_counts: Counter[str] = field(default_factory=Counter)
    rejected_role_family_counts: Counter[str] = field(default_factory=Counter)
    rejected_jobs: list[tuple[DiscoveredJob, FilterDecision]] = field(default_factory=list)
    accepted_jobs: list[DiscoveredJob] = field(default_factory=list)
    error: str | None = None

    @property
    def label(self) -> str:
        return source_identity_label(self.source)


@dataclass
class DiscoveryDiagnostics:
    source_reports: list[SourceDiscoveryReport] = field(default_factory=list)

    @property
    def fetched_count(self) -> int:
        return sum(report.fetched_count for report in self.source_reports)

    @property
    def passed_count(self) -> int:
        return sum(report.passed_count for report in self.source_reports)

    @property
    def rejected_count(self) -> int:
        return sum(report.rejected_count for report in self.source_reports)

    @property
    def reason_counts(self) -> Counter[str]:
        return Counter({key: sum(report.reason_counts[key] for report in self.source_reports) for key in {reason for report in self.source_reports for reason in report.reason_counts}})


@dataclass(frozen=True)
class LoadedJobSources:
    sources: list[JobSource]
    origin: str
    path: Path | None = None


def ingest_text_file(path: Path) -> ParsedJob:
    if _is_workspace_metadata_file(path):
        raise ValueError(f"{path.name} is workspace/package metadata, not a job posting.")
    text = path.read_text(encoding="utf-8")
    return parse_job_text(text)


def _is_workspace_metadata_file(path: Path) -> bool:
    """Reject only known metadata files from this workspace's own tree."""
    resolved = path.resolve()
    workspace = root_dir().resolve()
    if resolved in {workspace / "README.md", workspace / "AGENTS.md", workspace / "pyproject.toml"}:
        return True
    return resolved.name == "PKG-INFO" and workspace / "jobpipe.egg-info" in resolved.parents


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
        sources.append(normalize_job_source(source))
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


def discover_jobs_from_sources(sources: list[JobSource], profile: SearchProfile | None = None) -> list[DiscoveredJob]:
    active_profile = profile or load_search_profile()
    jobs: list[DiscoveredJob] = []
    for source in sources:
        jobs.extend(filter_discovered_jobs(discover_provider_jobs(source), source, active_profile))
    return jobs


def collect_discovery_diagnostics(
    sources: list[JobSource],
    profile: SearchProfile | None = None,
    *,
    include_disabled: bool = False,
) -> DiscoveryDiagnostics:
    """Fetch and evaluate sources in memory without persisting rejected jobs."""
    active_profile = profile or load_search_profile()
    reports: list[SourceDiscoveryReport] = []
    for source in sources:
        if not source.enabled:
            if include_disabled:
                reports.append(SourceDiscoveryReport(source=source, status="not_run_disabled"))
            continue
        try:
            discovered = discover_provider_jobs(source)
        except Exception as exc:  # pragma: no cover - provider/network guard
            reports.append(SourceDiscoveryReport(source=source, status="failed", error=str(exc)))
            continue
        report = SourceDiscoveryReport(source=source, status="success", fetched_count=len(discovered))
        for job in discovered:
            decision = evaluate_discovered_job(job, source, active_profile)
            report.role_family_counts[decision.role_family or "unknown"] += 1
            if decision.accepted:
                report.passed_count += 1
                report.accepted_jobs.append(job)
            else:
                report.rejected_count += 1
                report.reason_counts.update(decision.reason_codes)
                report.rejected_role_family_counts[decision.role_family or "unknown"] += 1
                report.rejected_jobs.append((job, decision))
        reports.append(report)
    return DiscoveryDiagnostics(reports)


def filter_discovered_jobs(jobs: list[DiscoveredJob], source: JobSource, profile: SearchProfile | None = None) -> list[DiscoveredJob]:
    active_profile = profile or load_search_profile()
    include_keywords = _explicit_keywords(source.include_keywords)
    exclude_keywords = _effective_keywords(source.exclude_keywords, DEFAULT_EXCLUDE_KEYWORDS)
    target_role_families = _effective_role_families(source.target_role_families, active_profile.preferred + active_profile.acceptable)
    profile_keywords = _explicit_keywords(active_profile.positive_keywords)
    profile_negative_keywords = _explicit_keywords(active_profile.negative_keywords)
    return [
        job for job in jobs
        if evaluate_discovered_job(
            job, source, active_profile,
            target_role_families=target_role_families,
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            profile_keywords=profile_keywords,
            profile_negative_keywords=profile_negative_keywords,
        ).accepted
    ]


def _normalize_job_source(source: JobSource) -> JobSource:
    return normalize_job_source(source)


def _explicit_keywords(explicit: list[str]) -> list[str]:
    return [term.strip().lower() for term in explicit if term and term.strip()]


def _effective_keywords(explicit: list[str], defaults: list[str]) -> list[str]:
    values = _explicit_keywords(explicit)
    return values or defaults


def _effective_role_families(explicit: list[str], profile_role_families: list[str] | None = None) -> list[str]:
    values = [value.strip().lower() for value in explicit if value and value.strip()]
    if values and profile_role_families:
        return [value for value in values if value in [role.strip().lower() for role in profile_role_families]] or values
    return values or [value.strip().lower() for value in (profile_role_families or DEFAULT_TARGET_ROLE_FAMILIES) if value and value.strip()]


def evaluate_discovered_job(
    job: DiscoveredJob,
    source: JobSource,
    profile: SearchProfile | None = None,
    *,
    target_role_families: list[str] | None = None,
    include_keywords: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
    profile_keywords: list[str] | None = None,
    profile_negative_keywords: list[str] | None = None,
) -> FilterDecision:
    """Evaluate every current filter rule and return deterministic reason codes."""
    active_profile = profile or load_search_profile()
    include = include_keywords if include_keywords is not None else _explicit_keywords(source.include_keywords)
    exclude = exclude_keywords if exclude_keywords is not None else _effective_keywords(source.exclude_keywords, DEFAULT_EXCLUDE_KEYWORDS)
    targets = target_role_families if target_role_families is not None else _effective_role_families(source.target_role_families, active_profile.preferred + active_profile.acceptable)
    positive = profile_keywords if profile_keywords is not None else _explicit_keywords(active_profile.positive_keywords)
    negative = profile_negative_keywords if profile_negative_keywords is not None else _explicit_keywords(active_profile.negative_keywords)
    family, _matched, _reason = classify_role_family(job)
    parsed_job = ParsedJob(title=job.title, company=job.company, location=job.location, description=job.description)
    seniority, _flags = detect_seniority(parsed_job)
    text = " ".join([job.title, job.company, job.location, job.description]).lower()
    reasons: list[str] = []
    if family not in targets:
        reasons.append("role_family_not_targeted")
    if family in {value.strip().lower() for value in active_profile.avoid if value and value.strip()}:
        reasons.append("role_family_avoided")
    if seniority in {level.strip().lower() for level in active_profile.excluded_levels if level and level.strip()}:
        reasons.append("seniority_excluded")
    if any(_phrase_in_text(text, term) for term in negative):
        reasons.append("profile_negative_keyword")
    if not _has_title_signal(job, family, include, positive):
        reasons.append("missing_title_signal")
    required_keywords = include or positive
    if required_keywords and not any(_phrase_in_text(text, term) for term in required_keywords):
        reasons.append("keyword_requirement_missing")
    if any(_phrase_in_text(text, term) for term in exclude):
        reasons.append("keyword_excluded")
    positive_matches = tuple(term for term in required_keywords if _phrase_in_text(text, term))
    negative_matches = tuple(term for term in list(exclude) + list(negative) if _phrase_in_text(text, term))
    missing_signals = tuple(reason for reason in reasons if reason in {"missing_title_signal", "keyword_requirement_missing"})
    return FilterDecision(not reasons, tuple(reasons), family, seniority, positive_matches, negative_matches, missing_signals)


def _job_matches_source(
    job: DiscoveredJob,
    target_role_families: list[str],
    include_keywords: list[str],
    exclude_keywords: list[str],
    profile_keywords: list[str],
    profile_negative_keywords: list[str],
    excluded_levels: list[str],
    avoid_role_families: list[str],
) -> bool:
    profile = SearchProfile(name="filter", preferred=[], acceptable=[], avoid=avoid_role_families, positive_keywords=profile_keywords, negative_keywords=profile_negative_keywords, excluded_levels=excluded_levels)
    source = JobSource(provider=job.provider, target_role_families=target_role_families, include_keywords=include_keywords, exclude_keywords=exclude_keywords)
    return evaluate_discovered_job(job, source, profile).accepted

def _job_matches_keywords(
    job: DiscoveredJob,
    include_keywords: list[str],
    exclude_keywords: list[str],
    profile_keywords: list[str],
    profile_negative_keywords: list[str],
) -> bool:
    text = " ".join([job.title, job.company, job.location, job.description]).lower()
    required_keywords = include_keywords or profile_keywords
    if required_keywords and not any(_phrase_in_text(text, term) for term in required_keywords):
        return False
    if any(_phrase_in_text(text, term) for term in exclude_keywords):
        return False
    if any(_phrase_in_text(text, term) for term in profile_negative_keywords):
        return False
    return True


def _has_title_signal(job: DiscoveredJob, family: str, include_keywords: list[str], profile_keywords: list[str]) -> bool:
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
    if title_family == family:
        return True
    if any(_phrase_in_text(title_text, term) for term in include_keywords + profile_keywords):
        return True
    return any(_phrase_in_text(title_text, term) for term in TITLE_SIGNAL_KEYWORDS)


def _phrase_in_text(text: str, phrase: str) -> bool:
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return re.search(pattern, text) is not None


def _job_matches_profile(job: DiscoveredJob, profile_negative_keywords: list[str], excluded_levels: list[str]) -> bool:
    if profile_negative_keywords:
        text = " ".join([job.title, job.company, job.location, job.description]).lower()
        if any(_phrase_in_text(text, term) for term in profile_negative_keywords):
            return True

    parsed_job = ParsedJob(title=job.title, company=job.company, location=job.location, description=job.description)
    seniority, _flags = detect_seniority(parsed_job)
    return seniority in {level.strip().lower() for level in excluded_levels if level and level.strip()}


def _path_is_blank_or_empty_list(path: Path) -> bool:
    contents = path.read_text(encoding="utf-8").strip()
    return contents in {"", "[]"}


def _clean_html_text(value: str) -> str:
    from html import unescape

    return unescape(value or "")
