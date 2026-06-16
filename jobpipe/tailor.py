from __future__ import annotations

from .models import CandidateProfile, ParsedJob, RolePack, TailorResult, TailoredBullet


FORBIDDEN_TOKENS = {
    "active directory admin experience",
    "servicenow experience",
    "corporate help desk experience",
    "security clearance",
    "zendesk",
    "salesforce",
    "office 365 admin",
}


def tailor_job(job: ParsedJob, role_type: str, pack: RolePack | None, candidate: CandidateProfile) -> TailorResult:
    text = f"{job.title} {job.description}".lower()
    angle = _choose_angle(text, pack)
    skills = _select_skills(text, candidate)
    bullets = _select_bullets(text, angle, candidate)
    answers = _draft_answers(pack, angle)
    summary = candidate.profile.get("summary", "")
    cover_note = (
        f"I am interested in this {job.title or role_type} role because it aligns with my background in "
        f"{angle}, clear communication, and structured problem solving."
    )
    return TailorResult(
        resume_summary=summary,
        selected_skills=skills,
        tailored_bullets=bullets,
        short_answers=answers,
        cover_note=cover_note,
        resume_angle=angle,
    )


def _choose_angle(text: str, pack: RolePack | None) -> str:
    if pack and pack.resume_angles:
        for angle in pack.resume_angles:
            if angle.replace("_", " ") in text:
                return angle
        return pack.resume_angles[0]
    return "transferable problem solving"


def _select_skills(text: str, candidate: CandidateProfile) -> list[str]:
    selected: list[str] = []
    for values in candidate.skills.values():
        for item in values:
            if item.lower() in text or len(selected) < 8:
                if item not in selected:
                    selected.append(item)
    return selected[:10]


def _select_bullets(text: str, angle: str, candidate: CandidateProfile) -> list[TailoredBullet]:
    chosen: list[TailoredBullet] = []
    for project in candidate.projects:
        project_blob = " ".join(project.technologies + project.support_angles + project.technical_angles + project.proof_points).lower()
        if any(term in text for term in project_blob.split()) or len(chosen) < 4:
            for bullet in project.bullets[:2]:
                rewritten = _rewrite_bullet(bullet, angle)
                _assert_truthful(rewritten)
                chosen.append(
                    TailoredBullet(
                        text=rewritten,
                        angle=angle,
                        provenance=f"project:{project.name}",
                        support_note=project.description,
                    )
                )
                if len(chosen) >= 6:
                    return chosen
    for experience in candidate.experience:
        for bullet in experience.bullets[:1]:
            rewritten = _rewrite_bullet(bullet, angle)
            _assert_truthful(rewritten)
            chosen.append(
                TailoredBullet(
                    text=rewritten,
                    angle=angle,
                    provenance=f"experience:{experience.title}",
                    support_note=experience.organization,
                )
            )
    return chosen[:6]


def _rewrite_bullet(bullet: str, angle: str) -> str:
    if angle == "documentation" and "document" not in bullet.lower():
        return f"{bullet.rstrip('.')} while documenting repeatable steps for future troubleshooting."
    if angle == "customer communication" and "communication" not in bullet.lower():
        return f"{bullet.rstrip('.')} with attention to clear user-facing explanations."
    return bullet


def _assert_truthful(text: str) -> None:
    lowered = text.lower()
    for token in FORBIDDEN_TOKENS:
        if token in lowered:
            raise ValueError(f"Tailored output contains forbidden claim: {token}")


def _draft_answers(pack: RolePack | None, angle: str) -> dict[str, str]:
    why_interested = (
        pack.answer_angles.get("why_interested", ["I am interested because the role fits my troubleshooting and communication strengths."])[0]
        if pack
        else "I am interested because the role aligns with my practical problem-solving background."
    )
    weakness_bridge = (
        pack.answer_angles.get("weakness_bridge", ["My background is project-driven rather than corporate, but I have built real workflows and can learn quickly."])[0]
        if pack
        else "My background is project-driven rather than role-title-driven, but I have built and debugged real systems."
    )
    return {
        "why_interested": why_interested,
        "weakness_bridge": weakness_bridge,
        "resume_angle": f"I would emphasize {angle} and transferable technical support habits.",
    }
