from __future__ import annotations

import re

from .models import ParsedJob
from .utils import normalize_text


REMOTE_PATTERNS = {
    "remote": ["remote", "work from home", "fully remote"],
    "hybrid": ["hybrid"],
    "onsite": ["on-site", "onsite", "in office"],
}

EMPLOYMENT_PATTERNS = {
    "full_time": ["full-time", "full time"],
    "part_time": ["part-time", "part time"],
    "contract": ["contract", "contractor", "temporary"],
}


def parse_job_text(raw_text: str, source_url: str | None = None) -> ParsedJob:
    text = raw_text.strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = ""
    company = ""
    location = ""
    salary = _extract_salary(text)
    remote_type = _extract_tag(text, REMOTE_PATTERNS)
    employment_type = _extract_tag(text, EMPLOYMENT_PATTERNS)

    for line in lines[:8]:
        lower = line.lower()
        if line.lower().startswith("title:"):
            title = line.split(":", 1)[1].strip()
        elif line.lower().startswith("company:"):
            company = line.split(":", 1)[1].strip()
        elif line.lower().startswith("location:"):
            location = line.split(":", 1)[1].strip()

    if not title and lines:
        title = lines[0][:120]
    if not company and len(lines) > 1:
        company = _infer_company(lines[1:6])
    if not location:
        location = _infer_location(lines[:10])

    description = normalize_text(text)
    return ParsedJob(
        source_url=source_url,
        title=title,
        company=company,
        location=location,
        remote_type=remote_type,
        employment_type=employment_type,
        salary_text=salary,
        description=description,
        raw_text=text,
    )


def _extract_salary(text: str) -> str:
    match = re.search(r"(\$[\d,]+(?:\.\d+)?(?:\s*[-/to]+\s*\$?[\d,]+(?:\.\d+)?)?(?:\s*(?:per hour|/hour|hour|year|annually))?)", text, re.I)
    return match.group(1).strip() if match else ""


def _extract_tag(text: str, patterns: dict[str, list[str]]) -> str:
    lowered = text.lower()
    for label, terms in patterns.items():
        if any(term in lowered for term in terms):
            return label
    return ""


def _infer_company(lines: list[str]) -> str:
    for line in lines:
        if any(token in line.lower() for token in ["inc", "llc", "corp", "company", "technologies", "software"]):
            return line[:120]
    return lines[0][:120] if lines else ""


def _infer_location(lines: list[str]) -> str:
    for line in lines:
        if line.lower().startswith("location"):
            return line.split(":", 1)[1].strip()
        if "," in line and len(line) < 80:
            return line
    return ""
