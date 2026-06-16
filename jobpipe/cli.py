from __future__ import annotations

from pathlib import Path

import typer
from jinja2 import Environment, FileSystemLoader
from rich.console import Console
from rich.table import Table

from .browser import open_url
from .classify import classify_job
from .db import (
    get_analysis,
    get_application,
    get_job,
    init_database,
    insert_job,
    list_jobs as db_list_jobs,
    shortlist_jobs,
    update_job,
    upsert_analysis,
    upsert_packet_paths,
)
from .export import export_tracker_csv
from .ingest import ingest_text_file, ingest_url
from .models import CandidateProfile, JobAnalysis, ParsedJob, RolePack
from .packet import build_packet_context
from .parse import parse_job_text
from .score import score_fit, score_quality, score_risk
from .tailor import tailor_job
from .tracker import ALLOWED_STATUSES, update_status
from .utils import data_dir, ensure_dir, load_json, load_yaml, output_dir, package_dir, root_dir, slugify

app = typer.Typer(help="Application cockpit for repetitive job applications.")
console = Console()


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(root_dir() / "templates")), autoescape=False, trim_blocks=True, lstrip_blocks=True)


def _load_candidate() -> CandidateProfile:
    return CandidateProfile.model_validate(load_yaml(data_dir() / "profiles" / "candidate.yaml"))


def _load_role_packs() -> dict[str, RolePack]:
    packs: dict[str, RolePack] = {}
    for path in sorted((root_dir() / "role_packs").glob("*.yaml")):
        packs[path.stem] = RolePack.model_validate(load_yaml(path))
    return packs


def _package_assets() -> list[tuple[Path, Path]]:
    pkg = package_dir()
    return [
        (pkg / "data" / "profiles" / "candidate.yaml", root_dir() / "data" / "profiles" / "candidate.yaml"),
        (pkg / "role_packs" / "helpdesk.yaml", root_dir() / "role_packs" / "helpdesk.yaml"),
        (pkg / "role_packs" / "technical_support.yaml", root_dir() / "role_packs" / "technical_support.yaml"),
        (pkg / "role_packs" / "junior_dev.yaml", root_dir() / "role_packs" / "junior_dev.yaml"),
        (pkg / "role_packs" / "customer_support.yaml", root_dir() / "role_packs" / "customer_support.yaml"),
        (pkg / "templates" / "resume.md.j2", root_dir() / "templates" / "resume.md.j2"),
        (pkg / "templates" / "short_answers.md.j2", root_dir() / "templates" / "short_answers.md.j2"),
        (pkg / "templates" / "cover_note.md.j2", root_dir() / "templates" / "cover_note.md.j2"),
        (pkg / "templates" / "packet.md.j2", root_dir() / "templates" / "packet.md.j2"),
    ]


def _ensure_workspace() -> None:
    for path in [
        root_dir() / "data" / "profiles",
        root_dir() / "role_packs",
        root_dir() / "templates",
        output_dir() / "packets",
        output_dir() / "resumes",
        output_dir() / "answers",
    ]:
        ensure_dir(path)
    init_database()
    for src, dst in _package_assets():
        if not dst.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _row_to_job(row) -> ParsedJob:
    return ParsedJob.model_validate(dict(row))


def _render_template(template_name: str, context: dict) -> str:
    return _env().get_template(template_name).render(**context)


@app.command("init")
def init_command() -> None:
    _ensure_workspace()
    console.print("Initialized jobpipe workspace.")


@app.command("add-text")
def add_text(file: Path = typer.Option(..., exists=True, dir_okay=False, readable=True)) -> None:
    _ensure_workspace()
    parsed = ingest_text_file(file)
    job_id = insert_job(parsed.model_dump())
    console.print(f"Added job {job_id}: {parsed.title or 'Untitled job'}")


@app.command("add-url")
def add_url(url: str) -> None:
    _ensure_workspace()
    parsed, warning = ingest_url(url)
    if parsed.description:
        parsed = parse_job_text(parsed.raw_text or parsed.description, source_url=url)
    job_id = insert_job(parsed.model_dump())
    console.print(f"Added job {job_id} from URL.")
    if warning:
        console.print(warning)


@app.command("list")
def list_command() -> None:
    rows = db_list_jobs()
    table = Table(title="Jobs")
    for column in ["ID", "Title", "Company", "Role", "Fit", "Risk", "Status"]:
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row["id"]),
            row["title"] or "",
            row["company"] or "",
            row["role_type"] or "",
            str(row["fit_score"] or ""),
            str(row["risk_score"] or ""),
            row["status"] or "saved",
        )
    console.print(table)


@app.command("analyze")
def analyze(job_id: int) -> None:
    _ensure_workspace()
    row = get_job(job_id)
    if not row:
        raise typer.BadParameter(f"Job {job_id} not found.")
    job = _row_to_job(row)
    candidate = _load_candidate()
    packs = _load_role_packs()
    role_type, seniority, matched_keywords, missing_keywords = classify_job(job, packs)
    pack = packs.get(role_type)
    fit_score, fit_notes = score_fit(job, role_type, seniority, pack, candidate, matched_keywords)
    risk_score, red_flags = score_risk(job, pack)
    quality_score, quality_notes = score_quality(job, role_type, seniority)
    analysis = JobAnalysis(
        role_type=role_type,
        seniority_level=seniority,
        fit_score=fit_score,
        risk_score=risk_score,
        quality_score=quality_score,
        matched_keywords=matched_keywords,
        missing_keywords=missing_keywords,
        red_flags=red_flags,
        notes="; ".join(fit_notes + quality_notes),
    )
    upsert_analysis(job_id, analysis.model_dump())
    console.print(f"Analyzed job {job_id}: fit {fit_score}, risk {risk_score}, quality {quality_score}")


@app.command("shortlist")
def shortlist(min_fit: int = 70, max_risk: int = 40, limit: int = 20) -> None:
    rows = shortlist_jobs(min_fit, max_risk, limit)
    table = Table(title="Shortlist")
    for column in ["ID", "Title", "Company", "Role", "Fit", "Risk", "Quality", "Combined", "Status"]:
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row["id"]),
            row["title"] or "",
            row["company"] or "",
            row["role_type"] or "",
            str(row["fit_score"] or 0),
            str(row["risk_score"] or 0),
            str(row["quality_score"] or 0),
            str(row["combined_score"] or 0),
            row["status"] or "",
        )
    console.print(table)


@app.command("tailor")
def tailor(job_id: int) -> None:
    _ensure_workspace()
    job_row = get_job(job_id)
    analysis_row = get_analysis(job_id)
    if not job_row or not analysis_row:
        raise typer.BadParameter("Run analyze before tailor.")
    job = _row_to_job(job_row)
    analysis = JobAnalysis(
        role_type=analysis_row["role_type"],
        seniority_level=analysis_row["seniority_level"],
        fit_score=analysis_row["fit_score"],
        risk_score=analysis_row["risk_score"],
        quality_score=analysis_row["quality_score"],
        matched_keywords=load_json(analysis_row["matched_keywords_json"]),
        missing_keywords=load_json(analysis_row["missing_keywords_json"]),
        red_flags=load_json(analysis_row["red_flags_json"]),
        notes=analysis_row["notes"] or "",
    )
    candidate = _load_candidate()
    pack = _load_role_packs().get(analysis.role_type)
    tailored = tailor_job(job, analysis.role_type, pack, candidate)
    slug = f"{job_id}-{slugify(job.title or analysis.role_type)}"
    resume_path = output_dir() / "resumes" / f"{slug}.md"
    answers_path = output_dir() / "answers" / f"{slug}-answers.md"
    cover_note_path = output_dir() / "answers" / f"{slug}-cover-note.md"
    resume_path.write_text(_render_template("resume.md.j2", {"candidate": candidate, "job": job, "analysis": analysis, "tailor": tailored}), encoding="utf-8")
    answers_path.write_text(_render_template("short_answers.md.j2", {"job": job, "analysis": analysis, "tailor": tailored}), encoding="utf-8")
    cover_note_path.write_text(_render_template("cover_note.md.j2", {"job": job, "analysis": analysis, "tailor": tailored}), encoding="utf-8")
    upsert_packet_paths(job_id, resume_path=str(resume_path), answers_path=str(answers_path), cover_note_path=str(cover_note_path))
    console.print(f"Generated tailored files for job {job_id}.")


@app.command("packet")
def packet(job_id: int) -> None:
    _ensure_workspace()
    job_row = get_job(job_id)
    analysis_row = get_analysis(job_id)
    if not job_row or not analysis_row:
        raise typer.BadParameter("Run analyze before packet.")
    job = _row_to_job(job_row)
    analysis = JobAnalysis(
        role_type=analysis_row["role_type"],
        seniority_level=analysis_row["seniority_level"],
        fit_score=analysis_row["fit_score"],
        risk_score=analysis_row["risk_score"],
        quality_score=analysis_row["quality_score"],
        matched_keywords=load_json(analysis_row["matched_keywords_json"]),
        missing_keywords=load_json(analysis_row["missing_keywords_json"]),
        red_flags=load_json(analysis_row["red_flags_json"]),
        notes=analysis_row["notes"] or "",
    )
    candidate = _load_candidate()
    pack = _load_role_packs().get(analysis.role_type)
    tailored = tailor_job(job, analysis.role_type, pack, candidate)
    packet_context = build_packet_context(analysis)
    slug = f"{job_id}-{slugify(job.title or analysis.role_type)}"
    packet_path = output_dir() / "packets" / f"{slug}-packet.md"
    packet_path.write_text(
        _render_template(
            "packet.md.j2",
            {"job": job, "analysis": analysis, "tailor": tailored, "packet": packet_context},
        ),
        encoding="utf-8",
    )
    upsert_packet_paths(job_id, packet_path=str(packet_path))
    console.print(f"Generated packet for job {job_id}: {packet_path}")


@app.command("status")
def status(job_id: int, status_value: str, notes: str = "") -> None:
    if status_value not in ALLOWED_STATUSES:
        raise typer.BadParameter(f"Status must be one of: {', '.join(ALLOWED_STATUSES)}")
    update_status(job_id, status_value, notes)
    console.print(f"Updated job {job_id} to {status_value}.")


@app.command("open")
def open_command(job_id: int) -> None:
    row = get_job(job_id)
    if not row or not row["source_url"]:
        raise typer.BadParameter("No source URL stored for this job.")
    opened = open_url(row["source_url"])
    console.print("Opened browser." if opened else "Browser open request failed.")


@app.command("export")
def export_command() -> None:
    _ensure_workspace()
    path = export_tracker_csv()
    console.print(f"Exported tracker CSV to {path}")
