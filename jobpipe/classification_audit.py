from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .classification import classify_job as classify_canonical_job, normalize_role_family
from .facts import classify_role_family as classify_filter_role, classify_seniority as classify_filter_seniority


def load_review_sample(path: Path) -> list[dict[str, Any]]:
    """Parse the deterministic false-negative review table without touching the DB."""
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "| # |" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 10 or not cells[0].isdigit():
            continue
        records.append({
            "company": cells[1], "title": cells[2], "source": cells[3],
            "recorded_role": cells[4], "recorded_seniority": cells[5],
            "provisional_label": cells[6], "rejection_reasons": [x.strip() for x in cells[8].split(",") if x.strip()],
            "url": cells[9],
        })
    return records


def load_gold(path: Path) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("titles", data) if isinstance(data, dict) else data
    return {str(item["title"]): item for item in entries if isinstance(item, dict) and item.get("title")}


def _canonical_seniority(value: str) -> str:
    return {"entry": "entry_level", "internship": "intern", "apprenticeship": "apprentice"}.get(value, value or "unknown")


def audit_records(records: list[dict[str, Any]], gold: dict[str, dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    role_correct = role_labeled = seniority_correct = seniority_labeled = 0
    role_confusion: Counter[tuple[str, str]] = Counter()
    seniority_confusion: Counter[tuple[str, str]] = Counter()
    for record in records:
        job = {"title": record["title"], "description": record.get("description", "")}
        canonical = classify_canonical_job(job)
        filter_role, filter_matches, filter_reason = classify_filter_role(job)
        filter_seniority, seniority_notes = classify_filter_seniority(job)
        expected = gold.get(record["title"], {})
        erole, esen = expected.get("expected_role", "unsure"), expected.get("expected_seniority", "unsure")
        predicted_role = normalize_role_family(filter_role)
        predicted_seniority = canonical.seniority
        role_ok = erole not in {"unsure", "not_applicable", None} and predicted_role == erole
        seniority_ok = esen not in {"unsure", "not_applicable", None} and predicted_seniority == esen
        if erole not in {"unsure", "not_applicable", None}:
            role_labeled += 1; role_correct += int(role_ok); role_confusion[(predicted_role, str(erole))] += int(not role_ok)
        if esen not in {"unsure", "not_applicable", None}:
            seniority_labeled += 1; seniority_correct += int(seniority_ok); seniority_confusion[(predicted_seniority, str(esen))] += int(not seniority_ok)
        results.append({**record, "predicted_role": predicted_role, "predicted_secondary_roles": list(canonical.secondary_role_families), "predicted_seniority": predicted_seniority, "canonical_role": canonical.primary_role_family, "role_evidence": list(canonical.role_evidence), "seniority_evidence": list(canonical.seniority_evidence), "filter_role_evidence": list(filter_matches), "filter_role_reason": filter_reason or "", "filter_seniority_evidence": seniority_notes, "expected_role": erole, "expected_seniority": esen, "role_correct": role_ok if erole not in {"unsure", "not_applicable", None} else None, "seniority_correct": seniority_ok if esen not in {"unsure", "not_applicable", None} else None})
    return {"sample_size": len(results), "labeled_roles": role_labeled, "correct_roles": role_correct, "role_accuracy": role_correct / role_labeled if role_labeled else 0.0, "labeled_seniority": seniority_labeled, "correct_seniority": seniority_correct, "seniority_accuracy": seniority_correct / seniority_labeled if seniority_labeled else 0.0, "role_confusion": {f"{a} -> {b}": n for (a, b), n in sorted(role_confusion.items())}, "seniority_confusion": {f"{a} -> {b}": n for (a, b), n in sorted(seniority_confusion.items())}, "results": results}


def write_audit(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

