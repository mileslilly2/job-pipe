from __future__ import annotations

from .greenhouse import fetch_greenhouse_jobs
from .lever import fetch_lever_jobs

PROVIDER_FETCHERS = {
    "greenhouse": fetch_greenhouse_jobs,
    "lever": fetch_lever_jobs,
}


def discover_jobs(source):
    provider = (source.provider or "").lower().strip()
    fetcher = PROVIDER_FETCHERS.get(provider)
    if not fetcher:
        raise ValueError(f"Unsupported provider: {source.provider}")
    return fetcher(source)
