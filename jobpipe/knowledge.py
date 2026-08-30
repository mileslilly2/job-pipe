from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import SearchProfile
from .utils import data_dir, load_yaml, package_dir


@dataclass(frozen=True)
class KnowledgeWeights:
    exact: int = 8
    alias: int = 6
    related: int = 3
    conflict: int = -12
    preferred_role_family: int = 4
    preferred_concept: int = 2


@dataclass(frozen=True)
class KnowledgeConcept:
    name: str
    aliases: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    role_families: tuple[str, ...] = ()
    weights: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeProfile:
    preferred_concepts: tuple[str, ...] = ()
    avoided_concepts: tuple[str, ...] = ()
    preferred_role_families: tuple[str, ...] = ()
    avoided_role_families: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeCatalog:
    concepts: dict[str, KnowledgeConcept] = field(default_factory=dict)
    profiles: dict[str, KnowledgeProfile] = field(default_factory=dict)
    weights: KnowledgeWeights = field(default_factory=KnowledgeWeights)


@dataclass(frozen=True)
class KnowledgeMatch:
    concept: str
    match_type: str
    phrase: str
    score: int
    source: str | None = None


@dataclass(frozen=True)
class KnowledgeReport:
    matches: list[KnowledgeMatch] = field(default_factory=list)
    profile_boosts: list[str] = field(default_factory=list)
    conflict_penalties: list[str] = field(default_factory=list)
    score_adjustment: int = 0
    goal_modifier: int = 0
    goal_notes: list[str] = field(default_factory=list)

    @property
    def exact_matches(self) -> list[KnowledgeMatch]:
        return [match for match in self.matches if match.match_type == "exact"]

    @property
    def alias_matches(self) -> list[KnowledgeMatch]:
        return [match for match in self.matches if match.match_type == "alias"]

    @property
    def related_matches(self) -> list[KnowledgeMatch]:
        return [match for match in self.matches if match.match_type == "related"]

    @property
    def notes(self) -> list[str]:
        notes: list[str] = []
        for match in self.matches:
            if match.match_type == "exact":
                notes.append(f"exact concept match: {match.concept} via '{match.phrase}' ({match.score:+d})")
            elif match.match_type == "alias":
                notes.append(f"alias match: {match.concept} via '{match.phrase}' ({match.score:+d})")
            elif match.match_type == "related":
                source = f" via {match.source}" if match.source else ""
                notes.append(f"related concept match: {match.concept}{source} ({match.score:+d})")
        notes.extend(self.profile_boosts)
        notes.extend(self.conflict_penalties)
        if self.score_adjustment or notes:
            notes.append(f"knowledge score adjustment: {self.score_adjustment:+d}")
        return notes


def knowledge_path() -> Path:
    return data_dir() / "knowledge.yaml"


def package_knowledge_path() -> Path:
    return package_dir() / "data" / "knowledge.yaml"


def load_knowledge(path: Path | None = None) -> KnowledgeCatalog:
    actual = path or knowledge_path()
    raw = _load_raw_knowledge(actual)
    return KnowledgeCatalog(
        concepts=_parse_concepts(raw.get("concepts", {})),
        profiles=_parse_profiles(raw.get("profiles", {})),
        weights=_parse_weights(raw.get("match_weights", {})),
    )


GOAL_DESCRIPTIONS = {
    "career_acceleration": "career acceleration",
    "maximize_income": "maximizing income",
    "get_employed_fast": "rapid employment",
    "remote_first": "remote-first work",
    "learn_new_skills": "learning new skills",
    "stable_employment": "stable employment",
    "resume_building": "resume building",
}


def build_knowledge_report(job: Any, profile: SearchProfile | None = None, catalog: KnowledgeCatalog | None = None) -> KnowledgeReport:
    knowledge = catalog or load_knowledge()
    text = _job_text(job)
    normalized_text = _normalize_phrase(text)

    direct_matches: list[KnowledgeMatch] = []
    matched_concepts: dict[str, KnowledgeMatch] = {}

    for concept_name in sorted(knowledge.concepts):
        concept = knowledge.concepts[concept_name]
        exact_phrase = _concept_phrase(concept.name)
        if _phrase_in_text(normalized_text, exact_phrase):
            match = KnowledgeMatch(concept=concept.name, match_type="exact", phrase=exact_phrase, score=knowledge.weights.exact)
            direct_matches.append(match)
            matched_concepts[concept.name] = match
            continue
        alias = _first_matching_phrase(normalized_text, concept.aliases)
        if alias is not None:
            match = KnowledgeMatch(concept=concept.name, match_type="alias", phrase=alias, score=knowledge.weights.alias)
            direct_matches.append(match)
            matched_concepts[concept.name] = match

    related_matches: list[KnowledgeMatch] = []
    seen_related: set[str] = set()
    for source_name in sorted(matched_concepts):
        source_concept = knowledge.concepts[source_name]
        for related_name in sorted(source_concept.related):
            if related_name in matched_concepts or related_name in seen_related:
                continue
            if related_name not in knowledge.concepts:
                continue
            match = KnowledgeMatch(
                concept=related_name,
                match_type="related",
                phrase=_concept_phrase(source_name),
                score=knowledge.weights.related,
                source=source_name,
            )
            related_matches.append(match)
            seen_related.add(related_name)

    matches = _dedupe_matches(direct_matches + related_matches)
    preferred_concepts, avoided_concepts, preferred_role_families, avoided_role_families = _profile_preferences(profile, knowledge, normalized_text)

    profile_boosts: list[str] = []
    conflict_penalties: list[str] = []
    score_adjustment = sum(match.score for match in matches)

    for match in matches:
        concept = knowledge.concepts[match.concept]

        if match.concept in preferred_concepts:
            score_adjustment += knowledge.weights.preferred_concept
            profile_boosts.append(f"profile preference boost: concept {match.concept} ({knowledge.weights.preferred_concept:+d})")

        preferred_roles = sorted(set(concept.role_families) & preferred_role_families)
        for role_family in preferred_roles:
            score_adjustment += knowledge.weights.preferred_role_family
            profile_boosts.append(f"profile preference boost: role family {role_family} via {match.concept} ({knowledge.weights.preferred_role_family:+d})")

        conflict_reasons: list[str] = []
        if match.concept in avoided_concepts:
            conflict_reasons.append(f"avoided concept {match.concept}")
        avoided_roles = sorted(set(concept.role_families) & avoided_role_families)
        if avoided_roles:
            conflict_reasons.append("avoided role family " + ", ".join(avoided_roles))
        conflicting_targets = sorted(set(concept.conflicts) & (preferred_concepts | preferred_role_families))
        if conflicting_targets:
            conflict_reasons.append("conflicts with " + ", ".join(conflicting_targets))
        if conflict_reasons:
            score_adjustment += knowledge.weights.conflict
            conflict_penalties.append(
                f"conflict penalty: {match.concept} ({knowledge.weights.conflict:+d}; " + "; ".join(conflict_reasons) + ")"
            )

    goal_modifier = 0
    goal_notes: list[str] = []
    active_goal = profile.active_goal if profile else None
    if active_goal:
        matched_concept_names = {match.concept for match in matches}
        for concept_name in sorted(matched_concept_names):
            concept = knowledge.concepts.get(concept_name)
            if concept and concept.weights and active_goal in concept.weights:
                wt = concept.weights[active_goal]
                goal_modifier += wt
                concept_display = " ".join(part.capitalize() for part in concept_name.split("_"))
                goal_desc = GOAL_DESCRIPTIONS.get(active_goal, active_goal.replace("_", " "))
                if wt >= 0:
                    goal_notes.append(f"+{wt} {concept_display} supports {goal_desc}")
                else:
                    goal_notes.append(f"{wt} {concept_display} conflicts with {goal_desc}")

    return KnowledgeReport(
        matches=matches,
        profile_boosts=profile_boosts,
        conflict_penalties=conflict_penalties,
        score_adjustment=score_adjustment,
        goal_modifier=goal_modifier,
        goal_notes=goal_notes,
    )


def _load_raw_knowledge(path: Path) -> dict[str, Any]:
    if path.exists():
        raw = load_yaml(path)
        if isinstance(raw, dict) and raw:
            return raw
    fallback = package_knowledge_path()
    if fallback.exists():
        raw = load_yaml(fallback)
        if isinstance(raw, dict) and raw:
            return raw
    return {}


def _parse_weights(raw: Any) -> KnowledgeWeights:
    if not isinstance(raw, dict):
        return KnowledgeWeights()
    return KnowledgeWeights(
        exact=int(raw.get("exact", KnowledgeWeights.exact)),
        alias=int(raw.get("alias", KnowledgeWeights.alias)),
        related=int(raw.get("related", KnowledgeWeights.related)),
        conflict=int(raw.get("conflict", KnowledgeWeights.conflict)),
        preferred_role_family=int(raw.get("preferred_role_family", KnowledgeWeights.preferred_role_family)),
        preferred_concept=int(raw.get("preferred_concept", KnowledgeWeights.preferred_concept)),
    )


def _parse_concepts(raw: Any) -> dict[str, KnowledgeConcept]:
    if not isinstance(raw, dict):
        return {}
    concepts: dict[str, KnowledgeConcept] = {}
    for name, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        concept_name = _normalize_identifier(str(name))
        weights_raw = payload.get("weights", {})
        weights: dict[str, int] = {}
        if isinstance(weights_raw, dict):
            for k, v in weights_raw.items():
                weights[str(k).strip().lower()] = int(v)
        concepts[concept_name] = KnowledgeConcept(
            name=concept_name,
            aliases=tuple(_normalize_phrases(payload.get("aliases", []))),
            related=tuple(_normalize_identifiers(payload.get("related", []))),
            conflicts=tuple(_normalize_identifiers(payload.get("conflicts", []))),
            role_families=tuple(_normalize_identifiers(payload.get("role_families", []))),
            weights=weights,
        )
    return concepts


def _parse_profiles(raw: Any) -> dict[str, KnowledgeProfile]:
    if not isinstance(raw, dict):
        return {}
    profiles: dict[str, KnowledgeProfile] = {}
    for name, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        profile_name = _normalize_identifier(str(name))
        profiles[profile_name] = KnowledgeProfile(
            preferred_concepts=tuple(_normalize_identifiers(payload.get("preferred_concepts", []))),
            avoided_concepts=tuple(_normalize_identifiers(payload.get("avoided_concepts", []))),
            preferred_role_families=tuple(_normalize_identifiers(payload.get("preferred_role_families", []))),
            avoided_role_families=tuple(_normalize_identifiers(payload.get("avoided_role_families", []))),
        )
    return profiles


def _profile_preferences(profile: SearchProfile | None, knowledge: KnowledgeCatalog, normalized_text: str) -> tuple[set[str], set[str], set[str], set[str]]:
    preferred_concepts: set[str] = set()
    avoided_concepts: set[str] = set()
    preferred_role_families: set[str] = set()
    avoided_role_families: set[str] = set()

    catalog_profile = knowledge.profiles.get("miles")
    if catalog_profile is not None:
        preferred_concepts.update(catalog_profile.preferred_concepts)
        avoided_concepts.update(catalog_profile.avoided_concepts)
        preferred_role_families.update(catalog_profile.preferred_role_families)
        avoided_role_families.update(catalog_profile.avoided_role_families)

    if profile is not None:
        for keyword in profile.positive_keywords:
            preferred_concepts.update(_concepts_for_phrase(keyword, knowledge, normalized_text))
        for keyword in profile.negative_keywords:
            avoided_concepts.update(_concepts_for_phrase(keyword, knowledge, normalized_text))

    return preferred_concepts, avoided_concepts, preferred_role_families, avoided_role_families


def _concepts_for_phrase(phrase: str, knowledge: KnowledgeCatalog, normalized_text: str) -> set[str]:
    normalized_phrase = _normalize_phrase(phrase)
    if not normalized_phrase or not _phrase_in_text(normalized_text, normalized_phrase):
        return set()
    matches: set[str] = set()
    for concept_name, concept in knowledge.concepts.items():
        if normalized_phrase == concept_name or normalized_phrase in concept.aliases:
            matches.add(concept_name)
    return matches


def _dedupe_matches(matches: list[KnowledgeMatch]) -> list[KnowledgeMatch]:
    seen: set[tuple[str, str]] = set()
    deduped: list[KnowledgeMatch] = []
    for match in matches:
        key = (match.concept, match.match_type)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(match)
    deduped.sort(key=lambda match: ({"exact": 0, "alias": 1, "related": 2}.get(match.match_type, 9), match.concept, match.phrase, match.source or ""))
    return deduped


def _normalize_phrases(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized = [_normalize_phrase(str(value)) for value in values if str(value).strip()]
    return [value for value in normalized if value]


def _normalize_identifiers(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized = [_normalize_identifier(str(value)) for value in values if str(value).strip()]
    return [value for value in normalized if value]


def _job_text(job: Any) -> str:
    if isinstance(job, dict):
        values = [job.get("title", ""), job.get("company", ""), job.get("location", ""), job.get("description", "")]
    else:
        values = [getattr(job, "title", ""), getattr(job, "company", ""), getattr(job, "location", ""), getattr(job, "description", "")]
    return " ".join(str(value or "") for value in values)


def _concept_phrase(name: str) -> str:
    return name.replace("_", " ")


def _normalize_phrase(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else " " for char in value)
    return " ".join(cleaned.split())


def _normalize_identifier(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "_" for char in value)
    return "_".join(part for part in cleaned.split("_") if part)


def _phrase_in_text(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
    return re.search(pattern, text) is not None


def _first_matching_phrase(text: str, phrases: tuple[str, ...]) -> str | None:
    for phrase in sorted(set(phrases), key=lambda item: (-len(item), item)):
        if _phrase_in_text(text, phrase):
            return phrase
    return None
