from __future__ import annotations

from .db import set_status
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


def update_status(job_id: int, status: str, notes: str = "") -> None:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    set_status(job_id, status, notes)
