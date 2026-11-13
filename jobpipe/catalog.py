from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import JobSource, SearchProfile
from .utils import data_dir, load_yaml, package_dir, slugify

DEFAULT_SEARCH_PROFILE_NAME = "miles"
SEARCH_PROFILES_FILENAME = "search_profiles.yaml"
SOURCE_PACKS_FILENAME = "source_packs.yaml"


@dataclass(frozen=True)
class LoadedSearchProfiles:
    profiles: dict[str, SearchProfile]
    origin: str
    path: Path | None = None


@dataclass(frozen=True)
class LoadedSourcePacks:
    packs: dict[str, list[JobSource]]
    origin: str
    path: Path | None = None


def search_profiles_path() -> Path:
    return data_dir() / SEARCH_PROFILES_FILENAME


def source_packs_path() -> Path:
    return data_dir() / SOURCE_PACKS_FILENAME


def package_search_profiles_path() -> Path:
    return package_dir() / "data" / SEARCH_PROFILES_FILENAME


def package_source_packs_path() -> Path:
    return package_dir() / "data" / SOURCE_PACKS_FILENAME


def load_search_profiles(path: Path | None = None) -> LoadedSearchProfiles:
    actual = path or search_profiles_path()
    raw, origin, resolved = _load_yaml_with_fallback(actual, package_search_profiles_path())
    profiles: dict[str, SearchProfile] = {}
    if raw:
        if not isinstance(raw, list):
            raise ValueError("search profile config must be a YAML list")
        for item in raw:
            profile = SearchProfile.model_validate(item)
            normalized = normalize_search_profile(profile)
            if normalized.active_goal:
                from .knowledge import require_goal
                require_goal(normalized.active_goal)
            profiles[normalized.name] = normalized
    return LoadedSearchProfiles(profiles=profiles, origin=origin, path=resolved)


def load_search_profile(name: str = DEFAULT_SEARCH_PROFILE_NAME, path: Path | None = None) -> SearchProfile:
    profiles = load_search_profiles(path).profiles
    key = name.strip().lower()
    if key not in profiles:
        raise ValueError(f"Search profile {name!r} not found.")
    return profiles[key]


def load_source_packs(path: Path | None = None) -> LoadedSourcePacks:
    actual = path or source_packs_path()
    raw, origin, resolved = _load_yaml_with_fallback(actual, package_source_packs_path())
    packs: dict[str, list[JobSource]] = {}
    if raw:
        if not isinstance(raw, dict):
            raise ValueError("source pack config must be a YAML mapping")
        for category, items in raw.items():
            if not isinstance(items, list):
                raise ValueError(f"source pack {category!r} must be a YAML list")
            packs[str(category).strip().lower()] = [normalize_job_source(JobSource.model_validate(item)) for item in items]
    return LoadedSourcePacks(packs=packs, origin=origin, path=resolved)


def load_source_pack(name: str, path: Path | None = None) -> list[JobSource]:
    packs = load_source_packs(path).packs
    key = name.strip().lower()
    if key not in packs:
        raise ValueError(f"Source pack {name!r} not found.")
    return packs[key]


def source_identity(source: JobSource) -> tuple[str, str, str, str]:
    provider = (source.provider or "").strip().lower()
    company = (source.company or "").strip().lower()
    company_slug = _source_company_slug(source)
    token = (source.board_token or source.account or "").strip().lower()
    return provider, company_slug, token, company


def source_identity_label(source: JobSource) -> str:
    provider, company_slug, token, company = source_identity(source)
    identifier = (token or company_slug or company or provider).strip().lower()
    return f"{provider}:{identifier}"


def normalize_job_source(source: JobSource) -> JobSource:
    provider = (source.provider or "").strip().lower()
    company = (source.company or "").strip()
    company_slug = _source_company_slug(source)
    board_token = (source.board_token or "").strip() or None
    account = (source.account or "").strip() or None
    target_role_families = [value.strip().lower() for value in source.target_role_families if value and value.strip()]
    return JobSource(
        provider=provider,
        company=company,
        company_slug=company_slug,
        board_token=board_token,
        account=account,
        target_role_families=target_role_families,
        include_keywords=[value.strip() for value in source.include_keywords if value and value.strip()],
        exclude_keywords=[value.strip() for value in source.exclude_keywords if value and value.strip()],
        enabled=source.enabled,
    )


def normalize_search_profile(profile: SearchProfile) -> SearchProfile:
    # Backwards compatibility fallback logic
    preferred_raw = profile.preferred if profile.preferred else profile.target_role_families
    avoid_raw = profile.avoid if profile.avoid else profile.excluded_role_families

    return SearchProfile(
        name=profile.name.strip().lower(),
        active_goal=profile.active_goal.strip().lower() if profile.active_goal else None,
        preferred=[value.strip().lower() for value in preferred_raw if value and value.strip()],
        acceptable=[value.strip().lower() for value in profile.acceptable if value and value.strip()],
        avoid=[value.strip().lower() for value in avoid_raw if value and value.strip()],
        target_role_families=[],
        excluded_role_families=[],
        positive_keywords=[value.strip().lower() for value in profile.positive_keywords if value and value.strip()],
        negative_keywords=[value.strip().lower() for value in profile.negative_keywords if value and value.strip()],
        preferred_locations=[value.strip().lower() for value in profile.preferred_locations if value and value.strip()],
        allowed_locations=[value.strip().lower() for value in profile.allowed_locations if value and value.strip()],
        excluded_locations=[value.strip().lower() for value in profile.excluded_locations if value and value.strip()],
        preferred_work_styles=[value.strip().lower() for value in profile.preferred_work_styles if value and value.strip()],
        preferred_role_levels=[value.strip().lower() for value in profile.preferred_role_levels if value and value.strip()],
        excluded_levels=[value.strip().lower() for value in profile.excluded_levels if value and value.strip()],
    )


def save_search_profiles(loaded: LoadedSearchProfiles, path: Path | None = None) -> None:
    actual = path or loaded.path or search_profiles_path()
    payload = [
        profile.model_dump(exclude_none=True, exclude={"target_role_families", "excluded_role_families"})
        for profile in loaded.profiles.values()
    ]
    import yaml
    actual.write_text(yaml.safe_dump(payload, sort_keys=False, default_flow_style=False), encoding="utf-8")


def search_profile_label(profile: SearchProfile) -> str:
    return profile.name


def source_pack_summary(packs: dict[str, list[JobSource]]) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {}
    for category, sources in packs.items():
        summary[category] = [source_identity_label(source) for source in sources]
    return summary


def _source_company_slug(source: JobSource) -> str:
    company = (source.company or "").strip()
    company_slug = (source.company_slug or "").strip()
    token = (source.board_token or source.account or "").strip()
    return company_slug or token or slugify(company or source.provider)


def _load_yaml_with_fallback(path: Path, fallback: Path) -> tuple[Any, str, Path]:
    if path.exists():
        raw = load_yaml(path)
        if raw not in (None, [], {}):
            return raw, "workspace", path
    if fallback.exists():
        return load_yaml(fallback), "built_in", fallback
    return None, "none", path
