from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from .utils import db_path, dump_json, ensure_dir, now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    remote_type TEXT,
    employment_type TEXT,
    salary_text TEXT,
    description TEXT,
    raw_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    role_type TEXT,
    seniority_level TEXT,
    fit_score INTEGER,
    risk_score INTEGER,
    quality_score INTEGER,
    matched_keywords_json TEXT,
    missing_keywords_json TEXT,
    red_flags_json TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS application_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    resume_path TEXT,
    packet_path TEXT,
    answers_path TEXT,
    cover_note_path TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL,
    applied_at TEXT,
    followup_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    actual = path or db_path()
    ensure_dir(actual.parent)
    conn = sqlite3.connect(actual)
    conn.row_factory = sqlite3.Row
    return conn


def init_database(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def insert_job(data: dict[str, Any]) -> int:
    timestamp = now_iso()
    payload = {
        "source_url": data.get("source_url", ""),
        "title": data.get("title", ""),
        "company": data.get("company", ""),
        "location": data.get("location", ""),
        "remote_type": data.get("remote_type", ""),
        "employment_type": data.get("employment_type", ""),
        "salary_text": data.get("salary_text", ""),
        "description": data.get("description", ""),
        "raw_text": data.get("raw_text", ""),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (
                source_url, title, company, location, remote_type, employment_type,
                salary_text, description, raw_text, created_at, updated_at
            ) VALUES (
                :source_url, :title, :company, :location, :remote_type, :employment_type,
                :salary_text, :description, :raw_text, :created_at, :updated_at
            )
            """,
            payload,
        )
        job_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO applications (job_id, status, created_at, updated_at)
            VALUES (?, 'saved', ?, ?)
            ON CONFLICT(job_id) DO NOTHING
            """,
            (job_id, timestamp, timestamp),
        )
        return job_id


def update_job(job_id: int, data: dict[str, Any]) -> None:
    data = dict(data)
    data["id"] = job_id
    data["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = :{key}" for key in data if key != "id")
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = :id", data)


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(query, params).fetchone()


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def get_job(job_id: int) -> sqlite3.Row | None:
    return fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))


def get_analysis(job_id: int) -> sqlite3.Row | None:
    return fetch_one("SELECT * FROM job_analysis WHERE job_id = ?", (job_id,))


def get_application(job_id: int) -> sqlite3.Row | None:
    return fetch_one("SELECT * FROM applications WHERE job_id = ?", (job_id,))


def upsert_analysis(job_id: int, analysis: dict[str, Any]) -> None:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO job_analysis (
                job_id, role_type, seniority_level, fit_score, risk_score, quality_score,
                matched_keywords_json, missing_keywords_json, red_flags_json, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                role_type = excluded.role_type,
                seniority_level = excluded.seniority_level,
                fit_score = excluded.fit_score,
                risk_score = excluded.risk_score,
                quality_score = excluded.quality_score,
                matched_keywords_json = excluded.matched_keywords_json,
                missing_keywords_json = excluded.missing_keywords_json,
                red_flags_json = excluded.red_flags_json,
                notes = excluded.notes,
                created_at = excluded.created_at
            """,
            (
                job_id,
                analysis["role_type"],
                analysis["seniority_level"],
                analysis["fit_score"],
                analysis["risk_score"],
                analysis["quality_score"],
                dump_json(analysis["matched_keywords"]),
                dump_json(analysis["missing_keywords"]),
                dump_json(analysis["red_flags"]),
                analysis["notes"],
                timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO applications (job_id, status, created_at, updated_at)
            VALUES (?, 'analyzed', ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET status = 'analyzed', updated_at = excluded.updated_at
            """,
            (job_id, timestamp, timestamp),
        )


def upsert_packet_paths(job_id: int, resume_path: str = "", packet_path: str = "", answers_path: str = "", cover_note_path: str = "") -> None:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO application_packets (job_id, resume_path, packet_path, answers_path, cover_note_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                resume_path = CASE WHEN excluded.resume_path = '' THEN application_packets.resume_path ELSE excluded.resume_path END,
                packet_path = CASE WHEN excluded.packet_path = '' THEN application_packets.packet_path ELSE excluded.packet_path END,
                answers_path = CASE WHEN excluded.answers_path = '' THEN application_packets.answers_path ELSE excluded.answers_path END,
                cover_note_path = CASE WHEN excluded.cover_note_path = '' THEN application_packets.cover_note_path ELSE excluded.cover_note_path END,
                created_at = excluded.created_at
            """,
            (job_id, resume_path, packet_path, answers_path, cover_note_path, timestamp),
        )


def set_status(job_id: int, status: str, notes: str = "") -> None:
    timestamp = now_iso()
    applied_at = timestamp if status == "applied" else None
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO applications (job_id, status, applied_at, followup_at, notes, created_at, updated_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                applied_at = COALESCE(excluded.applied_at, applications.applied_at),
                notes = CASE WHEN excluded.notes = '' THEN applications.notes ELSE excluded.notes END,
                updated_at = excluded.updated_at
            """,
            (job_id, status, applied_at, notes, timestamp, timestamp),
        )


def list_jobs() -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT
            jobs.id, jobs.title, jobs.company, jobs.source_url,
            job_analysis.role_type, job_analysis.fit_score, job_analysis.risk_score, job_analysis.quality_score,
            applications.status
        FROM jobs
        LEFT JOIN job_analysis ON job_analysis.job_id = jobs.id
        LEFT JOIN applications ON applications.job_id = jobs.id
        ORDER BY jobs.id ASC
        """
    )


def shortlist_jobs(min_fit: int, max_risk: int, limit: int) -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT
            jobs.id, jobs.title, jobs.company,
            job_analysis.role_type, job_analysis.fit_score, job_analysis.risk_score, job_analysis.quality_score,
            applications.status,
            (COALESCE(job_analysis.fit_score, 0) + COALESCE(job_analysis.quality_score, 0) - COALESCE(job_analysis.risk_score, 0)) AS combined_score
        FROM jobs
        JOIN job_analysis ON job_analysis.job_id = jobs.id
        LEFT JOIN applications ON applications.job_id = jobs.id
        WHERE job_analysis.fit_score >= ? AND job_analysis.risk_score <= ?
        ORDER BY combined_score DESC, jobs.id ASC
        LIMIT ?
        """,
        (min_fit, max_risk, limit),
    )


def export_applications_csv(path: Path) -> Path:
    rows = fetch_all(
        """
        SELECT
            jobs.id, jobs.title, jobs.company, jobs.location, jobs.source_url,
            job_analysis.role_type, job_analysis.fit_score, job_analysis.risk_score, job_analysis.quality_score,
            applications.status, applications.applied_at, applications.followup_at, applications.notes
        FROM jobs
        LEFT JOIN job_analysis ON job_analysis.job_id = jobs.id
        LEFT JOIN applications ON applications.job_id = jobs.id
        ORDER BY jobs.id ASC
        """
    )
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [
            "id", "title", "company", "location", "source_url", "role_type",
            "fit_score", "risk_score", "quality_score", "status", "applied_at", "followup_at", "notes",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return path
