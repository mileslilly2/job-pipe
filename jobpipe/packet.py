from __future__ import annotations

from .models import JobAnalysis, PacketContext


def build_packet_context(analysis: JobAnalysis) -> PacketContext:
    if analysis.risk_score >= 65:
        return PacketContext(
            recommendation="Skip",
            rationale="Risk indicators outweigh the potential fit.",
            concerns=analysis.red_flags or ["Multiple credibility issues."],
        )
    if analysis.fit_score >= 70 and analysis.quality_score >= 55:
        return PacketContext(
            recommendation="Apply",
            rationale="The role looks credible and aligns with the target profile.",
            concerns=analysis.red_flags,
        )
    return PacketContext(
        recommendation="Maybe apply",
        rationale="There is some alignment, but the role needs closer human review.",
        concerns=analysis.red_flags,
    )
