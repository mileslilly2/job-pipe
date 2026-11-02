"""Aggregate ingest measurements and read-only, current-corpus diagnostics.

Discovery yield is passed/fetched. Current save/apply rates use all retained
canonical jobs per source, including inactive jobs, and measure current buckets.
Neither rate is a historical conversion rate or a source score.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from typing import Any

from .analytics import _display_status
from .application_package import application_package_paths
from .catalog import normalize_job_source, source_identity_label
from .db import connect
from .ingest import SourceDiscoveryReport
from .models import JobSource, SearchProfile
from .utils import db_path, dump_json, now_iso
from .workflow import job_bucket


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def format_rate(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def start_ingest_run(profile: SearchProfile) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO ingest_runs (started_at, profile, goal, profile_json, status) VALUES (?, ?, ?, ?, 'running')",
            (now_iso(), profile.name, profile.active_goal, dump_json(profile.model_dump())),
        )
        return int(cursor.lastrowid)


def record_source_run(run_id: int, report: SourceDiscoveryReport, *, inserted: int = 0, updated: int = 0, connection: sqlite3.Connection | None = None) -> None:
    with nullcontext(connection) if connection is not None else closing(connect()) as conn:
        if connection is None:
            with conn:
                return record_source_run(run_id, report, inserted=inserted, updated=updated, connection=conn)
        conn.execute(
            """INSERT INTO source_run_stats
            (run_id, source_id, provider, company, source_json, fetch_status, error,
             fetched, passed, rejected, inserted, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, source_id) DO UPDATE SET
                fetch_status = excluded.fetch_status, error = excluded.error,
                fetched = excluded.fetched, passed = excluded.passed, rejected = excluded.rejected,
                inserted = excluded.inserted, updated = excluded.updated""",
            (run_id, report.label, report.source.provider, report.source.company,
             dump_json(report.source.model_dump()), report.status, report.error,
             report.fetched_count, report.passed_count, report.rejected_count, inserted, updated),
        )


def finish_ingest_run(run_id: int, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE ingest_runs SET completed_at = ?, status = ?, error = ? WHERE id = ?",
            (now_iso(), "aborted" if error is not None else "completed", error, run_id),
        )


@dataclass
class SourceMetrics:
    runs: list[dict[str, Any]] = field(default_factory=list)
    stats: list[dict[str, Any]] = field(default_factory=list)
    corpus: dict[str, Counter[str]] = field(default_factory=dict)
    employers: Counter[str] = field(default_factory=Counter)
    active_sources: Counter[str] = field(default_factory=Counter)
    unattributed_active: int = 0
    inferred_jobs: int = 0

    @property
    def active_count(self) -> int:
        return sum(self.employers.values())


def _read_database(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not path.exists():
        return [], [], []
    # Never initialize, migrate, seed, or create a database during reporting.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        runs = [dict(row) for row in conn.execute("SELECT * FROM ingest_runs ORDER BY completed_at DESC, id DESC")] if "ingest_runs" in tables else []
        stats = [dict(row) for row in conn.execute("SELECT * FROM source_run_stats ORDER BY run_id, source_id")] if "source_run_stats" in tables else []
        jobs: list[dict[str, Any]] = []
        if "jobs" in tables:
            applications = {row["job_id"]: row["status"] for row in conn.execute("SELECT job_id, status FROM applications")} if "applications" in tables else {}
            for row in conn.execute("SELECT * FROM jobs ORDER BY id"):
                job = dict(row)
                job["review_status"] = job.get("status")
                job["status"] = applications.get(job["id"])
                jobs.append(job)
    return runs, stats, jobs


def build_source_metrics(sources: list[JobSource], *, path: Path | None = None) -> SourceMetrics:
    runs, stats, jobs = _read_database(path or db_path())
    report = SourceMetrics(runs=runs, stats=stats)
    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for source in sources:
        source = normalize_job_source(source)
        candidates[(source.provider, source.company.strip().casefold())].add(source_identity_label(source))
    for stat in stats:
        candidates[(stat["provider"].strip().lower(), stat["company"].strip().casefold())].add(stat["source_id"])
    employer_names: dict[str, str] = {}
    for job in jobs:
        provider = str(job.get("provider") or "manual").strip().lower()
        company = str(job.get("company") or "unknown company").strip()
        source_id = job.get("source_id")
        if not source_id:
            matches = candidates.get((provider, company.casefold()), set())
            if len(matches) == 1:
                source_id = next(iter(matches))
                report.inferred_jobs += 1
        # This is a labeled attribution bucket, not a made-up source identity.
        label = str(source_id) if source_id else f"unattributed ({provider} / {company})"
        counts = report.corpus.setdefault(label, Counter())
        counts["total"] += 1
        paths = application_package_paths(job)
        job["has_package"] = any(p.exists() for p in (paths.job_md, paths.fit_md, paths.resume_tailored_md, paths.cover_note_md, paths.apply_md))
        bucket = job_bucket(job)
        if bucket in {"saved", "packaged", "applied"}:
            counts[bucket] += 1
        if _display_status(job) not in {"rejected", "skipped", "archived", "ignored"}:
            counts["active"] += 1
            employer = employer_names.setdefault(company.casefold(), company)
            report.employers[employer] += 1
            if source_id:
                report.active_sources[str(source_id)] += 1
            else:
                report.unattributed_active += 1
    return report


def completed_runs(report: SourceMetrics) -> list[dict[str, Any]]:
    return [run for run in report.runs if run["status"] == "completed" and run["completed_at"]]


def concentration(report: SourceMetrics) -> dict[str, Any]:
    """The same active-corpus employer calculation used by CLI and MCP."""
    total = report.active_count
    ranked = sorted(report.employers.items(), key=lambda item: (-item[1], item[0]))
    largest = rate(ranked[0][1], total) if ranked else None
    return {"active_jobs": total, "employers": len(ranked),
            "sources": len(report.active_sources), "largest_employer_share": largest,
            "warning": bool(largest is not None and largest > 0.25),
            "top_employers": [{"company": name, "active_jobs": count, "share": rate(count, total)} for name, count in ranked[:5]]}


def run_stats(report: SourceMetrics, run_id: int) -> dict[str, dict[str, Any]]:
    return {stat["source_id"]: stat for stat in report.stats if stat["run_id"] == run_id}


def pack_metrics(packs: dict[str, list[JobSource]], stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for name, sources in sorted(packs.items()):
        members = {source_identity_label(source) for source in sources}
        measured = [stats[label] for label in sorted(members) if label in stats and stats[label]["fetch_status"] == "success"]
        fetched = sum(item["fetched"] for item in measured)
        passed = sum(item["passed"] for item in measured)
        rows.append(dict(pack=name, sources=len(members), measured=len(measured), fetched=fetched, passed=passed, yield_rate=rate(passed, fetched)))
    return rows


def catalog_sources(workspace_path: Path, fallback_path: Path) -> list[JobSource]:
    """Read all configured sources, including disabled ones, without seeding."""
    from .utils import load_yaml
    raw = load_yaml(workspace_path) if workspace_path.exists() else None
    if not raw and fallback_path.exists():
        raw = load_yaml(fallback_path)
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("job source config must be a YAML list")
    return [normalize_job_source(JobSource.model_validate(item)) for item in raw]
