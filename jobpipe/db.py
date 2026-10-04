from __future__ import annotations

import csv
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from .catalog import load_search_profile
from .classification import classify_job as classify_canonical_job
from .score import score_job_fit_v1
from .utils import db_path, dump_json, ensure_dir, now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT NOT NULL DEFAULT '',
    source_url TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    remote INTEGER NOT NULL DEFAULT 0,
    apply_url TEXT,
    posted_at TEXT,
    discovered_at TEXT NOT NULL,
    fit_score INTEGER,
    status TEXT NOT NULL DEFAULT 'new',
    remote_type TEXT,
    employment_type TEXT,
    salary_text TEXT,
    description TEXT,
    raw_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, external_id)
);


CREATE TABLE IF NOT EXISTS job_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    role_type TEXT,
    seniority_level TEXT,
    fit_score INTEGER,
    risk_score INTEGER,
    quality_score INTEGER,
    recommendation TEXT,
    skip_reason TEXT,
    matched_keywords_json TEXT,
    missing_keywords_json TEXT,
    red_flags_json TEXT,
    eligibility_flags_json TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS job_classifications (
    job_id INTEGER PRIMARY KEY,
    primary_role_family TEXT NOT NULL,
    secondary_role_families_json TEXT NOT NULL DEFAULT "[]",
    seniority TEXT NOT NULL,
    role_evidence_json TEXT NOT NULL DEFAULT "[]",
    seniority_evidence_json TEXT NOT NULL DEFAULT "[]",
    provenance TEXT NOT NULL DEFAULT "deterministic",
    updated_at TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason_codes TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
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
        _migrate_jobs(conn)
        _migrate_job_analysis(conn)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_provider_external_id ON jobs(provider, external_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_review_queue ON jobs(status, fit_score DESC, discovered_at ASC, id ASC)")


def _migrate_jobs(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    additions = {
        "provider": "TEXT",
        "external_id": "TEXT",
        "remote": "INTEGER NOT NULL DEFAULT 0",
        "apply_url": "TEXT",
        "posted_at": "TEXT",
        "discovered_at": "TEXT",
        "fit_score": "INTEGER",
        "status": "TEXT NOT NULL DEFAULT 'new'",
    }
    for column, ddl in additions.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")

    if not columns:
        return

    conn.execute("UPDATE jobs SET provider = COALESCE(NULLIF(provider, ''), 'manual')")
    conn.execute("UPDATE jobs SET external_id = COALESCE(NULLIF(external_id, ''), 'legacy:' || id)")
    conn.execute("UPDATE jobs SET remote = CASE WHEN lower(COALESCE(remote_type, '')) = 'remote' THEN 1 ELSE COALESCE(remote, 0) END")
    conn.execute("UPDATE jobs SET apply_url = COALESCE(NULLIF(apply_url, ''), source_url)")
    conn.execute("UPDATE jobs SET discovered_at = COALESCE(NULLIF(discovered_at, ''), created_at, updated_at, CURRENT_TIMESTAMP)")
    conn.execute("UPDATE jobs SET status = COALESCE(NULLIF(status, ''), 'saved')")
    conn.execute(
        """
        UPDATE jobs
        SET fit_score = COALESCE(
            fit_score,
            (SELECT job_analysis.fit_score FROM job_analysis WHERE job_analysis.job_id = jobs.id)
        )
        """
    )


def _migrate_job_analysis(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_analysis)").fetchall()}
    required = {
        "recommendation": "TEXT",
        "skip_reason": "TEXT",
        "eligibility_flags_json": "TEXT",
    }
    for column, ddl in required.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE job_analysis ADD COLUMN {column} {ddl}")


def _manual_external_id(data: dict[str, Any]) -> str:
    blob = "|".join(
        str(part or "").strip().lower()
        for part in (
            data.get("source_url"),
            data.get("title"),
            data.get("company"),
            data.get("location"),
            data.get("description"),
            data.get("raw_text"),
        )
    )
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    return f"manual:{digest}"


def insert_job(data: dict[str, Any], review_status: str | None = None) -> int:
    timestamp = now_iso()
    provider = str(data.get("provider") or "manual")
    external_id = str(data.get("external_id") or data.get("source_url") or _manual_external_id(data))
    discovered_at = str(data.get("discovered_at") or timestamp)
    remote = data.get("remote")
    if remote is None:
        remote = str(data.get("remote_type") or "").lower() == "remote"
    apply_url = str(data.get("apply_url") or data.get("source_url") or "")
    status = review_status or data.get("status") or ("saved" if provider == "manual" else "new")
    payload = {
        "provider": provider,
        "external_id": external_id,
        "source_url": data.get("source_url", ""),
        "title": data.get("title", ""),
        "company": data.get("company", ""),
        "location": data.get("location", ""),
        "remote": 1 if remote else 0,
        "apply_url": apply_url,
        "posted_at": data.get("posted_at", ""),
        "discovered_at": discovered_at,
        "fit_score": data.get("fit_score"),
        "status": status,
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
                provider, external_id, source_url, title, company, location, remote, apply_url,
                posted_at, discovered_at, fit_score, status, remote_type, employment_type,
                salary_text, description, raw_text, created_at, updated_at
            ) VALUES (
                :provider, :external_id, :source_url, :title, :company, :location, :remote, :apply_url,
                :posted_at, :discovered_at, :fit_score, :status, :remote_type, :employment_type,
                :salary_text, :description, :raw_text, :created_at, :updated_at
            )
            ON CONFLICT(provider, external_id) DO UPDATE SET
                source_url = CASE WHEN excluded.source_url = '' THEN jobs.source_url ELSE excluded.source_url END,
                title = CASE WHEN excluded.title = '' THEN jobs.title ELSE excluded.title END,
                company = CASE WHEN excluded.company = '' THEN jobs.company ELSE excluded.company END,
                location = CASE WHEN excluded.location = '' THEN jobs.location ELSE excluded.location END,
                remote = excluded.remote,
                apply_url = CASE WHEN excluded.apply_url = '' THEN jobs.apply_url ELSE excluded.apply_url END,
                posted_at = CASE WHEN excluded.posted_at = '' THEN jobs.posted_at ELSE excluded.posted_at END,
                discovered_at = CASE WHEN jobs.discovered_at IS NULL OR jobs.discovered_at = '' THEN excluded.discovered_at ELSE jobs.discovered_at END,
                fit_score = excluded.fit_score,
                status = CASE WHEN jobs.status IN ('', 'new') THEN excluded.status ELSE jobs.status END,
                remote_type = CASE WHEN excluded.remote_type = '' THEN jobs.remote_type ELSE excluded.remote_type END,
                employment_type = CASE WHEN excluded.employment_type = '' THEN jobs.employment_type ELSE excluded.employment_type END,
                salary_text = CASE WHEN excluded.salary_text = '' THEN jobs.salary_text ELSE excluded.salary_text END,
                description = CASE WHEN excluded.description = '' THEN jobs.description ELSE excluded.description END,
                raw_text = CASE WHEN excluded.raw_text = '' THEN jobs.raw_text ELSE excluded.raw_text END,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        row = conn.execute(
            "SELECT id FROM jobs WHERE provider = ? AND external_id = ?",
            (provider, external_id),
        ).fetchone()
        job_id = int(row["id"] if row else cur.lastrowid)
        conn.execute(
            """
            INSERT INTO applications (job_id, status, created_at, updated_at)
            VALUES (?, 'saved', ?, ?)
            ON CONFLICT(job_id) DO NOTHING
            """,
            (job_id, timestamp, timestamp),
        )
    existing = get_classification(job_id)
    if existing is None or existing["provenance"] != "manual":
        upsert_classification(job_id, classify_canonical_job(payload))
    return job_id


def update_job(job_id: int, data: dict[str, Any]) -> None:
    data = dict(data)
    data["id"] = job_id
    data["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = :{key}" for key in data if key != "id")
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = :id", data)


def set_review_status(job_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), job_id),
        )


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(query, params).fetchone()


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def get_job(job_id: int) -> sqlite3.Row | None:
    return fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))


def get_job_by_provider_external_id(provider: str, external_id: str) -> sqlite3.Row | None:
    return fetch_one("SELECT * FROM jobs WHERE provider = ? AND external_id = ?", (provider, external_id))


def get_analysis(job_id: int) -> sqlite3.Row | None:
    return fetch_one("SELECT * FROM job_analysis WHERE job_id = ?", (job_id,))


def get_classification(job_id: int) -> sqlite3.Row | None:
    return fetch_one("SELECT * FROM job_classifications WHERE job_id = ?", (job_id,))


def upsert_classification(job_id: int, classification: Any, provenance: str = "deterministic") -> None:
    with connect() as conn:
        conn.execute("""INSERT INTO job_classifications (job_id, primary_role_family, secondary_role_families_json, seniority, role_evidence_json, seniority_evidence_json, provenance, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET primary_role_family=excluded.primary_role_family, secondary_role_families_json=excluded.secondary_role_families_json, seniority=excluded.seniority, role_evidence_json=excluded.role_evidence_json, seniority_evidence_json=excluded.seniority_evidence_json, provenance=excluded.provenance, updated_at=excluded.updated_at""", (job_id, classification.primary_role_family, dump_json(list(classification.secondary_role_families)), classification.seniority, dump_json(list(classification.role_evidence)), dump_json(list(classification.seniority_evidence)), provenance, now_iso()))


def get_application(job_id: int) -> sqlite3.Row | None:
    return fetch_one("SELECT * FROM applications WHERE job_id = ?", (job_id,))


def set_followup_at(job_id: int, followup_at: str | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE applications
            SET followup_at = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (followup_at, now_iso(), job_id),
        )


def upsert_analysis(job_id: int, analysis: dict[str, Any]) -> None:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO job_analysis (
                job_id, role_type, seniority_level, fit_score, risk_score, quality_score,
                recommendation, skip_reason, matched_keywords_json, missing_keywords_json, red_flags_json,
                eligibility_flags_json, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                role_type = excluded.role_type,
                seniority_level = excluded.seniority_level,
                fit_score = excluded.fit_score,
                risk_score = excluded.risk_score,
                quality_score = excluded.quality_score,
                recommendation = excluded.recommendation,
                skip_reason = excluded.skip_reason,
                matched_keywords_json = excluded.matched_keywords_json,
                missing_keywords_json = excluded.missing_keywords_json,
                red_flags_json = excluded.red_flags_json,
                eligibility_flags_json = excluded.eligibility_flags_json,
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
                analysis["recommendation"],
                analysis["skip_reason"],
                dump_json(analysis["matched_keywords"]),
                dump_json(analysis["missing_keywords"]),
                dump_json(analysis["red_flags"]),
                dump_json(analysis["eligibility_flags"]),
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
            jobs.id, jobs.provider, jobs.external_id, jobs.title, jobs.company, jobs.source_url, jobs.apply_url,
            jobs.location, jobs.remote, jobs.posted_at, jobs.discovered_at, jobs.fit_score, jobs.description, jobs.raw_text,
            jobs.status AS review_status,
            job_classifications.primary_role_family AS role_type,
            job_classifications.primary_role_family AS primary_role_family, job_classifications.secondary_role_families_json, job_classifications.seniority AS canonical_seniority,
            job_classifications.seniority AS seniority_level, job_analysis.risk_score, job_analysis.quality_score,
            job_analysis.recommendation, job_analysis.skip_reason,
            applications.status, applications.followup_at
        FROM jobs
        LEFT JOIN job_analysis ON job_analysis.job_id = jobs.id
        LEFT JOIN job_classifications ON job_classifications.job_id = jobs.id
        LEFT JOIN applications ON applications.job_id = jobs.id
        ORDER BY jobs.id ASC
        """
    )


def insert_feedback(job_id: int, action: str, reason_codes: str | None = None, note: str | None = None) -> None:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO feedback (job_id, action, reason_codes, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, action, reason_codes, note, timestamp),
        )


def list_feedback_with_analysis() -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT
            f.id, f.job_id, f.action, f.reason_codes, f.note, f.created_at,
            ja.role_type, ja.matched_keywords_json
        FROM feedback f
        LEFT JOIN job_analysis ja ON f.job_id = ja.job_id
        ORDER BY f.created_at ASC
        """
    )


def list_review_jobs(limit: int | None = None) -> list[sqlite3.Row]:
    from .learn import load_learned_weights
    learned_weights = load_learned_weights()
    active_profile = load_search_profile()
    rows = fetch_all(
        """
        SELECT
            id, provider, external_id, title, company, location, remote, apply_url,
            posted_at, discovered_at, fit_score, status, description, source_url, raw_text
        FROM jobs
        WHERE status = 'new'
        """
    )
    ranked = sorted(
        rows,
        key=lambda row: (
            -score_job_fit_v1(dict(row), learned_weights=learned_weights, profile=active_profile)[0],
            str(row["discovered_at"] or ""),
            int(row["id"]),
        ),
    )
    if limit is not None:
        ranked = ranked[:limit]
    return ranked


def shortlist_jobs(min_fit: int, max_risk: int, limit: int) -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT
            jobs.id, jobs.title, jobs.company,
            COALESCE(jobs.fit_score, job_analysis.fit_score, 0) AS fit_score,
            job_analysis.role_type, job_analysis.seniority_level,
            job_analysis.risk_score, job_analysis.quality_score,
            job_analysis.recommendation, job_analysis.skip_reason,
            applications.status,
            (COALESCE(jobs.fit_score, job_analysis.fit_score, 0) + COALESCE(job_analysis.quality_score, 0) - COALESCE(job_analysis.risk_score, 0)) AS combined_score
        FROM jobs
        LEFT JOIN job_analysis ON job_analysis.job_id = jobs.id
        LEFT JOIN applications ON applications.job_id = jobs.id
        WHERE COALESCE(jobs.fit_score, job_analysis.fit_score, 0) >= ?
          AND COALESCE(job_analysis.risk_score, 0) <= ?
        ORDER BY combined_score DESC, jobs.id ASC
        LIMIT ?
        """,
        (min_fit, max_risk, limit),
    )


def due_followups(limit: int = 20, as_of: str | None = None) -> list[sqlite3.Row]:
    cutoff = as_of or now_iso()
    return fetch_all(
        """
        SELECT
            jobs.id, jobs.title, jobs.company, jobs.source_url,
            applications.status, applications.followup_at, applications.notes,
            job_analysis.role_type, job_analysis.seniority_level, job_analysis.fit_score, job_analysis.risk_score, job_analysis.quality_score,
            job_analysis.recommendation, job_analysis.skip_reason
        FROM applications
        JOIN jobs ON jobs.id = applications.job_id
        LEFT JOIN job_analysis ON job_analysis.job_id = jobs.id
        WHERE applications.status = 'applied'
          AND applications.followup_at IS NOT NULL
          AND applications.followup_at <= ?
        ORDER BY applications.followup_at ASC, jobs.id ASC
        LIMIT ?
        """,
        (cutoff, limit),
    )


def export_applications_csv(path: Path) -> Path:
    rows = fetch_all(
        """
        SELECT
            jobs.id, jobs.title, jobs.company, jobs.location, jobs.source_url,
            job_analysis.role_type, job_analysis.seniority_level, job_analysis.fit_score, job_analysis.risk_score, job_analysis.quality_score,
            job_analysis.recommendation, job_analysis.skip_reason,
            applications.status, applications.applied_at, applications.followup_at, applications.notes
        FROM jobs
        LEFT JOIN job_analysis ON job_analysis.job_id = jobs.id
        LEFT JOIN applications ON applications.job_id = jobs.id
        ORDER BY jobs.id ASC
        """
    )
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "id", "title", "company", "location", "source_url", "role_type", "seniority_level",
            "fit_score", "risk_score", "quality_score", "recommendation", "skip_reason",
            "status", "applied_at", "followup_at", "notes",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return path
