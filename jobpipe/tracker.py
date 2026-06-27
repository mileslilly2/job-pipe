from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .db import due_followups as db_due_followups, set_followup_at, set_status
from .models import ApplicationStatus


ALLOWED_STATUSES: tuple[ApplicationStatus, ...] = (
    "saved",
    "analyzed",
    "ready_to_apply",
    "applied",
    "rejected",
    "interview",
    "offer",
    "ignored",
    "archived",
)

DEFAULT_FOLLOWUP_DAYS = 7


def _default_followup_at() -> str:
    return (datetime.now(UTC) + timedelta(days=DEFAULT_FOLLOWUP_DAYS)).isoformat(timespec="seconds")


def due_followups(limit: int = 20):
    return db_due_followups(limit)


def update_status(job_id: int, status: str, notes: str = "") -> None:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    set_status(job_id, status, notes)
    if status == "applied":
        set_followup_at(job_id, _default_followup_at())
