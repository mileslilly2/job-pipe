from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .application_package import application_package_paths
from .analytics import build_analytics_report
from .browser import copy_file, open_path, open_url
from .catalog import load_search_profile
from .db import due_followups, fetch_one, get_analysis, get_application, get_classification, get_job, insert_feedback, list_jobs, set_review_status
from .facts import build_fact_report
from .learn import sync_weights
from .tracker import update_status
from .workflow import job_bucket as _job_bucket
from . import services
from .utils import load_json, package_dir

HOST = "127.0.0.1"
PORT = 8765

ACTIVE_BUCKETS = {"new", "saved", "packaged", "assessment", "applied", "interview", "offer"}
ASSESSMENT_STATUSES = {"analyzed", "ready_to_apply"}
KANBAN_COLUMN_ORDER = ("new", "saved", "packaged", "assessment", "applied", "interview", "offer", "rejected", "skipped")
KANBAN_COLUMNS = {
    "new": {"label": "New", "description": "Fresh jobs pulled into the workspace."},
    "saved": {"label": "Saved", "description": "Jobs worth keeping but not yet packaged."},
    "packaged": {"label": "Packaged", "description": "Jobs with generated application materials."},
    "assessment": {"label": "Assessment", "description": "Jobs under review or ready to apply."},
    "applied": {"label": "Applied", "description": "Submitted applications waiting on a response."},
    "interview": {"label": "Interview", "description": "Pipeline jobs in interview stages."},
    "offer": {"label": "Offer", "description": "Late-stage opportunities."},
    "rejected": {"label": "Rejected", "description": "Closed-out jobs that were declined or rejected."},
    "skipped": {"label": "Skipped", "description": "Jobs intentionally ignored or skipped."},
}
SAVED_VIEWS: tuple[dict[str, Any], ...] = (
    {
        "name": "High Fit New Jobs",
        "description": "New jobs with a strong fit score.",
        "filters": {"status": "new", "min_score": 70},
    },
    {
        "name": "Saved Not Packaged",
        "description": "Saved jobs that still need a package.",
        "filters": {"status": "saved", "packaged": 0},
    },
    {
        "name": "Packaged Not Applied",
        "description": "Jobs with generated materials that have not been submitted.",
        "filters": {"status": "packaged"},
    },
    {
        "name": "Applied Awaiting Response",
        "description": "Submitted jobs waiting on the employer.",
        "filters": {"status": "applied"},
    },
    {
        "name": "Follow-Up Due",
        "description": "Applied jobs with overdue follow-up dates.",
        "filters": {"status": "applied", "followup_due": 1},
    },
    {
        "name": "Interview Pipeline",
        "description": "Jobs currently in the interview stage.",
        "filters": {"status": "interview"},
    },
    {
        "name": "Rejected Archive",
        "description": "Rejected jobs kept for reference.",
        "filters": {"status": "rejected"},
    },
)


def _template_dir() -> Path:
    return package_dir() / "templates" / "web"


templates = Jinja2Templates(directory=str(_template_dir()))


def create_app() -> FastAPI:
    app = FastAPI(title="JobPipe Dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        jobs = _decorate_jobs(list_jobs())
        summary = _dashboard_summary(jobs)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "summary": summary,
                "jobs": jobs,
            },
        )

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(
        request: Request,
        status: str | None = None,
        provider: str | None = None,
        company: str | None = None,
        min_score: int = 0,
        remote_only: bool = False,
        q: str | None = None,
        packaged: str | None = None,
        followup_due: str | None = None,
    ):
        jobs = _decorate_jobs(list_jobs())
        jobs = _filter_jobs(
            jobs,
            status=status,
            provider=provider,
            company=company,
            min_score=min_score,
            remote_only=remote_only,
            query=q,
            packaged=_parse_bool(packaged),
            followup_due=_parse_bool(followup_due),
        )
        return templates.TemplateResponse(
            request,
            "jobs.html",
            {
                "jobs": jobs,
                "status_options": _jobs_status_options(),
                "filters": {
                    "status": status or "",
                    "provider": provider or "",
                    "company": company or "",
                    "min_score": min_score,
                    "remote_only": remote_only,
                    "q": q or "",
                    "packaged": _parse_bool(packaged),
                    "followup_due": _parse_bool(followup_due),
                },
            },
        )

    @app.get("/kanban", response_class=HTMLResponse)
    def kanban(request: Request):
        jobs = [job for job in _decorate_jobs(list_jobs()) if _job_bucket(job) != "archived"]
        columns = [
            {
                "key": key,
                "label": KANBAN_COLUMNS[key]["label"],
                "description": KANBAN_COLUMNS[key]["description"],
                "jobs": sorted(
                    [job for job in jobs if _job_bucket(job) == key],
                    key=lambda job: (
                        -int(job.get("score") or 0),
                        str(job.get("discovered_at") or ""),
                        int(job.get("id") or 0),
                    ),
                ),
            }
            for key in KANBAN_COLUMN_ORDER
        ]
        return templates.TemplateResponse(
            request,
            "kanban.html",
            {
                "columns": columns,
            },
        )

    @app.get("/views", response_class=HTMLResponse)
    def views(request: Request):
        views = [
            {
                "name": view["name"],
                "description": view["description"],
                "url": _jobs_url(**view["filters"]),
                "filters": view["filters"],
            }
            for view in SAVED_VIEWS
        ]
        return templates.TemplateResponse(
            request,
            "views.html",
            {
                "views": views,
            },
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: int):
        context = _job_detail_context(job_id)
        return templates.TemplateResponse(request, "job_detail.html", context)

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics(request: Request):
        report = build_analytics_report()
        return templates.TemplateResponse(
            request,
            "analytics.html",
            {
                "report": report,
            },
        )

    @app.post("/jobs/{job_id}/save")
    def save_job(job_id: int, next: str | None = None):
        _require_job(job_id)
        services.save_job(job_id, sync_learning=True)
        return _redirect(next, f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/skip")
    def skip_job(job_id: int, next: str | None = None):
        _require_job(job_id)
        set_review_status(job_id, "skipped")
        insert_feedback(job_id, "skipped")
        sync_weights()
        return _redirect(next, f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/package")
    def package_job(job_id: int, next: str | None = None):
        _require_job(job_id)
        try:
            services.prepare_application(job_id, sync_learning=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _redirect(next, f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/apply-flow")
    def open_apply(job_id: int, next: str | None = None):
        job = _require_job(job_id)
        apply_url = _job_apply_url(dict(job))
        if not apply_url:
            raise HTTPException(status_code=400, detail="No apply URL stored for this job.")
        open_url(apply_url)
        return _redirect(next, f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/open-package")
    def open_package(job_id: int, next: str | None = None):
        job = dict(_require_job(job_id))
        directory = application_package_paths(job).directory
        directory.mkdir(parents=True, exist_ok=True)
        open_path(directory)
        return _redirect(next, f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/copy-cover-note")
    def copy_cover_note(job_id: int, next: str | None = None):
        job = dict(_require_job(job_id))
        path = application_package_paths(job).cover_note_md
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run package before copying the cover note.")
        if not copy_file(path):
            raise HTTPException(status_code=400, detail="Clipboard copy failed.")
        return _redirect(next, f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/copy-resume-text")
    def copy_resume_text(job_id: int, next: str | None = None):
        job = dict(_require_job(job_id))
        path = application_package_paths(job).resume_tailored_md
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run package before copying the resume text.")
        if not copy_file(path):
            raise HTTPException(status_code=400, detail="Clipboard copy failed.")
        return _redirect(next, f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/status/{status}")
    def move_status(job_id: int, status: str, next: str | None = None):
        _require_job(job_id)
        _set_status(job_id, status)
        return _redirect(next, f"/jobs/{job_id}")

    @app.get("/followups", response_class=HTMLResponse)
    def followups(request: Request):
        rows = due_followups(limit=100)
        return templates.TemplateResponse(
            request,
            "followups.html",
            {
                "jobs": [_decorate_due_followup(row) for row in rows],
            },
        )

    return app


def run_web_server(*, open_browser: bool = True, host: str = HOST, port: int = PORT) -> None:
    if open_browser:
        open_url(f"http://{host}:{port}")
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def _require_job(job_id: int):
    row = get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return row


def _redirect(next_path: str | None, fallback: str) -> RedirectResponse:
    target = next_path if _is_safe_local_path(next_path) else fallback
    return RedirectResponse(url=target, status_code=303)


def _is_safe_local_path(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith("/") and not value.startswith("//")


def _job_score(job: dict[str, Any]) -> tuple[int, list[str]]:
    return services.job_score(job)


def _job_apply_url(job: dict[str, Any]) -> str:
    return str(job.get("apply_url") or job.get("source_url") or "")


def _jobs_url(**params: Any) -> str:
    query_items: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            query_items.append((key, "1" if value else "0"))
        else:
            query_items.append((key, str(value)))
    return "/jobs" if not query_items else f"/jobs?{urlencode(query_items)}"


def _package_paths(job: dict[str, Any]) -> dict[str, str]:
    paths = application_package_paths(job)
    artifacts = {
        "directory": str(paths.directory),
        "job_md": str(paths.job_md),
        "fit_md": str(paths.fit_md),
        "resume_tailored_md": str(paths.resume_tailored_md),
        "cover_note_md": str(paths.cover_note_md),
        "apply_md": str(paths.apply_md),
    }
    return {name: path for name, path in artifacts.items() if Path(path).exists()}


def _decorate_jobs(rows) -> list[dict[str, Any]]:
    return services.decorate_jobs(rows)


def _filter_jobs(jobs, **filters) -> list[dict[str, Any]]:
    return services.filter_jobs(jobs, **filters)


def _jobs_status_options() -> list[tuple[str, str]]:
    return [
        ("", "All active"),
        ("new", "New"),
        ("saved", "Saved"),
        ("packaged", "Packaged"),
        ("assessment", "Assessment"),
        ("applied", "Applied"),
        ("interview", "Interview"),
        ("offer", "Offer"),
        ("rejected", "Rejected"),
        ("skipped", "Skipped"),
    ]


def _dashboard_summary(jobs: list[dict[str, Any]]) -> dict[str, int]:
    total = len(jobs)
    new_jobs = sum(1 for job in jobs if _display_status(job) == "new")
    saved_jobs = sum(1 for job in jobs if _display_status(job) == "saved")
    applied_jobs = sum(1 for job in jobs if _display_status(job) == "applied")
    skipped_jobs = sum(1 for job in jobs if _display_status(job) == "skipped")
    packaged_jobs = sum(1 for job in jobs if job.get("has_package"))
    due_jobs = len(due_followups(limit=100))
    return {
        "total_jobs": total,
        "new_jobs": new_jobs,
        "saved_jobs": saved_jobs,
        "packaged_jobs": packaged_jobs,
        "applied_jobs": applied_jobs,
        "skipped_jobs": skipped_jobs,
        "due_followups": due_jobs,
    }


def _display_status(job: dict[str, Any]) -> str:
    application_status = str(job.get("status") or "").strip().lower()
    review_status = str(job.get("review_status") or "").strip().lower()
    if application_status in {"applied", "rejected", "interview", "offer", "archived", "ignored"}:
        return application_status
    if application_status in ASSESSMENT_STATUSES:
        return application_status
    return review_status or application_status


def _job_matches_status(job: dict[str, Any], status: str) -> bool:
    return services.matches_status(job, status)


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    needle = value.strip().lower()
    if needle == "":
        return None
    if needle in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if needle in {"0", "false", "f", "no", "n", "off"}:
        return False
    return None


def _job_followup_due(job: dict[str, Any]) -> bool:
    return services.followup_due(job)


def _set_status(job_id: int, status: str) -> None:
    normalized = status.strip().lower()
    if normalized == "saved":
        services.save_job(job_id, sync_learning=True)
        return
    if normalized == "skipped":
        set_review_status(job_id, "skipped")
        insert_feedback(job_id, "skipped")
        sync_weights()
        return
    if normalized == "new":
        set_review_status(job_id, "new")
        return
    if normalized in {"analyzed", "ready_to_apply", "applied", "rejected", "interview", "offer", "ignored", "archived"}:
        update_status(job_id, normalized)
        if normalized in {"rejected", "interview", "offer"}:
            insert_feedback(job_id, normalized)
            sync_weights()
        return
    raise HTTPException(status_code=400, detail=f"Unsupported status: {status}")


def _job_detail_context(job_id: int) -> dict[str, Any]:
    row = get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")

    job = dict(row)
    analysis_row = get_analysis(job_id)
    classification_row = get_classification(job_id)
    application_row = get_application(job_id)
    packet_row = fetch_one("SELECT * FROM application_packets WHERE job_id = ?", (job_id,))
    score, notes = _job_score(job)
    learned_adjustments = [note for note in notes if "learned adjustment:" in note]
    package_paths = _package_paths(job)
    profile = load_search_profile()
    fact_report = build_fact_report(job, profile)

    return {
        "job": {
            **job,
            "score": score,
            "score_notes": notes,
            "display_status": _display_status(job),
            "apply_url": _job_apply_url(job),
            "package_directory": str(application_package_paths(job).directory),
        },
        "analysis": _analysis_context(analysis_row),
        "classification": _classification_context(classification_row),
        "application": dict(application_row) if application_row else None,
        "packet": dict(packet_row) if packet_row else None,
        "package_paths": package_paths,
        "learned_adjustments": learned_adjustments,
        "fact_report": fact_report,
    }


def _classification_context(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"primary_role_family": row["primary_role_family"], "secondary_role_families": load_json(row["secondary_role_families_json"]), "seniority": row["seniority"], "role_evidence": load_json(row["role_evidence_json"]), "seniority_evidence": load_json(row["seniority_evidence_json"]), "provenance": row["provenance"]}


def _analysis_context(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "role_type": row["role_type"],
        "seniority_level": row["seniority_level"],
        "fit_score": row["fit_score"],
        "risk_score": row["risk_score"],
        "quality_score": row["quality_score"],
        "recommendation": row["recommendation"],
        "skip_reason": row["skip_reason"],
        "matched_keywords": load_json(row["matched_keywords_json"]),
        "missing_keywords": load_json(row["missing_keywords_json"]),
        "red_flags": load_json(row["red_flags_json"]),
        "eligibility_flags": load_json(row["eligibility_flags_json"]),
        "notes": row["notes"],
    }


def _decorate_due_followup(row) -> dict[str, Any]:
    job = dict(row)
    score, notes = _job_score(job)
    job["score"] = score
    job["score_notes"] = notes
    return job
