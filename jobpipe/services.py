"""Shared local application operations. No terminal, HTTP, or MCP dependencies."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import sqlite3
from typing import Any

from . import db
from .analytics import _display_status as display_status
from .application_package import application_package_paths, build_application_package, existing_package
from .catalog import load_search_profile, load_search_profiles, source_identity_label
from .facts import build_fact_report
from .job_requirements import extract_job_requirements
from .knowledge import available_goals, require_goal
from .learn import load_learned_weights, sync_weights
from .models import SearchProfile
from .score import score_job_fit_breakdown, score_job_fit_v1
from .source_metrics import build_source_metrics, catalog_sources, completed_runs, rate, run_stats
from .utils import data_dir, db_path, load_json, now_iso, package_dir
from .workflow import job_bucket

ACTIVE_BUCKETS = {"new", "saved", "packaged", "assessment", "applied", "interview", "offer"}


class ServiceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_database() -> None:
    """Inspect only; never initialize, migrate, or create a workspace database."""
    if not db_path().is_file():
        raise ServiceError("missing_database", "No workspace database. Run `jobpipe init` from the selected workspace first.")
    try:
        with closing(sqlite3.connect(":memory:")) as expected, closing(db.connect(mode="ro")) as actual:
            expected.executescript(db.SCHEMA)
            tables = [row[0] for row in expected.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")]
            for table in tables:
                # Table names come only from the application's constant schema.
                required = {row[1] for row in expected.execute(f'PRAGMA table_info("{table}")')}
                present = {row[1] for row in actual.execute(f'PRAGMA table_info("{table}")')}
                if not required <= present:
                    raise ServiceError("invalid_database", "Database schema is incomplete or outdated. Run `jobpipe init` in this workspace to initialize/migrate it.")
    except sqlite3.Error as exc:
        raise ServiceError("invalid_database", "Cannot read the workspace SQLite database; verify its format and permissions.") from exc


def require_job(job_id: int) -> dict[str, Any]:
    row = db.get_job(job_id)
    if row is None:
        raise ServiceError("unknown_job", f"Job {job_id} not found. Use search_jobs to find an existing ID.")
    return dict(row)


def scoring_job(job: Any) -> dict[str, Any]:
    """Hydrate persisted classification for every presentation of the same job."""
    result = dict(job)
    classification = db.get_classification(int(result["id"]))
    if classification is not None:
        result.update(primary_role_family=classification["primary_role_family"],
                      secondary_role_families=load_json(classification["secondary_role_families_json"]),
                      canonical_seniority=classification["seniority"])
    return result


def selected_profile(goal: str | None = None) -> SearchProfile:
    profile = load_search_profile()
    if goal is not None:
        try:
            definition = require_goal(goal)
        except ValueError as exc:
            raise ServiceError("invalid_arguments", str(exc)) from exc
        profile = profile.model_copy(update={"active_goal": definition.name})
    return profile


def job_score(job: Any, profile: SearchProfile | None = None) -> tuple[int, list[str]]:
    return score_job_fit_v1(scoring_job(job), learned_weights=load_learned_weights(), profile=profile or selected_profile())


def package_paths(job: dict[str, Any]) -> dict[str, str]:
    paths = application_package_paths(job)
    return {key: str(value) for key, value in asdict(paths).items() if value.exists() and not value.is_symlink()}


def decorate_jobs(rows: Any, profile: SearchProfile | None = None) -> list[dict[str, Any]]:
    profile = profile or selected_profile()
    weights = load_learned_weights()
    jobs = []
    for row in rows:
        job = scoring_job(row)
        job["score"], job["score_notes"] = score_job_fit_v1(job, learned_weights=weights, profile=profile)
        job["display_status"] = display_status(job)
        paths = application_package_paths(job)
        job["has_package"] = any(p.is_file() for p in (paths.job_md, paths.fit_md, paths.resume_tailored_md, paths.cover_note_md, paths.apply_md))
        job["bucket"] = job_bucket(job)
        jobs.append(job)
    return jobs


def is_followup_due(job: dict[str, Any]) -> bool:
    if job.get("status") != "applied" or not job.get("followup_at"):
        return False
    try:
        timestamp = datetime.fromisoformat(str(job["followup_at"]).replace("Z", "+00:00"))
        return timestamp.replace(tzinfo=timestamp.tzinfo or timezone.utc) <= datetime.now(timezone.utc)
    except ValueError:
        return False


def matches_status(job: dict[str, Any], status: str) -> bool:
    if status in {"analyzed", "ready_to_apply", "archived", "ignored"}:
        return display_status(job) == status
    return job_bucket(job) == status


def filter_jobs(jobs: list[dict[str, Any]], *, status: str | None = None, provider: str | None = None,
                company: str | None = None, min_score: int = 0, remote_only: bool = False,
                query: str | None = None, packaged: bool | None = None,
                followup_due: bool | None = None, active_only: bool = True) -> list[dict[str, Any]]:
    """The dashboard's existing filters and stable score/discovery/ID ordering."""
    selected = []
    status = status.strip().lower() if status else None
    for job in jobs:
        if active_only and status not in {"rejected", "skipped", "archived", "ignored"} and job["bucket"] not in ACTIVE_BUCKETS:
            continue
        if status and not matches_status(job, status):
            continue
        if provider and provider.strip().lower() not in str(job.get("provider") or "").lower():
            continue
        if company and company.strip().lower() not in str(job.get("company") or "").lower():
            continue
        if packaged is not None and bool(job.get("has_package")) is not packaged:
            continue
        if followup_due is not None and is_followup_due(job) is not followup_due:
            continue
        if int(job.get("score") or 0) < min_score or (remote_only and not job.get("remote")):
            continue
        if query and query.strip().lower() not in " ".join(str(job.get(k) or "") for k in ("company", "title", "location", "description", "raw_text")).lower():
            continue
        selected.append(job)
    return sorted(selected, key=lambda j: (-int(j["score"]), str(j.get("discovered_at") or ""), int(j["id"])))


def page(items: list[Any], limit: int, offset: int) -> dict[str, Any]:
    end = offset + limit
    return {"items": items[offset:end], "total": len(items), "limit": limit, "offset": offset,
            "next_offset": end if end < len(items) else None}


def job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {"job_id": job["id"], "title": job.get("title") or "", "company": job.get("company") or "",
            "location": job.get("location") or "", "remote": bool(job.get("remote")),
            "primary_role_family": job.get("primary_role_family"), "seniority": job.get("canonical_seniority"),
            "fit_score": job["score"], "status": job["display_status"], "review_status": job.get("review_status"),
            "application_status": job.get("status"), "workflow_bucket": job["bucket"],
            "source": {"source_id": job.get("source_id"), "provider": job.get("provider") or "manual"},
            "application_url": job.get("apply_url") or job.get("source_url") or ""}


def search_jobs(query: str | None = None, role_family: str | None = None, company: str | None = None,
                remote: bool | None = None, min_fit_score: int = 0, status: str | None = None,
                goal: str | None = None, active_only: bool = True, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    profile = selected_profile(goal)
    jobs = filter_jobs(decorate_jobs(db.list_jobs(), profile), status=status, company=company,
                       query=query, min_score=min_fit_score, active_only=active_only)
    jobs = [j for j in jobs if (remote is None or bool(j.get("remote")) is remote)
            and (role_family is None or j.get("primary_role_family") == role_family)]
    return {**page([job_summary(j) for j in jobs], limit, offset), "goal": profile.active_goal}


def get_job(job_id: int) -> dict[str, Any]:
    raw = require_job(job_id)
    rows = [r for r in db.list_jobs() if r["id"] == job_id]
    job = decorate_jobs(rows)[0]
    classification = db.get_classification(job_id)
    application = db.get_application(job_id)
    return {**job_summary(job), "canonical": raw,
            "classification": None if classification is None else {
                **{k: classification[k] for k in ("primary_role_family", "seniority", "provenance")},
                **{k: load_json(classification[k + "_json"]) for k in ("secondary_role_families", "role_evidence", "seniority_evidence")}},
            "requirements": asdict(extract_job_requirements(scoring_job(raw))), "description": raw.get("description") or raw.get("raw_text") or "",
            "application": dict(application) if application else None, "package_paths": package_paths(raw)}


def get_fit_breakdown(job_id: int, goal: str | None = None) -> dict[str, Any]:
    job = scoring_job(require_job(job_id))
    profile = selected_profile(goal)
    breakdown = score_job_fit_breakdown(job, learned_weights=load_learned_weights(), profile=profile)
    facts = build_fact_report(job, profile)
    contributions = [asdict(c) for c in breakdown.contributions]
    classification = db.get_classification(job_id)
    return {"job_id": job_id, "goal": profile.active_goal, **asdict(breakdown),
            "seniority": job.get("canonical_seniority"),
            "positive_signals": [c for c in contributions if c["amount"] > 0],
            "negative_signals": [c for c in contributions if c["amount"] < 0],
            "eligibility_concerns": facts.concerns + facts.missing_mismatched,
            "caps": {"location": breakdown.location_cap, "minimum": 0, "maximum": 100},
            "classification": None if classification is None else {
                **{key: classification[key] for key in ("primary_role_family", "seniority", "provenance")},
                **{key: load_json(classification[key + "_json"]) for key in ("secondary_role_families", "role_evidence", "seniority_evidence")}}}


def list_applications(status: str | None = None, company: str | None = None,
                      date_from: date | None = None, date_to: date | None = None,
                      due_followup: bool | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    rows = db.fetch_all("""SELECT applications.*, jobs.title, jobs.company FROM applications
                         JOIN jobs ON jobs.id = applications.job_id ORDER BY applications.created_at DESC, jobs.id ASC""")
    result = []
    for row in rows:
        item = dict(row)
        if status and item["status"] != status:
            continue
        if company and company.casefold() not in str(item["company"] or "").casefold():
            continue
        if due_followup is not None and is_followup_due(item) is not due_followup:
            continue
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        created = created.replace(tzinfo=created.tzinfo or timezone.utc).astimezone(timezone.utc).date()
        if (date_from and created < date_from) or (date_to and created > date_to):
            continue
        result.append(item)
    return page(result, limit, offset)


def get_followups(due_only: bool = False, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    rows = db.fetch_all("""SELECT applications.*, jobs.title, jobs.company FROM applications
                          JOIN jobs ON jobs.id = applications.job_id
                          WHERE applications.status = 'applied' AND applications.followup_at IS NOT NULL
                          ORDER BY applications.followup_at ASC, jobs.id ASC""")
    items = [{**dict(r), "due": is_followup_due(dict(r))} for r in rows]
    return page([i for i in items if not due_only or i["due"]], limit, offset)


def get_active_goal() -> dict[str, Any]:
    loaded = load_search_profiles()
    profile = selected_profile()
    return {"profile": profile.name, "active_goal": profile.active_goal, "configuration_origin": loaded.origin,
            "available_goals": [asdict(g) for g in available_goals()]}


def get_source_yield(source_id: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    sources = catalog_sources(data_dir() / "job_sources.yaml", package_dir() / "data" / "job_sources.yaml")
    report = build_source_metrics(sources)
    runs = completed_runs(report)
    latest = runs[0] if runs else None
    stats = run_stats(report, latest["id"]) if latest else {}
    labels = sorted(set(report.corpus) | set(stats) | {source_identity_label(s) for s in sources})
    items = []
    for label in labels:
        if source_id is not None and label != source_id:
            continue
        stat = stats.get(label)
        measured = stat is not None and stat["fetch_status"] == "success"
        counts = report.corpus.get(label, {})
        items.append({"source_id": label, "ingest_measurement": {
            "run_id": latest["id"] if latest else None,
            "measured_at": latest["completed_at"] if stat and latest else None,
            "fetch_status": stat["fetch_status"] if stat else "unmeasured", "error": stat["error"] if stat else None,
            **{key: stat[key] if measured else None for key in ("fetched", "passed", "rejected")},
            "yield_rate": rate(stat["passed"], stat["fetched"]) if measured else None},
            "current_corpus": {key: counts.get(key, 0) for key in ("total", "active", "saved", "packaged", "applied")}})
    return {**page(items, limit, offset), "latest_completed_run": {k: latest[k] for k in ("id", "started_at", "completed_at", "profile", "goal", "status")} if latest else None, "inferred_jobs": report.inferred_jobs,
            "unattributed_active": report.unattributed_active}


def save_job(job_id: int, reason: str | None = None, note: str | None = None, *, sync_learning: bool = False) -> dict[str, Any]:
    """Save review state once, retaining the independent application lifecycle."""
    with closing(db.connect(mode="rw")) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ServiceError("unknown_job", f"Job {job_id} not found.")
        changed = row["status"] != "saved"
        if changed:
            conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", ("saved", now_iso(), job_id))
            conn.execute("INSERT INTO feedback (job_id, action, reason_codes, note, created_at) VALUES (?, ?, ?, ?, ?)",
                         (job_id, "saved", reason, note, now_iso()))
    if changed and sync_learning:
        sync_weights()
    result = get_job(job_id)
    return {key: result[key] for key in ("job_id", "status", "review_status", "application_status", "workflow_bucket")} | {"changed": changed}


def prepare_application(job_id: int, reason: str | None = None, note: str | None = None, *, sync_learning: bool = False, require_eligible: bool = False, mode: str = "ready_for_submission") -> dict[str, Any]:
    """Reuse complete bundles and record one preparation event; never submit."""
    from .applicant_profile import get_applicant_profile, state, verified_values
    from .applicant_models import SECTIONS
    from .readiness import artifact_hashes, context_fingerprint, get_application_readiness
    if mode not in {"ready_for_submission", "draft_preparation"}:
        raise ValueError("Unknown preparation mode")
    with closing(db.connect(mode="rw")) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job = require_job(job_id)
        if job["status"] not in {"saved", "packaged"}:
            raise ServiceError("invalid_state_transition", "Run save before package.")
        assessment = get_job_eligibility(job_id)["eligibility"]
        if require_eligible and (assessment["status"] == "ineligible" or (assessment["status"] != "eligible" and mode != "draft_preparation")):
            raise ServiceError("eligibility_unresolved", f"Preparation requires confirmed eligibility ({assessment['status']}). Use get_job_eligibility and resolve its evidence gaps first.")
        if not require_eligible and assessment["status"] != "eligible":
            mode = "draft_preparation"
        try:
            reused = existing_package(job) is not None
            paths = build_application_package(job_id, preparation_mode=mode)
        except (ValueError, OSError) as exc:
            raise ServiceError("application_preparation_failure", str(exc)) from exc
        recorded = conn.execute("SELECT 1 FROM feedback WHERE job_id = ? AND action = 'packaged'", (job_id,)).fetchone()
        if recorded is None:
            conn.execute("INSERT INTO feedback (job_id, action, reason_codes, note, created_at) VALUES (?, ?, ?, ?, ?)",
                         (job_id, "packaged", reason, note, now_iso()))
        if not reused and state(conn)["active"]:
            snapshot = get_applicant_profile(list(SECTIONS))
            conn.execute("INSERT OR REPLACE INTO application_package_provenance VALUES (?,?,?,?,?,?,?)",
                         (job_id, snapshot["revision"], context_fingerprint(scoring_job(job)), mode,
                          json.dumps({k: v["revision_id"] for k, v in verified_values(snapshot).items()}, sort_keys=True),
                          json.dumps(artifact_hashes(job), sort_keys=True), now_iso()))
    if recorded is None and sync_learning:
        sync_weights()
    result = get_job(job_id)
    concerns = list(extract_job_requirements(job).concerns)
    if not require_eligible:
        try:
            manual_assessment = get_job_eligibility(job_id)["eligibility"]
            concerns.extend([f"Application eligibility: {manual_assessment['status']}.", *manual_assessment["concerns"]])
        except ValueError:
            concerns.append("Application eligibility could not be assessed; inspect applicant configuration.")
    if reused:
        concerns.append("Existing materials reused unchanged; they may reflect an older profile or job description.")
        from .readiness import package_record
        previous = package_record(job_id)
        if previous:
            mode = previous["mode"]
    return {key: result[key] for key in ("job_id", "status", "review_status", "application_status", "workflow_bucket")} | {
        "package_directory": str(paths.directory), "resume_path": str(paths.resume_tailored_md),
        "cover_note_path": str(paths.cover_note_md), "fit_analysis_path": str(paths.fit_md),
        "application_instruction_path": str(paths.apply_md), "validation_concerns": concerns, "reused": reused,
        "mode": mode, "readiness": get_application_readiness(job_id)}


def get_job_eligibility(job_id: int, applicant_profile: str | None = None) -> dict[str, Any]:
    from .eligibility import assess_eligibility, load_applicant
    applicant, raw = load_applicant(applicant_profile)
    job = scoring_job(require_job(job_id))
    return {"job_id": job_id, "technical_fit_score": job_score(job)[0],
            "applicant_profile": applicant_profile or "candidate",
            "eligibility": assess_eligibility(job, applicant, selected_profile(), raw).model_dump()}


def search_eligible_jobs(query: str | None = None, role_family: str | None = None,
                         remote_preference: str | None = None, minimum_fit_score: int = 0,
                         eligibility_status: str = "eligible", limit: int = 20,
                         offset: int = 0) -> dict[str, Any]:
    from .eligibility import assess_eligibility, load_applicant
    applicant, raw = load_applicant()
    profile = selected_profile()
    jobs = filter_jobs(decorate_jobs(db.list_jobs(), profile), query=query,
                       min_score=minimum_fit_score, active_only=True)
    items = []
    for job in jobs:
        if role_family and job.get("primary_role_family") != role_family:
            continue
        assessment = assess_eligibility(job, applicant, profile, raw)
        if eligibility_status != "all" and assessment.status != eligibility_status:
            continue
        if remote_preference == "remote_us":
            if not assessment.location_scope.startswith("remote_") or (assessment.location_scope != "remote_anywhere" and "US" not in assessment.hiring_countries):
                continue
        if remote_preference == "remote" and not assessment.location_scope.startswith("remote_"):
            continue
        items.append({**job_summary(job), "eligibility": assessment.model_dump()})
    # decorate/filter already implement the canonical score/time/ID ordering.
    order = {"eligible": 0, "needs_review": 1, "ineligible": 2}
    items.sort(key=lambda item: order[item["eligibility"]["status"]])
    return {**page(items, limit, offset), "goal": profile.active_goal}


followup_due = is_followup_due

# Fixed service facade shared by adapters; imports do not start network/workers.
from .discovery_service import ingest_sources, trial_source, get_discovery_status
from .applicant_profile import get_applicant_profile, propose_applicant_facts
from .readiness import get_applicant_readiness, get_application_readiness, get_readiness_questions, get_application_answer
