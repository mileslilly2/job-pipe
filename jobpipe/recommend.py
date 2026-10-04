from __future__ import annotations

from .models import JobAnalysis


def make_recommendation(
    fit_score: int,
    risk_score: int,
    quality_score: int,
    role_type: str,
    seniority_level: str,
    red_flags: list[str],
) -> tuple[str, str | None]:
    if risk_score >= 75:
        return "skip", "high scam/quality risk"

    if seniority_level in {"senior", "staff", "principal", "manager", "director"}:
        return "skip", "seniority mismatch"

    if role_type == "unknown":
        return "skip", "unsupported role"

    if fit_score < 40:
        return "skip", "poor fit"

    if fit_score >= 70 and risk_score <= 40 and quality_score >= 45:
        return "apply", None

    return "maybe", None


def reasoned_recommendation(analysis: JobAnalysis) -> tuple[str, str | None]:
    return make_recommendation(
        analysis.fit_score,
        analysis.risk_score,
        analysis.quality_score,
        analysis.role_type,
        analysis.seniority_level,
        analysis.red_flags,
    )
