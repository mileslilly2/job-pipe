"""Direct local applicant review. No confirmation operation is exposed over MCP."""
from __future__ import annotations

from contextlib import closing
import json

import typer
from rich.console import Console

from . import applicant_profile as profile, db
from .applicant_models import SECTIONS, validate_fact

app = typer.Typer(help="Inspect, import, and confirm local applicant information.")
console = Console()


def _require_database() -> None:
    from .services import validate_database
    try:
        validate_database()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _display(facts: list[dict]) -> None:
    for fact in facts:
        console.print(f"[{fact.get('revision_id') or '-'}] {fact['key']} = {json.dumps(fact['value'], ensure_ascii=False)}", markup=False)
        console.print(f"  {fact['verification_status']} | {fact['source_type']} | {fact.get('source_reference') or 'local'}", markup=False)
        if fact.get("evidence"):
            console.print("  Evidence: " + fact["evidence"], markup=False)


@app.command("show")
def show(section: list[str] | None = typer.Option(None, "--section")) -> None:
    """Display applicant facts and proposals, including their provenance."""
    try:
        result = profile.get_applicant_profile(section or list(SECTIONS))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Applicant {result['profile_id']} | revision {result['revision']} | {result['storage_mode']}")
    _display(result["facts"])
    console.print("Pending proposals:")
    _display(result["proposals"])
    if result["conflicting_keys"]:
        console.print("Conflicts: " + ", ".join(result["conflicting_keys"]), markup=False)


@app.command("import")
def import_profile(accept: bool = typer.Option(False, "--accept", help="Display and interactively accept the import; does not verify ordinary imported claims.")) -> None:
    """Preview existing candidate YAML and profile.md without changing either file."""
    try:
        preview = profile.import_preview()
        console.print(f"Import preview | current revision {preview['revision']}")
        _display(preview["facts"])
        for warning in preview["warnings"]:
            console.print(warning, markup=False)
        console.print("Existing verified facts retain their evidence. Other imported claims remain unverified.")
        if accept:
            _require_database()
            if typer.confirm("Accept these imported values and activate the canonical applicant profile?", default=False):
                result = profile.accept_import(preview)
                console.print(f"Canonical applicant profile revision {result['revision']}.")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _ids(text: str) -> list[int]:
    try:
        return [int(v.strip()) for v in text.split(",") if v.strip()]
    except ValueError as exc:
        raise typer.BadParameter("Use comma-separated revision IDs") from exc


@app.command("review")
def review(confirm: str | None = typer.Option(None, "--confirm"), reject: str | None = typer.Option(None, "--reject")) -> None:
    """Review exact proposed/imported values and confirm or reject one batch."""
    _require_database()
    snapshot = profile.get_applicant_profile(list(SECTIONS))
    if snapshot["storage_mode"] != "canonical":
        raise typer.BadParameter("Run applicant import --accept first")
    candidates = [f for f in snapshot["facts"] if f["verification_status"] != "verified" and f["value"] is not None] + snapshot["proposals"]
    if not candidates:
        console.print("No unverified facts or proposals to review.")
        return
    _display(candidates)
    confirms = _ids(confirm if confirm is not None else typer.prompt("Revision IDs to confirm (comma-separated; blank skips)", default=""))
    rejects = _ids(reject if reject is not None else typer.prompt("Proposal IDs to reject (comma-separated; blank skips)", default=""))
    if not confirms and not rejects:
        console.print("No changes.")
        return
    by_id = {f["revision_id"]: f for f in candidates}
    if any(rid not in by_id for rid in confirms + rejects):
        raise typer.BadParameter("Select only displayed review IDs")
    console.print("Confirm these exact values as applicant attestations:")
    _display([by_id[rid] for rid in confirms])
    console.print("Reject these proposals:")
    _display([by_id[rid] for rid in rejects])
    if typer.confirm("Apply this review batch?", default=False):
        try:
            result = profile.review_facts(confirms, rejects, snapshot["revision"])
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        console.print(f"Applicant profile revision {result['revision']}. Affected eligibility will be reassessed on the next query.")


@app.command("edit")
def edit(key: str, value: str | None = typer.Option(None, "--value", help="New value as JSON; final local confirmation is still required.")) -> None:
    """Correct a typed fact while preserving its previous revisions."""
    _require_database()
    snapshot = profile.get_applicant_profile(list(SECTIONS))
    old = next((f for f in snapshot["facts"] if f["key"] == key), None)
    if old:
        console.print("Current value:")
        _display([old])
    text = value if value is not None else typer.prompt("New value as JSON (null means unknown)")
    try:
        parsed = json.loads(text)
        validate_fact(key, parsed)
        console.print(f"Confirm {key} = {json.dumps(parsed, ensure_ascii=False)}", markup=False)
        console.print("This correction will resolve and retain pending alternatives as rejected history.")
        if typer.confirm("Store this applicant attestation?", default=False):
            result = profile.correct_fact(key, parsed, snapshot["revision"])
            console.print(f"Applicant profile revision {result['revision']}.")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("history")
def history(key: str) -> None:
    """Inspect the revision trail for one applicant fact."""
    _require_database()
    with closing(db.connect(mode="ro")) as conn:
        _display([profile._view(row) for row in conn.execute("SELECT * FROM applicant_fact_revisions WHERE profile_id=? AND fact_key=? ORDER BY id", (profile.PROFILE_ID, key))])


@app.command("readiness")
def readiness(job_id: int | None = typer.Option(None, "--job-id")) -> None:
    """Display current readiness without changing application statuses."""
    _require_database()
    from .readiness import get_applicant_readiness, get_application_readiness
    console.print_json(data=get_application_readiness(job_id) if job_id else get_applicant_readiness())


@app.command("questions")
def questions(job_id: list[int] | None = typer.Option(None, "--job-id"), limit: int = typer.Option(5, min=1, max=20)) -> None:
    """Show deduplicated applicant questions and separate employer uncertainties."""
    _require_database()
    from .readiness import get_readiness_questions
    console.print_json(data=get_readiness_questions(job_ids=job_id, limit=limit))
