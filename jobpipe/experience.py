"""Conservative experience requirements; ranges are consumed as a single match."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import re


@dataclass(frozen=True)
class ExperienceRequirement:
    minimum: float | None
    maximum: float | None
    upper_open: bool
    preferred: bool
    ambiguous: bool
    evidence: str


_NUMBER = r"\d{1,2}(?:\.\d+)?"
_YEARS = re.compile(
    rf"\b(?P<prefix>at least\s+|minimum(?: of)?\s+|min\.?\s+|up to\s+|maximum(?: of)?\s+|no more than\s+)?"
    rf"(?P<lo>{_NUMBER})\s*(?:(?:[-–—]|to)\s*(?P<hi>{_NUMBER})\s*)?"
    r"(?P<plus>\+)?\s*years?\b", re.I)
_PREFERRED = re.compile(r"\b(preferred|preferably|nice.to.have|bonus|ideally|desirable)\b", re.I)


def extract_experience_requirements(text: str) -> list[ExperienceRequirement]:
    """Retain evidence and alternatives instead of inventing one mandatory number."""
    result: list[ExperienceRequirement] = []
    preferred_section = False
    for clause in re.split(r"[\n;]|(?<=[.!?])\s+", text):
        stripped = clause.strip()
        if not stripped:
            continue
        heading = stripped.lower().strip(" :*-#")
        if heading in {"preferred", "preferred qualifications", "preferred skills", "nice to have", "nice-to-have", "bonus", "bonus points"}:
            preferred_section = True
        elif heading in {"required", "requirements", "required qualifications", "required skills", "qualifications", "minimum qualifications", "responsibilities", "benefits", "what you will do", "what you need"}:
            preferred_section = False
        for match in _YEARS.finditer(stripped):
            # Salary periods, company age, vacation and product history are not experience.
            context = stripped.lower()
            if not (re.search(r"experience|\b(?:at least|minimum|min\.?|up to|no more than)\b", context)
                    or match["hi"] or match["plus"] or _PREFERRED.search(context)):
                continue
            if re.search(r"(?:founded|company is|in business|vacation|paid leave|warranty)", context):
                continue
            lo = float(match["lo"])
            hi = float(match["hi"]) if match["hi"] else None
            upper_only = (match["prefix"] or "").lower().strip() in {"up to", "no more than", "maximum", "maximum of"}
            result.append(ExperienceRequirement(
                minimum=None if upper_only else lo,
                maximum=lo if upper_only else hi,
                upper_open=bool(match["plus"]),
                preferred=preferred_section or bool(_PREFERRED.search(stripped)),
                ambiguous=bool(hi is not None and (hi < lo or match["plus"]))
                          or bool(re.search(r"\bor\b|equivalent|combined", context)),
                evidence=stripped,
            ))
    return list(dict.fromkeys(result))


def experience_dicts(text: str) -> list[dict]:
    return [asdict(item) for item in extract_experience_requirements(text)]
