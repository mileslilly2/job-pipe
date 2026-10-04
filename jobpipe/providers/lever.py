from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..models import DiscoveredJob, JobSource


def fetch_lever_jobs(source: JobSource) -> list[DiscoveredJob]:
    account = source.account or source.company_slug
    if not account:
        raise ValueError("Lever sources require account or company_slug")
    payload = _fetch_with_fallback(account)
    rows = payload.get("data", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    company = source.company or account
    discovered: list[DiscoveredJob] = []
    for item in rows:
        categories = item.get("categories") or {}
        urls = item.get("urls") or {}
        content = item.get("content") or {}
        description = _build_description(content)
        location = str(categories.get("location") or _first_value(categories.get("allLocations")) or "")
        remote = _is_remote(str(item.get("workplaceType") or ""), location, description)
        posted_at = _millis_to_iso(item.get("createdAt"))
        discovered.append(
            DiscoveredJob(
                provider="lever",
                external_id=str(item.get("id") or ""),
                company=company,
                title=str(item.get("text") or ""),
                location=location,
                remote=remote,
                apply_url=str(urls.get("apply") or ""),
                description=description,
                posted_at=posted_at,
                source_url=str(urls.get("show") or urls.get("list") or urls.get("apply") or ""),
            )
        )
    return discovered


def _fetch_with_fallback(account: str) -> dict:
    urls = [
        f"https://api.lever.co/v0/postings/{account}?mode=json&include=content",
        f"https://api.lever.co/v1/postings/{account}?mode=json&include=content",
        f"https://api.lever.co/v0/postings/{account}?include=content",
        f"https://api.lever.co/v1/postings/{account}?include=content",
    ]
    last_error: Exception | None = None
    for url in urls:
        try:
            return _fetch_json(url)
        except (URLError, ValueError, TimeoutError) as exc:
            last_error = exc
    raise RuntimeError(f"Failed to fetch Lever account {account}") from last_error


def _fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "jobpipe/0.1"})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _build_description(content: dict) -> str:
    parts: list[str] = []
    description = str(content.get("description") or "").strip()
    if description:
        parts.append(description)
    for item in content.get("lists") or []:
        heading = str(item.get("text") or "").strip()
        body = _strip_html(str(item.get("content") or ""))
        if heading:
            parts.append(heading)
        if body:
            parts.append(body)
    closing = str(content.get("closing") or "").strip()
    if closing:
        parts.append(closing)
    return " ".join(part for part in parts if part)


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(text.split())


def _first_value(value) -> str:
    if isinstance(value, list):
        return str(value[0] or "") if value else ""
    return str(value or "")


def _millis_to_iso(value) -> str:
    if not value:
        return ""
    dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    return dt.isoformat(timespec="seconds")


def _is_remote(workplace_type: str, location: str, description: str) -> bool:
    text = f"{workplace_type} {location} {description}".lower()
    return any(term in text for term in ["remote", "work from home", "anywhere"])
