"""Additive applicant schema. Initialization is always an explicit local action."""

APPLICANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS applicant_profiles (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applicant_fact_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL REFERENCES applicant_profiles(id),
    fact_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK(verification_status IN ('verified','self_reported','imported_unverified','inferred','unknown')),
    source_type TEXT NOT NULL,
    source_reference TEXT,
    evidence TEXT NOT NULL,
    confirmed_at TEXT,
    modified_at TEXT NOT NULL,
    previous_revision_id INTEGER REFERENCES applicant_fact_revisions(id),
    review_state TEXT NOT NULL CHECK(review_state IN ('pending','accepted','confirmed','rejected')),
    reviewed_at TEXT,
    reviewer TEXT
);
CREATE INDEX IF NOT EXISTS idx_applicant_proposals ON applicant_fact_revisions(profile_id, review_state, fact_key, id);
CREATE TABLE IF NOT EXISTS applicant_facts (
    profile_id TEXT NOT NULL REFERENCES applicant_profiles(id),
    fact_key TEXT NOT NULL,
    current_revision_id INTEGER NOT NULL REFERENCES applicant_fact_revisions(id),
    PRIMARY KEY(profile_id, fact_key)
);
CREATE TABLE IF NOT EXISTS application_package_provenance (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    profile_revision INTEGER NOT NULL,
    context_fingerprint TEXT NOT NULL,
    mode TEXT NOT NULL,
    fact_references_json TEXT NOT NULL,
    artifact_hashes_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
