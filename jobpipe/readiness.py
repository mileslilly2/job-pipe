"""Deterministic readiness, reusable clarification, and common application answers."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from typing import Any

from . import db
from .applicant_models import SECTIONS
from .applicant_profile import get_applicant_profile, records, state, verified_values


def context_fingerprint(job: dict[str, Any]) -> str:
    from .services import selected_profile
    payload = {k: v for k, v in job.items() if k in {"id", "title", "company", "location", "remote", "remote_type", "employment_type", "description", "raw_text", "canonical_seniority"}}
    payload["search_profile"] = selected_profile().model_dump()
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def artifact_hashes(job: dict[str, Any]) -> dict[str, str]:
    from .application_package import existing_package
    paths = existing_package(job)
    if not paths:
        return {}
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (paths.job_md, paths.fit_md, paths.resume_tailored_md, paths.cover_note_md, paths.apply_md)}


def package_record(job_id: int) -> dict[str, Any] | None:
    with closing(db.connect(mode="ro")) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='application_package_provenance'").fetchone():
            return None
        row = conn.execute("SELECT * FROM application_package_provenance WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def _facts_and_assessment(job_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from .services import get_job_eligibility, require_job, scoring_job
    # Retry if an independent local confirmation occurs between reads.
    for _ in range(3):
        profile = get_applicant_profile(list(SECTIONS))
        assessment = get_job_eligibility(job_id)["eligibility"]
        if profile["revision"] == state()["revision"]:
            return profile, assessment, scoring_job(require_job(job_id))
    from .services import ServiceError
    raise ServiceError("profile_conflict", "Applicant changed during assessment; retry.")


def get_application_readiness(job_id: int) -> dict[str, Any]:
    profile, assessment, job = _facts_and_assessment(job_id)
    keys = sorted({key for check in assessment["checks"].values() for key in check["applicant_fact_keys"]})
    values = verified_values(profile)
    missing = sorted({key for key in [*keys, "identity.name", "contact.email"] if key not in values})
    conflicts = profile["conflicting_keys"]
    status = "not_prepared"
    try:
        hashes = artifact_hashes(job)
        if hashes:
            meta = package_record(job_id)
            if meta is None:
                status = "legacy"
            elif meta["profile_revision"] != profile["revision"] or meta["context_fingerprint"] != context_fingerprint(job):
                status = "stale"
            elif json.loads(meta["artifact_hashes_json"]) != hashes:
                status = "edited"
            else:
                status = "draft" if meta["mode"] == "draft_preparation" else "prepared"
    except (ValueError, OSError):
        status = "incomplete"
    ready = profile["storage_mode"] == "canonical" and assessment["status"] == "eligible" and status == "prepared" and not missing and not conflicts
    next_action = "human_review" if ready else "review_ineligibility" if assessment["status"] == "ineligible" else "confirm_applicant_information" if missing or conflicts else "clarify_employer_requirements" if assessment["missing_employer_information"] else "prepare_application" if status == "not_prepared" else "review_existing_materials"
    return {"job_id": job_id, "profile_revision": profile["revision"], "eligibility_status": assessment["status"], "package_status": status,
            "submission_readiness": "ready_for_human_review" if ready else "blocked", "blocking_facts": sorted(set(keys) | set(missing)),
            "missing_facts": missing, "conflicting_facts": conflicts, "employer_uncertainties": assessment["missing_employer_information"], "next_action": next_action}


def _selection(job_ids: list[int] | None, query: str | None, job_limit: int) -> tuple[list[int], bool]:
    from .services import require_job, search_jobs
    if not 1 <= job_limit <= 100:
        raise ValueError("job_limit must be 1–100")
    if job_ids is not None:
        if query is not None or not 1 <= len(job_ids) <= 100 or len(set(job_ids)) != len(job_ids) or any(type(i) is not int or i <= 0 for i in job_ids):
            raise ValueError("Use up to 100 unique positive job IDs or a search query")
        for job_id in job_ids:
            require_job(job_id)
        return sorted(job_ids), False
    result = search_jobs(query=query, limit=job_limit)
    return [j["job_id"] for j in result["items"]], result["total"] > job_limit


def get_readiness_questions(job_ids: list[int] | None = None, query: str | None = None, job_limit: int = 100, limit: int = 5) -> dict[str, Any]:
    from .services import get_job_eligibility, get_fit_breakdown
    if not 1 <= limit <= 20:
        raise ValueError("Question limit must be 1–20")
    selected, truncated = _selection(job_ids, query, job_limit)
    profile = get_applicant_profile(list(SECTIONS))
    values = verified_values(profile)
    all_facts = {f["key"]: f for f in [*profile["facts"], *profile["proposals"]]}
    grouped: dict[str, dict[str, Any]] = {}
    employer: dict[str, list[str]] = {}
    for job_id in selected:
        assessment = get_job_eligibility(job_id)["eligibility"]
        if assessment["missing_employer_information"]:
            employer[str(job_id)] = assessment["missing_employer_information"]
        goal = get_fit_breakdown(job_id)
        # Existing goal modifiers determine relevance; this is not an estimated benefit.
        relevance = sum(c["amount"] for c in goal["contributions"] if c["category"] in {"primary_role_goal", "secondary_role_goal", "secondary_role_goal_cap", "goal"})
        for check_name, check in assessment["checks"].items():
            if check["status"] != "needs_review":
                continue
            needed = []
            from .applicant_profile import _record_id
            language_names = {r["id"]: r.get("name", "") for r in records({f["key"]: f for f in profile["facts"]}, "languages")}
            for key in check["applicant_fact_keys"]:
                if key.startswith("languages."):
                    parts = key.split(".")
                    matched = next((rid for rid, name in language_names.items() if _record_id(name) == parts[1]), None)
                    if matched:
                        key = f"languages.{matched}.{parts[2]}"
                if key not in values:
                    needed.append(key)
            # An unknown employer country cannot be answered by asking the applicant.
            for key in needed:
                qid = key
                if key.startswith("languages."):
                    qid = ".".join(key.split(".")[:2])
                elif key == "residence.commute_locations":
                    from .services import require_job
                    qid += ":" + str(require_job(job_id).get("location", ""))
                question = grouped.setdefault(qid, {"keys": set(), "jobs": set(), "blocking": True, "goal": 0, "check": check_name})
                question["keys"].add(key)
                question["jobs"].add(job_id)
                question["goal"] = max(question["goal"], relevance)
    rank = {"verified": 4, "self_reported": 3, "imported_unverified": 2, "inferred": 1, "unknown": 0}
    ordered = []
    for qid, group in grouped.items():
        keys = sorted(group["keys"])
        best = max((all_facts.get(k, {}).get("verification_status", "unknown") for k in keys), key=lambda s: rank[s])
        reusable = 0 if ":" in qid else 1
        text = _question_text(qid, keys)
        if qid.startswith("languages."):
            from .applicant_profile import _record_id
            rid = qid.split(".")[1]
            name = all_facts.get(f"languages.{rid}.name", {}).get("value")
            if not name:
                name = next((lang for lang in ("english", "spanish", "french", "german", "portuguese", "japanese", "italian", "chinese simplified", "chinese traditional") if _record_id(lang) == rid), rid)
            text = f"Confirm your {name} proficiency at the level quoted in the affected jobs; limited working proficiency does not establish fluency."
        item = {"question_id": qid, "fact_keys": keys, "question": text, "potentially_affected_job_ids": sorted(group["jobs"]),
                "potentially_affected_jobs": len(group["jobs"]), "blocking": group["blocking"], "evidence_strength": best}
        ordered.append(((-len(group["jobs"]), -int(group["blocking"]), -group["goal"], -rank[best], -reusable, qid), item))
    ordered.sort(key=lambda pair: pair[0])
    if state()["revision"] != profile["revision"]:
        from .services import ServiceError
        raise ServiceError("profile_conflict", "Applicant changed during question consolidation; retry.")
    return {"profile_revision": profile["revision"], "questions": [item for _, item in ordered[:limit]], "total_questions": len(ordered),
            "assessed_job_ids": selected, "selection_truncated": truncated, "employer_questions": employer}


def _question_text(qid: str, keys: list[str]) -> str:
    known = {"residence.country": "Confirm your current country of residence for employment purposes.",
             "residence.state": "Which US state is your current residence for employment purposes?",
             "qualifications.professional_experience_years": "Confirm your total professional employment experience in years. Exclude independent projects and preserve any uncertainty.",
             "education.enrolled": "Are you currently enrolled as a student? Historical attendance does not establish enrollment.",
             "education.institution_country": "Confirm the country of your educational institution.",
             "education.graduation_year": "Confirm your graduation year, distinguishing expected from completed graduation.",
             "qualifications.credentials": "Confirm your completed credentials relevant to the quoted job requirements."}
    if qid in known:
        return known[qid]
    if qid.startswith("work_authorization."):
        _, country, field = qid.split(".")
        return f"Confirm {'whether you require sponsorship' if 'sponsorship' in field else 'whether you are authorized to work'} in {country}."
    if qid.startswith("languages."):
        return f"Confirm the language and explicit professional proficiency for {qid}; do not assume fluency. See the affected jobs' quoted requirements."
    if qid.startswith("residence.commute_locations:"):
        return f"Can you attend work at {qid.split(':', 1)[1]}? Relocation is not assumed."
    if qid.startswith("qualifications.") and qid.endswith(".professional_experience_years"):
        return f"Confirm professional {qid.split('.')[1].replace('_', ' ')} experience in years, excluding projects and preserving uncertainty."
    return f"Confirm {', '.join(keys)} against the affected jobs' requirements."


def get_applicant_readiness(job_ids: list[int] | None = None, query: str | None = None, job_limit: int = 100, limit: int = 5) -> dict[str, Any]:
    questions = get_readiness_questions(job_ids, query, job_limit, limit)
    profile = get_applicant_profile(list(SECTIONS))
    values = verified_values(profile)
    assessments = [get_application_readiness(i) for i in questions["assessed_job_ids"]]
    if (questions["profile_revision"] != profile["revision"] or state()["revision"] != profile["revision"]
            or any(a["profile_revision"] != profile["revision"] for a in assessments)):
        from .services import ServiceError
        raise ServiceError("profile_conflict", "Applicant changed during readiness consolidation; retry.")
    baseline = ["identity.name", "contact.email", "residence.country"]
    if "residence.country" in values:
        baseline.append(f"work_authorization.{values['residence.country']['value']}.authorized")
    missing = sorted({k for a in assessments for k in a["missing_facts"]} | {k for k in baseline if k not in values})
    blockers = {str(a["job_id"]): a["blocking_facts"] for a in assessments if a["eligibility_status"] != "eligible"}
    return {"profile_revision": profile["revision"], "status": "needs_setup" if profile["storage_mode"] == "legacy" else "needs_review" if missing or profile["conflicting_keys"] or blockers else "complete_for_assessed_requirements",
            "verified_fact_keys": sorted(values), "missing_facts": missing, "conflicting_facts": profile["conflicting_keys"], "eligibility_blockers": blockers,
            "affected_jobs": [a["job_id"] for a in assessments if a["eligibility_status"] != "eligible" or a["missing_facts"]], "questions": questions}


def get_application_answer(job_id: int, question_type: str, country: str = "US", experience_area: str | None = None) -> dict[str, Any]:
    from .services import require_job
    require_job(job_id)
    profile = get_applicant_profile(list(SECTIONS))
    values = verified_values(profile)
    aliases = {"us_work_authorization": "work_authorization", "visa_sponsorship": "sponsorship_required", "current_residence_state": "residence_state", "bachelors_degree": "has_bachelors", "professional_experience_years": "professional_experience"}
    kind = aliases.get(question_type, question_type)
    if question_type == "us_work_authorization":
        country = "US"
    mapping = {"work_authorization": (f"work_authorization.{country}.authorized", f"Are you authorized to work in {country}?"),
               "sponsorship_required": (f"work_authorization.{country}.sponsorship_required", f"Do you currently require work sponsorship in {country}?"),
               "future_sponsorship_required": (f"work_authorization.{country}.future_sponsorship_required", f"Will you require work sponsorship in {country} in the future?"),
               "residence_state": ("residence.state", "What is your current residence state?"),
               "has_bachelors": ("education.has_bachelors", "Have you completed a bachelor's degree?"),
               "professional_experience": (f"qualifications.{experience_area}.professional_experience_years" if experience_area else "qualifications.professional_experience_years", f"How many years of professional {experience_area or 'total'} experience do you have?")}
    key, question = mapping.get(kind, ("", question_type))
    sources = [values[key]] if key in values else []
    answer = sources[0]["value"] if sources else None
    if kind == "has_bachelors" and not sources:
        degrees = records(values, "education")
        for degree in degrees:
            if degree.get("credential_type") == "bachelor" and degree.get("enrollment_status") == "completed":
                keys = [f"education.{degree['id']}.credential_type", f"education.{degree['id']}.enrollment_status"]
                sources = [values[k] for k in keys]
                answer = True
                break
    if kind == "professional_experience" and answer is not None:
        answer = {"range": answer, "precision": "range", "includes_independent_projects": False} if isinstance(answer, dict) else {"years": answer, "precision": "applicant_attested", "includes_independent_projects": False}
    conflict = key in profile["conflicting_keys"]
    return {"job_id": job_id, "question_type": question_type, "question": question,
            "status": "unsupported" if not key else "needs_review" if conflict or isinstance(answer, dict) and answer.get("precision") == "range" else "answerable" if sources else "unknown",
            "answer": None if conflict else answer, "fact_keys": [s["key"] for s in sources] or ([key] if key else []),
            "provenance": sources, "profile_revision": profile["revision"]}
