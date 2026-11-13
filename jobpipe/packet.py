from __future__ import annotations

from .models import JobAnalysis, PacketContext


def build_packet_context(analysis: JobAnalysis) -> PacketContext:
    recommendation = analysis.recommendation
    reason = analysis.skip_reason

    if reason == "high scam/quality risk":
        explanation = "This posting has multiple scam or low-quality signals. Do not apply unless the company and application process can be independently verified."
    elif reason == "seniority mismatch":
        explanation = "This appears to be a real help desk / IT support role, but it is not a good target for the current candidate profile because it is senior/staff/principal/manager-level."
    elif reason == "poor fit":
        explanation = "The role family may be relevant, but the fit score is too low for this candidate profile."
    elif reason == "unsupported role":
        explanation = "The posting does not clearly map to a supported role family."
    elif recommendation == "apply":
        explanation = "This looks like a strong target based on role family, seniority, fit score, and low risk score."
    else:
        explanation = "There is some alignment, but the role needs closer human review."

    return PacketContext(
        recommendation=recommendation,
        reason=reason,
        explanation=explanation,
        concerns=analysis.red_flags,
    )
