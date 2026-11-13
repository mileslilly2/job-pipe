"""Canonical local applicant knowledge; no agent SDK, network, or UI dependencies."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from . import db
from .applicant_models import DEFAULT_SECTIONS, SECTIONS, FactProposal, FactView, validate_fact
from .catalog import load_search_profile
from .utils import data_dir, db_path, load_yaml, now_iso, package_dir, root_dir

PROFILE_ID = "candidate"
LEGACY_KEYS = {
    "residence_country": "residence.country", "residence_state": "residence.state",
    "commute_locations": "residence.commute_locations",
    "citizenship_countries": "qualifications.citizenship_countries",
    "professional_experience_years": "qualifications.professional_experience_years",
    "enrolled": "education.enrolled", "institution_country": "education.institution_country",
    "graduation_year": "education.graduation_year", "credentials": "qualifications.credentials",
    "restricted_employers": "preferences.restricted_employers",
    "allowed_employment_types": "preferences.allowed_employment_types",
}


def _error(code: str, message: str) -> ValueError:
    from .services import ServiceError
    return ServiceError(code, message)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def state(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    if conn is None:
        if not db_path().is_file():
            return {"revision": 0, "active": False}
        with closing(db.connect(mode="ro")) as reader:
            return state(reader)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='applicant_profiles'").fetchone():
        return {"revision": 0, "active": False}
    row = conn.execute("SELECT revision, active FROM applicant_profiles WHERE id=?", (PROFILE_ID,)).fetchone()
    return dict(row) if row else {"revision": 0, "active": False}


def _begin(conn: sqlite3.Connection, expected_revision: int) -> None:
    conn.execute("BEGIN IMMEDIATE")
    if state(conn)["revision"] != expected_revision:
        raise _error("profile_conflict", "Applicant profile changed; retrieve it and review this batch again.")
    conn.execute("INSERT OR IGNORE INTO applicant_profiles(id, revision, active, updated_at) VALUES (?,0,0,?)", (PROFILE_ID, now_iso()))


def _bump(conn: sqlite3.Connection, *, activate: bool = False) -> int:
    conn.execute("UPDATE applicant_profiles SET revision=revision+1, active=MAX(active,?), updated_at=? WHERE id=?", (int(activate), now_iso(), PROFILE_ID))
    return int(state(conn)["revision"])


def _view(row: Any) -> dict[str, Any]:
    return FactView(key=row["fact_key"], value=json.loads(row["value_json"]),
                    verification_status=row["verification_status"], source_type=row["source_type"],
                    source_reference=row["source_reference"], evidence=row["evidence"],
                    confirmed_at=row["confirmed_at"], modified_at=row["modified_at"],
                    revision_id=row["id"], review_state=row["review_state"]).model_dump()


def current_facts(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {r["fact_key"]: _view(r) for r in conn.execute(
        "SELECT r.* FROM applicant_facts f JOIN applicant_fact_revisions r ON r.id=f.current_revision_id WHERE f.profile_id=? ORDER BY f.fact_key", (PROFILE_ID,))}


def _append(conn: sqlite3.Connection, item: dict[str, Any], *, review_state: str, previous: int | None = None) -> int:
    validate_fact(item["key"], item["value"])
    cur = conn.execute("""INSERT INTO applicant_fact_revisions
        (profile_id,fact_key,value_json,verification_status,source_type,source_reference,evidence,confirmed_at,modified_at,previous_revision_id,review_state)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (PROFILE_ID, item["key"], _json(item["value"]), item["verification_status"], item["source_type"],
        item.get("source_reference"), item.get("evidence", ""), item.get("confirmed_at"), now_iso(), previous, review_state))
    rid = int(cur.lastrowid)
    if review_state == "accepted":
        conn.execute("INSERT INTO applicant_facts VALUES (?,?,?) ON CONFLICT(profile_id,fact_key) DO UPDATE SET current_revision_id=excluded.current_revision_id", (PROFILE_ID, item["key"], rid))
    return rid


def _source_item(key: str, value: Any, source: str, *, verified: bool = False, evidence: str = "", source_type: str | None = None) -> dict[str, Any]:
    validate_fact(key, value)
    return dict(key=key, value=value, verification_status="unknown" if value is None else "verified" if verified else "imported_unverified",
                source_type=source_type or ("legacy_attestation" if verified else "profile_import"), source_reference=source,
                evidence=evidence, confirmed_at=None, modified_at="", revision_id=None, review_state="accepted", conflicts_with_current=False)


def _record_id(value: str) -> str:
    return hashlib.sha256(value.strip().casefold().encode()).hexdigest()[:16]


def legacy_items(raw: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """Map only explicit fields. No inference of authorization, dates, or fluency."""
    items = []
    eligibility = raw.get("eligibility", {})
    for old, new in LEGACY_KEYS.items():
        fact = eligibility.get(old)
        if isinstance(fact, dict) and "value" in fact:
            items.append(_source_item(new, fact["value"], source, verified=fact.get("verified") is True, evidence=str(fact.get("evidence", ""))))
    for auth in eligibility.get("work_authorizations", []):
        for field in ("authorized", "sponsorship_required", "future_sponsorship_required", "restrictions"):
            if field in auth:
                items.append(_source_item(f"work_authorization.{auth['country']}.{field}", auth[field], source,
                                          verified=auth.get("verified") is True, evidence=str(auth.get("evidence", ""))))
    for area, fact in eligibility.get("experience_by_area", {}).items():
        key = area.strip().lower().replace(" ", "_")
        items.append(_source_item(f"qualifications.{key}.professional_experience_years", fact["value"], source,
                                  verified=fact.get("verified") is True, evidence=fact.get("evidence", "")))
    # Legacy language lists never asserted proficiency, except the explicit native list.
    for field, proficiency in (("languages", None), ("native_languages", "native")):
        fact = eligibility.get(field, {})
        for name in fact.get("value", []):
            rid = _record_id(name)
            items.append(_source_item(f"languages.{rid}.name", name, source, verified=fact.get("verified") is True, evidence=fact.get("evidence", "")))
            if proficiency:
                items.append(_source_item(f"languages.{rid}.proficiency", proficiency, source, verified=fact.get("verified") is True, evidence=fact.get("evidence", "")))
    profile = raw.get("profile", {})
    for old, key in {"name": "identity.name", "summary": "identity.summary", "email": "contact.email", "phone": "contact.phone", "location": "residence.location",
                     "github_url": "contact.github_url", "linkedin_url": "contact.linkedin_url", "portfolio_url": "contact.portfolio_url"}.items():
        if profile.get(old):
            items.append(_source_item(key, profile[old], source))
    for category, values in raw.get("skills", {}).items():
        for name in values:
            rid = _record_id(name)
            items.extend([_source_item(f"skills.{rid}.name", name, source), _source_item(f"skills.{rid}.category", category, source)])
    for section, records in (("projects", raw.get("projects", [])), ("employment", raw.get("experience", [])), ("education", raw.get("education", []))):
        for index, record in enumerate(records):
            identity = str(record.get("name") or index)
            if section == "employment":
                identity = "|".join(str(record.get(field, "")) for field in ("organization", "title", "start_date", "dates"))
            elif section == "education":
                identity = "|".join(str(record.get(field, "")) for field in ("school", "degree", "start_date", "dates"))
            rid = str(record.get("id") or _record_id(identity))
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", rid):
                raise ValueError("Imported record IDs must be bounded identifiers")
            mapping = {"projects": {"name": "name", "description": "description", "bullets": "responsibilities", "technologies": "technologies"},
                       "employment": {"organization": "employer", "title": "title", "bullets": "responsibilities", "start_date": "start_date", "end_date": "end_date", "employment_type": "employment_type", "current": "current"},
                       "education": {"school": "institution", "degree": "credential", "field": "field", "start_date": "start_date", "end_date": "end_date"}}[section]
            for old, field in mapping.items():
                if old in record and record[old] != "":
                    items.append(_source_item(f"{section}.{rid}.{field}", record[old], source, evidence=str(record.get("dates", ""))))
    for field in ("dealbreakers", "avoid_claiming"):
        if field in raw.get("constraints", {}):
            items.append(_source_item(f"preferences.{field}", raw["constraints"][field], source))
    # Equivalent facts can have several references, but only one current value.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        identity = (item["key"], _json(item["value"]))
        if identity not in unique or item["verification_status"] == "verified":
            unique[identity] = item
    return list(unique.values())


def import_preview() -> dict[str, Any]:
    """Read registered workspace inputs only; previews do not create database state."""
    items: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    warnings: list[str] = []
    candidate_path = data_dir() / "profiles" / "candidate.yaml"
    profile_path = root_dir() / "profile.md"
    for path in (candidate_path, profile_path):
        if not path.exists():
            continue
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root_dir().parent) or not path.is_file():
            raise ValueError("Profile import requires regular local files")
        text = path.read_text(encoding="utf-8")
        if len(text.encode()) > 1_000_000:
            raise ValueError("Profile import exceeds the 1 MB text limit")
        relative = str(path.relative_to(root_dir()))
        digest = hashlib.sha256(text.encode()).hexdigest()
        sources[relative] = digest
        ref = f"{relative}#sha256={digest}"
        if path == candidate_path:
            raw = load_yaml(path)
            if not isinstance(raw, dict):
                raise ValueError("Candidate profile must be a YAML mapping")
            mapped = legacy_items(raw, ref)
            stock = load_yaml(package_dir() / "data/profiles/candidate.yaml")
            stock_items = {(i["key"], _json(i["value"])) for i in legacy_items(stock, "template")}
            for item in mapped:
                if item["verification_status"] != "verified" and (item["key"], _json(item["value"])) in stock_items:
                    warnings.append(f"Skipped packaged example: {item['key']}")
                else:
                    items.append(item)
        else:
            from .application_package import _parse_base_profile
            profile = _parse_base_profile(text)
            stock = _parse_base_profile((package_dir() / "profile.md").read_text())
            raw = {"profile": {"summary": profile.summary}, "skills": {"technical": profile.skills},
                   "projects": [{"name": p.title, "bullets": p.bullets} for p in profile.projects]}
            stock_raw = {"profile": {"summary": stock.summary}, "skills": {"technical": stock.skills},
                         "projects": [{"name": p.title, "bullets": p.bullets} for p in stock.projects]}
            stock_items = {(i["key"], _json(i["value"])) for i in legacy_items(stock_raw, "template")}
            for item in legacy_items(raw, ref):
                if (item["key"], _json(item["value"])) not in stock_items:
                    items.append(item)
            # Education prose is preserved as an unverified credential, never parsed into a degree/enrollment assertion.
            for line in profile.education:
                if line not in stock.education:
                    items.append(_source_item(f"education.{_record_id(line)}.credential", line, ref))
    unique = {(i["key"], _json(i["value"])): i for i in items}
    return {"revision": state()["revision"], "sources": sources, "facts": list(unique.values()), "warnings": warnings}


def accept_import(preview: dict[str, Any]) -> dict[str, Any]:
    if import_preview() != preview:
        raise _error("profile_conflict", "Import sources or applicant state changed; preview again.")
    with closing(db.connect(mode="rw")) as conn, conn:
        _begin(conn, preview["revision"])
        current = current_facts(conn)
        changed = not state(conn)["active"]
        for item in preview["facts"]:
            old = current.get(item["key"])
            if old and old["value"] == item["value"]:
                continue
            pending = old is not None
            if pending and conn.execute("SELECT 1 FROM applicant_fact_revisions WHERE profile_id=? AND fact_key=? AND value_json=? AND review_state='pending'", (PROFILE_ID, item["key"], _json(item["value"]))).fetchone():
                continue
            # Reimports cannot replace accepted values, including verified facts.
            _append(conn, item, review_state="pending" if pending else "accepted", previous=old["revision_id"] if old else None)
            if not pending:
                current[item["key"]] = item
            changed = True
        revision = _bump(conn, activate=True) if changed else state(conn)["revision"]
    return {"revision": revision, "changed": changed}


def get_applicant_profile(sections: list[str] | None = None) -> dict[str, Any]:
    sections = list(dict.fromkeys(sections or DEFAULT_SECTIONS))
    if not set(sections) <= set(SECTIONS):
        raise ValueError("Unknown applicant section")
    profile_state = state()
    pending = []
    if profile_state["active"]:
        with closing(db.connect(mode="ro")) as conn:
            conn.execute("BEGIN")
            profile_state = state(conn)
            facts = current_facts(conn)
            pending = [_view(r) for r in conn.execute("SELECT * FROM applicant_fact_revisions WHERE profile_id=? AND review_state='pending' ORDER BY fact_key,id", (PROFILE_ID,))]
    else:
        from .eligibility import _load_legacy_applicant
        _, raw = _load_legacy_applicant()
        facts = {i["key"]: i for i in legacy_items(raw, "data/profiles/candidate.yaml")}
    for proposal in pending:
        old = facts.get(proposal["key"])
        proposal["conflicts_with_current"] = bool(old and old["value"] != proposal["value"])
    conflicts = sorted({p["key"] for p in pending if p["conflicts_with_current"]} | _semantic_conflicts(facts))
    wanted = lambda key: key.split(".")[0] in sections
    preferences = {}
    if "preferences" in sections:
        search = load_search_profile()
        preferences = {"configuration_origin": "search_profile", "search_profile": search.name, "active_goal": search.active_goal,
                       "preferred_job_families": search.preferred, "acceptable_job_families": search.acceptable, "excluded_job_families": search.avoid,
                       "preferred_work_styles": search.preferred_work_styles, "preferred_locations": search.preferred_locations,
                       "allowed_locations": search.allowed_locations, "excluded_locations": search.excluded_locations}
    required = [k for k in ("identity.name", "contact.email", "residence.country") if wanted(k)]
    return {"profile_id": PROFILE_ID, "revision": profile_state["revision"], "storage_mode": "canonical" if profile_state["active"] else "legacy",
            "sections": sections, "facts": [f for k, f in sorted(facts.items()) if wanted(k)], "proposals": [p for p in pending if wanted(p["key"])],
            "conflicting_keys": [k for k in conflicts if wanted(k)], "missing_fields": [k for k in required if k not in facts or facts[k]["value"] is None],
            "preferences": preferences}


def _semantic_conflicts(facts: dict[str, dict[str, Any]]) -> set[str]:
    """Small consistency checks for overlapping supported representations."""
    verified = {k: f for k, f in facts.items() if f["verification_status"] == "verified" and f["value"] is not None}
    result: set[str] = set()
    for record in records(verified, "education"):
        prefix = "education." + record["id"]
        if verified.get("education.enrolled", {}).get("value") is False and record.get("enrollment_status") == "enrolled":
            result.update(["education.enrolled", prefix + ".enrollment_status"])
        if verified.get("education.has_bachelors", {}).get("value") is False and record.get("credential_type") == "bachelor" and record.get("enrollment_status") == "completed":
            result.update(["education.has_bachelors", prefix + ".credential_type", prefix + ".enrollment_status"])
    total = verified.get("qualifications.professional_experience_years", {}).get("value")
    for record in records(verified, "qualifications"):
        area = record.get("professional_experience_years", 0)
        if type(total) in {float, int} and type(area) in {float, int} and area > total:
            result.update(["qualifications.professional_experience_years", f"qualifications.{record['id']}.professional_experience_years"])
    for record in records(verified, "employment"):
        prefix = f"employment.{record['id']}"
        if record.get("current") is True and record.get("end_date"):
            result.update([prefix + ".current", prefix + ".end_date"])
        if record.get("start_date") and record.get("end_date") and len(record["start_date"]) == len(record["end_date"]) and record["start_date"] > record["end_date"]:
            result.update([prefix + ".start_date", prefix + ".end_date"])
    return result


def propose_applicant_facts(facts: list[dict[str, Any]], expected_revision: int) -> dict[str, Any]:
    proposals = [FactProposal.model_validate(f) for f in facts]
    if not 1 <= len(proposals) <= 50 or len({p.key for p in proposals}) != len(proposals):
        raise ValueError("Use 1–50 unique fact keys per proposal batch")
    if not state()["active"]:
        raise _error("profile_setup_required", "Preview and accept a local applicant import before proposing facts.")
    with closing(db.connect(mode="rw")) as conn, conn:
        _begin(conn, expected_revision)
        current = current_facts(conn)
        ids, changed = [], False
        for p in proposals:
            existing = conn.execute("SELECT id FROM applicant_fact_revisions WHERE profile_id=? AND fact_key=? AND value_json=? AND review_state='pending' ORDER BY id LIMIT 1", (PROFILE_ID, p.key, _json(p.value))).fetchone()
            if existing:
                ids.append(existing["id"])
                continue
            old = current.get(p.key)
            if old and old["value"] == p.value and old["verification_status"] == "verified":
                ids.append(old["revision_id"])
                continue
            item = _source_item(p.key, p.value, p.source_reference, source_type="agent_proposal", evidence=p.explanation)
            item["verification_status"] = "unknown" if p.value is None else "inferred"
            ids.append(_append(conn, item, review_state="pending", previous=old["revision_id"] if old else None))
            changed = True
        revision = _bump(conn) if changed else state(conn)["revision"]
        pending_ids = {row["id"] for row in conn.execute("SELECT id FROM applicant_fact_revisions WHERE profile_id=? AND review_state='pending'", (PROFILE_ID,))}
    return {"revision": revision, "proposal_ids": ids, "review_state": "pending" if any(rid in pending_ids for rid in ids) else "unchanged", "changed": changed}


def review_facts(confirm: list[int], reject: list[int], expected_revision: int) -> dict[str, Any]:
    """Trusted local adapter only. Not exported through the MCP tool registry."""
    if not confirm and not reject or set(confirm) & set(reject) or len(set(confirm + reject)) != len(confirm + reject):
        raise ValueError("Select distinct revisions to confirm or reject")
    with closing(db.connect(mode="rw")) as conn, conn:
        _begin(conn, expected_revision)
        current = current_facts(conn)
        selected_keys = set()
        changed = False
        for rid in confirm + reject:
            row = conn.execute("SELECT * FROM applicant_fact_revisions WHERE id=? AND profile_id=?", (rid, PROFILE_ID)).fetchone()
            if row is None or row["review_state"] not in {"pending", "accepted"} or (row["review_state"] == "accepted" and current.get(row["fact_key"], {}).get("revision_id") != rid):
                raise _error("profile_conflict", "A selected fact is no longer awaiting review.")
            if rid in reject:
                if row["review_state"] != "pending":
                    raise ValueError("Only pending proposals can be rejected; correct a current fact instead")
                conn.execute("UPDATE applicant_fact_revisions SET review_state='rejected',reviewed_at=?,reviewer='local_applicant' WHERE id=?", (now_iso(), rid))
                changed = True
                continue
            item = _view(row)
            if item["key"] in selected_keys or item["value"] is None:
                raise ValueError("Confirm one nonmissing value per fact key")
            selected_keys.add(item["key"])
            old = current.get(item["key"])
            if row["review_state"] == "pending" and row["previous_revision_id"] != (old["revision_id"] if old else None):
                raise _error("profile_conflict", "A proposal's original fact changed; propose the correction again.")
            if old and old["verification_status"] == "verified" and old["value"] == item["value"]:
                if row["review_state"] == "pending":
                    conn.execute("UPDATE applicant_fact_revisions SET review_state='confirmed',reviewed_at=?,reviewer='local_applicant' WHERE id=?", (now_iso(), rid))
                    changed = True
                continue
            item.update(verification_status="verified", source_type="applicant_attestation", confirmed_at=now_iso())
            _append(conn, item, review_state="accepted", previous=rid)
            changed = True
            if row["review_state"] == "pending":
                conn.execute("UPDATE applicant_fact_revisions SET review_state='confirmed',reviewed_at=?,reviewer='local_applicant' WHERE id=?", (now_iso(), rid))
        revision = _bump(conn) if changed else state(conn)["revision"]
    return {"revision": revision, "confirmed": confirm, "rejected": reject}


def correct_fact(key: str, value: Any, expected_revision: int) -> dict[str, Any]:
    validate_fact(key, value)
    with closing(db.connect(mode="rw")) as conn, conn:
        _begin(conn, expected_revision)
        if not state(conn)["active"]:
            raise _error("profile_setup_required", "Activate the structured profile first")
        old = current_facts(conn).get(key)
        pending = conn.execute("SELECT 1 FROM applicant_fact_revisions WHERE profile_id=? AND fact_key=? AND review_state='pending'", (PROFILE_ID, key)).fetchone()
        if old and old["value"] == value and old["verification_status"] == ("unknown" if value is None else "verified") and pending is None:
            return {"revision": state(conn)["revision"], "changed": False}
        item = _source_item(key, value, None, verified=value is not None, source_type="applicant_attestation", evidence="Direct local applicant correction")
        item["confirmed_at"] = now_iso() if value is not None else None
        _append(conn, item, review_state="accepted", previous=old["revision_id"] if old else None)
        # A direct correction resolves outstanding alternatives explicitly, retaining them as rejected history.
        conn.execute("UPDATE applicant_fact_revisions SET review_state='rejected',reviewed_at=?,reviewer='local_applicant_correction' WHERE profile_id=? AND fact_key=? AND review_state='pending'", (now_iso(), PROFILE_ID, key))
        revision = _bump(conn)
    return {"revision": revision, "changed": True}


def verified_values(profile: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    profile = profile or get_applicant_profile(list(SECTIONS))
    return {f["key"]: f for f in profile["facts"] if f["verification_status"] == "verified" and f["value"] is not None and f["key"] not in profile["conflicting_keys"]}


def records(values: dict[str, dict[str, Any]], section: str) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, fact in sorted(values.items()):
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == section:
            result.setdefault(parts[1], {"id": parts[1]})[parts[2]] = fact["value"]
    return list(result.values())


def eligibility_snapshot() -> tuple[Any, dict[str, Any]]:
    from .eligibility import ApplicantFacts
    profile = get_applicant_profile(list(SECTIONS))
    values = verified_values(profile)
    raw: dict[str, Any] = {}
    for old, key in LEGACY_KEYS.items():
        if key in values:
            if key.endswith("professional_experience_years") and isinstance(values[key]["value"], dict):
                continue
            raw[old] = {"value": values[key]["value"], "verified": True, "evidence": values[key]["evidence"] or "Verified local applicant fact"}
    authorizations = []
    for record in records(values, "work_authorization"):
        if "authorized" not in record:
            continue
        country = record["id"]
        authorizations.append({"country": country, "authorized": record["authorized"], "sponsorship_required": record.get("sponsorship_required"),
                               "verified": True, "evidence": values[f"work_authorization.{country}.authorized"]["evidence"] or "Verified local applicant authorization"})
    raw["work_authorizations"] = authorizations
    raw["experience_by_area"] = {r["id"].replace("_", " "): {"value": r["professional_experience_years"], "verified": True, "evidence": "Verified applicant professional experience summary"}
                                 for r in records(values, "qualifications") if type(r.get("professional_experience_years")) in {float, int}}
    language_records = records(values, "languages")
    raw["language_proficiencies"] = {r["name"].casefold(): {"value": r["proficiency"], "verified": True, "evidence": "Verified explicit language proficiency"}
                                    for r in language_records if r.get("name") and r.get("proficiency") and r["proficiency"] != "unknown"}
    for field, levels in (("languages", {"fluent", "native"}), ("native_languages", {"native"})):
        names = [r["name"] for r in language_records if r.get("name") and r.get("proficiency") in levels]
        if names:
            raw[field] = {"value": names, "verified": True, "evidence": "Verified explicit language proficiency"}
    education = records(values, "education")
    enrolled_records = [r for r in education if r.get("enrollment_status") == "enrolled"]
    if "enrolled" not in raw and enrolled_records:
        raw["enrolled"] = {"value": True, "verified": True, "evidence": "Verified explicit current enrollment status"}
    if len(enrolled_records) == 1:
        record = enrolled_records[0]
        if "institution_country" not in raw and record.get("country"):
            raw["institution_country"] = {"value": record["country"], "verified": True, "evidence": "Verified current institution country"}
        if "graduation_year" not in raw and record.get("graduation_date"):
            raw["graduation_year"] = {"value": int(record["graduation_date"][:4]), "verified": True, "evidence": "Verified expected graduation date for current enrollment"}
    credentials = [r["credential"] for r in education if r.get("credential") and r.get("enrollment_status") == "completed"]
    if credentials:
        raw["credentials"] = {"value": list(dict.fromkeys([*(raw.get("credentials", {}).get("value", [])), *credentials])), "verified": True, "evidence": "Verified completed education credentials"}
    constraints = {field: values[f"preferences.{field}"]["value"] for field in ("dealbreakers", "avoid_claiming") if f"preferences.{field}" in values}
    return ApplicantFacts.model_validate(raw), {"constraints": constraints, "profile_revision": profile["revision"], "conflicting_keys": profile["conflicting_keys"],
                                              "authorization_restrictions": {r["id"]: r["restrictions"] for r in records(values, "work_authorization") if r.get("restrictions")},
                                              "experience_ranges": [key for key, fact in values.items() if key.endswith("professional_experience_years") and isinstance(fact["value"], dict)],
                                              "language_record_ids": {r["name"].casefold(): r["id"] for r in records({f["key"]: f for f in profile["facts"]}, "languages") if r.get("name")}}


def candidate_projection() -> Any:
    """Compatibility adapter for existing template/tailoring code, never a second store."""
    from .models import CandidateProfile
    values = verified_values()
    get = lambda key, default="": values.get(key, {}).get("value", default)
    projects = [{"name": r["name"], "type": "project", "description": r.get("description", ""), "bullets": r.get("responsibilities", []), "technologies": r.get("technologies", [])}
                for r in records(values, "projects") if r.get("name")]
    experience = [{"title": r["title"], "organization": r["employer"], "dates": " – ".join(filter(None, [r.get("start_date"), "present" if r.get("current") else r.get("end_date")])), "bullets": r.get("responsibilities", [])}
                  for r in records(values, "employment") if r.get("title") and r.get("employer")]
    education = [{"school": r.get("institution", ""), "degree": r["credential"], "dates": r.get("graduation_date", "")}
                 for r in records(values, "education") if r.get("credential")]
    return CandidateProfile(profile={"name": get("identity.name"), "email": get("contact.email"), "location": get("residence.location"), "summary": get("identity.summary")},
                            skills={"technical": [r["name"] for r in records(values, "skills") if r.get("name")]}, projects=projects, experience=experience, education=education,
                            constraints={"avoid_claiming": get("preferences.avoid_claiming", [])})
