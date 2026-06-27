from pathlib import Path
import sqlite3

from jobpipe.db import init_database


def test_jobs_migration_adds_new_columns_and_dedupe_index(tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE jobs (
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
        )
        """
    )
    conn.commit()
    conn.close()

    init_database(db_path)

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(jobs)").fetchall()}
    conn.close()

    assert {"provider", "external_id", "remote", "apply_url", "posted_at", "discovered_at", "fit_score", "status"} <= columns
    assert "idx_jobs_provider_external_id" in indexes


def test_job_analysis_migration_adds_new_columns(tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE job_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE,
            role_type TEXT,
            fit_score INTEGER,
            risk_score INTEGER,
            quality_score INTEGER,
            matched_keywords_json TEXT,
            missing_keywords_json TEXT,
            red_flags_json TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    init_database(db_path)

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(job_analysis)").fetchall()}
    conn.close()
    assert "recommendation" in columns
    assert "skip_reason" in columns
    assert "eligibility_flags_json" in columns
