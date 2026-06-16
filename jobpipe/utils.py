from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def root_dir() -> Path:
    return Path.cwd()


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    return root_dir() / "data"


def output_dir() -> Path:
    return root_dir() / "output"


def db_path() -> Path:
    return root_dir() / "jobpipe.db"


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def load_json(value: str | None) -> Any:
    if not value:
        return []
    return json.loads(value)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    return "-".join(part for part in cleaned.split("-") if part) or "job"


def normalize_text(value: str) -> str:
    return " ".join(value.split())
