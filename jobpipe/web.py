from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .application_package import application_package_paths, build_application_package
from .catalog import load_search_profile
from .facts import build_fact_report
from .browser import open_url
from .db import due_followups, fetch_one, get_analysis, get_application, get_job, insert_feedback, list_jobs, set_review_status
from .learn import load_learned_weights, sync_weights
from .score import score_job_fit_v1
from .utils import load_json, package_dir

HOST = "127.0.0.1"
PORT = 8765


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
        )
        return templates.TemplateResponse(
            request,
            "jobs.html",
            {
                "jobs": jobs,
                "filters": {
                    "status": status or "",
                    "provider": provider or "",
                    "company": company or "",
                    "min_score": min_score,
                    "remote_only": remote_only,
                    "q": q or "",
                },
            },
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: int):
        context = _job_detail_context(job_id)
        return templates.TemplateResponse(request, "job_detail.html", context)

    @app.post("/jobs/{job_id}/save")
    def save_job(job_id: int, next: str | None = None):
        _require_job(job_id)
        set_review_status(job_id, "saved")
        insert_feedback(job_id, "saved")
        sync_weights()
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
            build_application_package(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        insert_feedback(job_id, "packaged")
        sync_weights()
        return _redirect(next, f"/jobs/{job_id}")

    @app.post("/jobs/{job_id}/apply-flow")
    def open_apply(job_id: int, next: str | None = None):
        job = _require_job(job_id)
        apply_url = _job_apply_url(dict(job))
        if not apply_url:
            raise HTTPException(status_code=400, detail="No apply URL stored for this job.")
        open_url(apply_url)
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
    learned_weights = load_learned_weights()
    profile = load_search_profile()
    return score_job_fit_v1(job, learned_weights=learned_weights, profile=profile)


def _job_apply_url(job: dict[str, Any]) -> str:
    return str(job.get("apply_url") or job.get("source_url") or "")


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
    learned_weights = load_learned_weights()
    profile = load_search_profile()
    jobs: list[dict[str, Any]] = []
    for row in rows:
        job = dict(row)
        score, notes = score_job_fit_v1(job, learned_weights=learned_weights, profile=profile)
        job["score"] = score
        job["score_notes"] = notes
        job["display_status"] = _display_status(job)
        job["has_package"] = bool(_package_paths(job))
        jobs.append(job)
    return jobs


def _filter_jobs(
    jobs: list[dict[str, Any]],
    *,
    status: str | None,
    provider: str | None,
    company: str | None,
    min_score: int,
    remote_only: bool,
    query: str | None,
) -> list[dict[str, Any]]:
    filtered = jobs
    if status:
        needle = status.strip().lower()
        filtered = [job for job in filtered if str(job.get("display_status") or "").strip().lower() == needle]
    if provider:
        needle = provider.strip().lower()
        filtered = [job for job in filtered if needle in str(job.get("provider") or "").lower()]
    if company:
        needle = company.strip().lower()
        filtered = [job for job in filtered if needle in str(job.get("company") or "").lower()]
    if min_score:
        filtered = [job for job in filtered if int(job.get("score") or 0) >= min_score]
    if remote_only:
        filtered = [job for job in filtered if bool(job.get("remote"))]
    if query:
        needle = query.strip().lower()
        filtered = [
            job
            for job in filtered
            if needle in " ".join(
                str(value or "")
                for value in (
                    job.get("company"),
                    job.get("title"),
                    job.get("location"),
                    job.get("description"),
                    job.get("raw_text"),
                )
            ).lower()
        ]
    return sorted(
        filtered,
        key=lambda job: (
            -int(job.get("score") or 0),
            str(job.get("discovered_at") or ""),
            int(job.get("id") or 0),
        ),
    )


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
    if application_status == "applied":
        return application_status
    return review_status or application_status


def _job_detail_context(job_id: int) -> dict[str, Any]:
    row = get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")

    job = dict(row)
    analysis_row = get_analysis(job_id)
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
        },
        "analysis": _analysis_context(analysis_row),
        "application": dict(application_row) if application_row else None,
        "packet": dict(packet_row) if packet_row else None,
        "package_paths": package_paths,
        "learned_adjustments": learned_adjustments,
        "fact_report": fact_report,
    }


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
