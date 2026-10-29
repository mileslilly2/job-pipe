"""Current dashboard workflow buckets, without scoring or state changes."""
from __future__ import annotations

from typing import Any

from .analytics import _display_status


def job_bucket(job: dict[str, Any]) -> str:
    display_status = _display_status(job)
    if display_status in {"skipped", "rejected", "interview", "offer", "applied"}:
        return display_status
    if display_status in {"analyzed", "ready_to_apply"}:
        return "assessment"
    if job.get("has_package"):
        return "packaged"
    if display_status in {"saved", "new", "archived", "ignored"}:
        return display_status
    return display_status or "new"
