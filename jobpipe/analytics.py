from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .catalog import load_search_profile
from .classification import normalize_role_family
from .application_package import application_package_paths
from .db import due_followups, fetch_all, list_jobs
from .learn import load_learned_weights
from .score import score_job_fit_breakdown

HIGH_FIT_THRESHOLD = 70
TOP_LIMIT = 10


@dataclass(frozen=True)
class AnalyticsReport:
    generated_at: str
    summary: dict[str, int]
    current_goal: str | None = None
    top_apply_today: list[dict[str, Any]] = field(default_factory=list)
    top_role_families_by_score: list[dict[str, Any]] = field(default_factory=list)
    top_sources_by_high_fit_jobs: list[dict[str, Any]] = field(default_factory=list)
    skip_reasons: list[dict[str, Any]] = field(default_factory=list)
    role_family_rates: list[dict[str, Any]] = field(default_factory=list)


def build_analytics_report(*, high_fit_threshold: int = HIGH_FIT_THRESHOLD, top_limit: int = TOP_LIMIT) -> AnalyticsReport:
    profile = load_search_profile()
    jobs = _decorate_jobs(list_jobs(), profile=profile)
    today = datetime.now().astimezone().date()
    week_start = today - timedelta(days=today.weekday())

    applications = fetch_all("SELECT job_id, status, applied_at FROM applications WHERE status = 'applied'")
    applications_today = 0
    applications_this_week = 0
    for row in applications:
        applied_at = _parse_timestamp(row['applied_at'])
        if applied_at is None:
            continue
        applied_date = applied_at.astimezone().date()
        if applied_date == today:
            applications_today += 1
        if applied_date >= week_start:
            applications_this_week += 1

    summary = {
        'applications_today': applications_today,
        'applications_this_week': applications_this_week,
        'saved_not_packaged': sum(1 for job in jobs if str(job.get('review_status') or '').strip().lower() == 'saved' and not job['has_package']),
        'packaged_not_applied': sum(1 for job in jobs if job['has_package'] and _display_status(job) != 'applied'),
        'followups_due': len(due_followups(limit=1000)),
        'high_fit_jobs': sum(1 for job in jobs if _is_high_fit_active(job, high_fit_threshold)),
        'active_jobs': sum(1 for job in jobs if _is_active_job(job)),
        "goal_satisfying_jobs": sum(1 for job in jobs if int(job.get("goal_modifier") or 0) > 0),
    }

    return AnalyticsReport(
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        current_goal=profile.active_goal,
        summary=summary,
        top_apply_today=_top_apply_candidates(jobs, top_limit=top_limit),
        top_role_families_by_score=_top_role_families_by_score(jobs),
        top_sources_by_high_fit_jobs=_top_sources_by_high_fit_jobs(jobs, high_fit_threshold=high_fit_threshold),
        skip_reasons=_skip_reasons(jobs),
        role_family_rates=_role_family_rates(jobs),
    )


def _decorate_jobs(rows, *, profile=None) -> list[dict[str, Any]]:
    learned_weights = load_learned_weights()
    profile = profile or load_search_profile()
    jobs: list[dict[str, Any]] = []
    for row in rows:
        job = dict(row)
        role_type = str(job.get("role_type") or "").strip()
        if role_type:
            job["role_type"] = normalize_role_family(role_type)
            job["primary_role_family"] = job["role_type"]
        from .utils import load_json
        job["secondary_role_families"] = load_json(job.get("secondary_role_families_json") or "[]")
        breakdown = score_job_fit_breakdown(job, learned_weights=learned_weights, profile=profile)
        job['score'] = breakdown.displayed_score
        job['score_notes'] = list(breakdown.notes)
        job["goal_modifier"] = breakdown.goal_specific_total
        job['display_status'] = _display_status(job)
        paths = application_package_paths(job)
        job['has_package'] = any(path.exists() for path in [paths.job_md, paths.fit_md, paths.resume_tailored_md, paths.cover_note_md, paths.apply_md])
        job['bucket'] = _job_bucket(job)
        jobs.append(job)
    return jobs


def _top_apply_candidates(jobs: list[dict[str, Any]], *, top_limit: int) -> list[dict[str, Any]]:
    candidates = [job for job in jobs if _is_actionable(job)]
    ranked = sorted(
        candidates,
        key=lambda job: (
            -int(job.get('score') or 0),
            0 if _display_status(job) == 'packaged' else 1,
            int(job.get('risk_score') or 0),
            -int(job.get('quality_score') or 0),
            str(job.get('discovered_at') or ''),
            int(job.get('id') or 0),
        ),
    )
    return [
        {
            'id': job['id'],
            'company': job.get('company') or '',
            'title': job.get('title') or '',
            'role_type': job.get('role_type') or '',
            'seniority_level': job.get('seniority_level') or '',
            'score': job.get('score') or 0,
            'recommendation': job.get('recommendation') or '',
            'status': _display_status(job),
            'has_package': bool(job.get('has_package')),
            'apply_url': job.get('apply_url') or job.get('source_url') or '',
            'source': _source_label(job),
        }
        for job in ranked[:top_limit]
    ]


def _top_role_families_by_score(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for job in jobs:
        family = str(job.get('role_type') or '').strip()
        if not family or family == 'unknown':
            continue
        grouped[family].append(int(job.get('score') or 0))
    ranked = sorted(
        (
            {
                'role_family': family,
                'count': len(scores),
                'average_score': round(sum(scores) / len(scores), 1),
                'high_fit_count': sum(1 for job in jobs if str(job.get('role_type') or '').strip() == family and _is_high_fit_active(job, HIGH_FIT_THRESHOLD)),
            }
            for family, scores in grouped.items()
        ),
        key=lambda item: (-item['average_score'], -item['high_fit_count'], -item['count'], item['role_family']),
    )
    return ranked[:10]


def _top_sources_by_high_fit_jobs(jobs: list[dict[str, Any]], *, high_fit_threshold: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for job in jobs:
        if not _is_high_fit_active(job, high_fit_threshold):
            continue
        score = int(job.get('score') or 0)
        grouped[_source_label(job)].append(score)
    ranked = sorted(
        (
            {
                'source': source,
                'count': len(scores),
                'average_score': round(sum(scores) / len(scores), 1),
            }
            for source, scores in grouped.items()
        ),
        key=lambda item: (-item['count'], -item['average_score'], item['source']),
    )
    return ranked[:10]


def _skip_reasons(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter()
    for job in jobs:
        reason = str(job.get('skip_reason') or '').strip()
        if reason:
            counter[reason] += 1
    return [{'reason': reason, 'count': count} for reason, count in counter.most_common(10)]


def _role_family_rates(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {'total': 0, 'saved': 0, 'applied': 0})
    for job in jobs:
        family = str(job.get('role_type') or '').strip() or 'unknown'
        if family == 'unknown':
            continue
        status = _display_status(job)
        bucket = grouped[family]
        bucket['total'] += 1
        if status == 'saved':
            bucket['saved'] += 1
        if status == 'applied':
            bucket['applied'] += 1
    ranked = sorted(
        (
            {
                'role_family': family,
                'total': values['total'],
                'saved_count': values['saved'],
                'applied_count': values['applied'],
                'saved_rate': round(values['saved'] / values['total'], 3) if values['total'] else 0.0,
                'applied_rate': round(values['applied'] / values['total'], 3) if values['total'] else 0.0,
            }
            for family, values in grouped.items()
        ),
        key=lambda item: (-item['total'], item['role_family']),
    )
    return ranked


def _is_actionable(job: dict[str, Any]) -> bool:
    status = _display_status(job)
    if status in {'applied', 'rejected', 'skipped', 'archived', 'ignored'}:
        return False
    recommendation = str(job.get('recommendation') or '').strip().lower()
    if recommendation == 'skip':
        return False
    return True


def _is_high_fit_active(job: dict[str, Any], threshold: int) -> bool:
    return _is_active_job(job) and int(job.get("score") or 0) >= threshold


def _is_active_job(job: dict[str, Any]) -> bool:
    return _display_status(job) not in {'rejected', 'skipped', 'archived', 'ignored'}


def _display_status(job: dict[str, Any]) -> str:
    application_status = str(job.get('status') or '').strip().lower()
    review_status = str(job.get('review_status') or '').strip().lower()
    if application_status in {'applied', 'rejected', 'interview', 'offer', 'archived', 'ignored'}:
        return application_status
    if application_status in {'analyzed', 'ready_to_apply'}:
        return application_status
    return review_status or application_status or 'new'


def _job_bucket(job: dict[str, Any]) -> str:
    display_status = _display_status(job)
    if display_status == 'skipped':
        return 'skipped'
    if display_status == 'rejected':
        return 'rejected'
    if display_status == 'interview':
        return 'interview'
    if display_status == 'offer':
        return 'offer'
    if display_status == 'applied':
        return 'applied'
    if display_status in {'analyzed', 'ready_to_apply'}:
        return 'assessment'
    if display_status == 'saved':
        return 'saved'
    if display_status == 'new':
        return 'new'
    if display_status in {'archived', 'ignored'}:
        return display_status
    return display_status or 'new'


def _source_label(job: dict[str, Any]) -> str:
    provider = str(job.get('provider') or 'manual').strip() or 'manual'
    company = str(job.get('company') or 'unknown company').strip() or 'unknown company'
    return f'{provider} / {company}'


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
