"""Shared discovery operations and a single bounded MCP ingest worker.

No scheduling, arbitrary endpoints, commands, or source-configuration writes.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone, timedelta
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
from typing import Any

from . import db
from .catalog import load_search_profile, load_source_packs, source_identity_label
from .eligibility import assess_eligibility, load_applicant
from .ingest import DiscoveryDiagnostics, SourceDiscoveryReport
from .ingest import collect_discovery_diagnostics, resolve_job_sources
from .models import JobSource, SearchProfile
from .source_metrics import (build_source_metrics, catalog_sources, completed_runs,
                             concentration, finish_ingest_run, rate, record_source_run,
                             run_stats, start_ingest_run)
from .utils import data_dir, package_dir, root_dir

RUN_TIMEOUT = 180
TRIAL_TIMEOUT = 30
MAX_SOURCES = 20
_workers: dict[int, subprocess.Popen] = {}
_watchers: dict[int, threading.Thread] = {}
_trials: set[subprocess.Popen] = set()
_workers_mutex = threading.Lock()
_trial_mutex = threading.Lock()


def _error(code: str, message: str) -> Exception:
    from .services import ServiceError
    return ServiceError(code, message)


def source_catalog() -> tuple[list[JobSource], dict[str, list[JobSource]]]:
    return (catalog_sources(data_dir() / "job_sources.yaml", package_dir() / "data/job_sources.yaml"),
            load_source_packs().packs)


def resolve_trial(source_id: str) -> tuple[JobSource, str]:
    sources, packs = source_catalog()

    def matches(source: JobSource) -> bool:
        value = source_id.strip().casefold()
        return value in {source_identity_label(source).casefold(), source.company.casefold(),
                         source.company_slug.casefold(), (source.board_token or source.account or "").casefold()}

    selected = {source_identity_label(s): (s, "workspace") for s in sources if matches(s)}
    if not selected:
        for pack, members in sorted(packs.items()):
            for source in members:
                if matches(source):
                    selected.setdefault(source_identity_label(source), (source, pack))
    if not selected:
        raise _error("unknown_source", "Source is not configured or cataloged; arbitrary URLs are not accepted.")
    if len(selected) != 1:
        raise _error("invalid_arguments", "Ambiguous source; use one of: " + ", ".join(sorted(selected)))
    source, origin = next(iter(selected.values()))
    _validate_source(source)
    return source.model_copy(update={"enabled": True}), origin


def _validate_source(source: JobSource) -> None:
    token = source.board_token if source.provider == "greenhouse" else source.account
    token = token or source.company_slug
    if source.provider not in {"greenhouse", "lever"} or not re.fullmatch(r"[a-zA-Z0-9_-]{1,150}", token):
        raise _error("configuration_error", "Source requires a supported provider and a plain configured ATS identifier.")


def selected_sources(source_ids: list[str] | None = None) -> list[JobSource]:
    sources = resolve_job_sources(data_dir() / "job_sources.yaml").sources
    indexed = {source_identity_label(s): s for s in sources}
    if source_ids is not None:
        unknown = sorted(set(source_ids) - indexed.keys())
        if unknown:
            raise _error("unknown_source", "Unknown or disabled configured source(s): " + ", ".join(unknown))
        sources = [indexed[key] for key in sorted(set(source_ids))]
    if len(sources) > MAX_SOURCES:
        raise _error("invalid_arguments", f"Select at most {MAX_SOURCES} source_ids per ingest; use get_discovery_status to choose batches.")
    if not sources:
        raise _error("invalid_arguments", "No enabled sources selected.")
    for source in sources:
        _validate_source(source)
    return sources


def acquire_ingest_lock() -> int:
    # Ingestion alone may create this lock. Read-only status/trials never do.
    path = data_dir() / ".discovery.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise _error("operation_in_progress", "An ingest is already running in this workspace; inspect get_discovery_status.")
    return fd


def execute_ingest(sources: list[JobSource], profile: SearchProfile, run_id: int) -> DiscoveryDiagnostics:
    """Canonical ingestion body shared by the CLI and the MCP worker."""
    from .score import score_job_fit_v1
    from .learn import load_learned_weights
    from .utils import load_json
    reports: list[SourceDiscoveryReport] = []
    try:
        for source in sources:
            record_source_run(run_id, SourceDiscoveryReport(source=source, status="running"))
            report = collect_discovery_diagnostics([source], profile).source_reports[0]
            reports.append(report)
            inserted = updated = 0
            # No network while holding SQLite's writer transaction. A hard-killed
            # worker rolls back this whole source, including its measured counts.
            with closing(db.connect(mode="rw")) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                if report.status == "success":
                    for job in report.accepted_jobs:
                        payload = job.model_dump() | {"source_id": report.label}
                        existing = conn.execute("SELECT id FROM jobs WHERE provider=? AND external_id=?",
                                                (job.provider, job.external_id)).fetchone()
                        job_id = db.insert_job(payload, review_status="new", connection=conn)
                        canonical = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
                        classification = conn.execute("SELECT * FROM job_classifications WHERE job_id=?", (job_id,)).fetchone()
                        if classification:
                            canonical.update(primary_role_family=classification["primary_role_family"],
                                             secondary_role_families=load_json(classification["secondary_role_families_json"]),
                                             canonical_seniority=classification["seniority"])
                        score, _ = score_job_fit_v1(canonical, profile=profile, learned_weights=load_learned_weights())
                        conn.execute("UPDATE jobs SET fit_score=? WHERE id=?", (score, job_id))
                        inserted += int(existing is None)
                        updated += int(existing is not None)
                record_source_run(run_id, report, inserted=inserted, updated=updated, connection=conn)
    except BaseException as exc:
        _abort(run_id, f"{type(exc).__name__}: {exc}; unfinished source rolled back")
        raise
    finish_ingest_run(run_id)
    return DiscoveryDiagnostics(reports)


def ingest_sync(sources: list[JobSource], profile: SearchProfile) -> tuple[int, DiscoveryDiagnostics]:
    fd = acquire_ingest_lock()
    try:
        run_id = start_ingest_run(profile)
        return run_id, execute_ingest(sources, profile, run_id)
    finally:
        os.close(fd)


def _abort(run_id: int, message: str) -> None:
    with closing(db.connect(mode="rw")) as conn, conn:
        conn.execute("UPDATE source_run_stats SET fetch_status='failed', error=? WHERE run_id=? AND fetch_status IN ('running', 'pending')", (message, run_id))
        running = conn.execute("SELECT status FROM ingest_runs WHERE id=?", (run_id,)).fetchone()
    if running and running[0] == "running":
        finish_ingest_run(run_id, error=message)


def _watch(run_id: int, process: subprocess.Popen, lock_fd: int) -> None:
    try:
        try:
            process.wait(timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            _abort(run_id, "Ingest exceeded its 180-second wall-clock deadline; completed source work is retained.")
        if process.returncode:
            _abort(run_id, "Ingest worker stopped; inspect source results and stderr diagnostics.")
    finally:
        # Parent and child both own the same inherited lock until the child exits.
        os.close(lock_fd)
        with _workers_mutex:
            _workers.pop(run_id, None)
            _watchers.pop(run_id, None)


def ingest_sources(source_ids: list[str] | None = None, profile: str | None = None,
                   diagnostics: bool = False) -> dict[str, Any]:
    sources = selected_sources(source_ids)
    selected = load_search_profile(profile or "miles")
    fd = acquire_ingest_lock()
    run_id = None
    try:
        # Reconcile a killed prior server only while holding the workspace lock.
        with closing(db.connect(mode="rw")) as conn:
            stale = [r[0] for r in conn.execute("SELECT id FROM ingest_runs WHERE status='running'")]
        for old in stale:
            _abort(old, "Previous ingest worker no longer holds the workspace lock.")
        run_id = start_ingest_run(selected)
        for source in sources:
            record_source_run(run_id, SourceDiscoveryReport(source=source, status="pending"))
        process = subprocess.Popen([sys.executable, "-m", "jobpipe.discovery_worker", "ingest", str(run_id), str(fd)],
                                   cwd=root_dir(), pass_fds=(fd,), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        with _workers_mutex:
            _workers[run_id] = process
        watcher = threading.Thread(target=_watch, args=(run_id, process, fd), daemon=True)
        with _workers_mutex:
            _watchers[run_id] = watcher
        watcher.start()
    except BaseException:
        if run_id is not None:
            _abort(run_id, "Unable to start ingest worker.")
        os.close(fd)
        raise
    return {"run_id": run_id, "status": "running", "selected_sources": [source_identity_label(s) for s in sources],
            "timeout_seconds": RUN_TIMEOUT, "next_action": "Poll get_discovery_status with this run_id until completed or aborted."}


def stop_workers() -> None:
    with _workers_mutex:
        processes = list(_workers.values()) + list(_trials)
        watchers = list(_watchers.values())
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait()
    for watcher in watchers:
        watcher.join(timeout=10)
    with _workers_mutex:
        _watchers.clear()


def run_trial(source_id: str) -> tuple[SourceDiscoveryReport, SearchProfile, str]:
    source, origin = resolve_trial(source_id)
    profile = load_search_profile()
    report = collect_discovery_diagnostics([source], profile).source_reports[0]
    return report, profile, origin


def trial_source_local(source_id: str, diagnostics: bool = False) -> dict[str, Any]:
    report, profile, origin = run_trial(source_id)
    applicant, candidate = load_applicant()
    assessments = [(job, assess_eligibility(job, applicant, profile, candidate)) for job in report.accepted_jobs]
    order = {"eligible": 0, "needs_review": 1, "ineligible": 2}
    examples = sorted(assessments, key=lambda item: (order[item[1].status], item[0].title.casefold(), item[0].external_id))[:5]
    success = report.status == "success"
    return {"source_id": report.label, "configuration_origin": origin, "profile": profile.name,
            "goal": profile.active_goal, "status": report.status, "error": report.error,
            "fetched": report.fetched_count if success else None,
            "passed": report.passed_count if success else None, "rejected": report.rejected_count if success else None,
            "yield_rate": rate(report.passed_count, report.fetched_count) if success else None,
            "role_family_distribution": dict(sorted(report.role_family_counts.items())),
            "distribution_denominators": {"role_family": "fetched", "seniority": "passed_discovery"},
            "seniority_distribution": dict(sorted(report.passed_seniority_counts.items())),
            "rejection_reasons": dict(sorted(report.reason_counts.items())),
            "eligibility_denominator": report.passed_count if success else None,
            "eligibility_counts": {status: sum(a.status == status for _, a in assessments) if success else None for status in ("eligible", "needs_review", "ineligible")},
            "representative_jobs": [{"external_id": j.external_id, "title": j.title, "company": j.company,
                                      "location": j.location, "application_url": j.apply_url,
                                      "eligibility": a.model_dump()} for j, a in examples]}


def trial_source(source_id: str, diagnostics: bool = False) -> dict[str, Any]:
    resolve_trial(source_id)  # Reject arbitrary input before starting a process.
    if not _trial_mutex.acquire(blocking=False):
        raise _error("operation_in_progress", "A source trial is already running on this server.")
    try:
        process = subprocess.Popen([sys.executable, "-m", "jobpipe.discovery_worker", "trial", source_id],
                                   cwd=root_dir(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        with _workers_mutex:
            _trials.add(process)
        try:
            stdout, _ = process.communicate(timeout=TRIAL_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise _error("operation_timeout", "Source trial exceeded its 30-second deadline; canonical data is unchanged.")
        if process.returncode:
            if process.returncode == 124:
                raise _error("operation_timeout", "Source trial exceeded its 30-second deadline; canonical data is unchanged.")
            raise _error("configuration_error", "Source trial worker failed; inspect stderr diagnostics.")
        return json.loads(stdout)
    finally:
        if "process" in locals():
            with _workers_mutex:
                _trials.discard(process)
        _trial_mutex.release()


def get_discovery_status(run_id: int | None = None) -> dict[str, Any]:
    sources, packs = source_catalog()
    metrics = build_source_metrics(sources + [s for members in packs.values() for s in members])
    completed = completed_runs(metrics)
    latest = completed[0] if completed else None
    selected = next((r for r in metrics.runs if r["id"] == run_id), None) if run_id else latest
    if run_id is not None and selected is None:
        raise _error("unknown_run", "Ingest run not found.")
    stats = run_stats(metrics, selected["id"]) if selected else {}
    successful = [s for s in stats.values() if s["fetch_status"] == "success"]
    failed = [s for s in stats.values() if s["fetch_status"] == "failed"]
    fetched, passed = sum(s["fetched"] for s in successful), sum(s["passed"] for s in successful)
    warnings = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    for source in sources:
        if not source.enabled:
            continue
        label = source_identity_label(source)
        measured = next((r for r in completed if (run_stats(metrics, r["id"]).get(label) or {}).get("fetch_status") == "success"), None)
        if measured is None:
            warnings.append(f"{label}: no successful completed measurement")
        elif datetime.fromisoformat(measured["completed_at"]) < cutoff:
            warnings.append(f"{label}: last successful measurement older than seven days ({measured['completed_at']})")
    return {"latest_ingest_timestamp": latest["completed_at"] if latest else None,
            "enabled_sources": [source_identity_label(s) for s in sources if s.enabled],
            "current_operations": [{"run_id": r["id"], "started_at": r["started_at"]} for r in metrics.runs if r["status"] == "running"],
            "run": {k: selected[k] for k in ("id", "started_at", "completed_at", "profile", "goal", "status", "error")} if selected else None,
            "sources_checked": len(successful) + len(failed),
            "successful_sources": len(successful), "failed_sources": len(failed),
            "fetched": fetched if successful else None, "passed": passed if successful else None,
            "inserted": sum(s["inserted"] for s in stats.values()) if selected else None,
            "updated": sum(s["updated"] for s in stats.values()) if selected else None,
            "yield_rate": rate(passed, fetched) if successful else None,
            "measurement_scope": "Selected run; successful source measurements only. Current corpus is reported separately.",
            "source_results": [{k: s[k] for k in ("source_id", "fetch_status", "error", "inserted", "updated")} |
                               {k: s[k] if s["fetch_status"] == "success" else None for k in ("fetched", "passed", "rejected")} for s in stats.values()],
            "current_corpus": concentration(metrics), "stale_source_warnings": warnings}
