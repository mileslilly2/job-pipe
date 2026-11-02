from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..models import DiscoveredJob, JobSource


def fetch_greenhouse_jobs(source: JobSource) -> list[DiscoveredJob]:
    board_token = source.board_token or source.company_slug
    if not board_token:
        raise ValueError("Greenhouse sources require board_token or company_slug")
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    try:
        payload = _fetch_json(url)
    except (URLError, ValueError, TimeoutError) as exc:
        raise RuntimeError(f"Failed to fetch Greenhouse board {board_token}: {type(exc).__name__}: {exc}") from exc
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    company = source.company or board_token
    discovered: list[DiscoveredJob] = []
    for item in jobs:
        location = _location_name(item.get("location"))
        description = _clean_text(item.get("content") or "")
        discovered.append(
            DiscoveredJob(
                provider="greenhouse",
                external_id=str(item.get("id") or ""),
                company=company,
                title=str(item.get("title") or ""),
                location=location,
                remote=_is_remote(location, description),
                apply_url=str(item.get("absolute_url") or ""),
                description=description,
                posted_at=str(item.get("updated_at") or ""),
                source_url=str(item.get("absolute_url") or ""),
            )
        )
    return discovered


def _fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "jobpipe/0.1"})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _location_name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def _clean_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"</?(?:p|div|li|h[1-6]|ul|ol)\b[^>]*>|<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def _is_remote(location: str, description: str) -> bool:
    text = f"{location} {description}".lower()
    return any(term in text for term in ["remote", "work from home", "anywhere"])
