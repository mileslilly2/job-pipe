from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .models import ParsedJob
from .parse import parse_job_text


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


def _clean_html_text(value: str) -> str:
    return unescape(value or "")
