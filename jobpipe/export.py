from __future__ import annotations

from pathlib import Path

from .db import export_applications_csv
from .utils import output_dir


def export_tracker_csv() -> Path:
    return export_applications_csv(output_dir() / "applications.csv")
