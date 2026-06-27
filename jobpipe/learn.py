from __future__ import annotations

import json
from typing import Any

import yaml

from .db import list_feedback_with_analysis
from .utils import data_dir, ensure_dir

WEIGHTS_FILE = data_dir() / "learned_weights.yaml"
MAX_ADJUSTMENT = 20


def load_learned_weights() -> dict[str, Any]:
    if not WEIGHTS_FILE.exists():
        return {"role_families": {}, "keywords": {}}
    try:
        content = WEIGHTS_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if not data or not isinstance(data, dict):
            return {"role_families": {}, "keywords": {}}
        return {
            "role_families": data.get("role_families", {}),
            "keywords": data.get("keywords", {}),
        }
    except Exception:
        return {"role_families": {}, "keywords": {}}


def save_learned_weights(weights: dict[str, Any]) -> None:
    ensure_dir(WEIGHTS_FILE.parent)
    with WEIGHTS_FILE.open("w", encoding="utf-8") as f:
        yaml.safe_dump(weights, f, sort_keys=False)


def compute_learned_weights() -> dict[str, Any]:
    feedback_rows = list_feedback_with_analysis()
    role_scores: dict[str, int] = {}
    keyword_scores: dict[str, int] = {}
    seen_votes: set[tuple[int, str]] = set()

    for row in feedback_rows:
        job_id = row["job_id"]
        action = row["action"]
        
        # Deduplicate: only count one 'saved' vote, one 'packaged' vote, etc. per job
        if (job_id, action) in seen_votes:
            continue
        seen_votes.add((job_id, action))
        
        role_type = row["role_type"]
        matched_keywords_json = row["matched_keywords_json"]

        # Scoring logic:
        # saved, packaged, applied -> Positive (+2 role, +1 keyword)
        # skipped -> Negative (-2 role, -1 keyword)

        delta_role = 0
        delta_keyword = 0
        if action in ("saved", "packaged", "applied"):
            delta_role = 2
            delta_keyword = 1
        elif action == "skipped":
            delta_role = -2
            delta_keyword = -1

        if role_type and role_type != "unknown":
            role_scores[role_type] = role_scores.get(role_type, 0) + delta_role

        if matched_keywords_json:
            try:
                keywords = json.loads(matched_keywords_json)
                for kw in keywords:
                    keyword_scores[kw] = keyword_scores.get(kw, 0) + delta_keyword
            except json.JSONDecodeError:
                pass

    # Clamp results
    role_families = {
        role: max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, score))
        for role, score in role_scores.items()
        if score != 0
    }
    keywords = {
        kw: max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, score))
        for kw, score in keyword_scores.items()
        if score != 0
    }

    return {"role_families": role_families, "keywords": keywords}


def sync_weights() -> dict[str, Any]:
    weights = compute_learned_weights()
    save_learned_weights(weights)
    return weights
