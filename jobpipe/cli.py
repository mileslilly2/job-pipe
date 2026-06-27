from __future__ import annotations

from pathlib import Path

import typer
import yaml
from jinja2 import Environment, FileSystemLoader
from rich.console import Console
from rich.table import Table

from .browser import open_url
from .application_package import application_package_paths, build_application_package
from .classify import classify_job, detect_seniority
from .db import (
    get_analysis,
    get_application,
    get_job,
    get_job_by_provider_external_id,
    init_database,
    insert_job,
    list_jobs as db_list_jobs,
    list_review_jobs,
    set_review_status,
    shortlist_jobs,
    update_job,
    upsert_analysis,
    upsert_packet_paths,
)
from .export import export_tracker_csv
from .ingest import filter_discovered_jobs, ingest_text_file, ingest_url, load_job_sources, resolve_job_sources
from .models import CandidateProfile, JobAnalysis, JobSource, ParsedJob, RolePack
from .packet import build_packet_context
from .parse import parse_job_text
from .providers import discover_jobs as discover_provider_jobs
from .recommend import make_recommendation
from .score import score_fit, score_job_fit_v1, score_quality, score_risk
from .tailor import tailor_job
from .tracker import ALLOWED_STATUSES, due_followups, update_status
from .utils import data_dir, ensure_dir, load_json, load_yaml, output_dir, package_dir, root_dir, slugify

app = typer.Typer(help="Application cockpit for repetitive job applications.")
sources_app = typer.Typer(help="Manage configured job sources.")
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
        (pkg / "profile.md", root_dir() / "profile.md"),
        (pkg / "data" / "profiles" / "candidate.yaml", root_dir() / "data" / "profiles" / "candidate.yaml"),
        (pkg / "data" / "job_sources.yaml", root_dir() / "data" / "job_sources.yaml"),
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
        root_dir() / "applications",
        output_dir() / "packets",
        output_dir() / "resumes",
        output_dir() / "answers",
    ]:
        ensure_dir(path)
    init_database()
    for src, dst in _package_assets():
        if dst.exists():
            if dst.name == "job_sources.yaml" and dst.read_text(encoding="utf-8").strip() in {"", "[]"}:
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            continue
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _row_to_job(row) -> ParsedJob:
    return ParsedJob.model_validate(dict(row))


def _job_sources_path() -> Path:
    return data_dir() / "job_sources.yaml"


def _load_job_sources_records(path: Path) -> list[JobSource]:
    if not path.exists():
        return []
    raw = load_yaml(path)
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("job source config must be a YAML list")
    return [JobSource.model_validate(item) for item in raw]


def _write_job_sources_records(path: Path, sources: list[JobSource]) -> None:
    ensure_dir(path.parent)
    payload = [source.model_dump(exclude_none=True) for source in sources]
    path.write_text(yaml.safe_dump(payload, sort_keys=False, default_flow_style=False), encoding="utf-8")


def _normalize_saved_job_source(source: JobSource) -> JobSource:
    provider = (source.provider or "").strip().lower()
    company = (source.company or "").strip()
    company_slug = (source.company_slug or "").strip() or slugify(company or source.board_token or source.account or provider)
    board_token = (source.board_token or "").strip() or None
    account = (source.account or "").strip() or None
    target_role_families = [value.strip() for value in source.target_role_families if value and value.strip()]
    include_keywords = [value.strip() for value in source.include_keywords if value and value.strip()]
    exclude_keywords = [value.strip() for value in source.exclude_keywords if value and value.strip()]
    return JobSource(
        provider=provider,
        company=company,
        company_slug=company_slug,
        board_token=board_token,
        account=account,
        target_role_families=target_role_families,
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords,
        enabled=source.enabled,
    )


def _source_matches_identifier(source: JobSource, identifier: str) -> bool:
    value = identifier.strip().lower()
    if not value:
        return False
    candidates = [
        source.company_slug,
        source.company,
        source.board_token or "",
        source.account or "",
        f"{source.provider}:{source.company_slug}",
        f"{source.provider}:{source.company}",
    ]
    return any(candidate.strip().lower() == value for candidate in candidates if candidate)


def _find_source_index(sources: list[JobSource], identifier: str) -> int:
    for index, source in enumerate(sources):
        if _source_matches_identifier(source, identifier):
            return index
    raise typer.BadParameter(f"Source {identifier!r} not found in data/job_sources.yaml.")


def _active_source_state(source: JobSource) -> str:
    return "active" if source.enabled else "disabled"


def _render_template(template_name: str, context: dict) -> str:
    return _env().get_template(template_name).render(**context)


def _score_notes(job_row) -> tuple[int, list[str]]:
    score, notes = score_job_fit_v1(dict(job_row))
    return score, notes


def _print_job_details(job_row) -> None:
    job = dict(job_row)
    app_row = get_application(job["id"])
    score, notes = _score_notes(job_row)
    table = Table(title=f"Job {job['id']}", show_header=False)
    table.add_column("Field")
    table.add_column("Value")
    rows = [
        ("Provider", job.get("provider", "")),
        ("External ID", job.get("external_id", "")),
        ("Company", job.get("company", "")),
        ("Title", job.get("title", "")),
        ("Location", job.get("location", "")),
        ("Remote", "yes" if job.get("remote") else "no"),
        ("Apply URL", job.get("apply_url", "") or job.get("source_url", "")),
        ("Posted At", job.get("posted_at", "") or ""),
        ("Discovered At", job.get("discovered_at", "") or ""),
        ("Review Status", job.get("status", "")),
        ("Fit Score", str(score)),
    ]
    if app_row is not None:
        rows.append(("Application Status", app_row["status"]))
    for field, value in rows:
        table.add_row(field, value)
    console.print(table)
    if job.get("description"):
        console.print("Description:")
        console.print(job["description"])
    if notes:
        console.print("Fit breakdown:")
        for note in notes:
            console.print(f"- {note}")


def _print_queue_job(row) -> None:
    score, _notes = _score_notes(row)
    console.rule(f"{row['company']} - {row['title']}")
    console.print(f"Location: {row['location'] or 'Unknown'}")
    console.print(f"Fit score: {score}")
    console.print(f"Apply URL: {row['apply_url'] or ''}")


def _job_apply_url(job: dict) -> str:
    return str(job.get("apply_url") or job.get("source_url") or "")


def _ensure_application_package(job_id: int, job: dict) -> tuple[Path, Path, Path]:
    paths = application_package_paths(job)
    if not all(path.exists() for path in [paths.job_md, paths.fit_md, paths.resume_tailored_md, paths.cover_note_md, paths.apply_md]):
        paths = build_application_package(job_id)
    return paths.resume_tailored_md, paths.cover_note_md, paths.apply_md


def _apply_flow_session(job_id: int) -> None:
    row = get_job(job_id)
    if not row:
        raise typer.BadParameter(f"Job {job_id} not found.")
    if str(row["status"] or "") not in {"saved", "packaged"}:
        raise typer.BadParameter("Run save before apply-flow.")
    job = dict(row)
    apply_url = _job_apply_url(job)
    if not apply_url:
        raise typer.BadParameter("No apply URL stored for this job.")

    resume_md, cover_md, apply_md = _ensure_application_package(job_id, job)

    console.print(f"Resume tailored: {resume_md}")
    console.print(f"Cover note: {cover_md}")
    console.print(f"Apply checklist: {apply_md}")
    opened = open_url(apply_url)
    console.print("Opened browser." if opened else "Browser open request failed.")

    answer = input("Mark this job as applied? [y/N] ").strip().lower()
    if answer == "y":
        update_status(job_id, "applied")
        set_review_status(job_id, "applied")
        console.print(f"Marked job {job_id} as applied.")
    else:
        console.print("Left job unchanged.")


def _batch_review_job(job_row) -> None:
    job = dict(job_row)
    while True:
        _print_queue_job(job_row)
        choice = input("[s] save only  [p] save + package  [a] save + package + apply-flow  [k] skip  [d] details  [o] open URL  [q] quit: ").strip().lower()
        job_id = int(job["id"])
        if choice == "s":
            set_review_status(job_id, "saved")
            console.print(f"Saved job {job_id}.")
            return
        if choice == "p":
            set_review_status(job_id, "saved")
            try:
                paths = build_application_package(job_id)
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
            console.print(f"Saved job {job_id}.")
            console.print(f"Generated application package: {paths.directory}")
            return
        if choice == "a":
            set_review_status(job_id, "saved")
            try:
                paths = build_application_package(job_id)
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
            console.print(f"Saved job {job_id}.")
            console.print(f"Generated application package: {paths.directory}")
            _apply_flow_session(job_id)
            return
        if choice == "k":
            set_review_status(job_id, "skipped")
            console.print(f"Skipped job {job_id}.")
            return
        if choice == "d":
            _print_job_details(get_job(job_id))
            continue
        if choice == "o":
            opened = open_url(str(job.get("apply_url") or job.get("source_url") or ""))
            console.print("Opened browser." if opened else "Browser open request failed.")
            continue
        if choice == "q":
            raise StopIteration
        console.print("Unrecognized action. Use s, p, a, k, d, o, or q.")


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


@app.command("ingest")
def ingest() -> None:
    _ensure_workspace()
    source_config = resolve_job_sources(data_dir() / "job_sources.yaml")
    sources = source_config.sources
    if not sources:
        console.print("No enabled job sources configured. Add entries to data/job_sources.yaml or run `jobpipe init` to restore the starter list.")
        return
    if source_config.origin == "built_in":
        console.print("Using built-in starter job sources because data/job_sources.yaml is missing or empty.")
    else:
        console.print("Using configured job sources from data/job_sources.yaml.")
    ingested = 0
    updated = 0
    filtered_out = 0
    for source in sources:
        try:
            discovered = discover_provider_jobs(source)
        except Exception as exc:  # pragma: no cover - defensive CLI guard
            console.print(f"[red]Failed to ingest {source.provider}:{source.company or source.company_slug or source.board_token or source.account}[/red] {exc}")
            continue
        filtered = filter_discovered_jobs(discovered, source)
        filtered_out += max(len(discovered) - len(filtered), 0)
        for job in filtered:
            payload = job.model_dump()
            payload["fit_score"], _notes = score_job_fit_v1(payload)
            payload["status"] = "new"
            existing = get_job_by_provider_external_id(job.provider, job.external_id)
            insert_job(payload, review_status="new")
            if existing is None:
                ingested += 1
            else:
                updated += 1
    console.print(f"Ingested {ingested} new job(s). Updated {updated} existing job(s). Filtered out {filtered_out} job(s).")


@app.command("list")
def list_command() -> None:
    rows = db_list_jobs()
    table = Table(title="Jobs")
    for column in ["ID", "Title", "Company", "Role", "Seniority", "Fit", "Risk", "Quality", "Rec", "Reason", "Status", "Due"]:
        table.add_column(column)
    for row in rows:
        score, _notes = _score_notes(row)
        table.add_row(
            str(row["id"]),
            row["title"] or "",
            row["company"] or "",
            row["role_type"] or "",
            row["seniority_level"] or "",
            str(score),
            str(row["risk_score"] or ""),
            str(row["quality_score"] or ""),
            row["recommendation"] or "",
            row["skip_reason"] or "",
            row["status"] or "saved",
            row["followup_at"] or "",
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
    role_type, matched_keywords, missing_keywords = classify_job(job, packs)
    pack = packs.get(role_type)
    risk_score, red_flags = score_risk(job, pack)
    seniority, eligibility_flags = detect_seniority(job, risk_score=risk_score, risk_flags=red_flags)
    display_fit_score, display_fit_notes = score_job_fit_v1(job)
    fit_score, fit_notes = score_fit(job, role_type, seniority, pack, candidate, matched_keywords)
    quality_score, quality_notes = score_quality(job, role_type, seniority)
    recommendation, skip_reason = make_recommendation(fit_score, risk_score, quality_score, role_type, seniority, red_flags)
    analysis = JobAnalysis(
        role_type=role_type,
        seniority_level=seniority,
        fit_score=display_fit_score,
        risk_score=risk_score,
        quality_score=quality_score,
        recommendation=recommendation,
        skip_reason=skip_reason,
        matched_keywords=matched_keywords,
        missing_keywords=missing_keywords,
        red_flags=red_flags,
        eligibility_flags=eligibility_flags,
        notes="; ".join(display_fit_notes + fit_notes + quality_notes),
    )
    upsert_analysis(job_id, analysis.model_dump())
    console.print(f"Analyzed job {job_id}: role {role_type}, seniority {seniority}, fit {display_fit_score}, risk {risk_score}, quality {quality_score}")


@app.command("shortlist")
def shortlist(min_fit: int = 70, max_risk: int = 40, limit: int = 20) -> None:
    rows = shortlist_jobs(min_fit, max_risk, limit)
    table = Table(title="Shortlist")
    for column in ["ID", "Title", "Role", "Seniority", "Fit", "Risk", "Qual", "Rec", "Reason", "Status"]:
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row["id"]),
            row["title"] or "",
            row["role_type"] or "",
            row["seniority_level"] or "",
            str(row["fit_score"] or 0),
            str(row["risk_score"] or 0),
            str(row["quality_score"] or 0),
            row["recommendation"] or "",
            row["skip_reason"] or "",
            row["status"] or "",
        )
    console.print(table)


@app.command("show")
def show(job_id: int) -> None:
    _ensure_workspace()
    row = get_job(job_id)
    if not row:
        raise typer.BadParameter(f"Job {job_id} not found.")
    _print_job_details(row)


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
        recommendation=analysis_row["recommendation"] or "maybe",
        skip_reason=analysis_row["skip_reason"],
        matched_keywords=load_json(analysis_row["matched_keywords_json"]),
        missing_keywords=load_json(analysis_row["missing_keywords_json"]),
        red_flags=load_json(analysis_row["red_flags_json"]),
        eligibility_flags=load_json(analysis_row["eligibility_flags_json"]),
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


@app.command("package")
def package(job_id: int) -> None:
    _ensure_workspace()
    try:
        paths = build_application_package(job_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Generated application package: {paths.directory}")


@app.command("apply-flow")
def apply_flow(job_id: int) -> None:
    _ensure_workspace()
    _apply_flow_session(job_id)


@app.command("review")
def review() -> None:
    _ensure_workspace()
    rows = list_review_jobs()
    if not rows:
        console.print("No jobs in the review queue.")
        return

    index = 0
    while index < len(rows):
        row = rows[index]
        _print_queue_job(row)
        choice = input("[y] save  [s] skip  [d] details  [o] open URL  [q] quit: ").strip().lower()
        if choice == "y":
            set_review_status(int(row["id"]), "saved")
            console.print(f"Saved job {row['id']}.")
            index += 1
            continue
        if choice == "s":
            set_review_status(int(row["id"]), "skipped")
            console.print(f"Skipped job {row['id']}.")
            index += 1
            continue
        if choice == "d":
            _print_job_details(get_job(int(row["id"])))
            continue
        if choice == "o":
            opened = open_url(str(row["apply_url"] or ""))
            console.print("Opened browser." if opened else "Browser open request failed.")
            continue
        if choice == "q":
            break
        console.print("Unrecognized action. Use y, s, d, o, or q.")


@app.command("batch-review")
def batch_review() -> None:
    _ensure_workspace()
    rows = list_review_jobs()
    if not rows:
        console.print("No jobs in the review queue.")
        return

    index = 0
    while index < len(rows):
        row = rows[index]
        try:
            _batch_review_job(row)
        except StopIteration:
            break
        index += 1


@app.command("save")
def save(job_id: int) -> None:
    _ensure_workspace()
    row = get_job(job_id)
    if not row:
        raise typer.BadParameter(f"Job {job_id} not found.")
    set_review_status(job_id, "saved")
    console.print(f"Saved job {job_id}.")


@app.command("skip")
def skip(job_id: int) -> None:
    _ensure_workspace()
    row = get_job(job_id)
    if not row:
        raise typer.BadParameter(f"Job {job_id} not found.")
    set_review_status(job_id, "skipped")
    console.print(f"Skipped job {job_id}.")


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
        recommendation=analysis_row["recommendation"] or "maybe",
        skip_reason=analysis_row["skip_reason"],
        matched_keywords=load_json(analysis_row["matched_keywords_json"]),
        missing_keywords=load_json(analysis_row["missing_keywords_json"]),
        red_flags=load_json(analysis_row["red_flags_json"]),
        eligibility_flags=load_json(analysis_row["eligibility_flags_json"]),
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


@app.command("edit")
def edit(
    job_id: int,
    title: str | None = typer.Option(None),
    company: str | None = typer.Option(None),
    location: str | None = typer.Option(None),
    source_url: str | None = typer.Option(None),
    remote_type: str | None = typer.Option(None),
    employment_type: str | None = typer.Option(None),
    salary_text: str | None = typer.Option(None),
    description: str | None = typer.Option(None),
    raw_text: str | None = typer.Option(None),
) -> None:
    row = get_job(job_id)
    if not row:
        raise typer.BadParameter(f"Job {job_id} not found.")
    updates: dict[str, str] = {}
    for key, value in {
        "title": title,
        "company": company,
        "location": location,
        "source_url": source_url,
        "remote_type": remote_type,
        "employment_type": employment_type,
        "salary_text": salary_text,
        "description": description,
        "raw_text": raw_text,
    }.items():
        if value is not None:
            updates[key] = value
    if not updates:
        raise typer.BadParameter("Provide at least one field to update.")
    update_job(job_id, updates)
    console.print(f"Updated job {job_id}. Re-run analyze if the posting changed.")


@app.command("due")
def due(limit: int = typer.Option(20, min=1, help="Maximum number of due follow-ups to show.")) -> None:
    rows = due_followups(limit)
    table = Table(title="Due Follow-ups")
    for column in ["ID", "Title", "Company", "Follow-up", "Status", "Rec", "Reason", "Notes"]:
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row["id"]),
            row["title"] or "",
            row["company"] or "",
            row["followup_at"] or "",
            row["status"] or "",
            row["recommendation"] or "",
            row["skip_reason"] or "",
            row["notes"] or "",
        )
    console.print(table)


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


@sources_app.command("list")
def sources_list() -> None:
    _ensure_workspace()
    sources = _load_job_sources_records(_job_sources_path())
    if not sources:
        console.print("No job sources configured.")
        return

    for source in sources:
        console.print(f"{_active_source_state(source):8} {source.provider or ''} {source.company or ''} ({source.company_slug or ''})")

    table = Table(title="Job Sources")
    for column in ["Active", "Provider", "Company", "Slug", "Board Token", "Account", "Target Roles", "Include", "Exclude"]:
        table.add_column(column)
    for source in sources:
        table.add_row(
            "yes" if source.enabled else "no",
            source.provider or "",
            source.company or "",
            source.company_slug or "",
            source.board_token or "",
            source.account or "",
            ", ".join(source.target_role_families) if source.target_role_families else "",
            ", ".join(source.include_keywords) if source.include_keywords else "",
            ", ".join(source.exclude_keywords) if source.exclude_keywords else "",
        )
    console.print(table)


@sources_app.command("add")
def sources_add(
    provider: str = typer.Option(..., "--provider", help="Source provider, such as greenhouse or lever."),
    company: str = typer.Option(..., "--company", help="Company name for display in the CLI."),
    company_slug: str = typer.Option("", "--company-slug", help="Stable jobpipe identifier for the company."),
    board_token: str = typer.Option("", "--board-token", help="Greenhouse board slug."),
    account: str = typer.Option("", "--account", help="Lever account slug."),
    target_role_family: list[str] | None = typer.Option(None, "--target-role-family", help="Repeatable target role family."),
    include_keyword: list[str] | None = typer.Option(None, "--include-keyword", help="Repeatable keyword to require."),
    exclude_keyword: list[str] | None = typer.Option(None, "--exclude-keyword", help="Repeatable keyword to exclude."),
    disabled: bool = typer.Option(False, "--disabled", help="Create the source as inactive."),
) -> None:
    _ensure_workspace()
    sources = _load_job_sources_records(_job_sources_path())
    normalized_provider = provider.strip().lower()
    if normalized_provider not in {"greenhouse", "lever"}:
        raise typer.BadParameter(f"Unsupported provider: {provider}")
    pending = _normalize_saved_job_source(
        JobSource(
            provider=normalized_provider,
            company=company.strip(),
            company_slug=company_slug.strip(),
            board_token=board_token.strip() or None,
            account=account.strip() or None,
            target_role_families=[value.strip() for value in (target_role_family or []) if value and value.strip()],
            include_keywords=[value.strip() for value in (include_keyword or []) if value and value.strip()],
            exclude_keywords=[value.strip() for value in (exclude_keyword or []) if value and value.strip()],
            enabled=not disabled,
        )
    )
    if any(existing.company_slug == pending.company_slug for existing in sources):
        raise typer.BadParameter(f"Source {pending.company_slug!r} already exists.")
    sources.append(pending)
    _write_job_sources_records(_job_sources_path(), sources)
    console.print(f"Added source {pending.company_slug} ({pending.provider}) and marked it as {'active' if pending.enabled else 'disabled'}.")


@sources_app.command("enable")
def sources_enable(source_id: str) -> None:
    _ensure_workspace()
    path = _job_sources_path()
    sources = _load_job_sources_records(path)
    index = _find_source_index(sources, source_id)
    sources[index] = sources[index].model_copy(update={"enabled": True})
    _write_job_sources_records(path, sources)
    console.print(f"Enabled source {sources[index].company_slug}.")


@sources_app.command("disable")
def sources_disable(source_id: str) -> None:
    _ensure_workspace()
    path = _job_sources_path()
    sources = _load_job_sources_records(path)
    index = _find_source_index(sources, source_id)
    sources[index] = sources[index].model_copy(update={"enabled": False})
    _write_job_sources_records(path, sources)
    console.print(f"Disabled source {sources[index].company_slug}.")


@sources_app.command("test")
def sources_test(source_id: str | None = typer.Argument(None, help="Optional company slug, company name, or provider token.")) -> None:
    _ensure_workspace()
    path = _job_sources_path()
    sources = _load_job_sources_records(path)
    if not sources:
        console.print("No job sources configured.")
        return

    if source_id is None:
        selected = [source for source in sources if source.enabled]
        if not selected:
            console.print("No enabled job sources configured.")
            return
    else:
        selected = [sources[_find_source_index(sources, source_id)]]

    failures = 0
    for source in selected:
        label = f"{source.provider}:{source.company_slug or source.company or source.board_token or source.account}"
        console.print(f"Testing {label} ({_active_source_state(source)})")
        try:
            discovered = discover_provider_jobs(source)
        except Exception as exc:  # pragma: no cover - defensive CLI guard
            failures += 1
            console.print(f"[red]Failed[/red] {label}: {exc}")
            continue
        console.print(f"  fetched {len(discovered)} job(s)")

    if failures:
        raise typer.Exit(code=1)


app.add_typer(sources_app, name="sources")


if __name__ == "__main__":
    app()
