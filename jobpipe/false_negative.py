from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .ingest import DiscoveryDiagnostics, FilterDecision

AUDIT_SEED = "jobpipe-false-negative-v1"
TECHNICAL_FAMILIES = {"backend_api", "data_engineer", "software_engineer", "python_automation", "qa_automation", "ai_evaluation", "technical_operations", "frontend_engineer", "full_stack", "data_analytics", "machine_learning", "ai_research"}
CLEARLY_IRRELEVANT_FAMILIES = {"legal_counsel", "sales", "manager", "director", "clinical_admin", "customer_support", "physician", "truck_driver"}


def _stable_key(item: tuple[Any, FilterDecision], source_label: str) -> str:
    job, decision = item
    raw = f"{AUDIT_SEED}|{source_label}|{decision.reason_codes[0] if decision.reason_codes else ""}|{decision.role_family}|{job.external_id}|{job.title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def provisional_label(decision: FilterDecision, active_goal: str | None = None, title: str = "") -> str:
    family = decision.role_family
    if family in CLEARLY_IRRELEVANT_FAMILIES or decision.seniority in {"manager", "director", "executive", "principal", "staff"}:
        return "clearly_correct_rejection"
    title_text = title.lower()
    strong_title_signal = any(term in title_text for term in ("engineer", "developer", "backend", "api", "python", "data", "software", "automation", "sdet", "qa", "devops", "programmer"))
    entry_like = decision.seniority in {"entry", "entry_level", "junior", "apprenticeship", "unknown"}
    if family in TECHNICAL_FAMILIES and entry_like and strong_title_signal:
        return "plausible_false_negative"
    if family in {"help_desk", "technical_support", "it_support"} and active_goal == "get_employed_fast" and decision.seniority not in {"senior", "staff", "principal", "manager", "director", "executive"} and strong_title_signal:
        return "plausible_false_negative"
    return "needs_human_review"


def sample_rejected_jobs(diagnostics: DiscoveryDiagnostics, limit: int = 100, active_goal: str | None = None) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[tuple[Any, FilterDecision, str]]] = {}
    for report in diagnostics.source_reports:
        for job, decision in report.rejected_jobs:
            reason = decision.reason_codes[0] if decision.reason_codes else "other"
            key = (report.label, reason, decision.role_family or "unknown")
            buckets.setdefault(key, []).append((job, decision, report.label))
    for values in buckets.values():
        values.sort(key=lambda item: _stable_key((item[0], item[1]), item[2]))
    selected: list[tuple[Any, FilterDecision, str]] = []
    keys = sorted(buckets)
    index = 0
    while len(selected) < min(limit, sum(len(v) for v in buckets.values())) and keys:
        progressed = False
        for key in keys:
            values = buckets[key]
            if index < len(values):
                selected.append(values[index])
                progressed = True
                if len(selected) >= limit:
                    break
        if not progressed:
            break
        index += 1
    records: list[dict[str, Any]] = []
    for job, decision, source in selected:
        records.append({"company": job.company, "title": job.title, "location": job.location, "remote": job.remote, "source": source, "role_family": decision.role_family, "seniority": decision.seniority, "rejection_reasons": list(decision.reason_codes), "matched_positive_keywords": list(decision.matched_positive_keywords), "matched_negative_keywords": list(decision.matched_negative_keywords), "missing_required_signals": list(decision.missing_required_signals), "url": job.apply_url or job.source_url or "", "description_excerpt": " ".join(job.description.split())[:300], "provisional_label": provisional_label(decision, active_goal, job.title), "human_label": ""})
    return records


def summarize_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(record["provisional_label"] for record in records)
    fn = [record for record in records if record["provisional_label"] == "plausible_false_negative"]
    return {"sample_size": len(records), "clearly_correct_rejections": counts["clearly_correct_rejection"], "plausible_false_negatives": len(fn), "needs_human_review": counts["needs_human_review"], "provisional_false_negative_rate": len(fn) / len(records) if records else 0.0, "review_needed_rate": (len(fn) + counts["needs_human_review"]) / len(records) if records else 0.0, "false_negatives_by_reason": dict(Counter(reason for record in fn for reason in record["rejection_reasons"])), "false_negatives_by_source": dict(Counter(record["source"] for record in fn)), "false_negatives_by_role_family": dict(Counter(record["role_family"] for record in fn)), "false_negatives_by_seniority": dict(Counter(record["seniority"] for record in fn))}


def write_audit_artifacts(records: list[dict[str, Any]], directory: Path = Path("diagnostics")) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "false_negative_audit.json"
    md_path = directory / "false_negative_review.md"
    json_path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# False-negative review sample", "", "human_label is intentionally blank; use should_show, should_reject, or unsure.", "", "| # | Company | Title | Source | Role | Seniority | Provisional | Human | Reasons | URL |", "|---:|---|---|---|---|---|---|---|---|---|"]
    for index, record in enumerate(records, 1):
        reasons = ", ".join(record["rejection_reasons"]).replace("|", "\\|")
        values = [str(index), record["company"], record["title"], record["source"], record["role_family"], record["seniority"], record["provisional_label"], record["human_label"], reasons, record["url"]]
        lines.append("| " + " | ".join(value.replace("|", "\\|").replace("\n", " ") for value in values) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
